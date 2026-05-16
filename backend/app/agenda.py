"""Deterministic agenda ranking for ADHDman's current-action display."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import sqlite3
from typing import Literal

from app.config import Settings
from app.db import get_connection

AgendaKind = Literal["task", "event", "inbox"]
Urgency = Literal[
    "before_event",
    "ongoing_event",
    "upcoming_event",
    "deadline",
    "overdue",
    "inbox",
]


@dataclass(frozen=True)
class AgendaItem:
    """A ranked item shown by the agenda engine."""

    kind: AgendaKind
    id: int
    title: str
    reason: str
    urgency: Urgency
    starts_at: str | None = None
    ends_at: str | None = None
    due_at: str | None = None
    suggested_commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgendaResponse:
    """Current recommendation plus compact next/later context."""

    now: AgendaItem | None
    next: list[AgendaItem]
    later: list[AgendaItem]
    counts: dict[str, int]


EVENT_SOON_WINDOW = timedelta(hours=2)


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _task_item(row: sqlite3.Row, *, urgency: Urgency, reason: str) -> AgendaItem:
    return AgendaItem(
        kind="task",
        id=row["id"],
        title=row["title"],
        reason=reason,
        urgency=urgency,
        due_at=row["due_at"],
        suggested_commands=("/집중", "/쪼개기", "/막힘"),
    )


def _event_item(row: sqlite3.Row, *, now_dt: datetime) -> AgendaItem:
    starts_at = _parse_dt(row["starts_at"])
    ends_at = _parse_dt(row["ends_at"])
    if starts_at is not None and starts_at <= now_dt and (ends_at is None or now_dt <= ends_at):
        urgency: Urgency = "ongoing_event"
        reason = "지금 진행 중인 일정이라서 먼저 보여줘요."
    else:
        urgency = "upcoming_event"
        reason = "곧 시작되는 일정이라서 준비할 수 있게 보여줘요."
    return AgendaItem(
        kind="event",
        id=row["id"],
        title=row["title"],
        reason=reason,
        urgency=urgency,
        starts_at=row["starts_at"],
        ends_at=row["ends_at"],
        suggested_commands=("/오늘", "/다음"),
    )


def _fetch_open_tasks(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT id, title, status, due_at, do_before_event_id, created_at, updated_at
            FROM tasks
            WHERE status = 'open'
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
    )


def _fetch_open_events(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT id, title, starts_at, ends_at, status, created_at, updated_at
            FROM events
            WHERE status != 'deleted'
            ORDER BY (starts_at IS NULL), starts_at ASC, id ASC
            """
        ).fetchall()
    )


def _fetch_open_inbox_count(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COUNT(*) FROM inbox_items WHERE status = 'open'"
    ).fetchone()
    return int(row[0])


def _event_by_id(events: list[sqlite3.Row]) -> dict[int, sqlite3.Row]:
    return {int(event["id"]): event for event in events}


def _future_events(events: list[sqlite3.Row], now_dt: datetime) -> list[sqlite3.Row]:
    visible: list[sqlite3.Row] = []
    for event in events:
        starts_at = _parse_dt(event["starts_at"])
        ends_at = _parse_dt(event["ends_at"])
        if starts_at is None:
            continue
        if starts_at >= now_dt or (ends_at is not None and starts_at <= now_dt <= ends_at):
            visible.append(event)
    return visible


def _is_ongoing_or_soon(event: sqlite3.Row, now_dt: datetime) -> bool:
    starts_at = _parse_dt(event["starts_at"])
    ends_at = _parse_dt(event["ends_at"])
    if starts_at is None:
        return False
    if starts_at <= now_dt and (ends_at is None or now_dt <= ends_at):
        return True
    return now_dt <= starts_at <= now_dt + EVENT_SOON_WINDOW


def _ranked_before_event_tasks(
    tasks: list[sqlite3.Row], events: list[sqlite3.Row], now_dt: datetime
) -> list[AgendaItem]:
    events_by_id = _event_by_id(events)
    ranked: list[tuple[datetime, int, AgendaItem]] = []
    for task in tasks:
        event_id = task["do_before_event_id"]
        if event_id is None:
            continue
        event = events_by_id.get(int(event_id))
        if event is None:
            continue
        starts_at = _parse_dt(event["starts_at"])
        if starts_at is None or starts_at <= now_dt:
            continue
        reason = f"{event['title']} 전에 끝내야 해서 지금 먼저 보여줘요."
        ranked.append((starts_at, int(task["id"]), _task_item(task, urgency="before_event", reason=reason)))
    return [item for _, _, item in sorted(ranked, key=lambda entry: (entry[0], entry[1]))]


def _ranked_events(
    events: list[sqlite3.Row], now_dt: datetime, *, only_soon: bool
) -> list[AgendaItem]:
    ranked: list[tuple[datetime, int, AgendaItem]] = []
    for event in _future_events(events, now_dt):
        if only_soon and not _is_ongoing_or_soon(event, now_dt):
            continue
        if not only_soon and _is_ongoing_or_soon(event, now_dt):
            continue
        starts_at = _parse_dt(event["starts_at"])
        if starts_at is None:
            continue
        ranked.append((starts_at, int(event["id"]), _event_item(event, now_dt=now_dt)))
    return [item for _, _, item in sorted(ranked, key=lambda entry: (entry[0], entry[1]))]


def _ranked_deadline_tasks(tasks: list[sqlite3.Row], now_dt: datetime) -> list[AgendaItem]:
    ranked: list[tuple[int, datetime, int, AgendaItem]] = []
    for task in tasks:
        if task["do_before_event_id"] is not None or task["due_at"] is None:
            continue
        due_at = _parse_dt(task["due_at"])
        if due_at is None:
            continue
        overdue = due_at < now_dt
        urgency: Urgency = "overdue" if overdue else "deadline"
        reason = "마감이 지나서 가장 먼저 복구해야 해요." if overdue else "마감이 가장 가까워서 먼저 보여줘요."
        ranked.append((0 if overdue else 1, due_at, int(task["id"]), _task_item(task, urgency=urgency, reason=reason)))
    return [item for _, _, _, item in sorted(ranked, key=lambda entry: (entry[0], entry[1], entry[2]))]


def get_agenda_now(*, now: str, settings: Settings | None = None) -> AgendaResponse:
    """Return the deterministic current agenda recommendation.

    This function is read-only. It never mutates task, event, inbox, or action rows.
    """

    now_dt = datetime.fromisoformat(now)
    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        tasks = _fetch_open_tasks(connection)
        events = _fetch_open_events(connection)
        inbox_count = _fetch_open_inbox_count(connection)

    before_event_items = _ranked_before_event_tasks(tasks, events, now_dt)
    soon_event_items = _ranked_events(events, now_dt, only_soon=True)
    deadline_items = _ranked_deadline_tasks(tasks, now_dt)
    later_event_items = _ranked_events(events, now_dt, only_soon=False)

    primary_items = before_event_items + soon_event_items + deadline_items
    ranked_items = primary_items + later_event_items
    now_item = ranked_items[0] if ranked_items else None
    primary_remainder = primary_items[1:] if now_item in primary_items else primary_items

    return AgendaResponse(
        now=now_item,
        next=primary_remainder[:3],
        later=primary_remainder[3:] + later_event_items,
        counts={"tasks": len(tasks), "events": len(events), "inbox": inbox_count},
    )
