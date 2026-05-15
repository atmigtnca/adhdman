"""Tests for the read-only web memory dashboard.

Covers:
- Dashboard schema serialization with representative sample data.
- Repository helpers (``list_recent_actions``, ``list_week_candidates``,
  ``get_dashboard``) for ordering, limit clamping, and soft-delete exclusion.
- ``GET /dashboard`` for empty and seeded states, plus the no-mutation
  guarantee on repeated reads.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.db import init_db
from app.main import app
from app.repositories import (
    RECENT_ACTIONS_MAX_LIMIT,
    capture_to_inbox,
    complete_task,
    delete_event as delete_event_repo,
    delete_task as delete_task_repo,
    get_dashboard,
    list_recent_actions,
    list_week_candidates,
    promote_inbox_item_to_task,
    update_event as update_event_repo,
    update_task as update_task_repo,
)
from app.schemas import (
    DashboardCounts,
    DashboardResponse,
    DashboardToday,
    EventResponse,
    InboxItemResponse,
    RecentActionResponse,
    TaskResponse,
    TodayOneThingResponse,
    WeekDay,
    WeekItem,
)


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )


def _row_counts(settings: Settings) -> dict[str, int]:
    """Snapshot table counts so we can prove a request did not mutate state."""

    with sqlite3.connect(settings.resolved_database_path) as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("inbox_items", "tasks", "events", "actions", "classifications")
        }


def test_dashboard_schema_serializes_sample_payload() -> None:
    payload = DashboardResponse(
        today=DashboardToday(
            message="One thing is ready.",
            one_thing=TodayOneThingResponse(type="task", id=2, text="pay rent"),
            counts=DashboardCounts(open_tasks=1, open_inbox=1, upcoming_events=1),
        ),
        inbox=[
            InboxItemResponse(
                id=1,
                text="ambiguous note",
                status="open",
                created_at="2026-05-16T09:00:00+00:00",
                updated_at="2026-05-16T09:00:00+00:00",
            )
        ],
        tasks=[
            TaskResponse(
                id=2,
                title="pay rent",
                status="open",
                source_inbox_item_id=None,
                due_at=None,
                created_at="2026-05-16T09:00:00+00:00",
                updated_at="2026-05-16T09:00:00+00:00",
                completed_at=None,
            )
        ],
        events=[
            EventResponse(
                id=3,
                title="dentist",
                starts_at="2026-05-20T10:00:00",
                ends_at=None,
                source_inbox_item_id=None,
                status="open",
                created_at="2026-05-16T09:00:00+00:00",
                updated_at="2026-05-16T09:00:00+00:00",
            )
        ],
        week=[
            WeekDay(
                date="2026-05-20",
                items=[WeekItem(type="event", id=3, title="dentist", time="10:00")],
            )
        ],
        recent_actions=[
            RecentActionResponse(
                id=9,
                action_type="capture",
                target_type="inbox_item",
                target_id=1,
                created_at="2026-05-16T09:00:00+00:00",
                undone_at=None,
            )
        ],
    )

    dumped = payload.model_dump()
    assert dumped["today"]["counts"] == {
        "open_tasks": 1,
        "open_inbox": 1,
        "upcoming_events": 1,
    }
    assert dumped["week"][0]["items"][0]["time"] == "10:00"
    # Public-safety: no raw snapshots leak through the recent_actions schema.
    recent = dumped["recent_actions"][0]
    assert "before_json" not in recent
    assert "after_json" not in recent


def test_list_recent_actions_orders_desc_and_clamps_limit(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    init_db(settings)
    for index in range(3):
        capture_to_inbox(f"thought {index}", settings)

    actions = list_recent_actions(settings=settings)
    assert [a.action_type for a in actions] == ["capture", "capture", "capture"]
    # Newest-first ordering keeps the dashboard's "Recent Changes" useful.
    assert actions[0].id > actions[1].id > actions[2].id

    # Limit is clamped to a safe upper bound and a minimum of 1.
    huge = list_recent_actions(limit=RECENT_ACTIONS_MAX_LIMIT + 500, settings=settings)
    assert len(huge) == 3
    one = list_recent_actions(limit=0, settings=settings)
    assert len(one) == 1


def test_list_week_candidates_groups_tasks_and_events_and_skips_soft_deleted(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    init_db(settings)

    inbox_one = capture_to_inbox("pay rent", settings)
    rent_task = promote_inbox_item_to_task(inbox_one.id, settings)
    update_task_repo(rent_task.id, {"due_at": "2026-05-20T09:30:00"}, settings)

    inbox_two = capture_to_inbox("dentist", settings)
    dentist_task = promote_inbox_item_to_task(inbox_two.id, settings)
    update_task_repo(dentist_task.id, {"due_at": "2026-05-20T08:00:00"}, settings)

    inbox_three = capture_to_inbox("call mom", settings)
    call_task = promote_inbox_item_to_task(inbox_three.id, settings)
    update_task_repo(call_task.id, {"due_at": "2026-05-21"}, settings)

    inbox_four = capture_to_inbox("hidden", settings)
    hidden_task = promote_inbox_item_to_task(inbox_four.id, settings)
    update_task_repo(hidden_task.id, {"due_at": "2026-05-22T12:00:00"}, settings)
    delete_task_repo(hidden_task.id, settings)

    # Seed an event row directly so we can control starts_at deterministically.
    with sqlite3.connect(settings.resolved_database_path) as connection:
        connection.execute(
            """
            INSERT INTO events (title, starts_at, ends_at, source_inbox_item_id,
                                created_at, updated_at)
            VALUES (?, ?, NULL, NULL, ?, ?)
            """,
            ("doctor", "2026-05-21T15:00:00", "2026-05-16T09:00:00", "2026-05-16T09:00:00"),
        )
        event_id = connection.execute(
            "SELECT id FROM events WHERE title = 'doctor'"
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO events (title, starts_at, ends_at, source_inbox_item_id,
                                created_at, updated_at)
            VALUES (?, ?, NULL, NULL, ?, ?)
            """,
            ("gone", "2026-05-23T10:00:00", "2026-05-16T09:00:00", "2026-05-16T09:00:00"),
        )
        gone_event_id = connection.execute(
            "SELECT id FROM events WHERE title = 'gone'"
        ).fetchone()[0]
        connection.commit()
    delete_event_repo(gone_event_id, settings)

    week = list_week_candidates(settings=settings)

    dates = [day.date for day in week]
    assert dates == ["2026-05-20", "2026-05-21"]
    # Soft-deleted task and event are excluded.
    assert all(item.title != "hidden" for day in week for item in day.items)
    assert all(item.title != "gone" for day in week for item in day.items)

    day_20 = week[0]
    assert [(item.title, item.time) for item in day_20.items] == [
        ("dentist", "08:00"),
        ("pay rent", "09:30"),
    ]
    day_21 = week[1]
    # Dateless tasks sort after timed items (call_task has no clock time).
    assert day_21.items[-1].title == "call mom"
    assert day_21.items[-1].time is None
    titles_21 = {item.title for item in day_21.items}
    assert titles_21 == {"call mom", "doctor"}
    doctor_item = next(item for item in day_21.items if item.title == "doctor")
    assert doctor_item.type == "event"
    assert doctor_item.id == event_id
    assert doctor_item.time == "15:00"


def test_get_dashboard_repository_composes_sections(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    init_db(settings)

    inbox_promote = capture_to_inbox("pay rent", settings)
    rent_task = promote_inbox_item_to_task(inbox_promote.id, settings)
    update_task_repo(rent_task.id, {"due_at": "2026-05-20T09:30:00"}, settings)
    capture_to_inbox("ambiguous note", settings)
    done_inbox = capture_to_inbox("done thing", settings)
    done_task = promote_inbox_item_to_task(done_inbox.id, settings)
    complete_task(done_task.id, settings)

    dashboard = get_dashboard(settings=settings)

    assert dashboard.today.counts.open_tasks == 1
    assert dashboard.today.counts.open_inbox == 1
    assert dashboard.today.one_thing is not None
    assert dashboard.today.one_thing.type == "task"
    assert dashboard.today.one_thing.id == rent_task.id
    # Completed task is excluded from the open tasks list.
    assert [task.id for task in dashboard.tasks] == [rent_task.id]
    assert [item.text for item in dashboard.inbox] == ["ambiguous note"]
    assert dashboard.week[0].date == "2026-05-20"
    assert {action.action_type for action in dashboard.recent_actions} >= {
        "capture",
        "promote_task",
        "complete_task",
        "update_task",
    }


def test_get_dashboard_endpoint_empty_state(tmp_path: Path, monkeypatch) -> None:
    settings = _make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.get("/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["today"]["one_thing"] is None
    assert payload["today"]["counts"] == {
        "open_tasks": 0,
        "open_inbox": 0,
        "upcoming_events": 0,
    }
    assert payload["today"]["message"].startswith("Nothing is waiting")
    assert payload["inbox"] == []
    assert payload["tasks"] == []
    assert payload["events"] == []
    assert payload["week"] == []
    assert payload["recent_actions"] == []


def test_get_dashboard_endpoint_returns_seeded_sections(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first = client.post("/capture", json={"text": "pay rent"}).json()
        client.post(f"/inbox/{first['inbox_item_id']}/promote-task")
        client.post("/capture", json={"text": "ambiguous note"})

        first_response = client.get("/dashboard").json()
        before_counts = _row_counts(settings)
        second_response = client.get("/dashboard").json()
        after_counts = _row_counts(settings)

    assert first_response == second_response
    assert before_counts == after_counts

    assert first_response["today"]["counts"]["open_tasks"] == 1
    assert first_response["today"]["counts"]["open_inbox"] == 1
    assert first_response["today"]["one_thing"]["type"] == "task"
    assert [task["title"] for task in first_response["tasks"]] == ["pay rent"]
    assert [item["text"] for item in first_response["inbox"]] == ["ambiguous note"]
    assert {action["action_type"] for action in first_response["recent_actions"]} >= {
        "capture",
        "promote_task",
    }
    for action in first_response["recent_actions"]:
        # The public payload must not leak raw before/after snapshots.
        assert "before_json" not in action
        assert "after_json" not in action


def test_get_dashboard_endpoint_excludes_soft_deleted_event(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)
    init_db(settings)

    with sqlite3.connect(settings.resolved_database_path) as connection:
        connection.execute(
            """
            INSERT INTO events (title, starts_at, ends_at, source_inbox_item_id,
                                created_at, updated_at)
            VALUES (?, ?, NULL, NULL, ?, ?)
            """,
            ("dentist", "2026-05-20T10:00:00", "2026-05-16T09:00:00", "2026-05-16T09:00:00"),
        )
        connection.execute(
            """
            INSERT INTO events (title, starts_at, ends_at, source_inbox_item_id,
                                created_at, updated_at)
            VALUES (?, ?, NULL, NULL, ?, ?)
            """,
            ("ghost", "2026-05-21T11:00:00", "2026-05-16T09:00:00", "2026-05-16T09:00:00"),
        )
        ghost_event_id = connection.execute(
            "SELECT id FROM events WHERE title = 'ghost'"
        ).fetchone()[0]
        connection.commit()
    update_event_repo(ghost_event_id, {"title": "ghost"}, settings)
    delete_event_repo(ghost_event_id, settings)

    with TestClient(app) as client:
        payload = client.get("/dashboard").json()

    titles = [event["title"] for event in payload["events"]]
    assert titles == ["dentist"]
    week_titles = {item["title"] for day in payload["week"] for item in day["items"]}
    assert "ghost" not in week_titles
    assert payload["today"]["counts"]["upcoming_events"] == 1
