"""Repository functions for ADHDman persistence operations."""

from datetime import UTC, datetime
import json
import sqlite3

from app.classification import ClassificationSource, ClassifierOutput
from app.config import Settings
from app.db import get_connection
from app.schemas import (
    CaptureClassification,
    CaptureClassificationCreated,
    CaptureResponse,
    DashboardCounts,
    DashboardResponse,
    DashboardToday,
    EventResponse,
    FocusSessionResponse,
    FocusTarget,
    InboxItemResponse,
    RecentActionResponse,
    TaskResponse,
    TodayOneThingResponse,
    TodayResponse,
    WeekDay,
    WeekItem,
)


RECENT_ACTIONS_MAX_LIMIT = 100
RECENT_ACTIONS_DEFAULT_LIMIT = 20


class InboxItemNotFoundError(Exception):
    """Raised when an inbox item does not exist."""


class InboxItemNotOpenError(Exception):
    """Raised when an inbox item cannot be promoted because it is not open."""


class TaskNotFoundError(Exception):
    """Raised when a task does not exist."""


class TaskNotOpenError(Exception):
    """Raised when a task cannot be completed because it is not open."""


class EventNotFoundError(Exception):
    """Raised when an event does not exist."""


class InvalidUpdateError(Exception):
    """Raised when a patch payload is empty or otherwise invalid."""


class FocusTargetNotFoundError(Exception):
    """Raised when a focus target row does not exist or is soft-deleted."""


class BreakdownConflictError(Exception):
    """Raised when a task cannot be broken down (deleted, already has children
    that have been completed, etc.)."""


class FocusSessionConflictError(Exception):
    """Raised when an active focus session already exists and replace is not set."""

    def __init__(self, existing: "FocusSessionResponse") -> None:
        super().__init__("a focus session is already active")
        self.existing = existing


def _now_iso() -> str:
    """Return a UTC timestamp as an ISO-8601 string."""

    return datetime.now(UTC).isoformat()


def _inbox_item_from_row(row: sqlite3.Row) -> InboxItemResponse:
    """Convert a SQLite row into an inbox item response model."""

    return InboxItemResponse(
        id=row["id"],
        text=row["text"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _task_from_row(row: sqlite3.Row) -> TaskResponse:
    """Convert a SQLite row into a task response model."""

    keys = row.keys()
    return TaskResponse(
        id=row["id"],
        title=row["title"],
        status=row["status"],
        source_inbox_item_id=row["source_inbox_item_id"],
        due_at=row["due_at"] if "due_at" in keys else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        parent_task_id=row["parent_task_id"] if "parent_task_id" in keys else None,
    )


def _event_from_row(row: sqlite3.Row) -> EventResponse:
    """Convert a SQLite row into an event response model."""

    keys = row.keys()
    return EventResponse(
        id=row["id"],
        title=row["title"],
        starts_at=row["starts_at"],
        ends_at=row["ends_at"],
        source_inbox_item_id=row["source_inbox_item_id"],
        status=row["status"] if "status" in keys else "open",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_inbox_items(
    status: str = "open", settings: Settings | None = None
) -> list[InboxItemResponse]:
    """Return inbox items with the requested status, ordered oldest first."""

    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, text, status, created_at, updated_at
            FROM inbox_items
            WHERE status = ?
            ORDER BY created_at ASC, id ASC
            """,
            (status,),
        ).fetchall()

    return [_inbox_item_from_row(row) for row in rows]


def list_events(settings: Settings | None = None) -> list[EventResponse]:
    """Return events created by classification, ordered by starts_at then id.

    Events without a ``starts_at`` sort after events that have one so the
    timeline-style view stays readable; ties break on id ascending.
    """

    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, title, starts_at, ends_at, source_inbox_item_id,
                   status, created_at, updated_at
            FROM events
            WHERE status != 'deleted'
            ORDER BY (starts_at IS NULL), starts_at ASC, id ASC
            """,
        ).fetchall()

    return [_event_from_row(row) for row in rows]


def list_tasks(status: str = "open", settings: Settings | None = None) -> list[TaskResponse]:
    """Return tasks with the requested status, ordered oldest first."""

    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, title, status, source_inbox_item_id, due_at, created_at, updated_at, completed_at
            FROM tasks
            WHERE status = ?
            ORDER BY created_at ASC, id ASC
            """,
            (status,),
        ).fetchall()

    return [_task_from_row(row) for row in rows]


def get_today_summary(settings: Settings | None = None) -> TodayResponse:
    """Return counts and one oldest open item to focus on today."""

    open_tasks = list_tasks(settings=settings)
    open_inbox_items = list_inbox_items(settings=settings)

    one_thing: TodayOneThingResponse | None = None
    if open_tasks:
        oldest_task = open_tasks[0]
        one_thing = TodayOneThingResponse(
            type="task",
            id=oldest_task.id,
            text=oldest_task.title,
        )
    elif open_inbox_items:
        oldest_inbox_item = open_inbox_items[0]
        one_thing = TodayOneThingResponse(
            type="inbox",
            id=oldest_inbox_item.id,
            text=oldest_inbox_item.text,
        )

    message = (
        "One thing is ready."
        if one_thing is not None
        else "Nothing is waiting right now. You can capture the next thought when it appears."
    )
    return TodayResponse(
        open_tasks_count=len(open_tasks),
        inbox_count=len(open_inbox_items),
        one_thing=one_thing,
        message=message,
    )


def capture_to_inbox(text: str, settings: Settings | None = None) -> InboxItemResponse:
    """Store normalized text as an open inbox item and log the capture action."""

    normalized_text = text.strip()
    if not normalized_text:
        raise ValueError("text must not be empty")

    timestamp = _now_iso()
    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(
            """
            INSERT INTO inbox_items (text, status, created_at, updated_at)
            VALUES (?, 'open', ?, ?)
            """,
            (normalized_text, timestamp, timestamp),
        )
        inbox_item_id = cursor.lastrowid
        row = connection.execute(
            """
            SELECT id, text, status, created_at, updated_at
            FROM inbox_items
            WHERE id = ?
            """,
            (inbox_item_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("failed to load captured inbox item")

        item = _inbox_item_from_row(row)
        connection.execute(
            """
            INSERT INTO actions (action_type, target_type, target_id, before_json, after_json, created_at)
            VALUES ('capture', 'inbox_item', ?, NULL, ?, ?)
            """,
            (item.id, json.dumps(item.model_dump()), timestamp),
        )

    return item


def apply_classification_to_inbox_item(
    inbox_item: InboxItemResponse,
    classification: ClassifierOutput,
    source: ClassificationSource,
    settings: Settings | None = None,
    *,
    persist_classification: bool = True,
) -> CaptureResponse:
    """Apply a classification outcome to an already-persisted open inbox item.

    The inbox row and its ``capture`` action must already exist (written by
    ``capture_to_inbox``) so the capture-first guarantee survives any failure
    inside classification or the LLM provider. When ``persist_classification``
    is False (kill switch), no classify_* row is written and the inbox row is
    returned untouched.
    """

    inbox_item_id = inbox_item.id
    final_inbox = inbox_item
    timestamp = _now_iso()
    created: CaptureClassificationCreated | None = None

    if persist_classification:
        with get_connection(settings) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            current_row = connection.execute(
                """
                SELECT id, text, status, created_at, updated_at
                FROM inbox_items
                WHERE id = ?
                """,
                (inbox_item_id,),
            ).fetchone()
            if current_row is None:
                raise InboxItemNotFoundError(
                    f"inbox item {inbox_item_id} not found"
                )
            if current_row["status"] != "open":
                raise InboxItemNotOpenError(
                    f"inbox item {inbox_item_id} is not open"
                )

            if classification.intent == "task":
                task_cursor = connection.execute(
                    """
                    INSERT INTO tasks (title, status, source_inbox_item_id, created_at, updated_at)
                    VALUES (?, 'open', ?, ?, ?)
                    """,
                    (
                        classification.title or inbox_item.text,
                        inbox_item_id,
                        timestamp,
                        timestamp,
                    ),
                )
                task_id = task_cursor.lastrowid
                connection.execute(
                    """
                    UPDATE inbox_items
                    SET status = 'promoted',
                        updated_at = ?,
                        promoted_to_type = 'task',
                        promoted_to_id = ?
                    WHERE id = ?
                    """,
                    (timestamp, task_id, inbox_item_id),
                )
                connection.execute(
                    """
                    INSERT INTO actions (action_type, target_type, target_id, before_json, after_json, created_at)
                    VALUES ('classify_task', 'task', ?, NULL, ?, ?)
                    """,
                    (
                        task_id,
                        json.dumps(
                            {
                                "inbox_item_id": inbox_item_id,
                                "task_id": task_id,
                                "title": classification.title,
                                "source": source,
                                "confidence": classification.confidence,
                            }
                        ),
                        timestamp,
                    ),
                )
                created = CaptureClassificationCreated(type="task", id=task_id)
            elif classification.intent == "event":
                event_cursor = connection.execute(
                    """
                    INSERT INTO events (title, starts_at, ends_at, source_inbox_item_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        classification.title or inbox_item.text,
                        classification.starts_at,
                        classification.ends_at,
                        inbox_item_id,
                        timestamp,
                        timestamp,
                    ),
                )
                event_id = event_cursor.lastrowid
                connection.execute(
                    """
                    UPDATE inbox_items
                    SET status = 'promoted',
                        updated_at = ?,
                        promoted_to_type = 'event',
                        promoted_to_id = ?
                    WHERE id = ?
                    """,
                    (timestamp, event_id, inbox_item_id),
                )
                connection.execute(
                    """
                    INSERT INTO actions (action_type, target_type, target_id, before_json, after_json, created_at)
                    VALUES ('classify_event', 'event', ?, NULL, ?, ?)
                    """,
                    (
                        event_id,
                        json.dumps(
                            {
                                "inbox_item_id": inbox_item_id,
                                "event_id": event_id,
                                "title": classification.title,
                                "starts_at": classification.starts_at,
                                "ends_at": classification.ends_at,
                                "source": source,
                                "confidence": classification.confidence,
                            }
                        ),
                        timestamp,
                    ),
                )
                created = CaptureClassificationCreated(type="event", id=event_id)
            else:
                connection.execute(
                    """
                    INSERT INTO actions (action_type, target_type, target_id, before_json, after_json, created_at)
                    VALUES ('classify_inbox_fallback', 'inbox_item', ?, NULL, ?, ?)
                    """,
                    (
                        inbox_item_id,
                        json.dumps(
                            {
                                "inbox_item_id": inbox_item_id,
                                "source": source,
                                "confidence": classification.confidence,
                                "reason": classification.reason,
                            }
                        ),
                        timestamp,
                    ),
                )

            connection.execute(
                """
                INSERT INTO classifications
                  (inbox_item_id, intent, confidence, source, raw_response, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    inbox_item_id,
                    classification.intent,
                    classification.confidence,
                    source,
                    json.dumps(classification.model_dump()),
                    timestamp,
                ),
            )

            updated_row = connection.execute(
                """
                SELECT id, text, status, created_at, updated_at
                FROM inbox_items
                WHERE id = ?
                """,
                (inbox_item_id,),
            ).fetchone()
            if updated_row is None:
                raise RuntimeError("failed to reload inbox item after classification")
            final_inbox = _inbox_item_from_row(updated_row)

    return CaptureResponse(
        id=final_inbox.id,
        inbox_item_id=final_inbox.id,
        text=final_inbox.text,
        status=final_inbox.status,
        created_at=final_inbox.created_at,
        updated_at=final_inbox.updated_at,
        classification=CaptureClassification(
            intent=classification.intent,
            confidence=classification.confidence,
            source=source,
            title=classification.title,
            starts_at=classification.starts_at,
            ends_at=classification.ends_at,
            reason=classification.reason,
            created=created,
        ),
    )


def promote_inbox_item_to_task(
    inbox_item_id: int, settings: Settings | None = None
) -> TaskResponse:
    """Promote an open inbox item into an open task and log the action."""

    timestamp = _now_iso()
    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        # Acquire SQLite's single-writer lock before reading the inbox row so two
        # concurrent promotions cannot both observe the item as open and insert
        # duplicate tasks before either one marks it promoted.
        connection.execute("BEGIN IMMEDIATE")
        inbox_row = connection.execute(
            """
            SELECT id, text, status, created_at, updated_at, promoted_to_type, promoted_to_id
            FROM inbox_items
            WHERE id = ?
            """,
            (inbox_item_id,),
        ).fetchone()
        if inbox_row is None:
            raise InboxItemNotFoundError(f"inbox item {inbox_item_id} not found")
        if inbox_row["status"] != "open":
            raise InboxItemNotOpenError(f"inbox item {inbox_item_id} is not open")

        before_inbox = dict(inbox_row)
        task_cursor = connection.execute(
            """
            INSERT INTO tasks (title, status, source_inbox_item_id, created_at, updated_at)
            VALUES (?, 'open', ?, ?, ?)
            """,
            (inbox_row["text"], inbox_item_id, timestamp, timestamp),
        )
        task_id = task_cursor.lastrowid
        connection.execute(
            """
            UPDATE inbox_items
            SET status = 'promoted',
                updated_at = ?,
                promoted_to_type = 'task',
                promoted_to_id = ?
            WHERE id = ?
            """,
            (timestamp, task_id, inbox_item_id),
        )
        task_row = connection.execute(
            """
            SELECT id, title, status, source_inbox_item_id, due_at, created_at, updated_at, completed_at
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        updated_inbox_row = connection.execute(
            """
            SELECT id, text, status, created_at, updated_at, promoted_to_type, promoted_to_id
            FROM inbox_items
            WHERE id = ?
            """,
            (inbox_item_id,),
        ).fetchone()
        if task_row is None or updated_inbox_row is None:
            raise RuntimeError("failed to load promoted task")

        task = _task_from_row(task_row)
        after_payload = {
            "task": task.model_dump(),
            "inbox_item": dict(updated_inbox_row),
        }
        connection.execute(
            """
            INSERT INTO actions (action_type, target_type, target_id, before_json, after_json, created_at)
            VALUES ('promote_task', 'task', ?, ?, ?, ?)
            """,
            (task.id, json.dumps(before_inbox), json.dumps(after_payload), timestamp),
        )

    return task


def complete_task_with_action(
    task_id: int, settings: Settings | None = None
) -> tuple[TaskResponse, int]:
    """Mark an open task as done, set completion time, and log the action."""

    timestamp = _now_iso()
    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        # Acquire the write lock before reading so concurrent completions cannot
        # both observe the task as open and create duplicate completion actions.
        connection.execute("BEGIN IMMEDIATE")
        task_row = connection.execute(
            """
            SELECT id, title, status, source_inbox_item_id, due_at, created_at, updated_at, completed_at
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        if task_row is None:
            raise TaskNotFoundError(f"task {task_id} not found")
        if task_row["status"] != "open":
            raise TaskNotOpenError(f"task {task_id} is not open")

        before_task = dict(task_row)
        connection.execute(
            """
            UPDATE tasks
            SET status = 'done', updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (timestamp, timestamp, task_id),
        )
        updated_row = connection.execute(
            """
            SELECT id, title, status, source_inbox_item_id, due_at, created_at, updated_at, completed_at
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        if updated_row is None:
            raise RuntimeError("failed to load completed task")

        task = _task_from_row(updated_row)
        cursor = connection.execute(
            """
            INSERT INTO actions (action_type, target_type, target_id, before_json, after_json, created_at)
            VALUES ('complete_task', 'task', ?, ?, ?, ?)
            """,
            (task.id, json.dumps(before_task), json.dumps(task.model_dump()), timestamp),
        )
        action_id = cursor.lastrowid

    return task, action_id


def complete_task(task_id: int, settings: Settings | None = None) -> TaskResponse:
    """Backward-compatible wrapper returning only the completed task."""

    task, _ = complete_task_with_action(task_id, settings)
    return task


_TASK_SELECT = (
    "SELECT id, title, status, source_inbox_item_id, due_at, "
    "created_at, updated_at, completed_at, parent_task_id "
    "FROM tasks WHERE id = ?"
)

_EVENT_SELECT = (
    "SELECT id, title, starts_at, ends_at, source_inbox_item_id, status, "
    "created_at, updated_at FROM events WHERE id = ?"
)


def get_task(task_id: int, settings: Settings | None = None) -> TaskResponse:
    """Return a task by id, including soft-deleted rows."""

    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(_TASK_SELECT, (task_id,)).fetchone()
    if row is None:
        raise TaskNotFoundError(f"task {task_id} not found")
    return _task_from_row(row)


def get_event(event_id: int, settings: Settings | None = None) -> EventResponse:
    """Return an event by id, including soft-deleted rows."""

    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(_EVENT_SELECT, (event_id,)).fetchone()
    if row is None:
        raise EventNotFoundError(f"event {event_id} not found")
    return _event_from_row(row)


_TASK_PATCH_FIELDS = ("title", "status", "due_at")


def update_task(
    task_id: int, patch: dict, settings: Settings | None = None
) -> tuple[TaskResponse, int]:
    """Apply a partial update to a task and log a full before/after snapshot.

    ``patch`` only contains keys the caller explicitly provided. Soft-deleted
    tasks cannot be updated; restore them through undo first.
    """

    unknown = set(patch) - set(_TASK_PATCH_FIELDS)
    if unknown:
        raise InvalidUpdateError(f"unknown task fields: {sorted(unknown)}")
    if not patch:
        raise InvalidUpdateError("at least one field is required")

    # Status->done is the existing Phase 1 completion: route through complete_task
    # so completed_at is set and we log a single complete_task action instead of a
    # duplicate update_task. Mixing other fields with status='done' is rejected to
    # keep the action log unambiguous; send those edits in a separate request.
    if patch.get("status") == "done":
        if set(patch) != {"status"}:
            raise InvalidUpdateError(
                "status='done' must be sent on its own; update other fields first"
            )
        return complete_task_with_action(task_id, settings)

    timestamp = _now_iso()
    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(_TASK_SELECT, (task_id,)).fetchone()
        if row is None:
            raise TaskNotFoundError(f"task {task_id} not found")
        if row["status"] == "deleted":
            raise TaskNotFoundError(f"task {task_id} not found")

        before = dict(row)
        assignments: list[str] = []
        params: list[object] = []
        for field in _TASK_PATCH_FIELDS:
            if field in patch:
                assignments.append(f"{field} = ?")
                params.append(patch[field])
        assignments.append("updated_at = ?")
        params.append(timestamp)
        params.append(task_id)
        connection.execute(
            f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?",
            params,
        )
        updated_row = connection.execute(_TASK_SELECT, (task_id,)).fetchone()
        if updated_row is None:
            raise RuntimeError("failed to reload task after update")
        task = _task_from_row(updated_row)
        cursor = connection.execute(
            """
            INSERT INTO actions (action_type, target_type, target_id, before_json, after_json, created_at)
            VALUES ('update_task', 'task', ?, ?, ?, ?)
            """,
            (
                task.id,
                json.dumps(before),
                json.dumps(dict(updated_row)),
                timestamp,
            ),
        )
        action_id = cursor.lastrowid

    return task, action_id


def delete_task(
    task_id: int, settings: Settings | None = None
) -> tuple[TaskResponse, int]:
    """Soft-delete a task by setting ``status='deleted'`` and log the snapshot."""

    timestamp = _now_iso()
    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(_TASK_SELECT, (task_id,)).fetchone()
        if row is None:
            raise TaskNotFoundError(f"task {task_id} not found")
        if row["status"] == "deleted":
            raise TaskNotFoundError(f"task {task_id} not found")

        before = dict(row)
        connection.execute(
            "UPDATE tasks SET status = 'deleted', updated_at = ? WHERE id = ?",
            (timestamp, task_id),
        )
        updated_row = connection.execute(_TASK_SELECT, (task_id,)).fetchone()
        if updated_row is None:
            raise RuntimeError("failed to reload task after delete")
        task = _task_from_row(updated_row)
        cursor = connection.execute(
            """
            INSERT INTO actions (action_type, target_type, target_id, before_json, after_json, created_at)
            VALUES ('delete_task', 'task', ?, ?, ?, ?)
            """,
            (
                task.id,
                json.dumps(before),
                json.dumps(dict(updated_row)),
                timestamp,
            ),
        )
        action_id = cursor.lastrowid
        _auto_end_focus_for_target(connection, timestamp, "task", task.id)

    return task, action_id


_EVENT_PATCH_FIELDS = ("title", "starts_at", "ends_at")


def update_event(
    event_id: int, patch: dict, settings: Settings | None = None
) -> tuple[EventResponse, int]:
    """Apply a partial update to an event and log a full before/after snapshot."""

    unknown = set(patch) - set(_EVENT_PATCH_FIELDS)
    if unknown:
        raise InvalidUpdateError(f"unknown event fields: {sorted(unknown)}")
    if not patch:
        raise InvalidUpdateError("at least one field is required")

    timestamp = _now_iso()
    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(_EVENT_SELECT, (event_id,)).fetchone()
        if row is None:
            raise EventNotFoundError(f"event {event_id} not found")
        if row["status"] == "deleted":
            raise EventNotFoundError(f"event {event_id} not found")

        before = dict(row)
        assignments: list[str] = []
        params: list[object] = []
        for field in _EVENT_PATCH_FIELDS:
            if field in patch:
                assignments.append(f"{field} = ?")
                params.append(patch[field])
        assignments.append("updated_at = ?")
        params.append(timestamp)
        params.append(event_id)
        connection.execute(
            f"UPDATE events SET {', '.join(assignments)} WHERE id = ?",
            params,
        )
        updated_row = connection.execute(_EVENT_SELECT, (event_id,)).fetchone()
        if updated_row is None:
            raise RuntimeError("failed to reload event after update")
        event = _event_from_row(updated_row)
        cursor = connection.execute(
            """
            INSERT INTO actions (action_type, target_type, target_id, before_json, after_json, created_at)
            VALUES ('update_event', 'event', ?, ?, ?, ?)
            """,
            (
                event.id,
                json.dumps(before),
                json.dumps(dict(updated_row)),
                timestamp,
            ),
        )
        action_id = cursor.lastrowid

    return event, action_id


def delete_event(
    event_id: int, settings: Settings | None = None
) -> tuple[EventResponse, int]:
    """Soft-delete an event by setting ``status='deleted'`` and log the snapshot."""

    timestamp = _now_iso()
    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(_EVENT_SELECT, (event_id,)).fetchone()
        if row is None:
            raise EventNotFoundError(f"event {event_id} not found")
        if row["status"] == "deleted":
            raise EventNotFoundError(f"event {event_id} not found")

        before = dict(row)
        connection.execute(
            "UPDATE events SET status = 'deleted', updated_at = ? WHERE id = ?",
            (timestamp, event_id),
        )
        updated_row = connection.execute(_EVENT_SELECT, (event_id,)).fetchone()
        if updated_row is None:
            raise RuntimeError("failed to reload event after delete")
        event = _event_from_row(updated_row)
        cursor = connection.execute(
            """
            INSERT INTO actions (action_type, target_type, target_id, before_json, after_json, created_at)
            VALUES ('delete_event', 'event', ?, ?, ?, ?)
            """,
            (
                event.id,
                json.dumps(before),
                json.dumps(dict(updated_row)),
                timestamp,
            ),
        )
        action_id = cursor.lastrowid
        _auto_end_focus_for_target(connection, timestamp, "event", event.id)

    return event, action_id


def list_recent_actions(
    limit: int = RECENT_ACTIONS_DEFAULT_LIMIT,
    settings: Settings | None = None,
) -> list[RecentActionResponse]:
    """Return the most recent action-log rows for the read-only dashboard.

    Only metadata is returned: ``before_json``/``after_json`` snapshots are
    excluded so raw row contents do not leak through the web payload. ``limit``
    is clamped to ``[1, RECENT_ACTIONS_MAX_LIMIT]`` to keep the response small.
    """

    safe_limit = max(1, min(int(limit), RECENT_ACTIONS_MAX_LIMIT))
    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, action_type, target_type, target_id, created_at, undone_at
            FROM actions
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()

    return [
        RecentActionResponse(
            id=row["id"],
            action_type=row["action_type"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            created_at=row["created_at"],
            undone_at=row["undone_at"],
        )
        for row in rows
    ]


def _date_and_time_from_iso(value: str | None) -> tuple[str, str | None] | None:
    """Split an ISO-8601 string into ``(YYYY-MM-DD, HH:MM)``.

    Returns ``None`` when the value is missing or unparseable so the week view
    only contains rows with a usable date.
    """

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    date_str = parsed.date().isoformat()
    has_time = parsed.hour or parsed.minute or parsed.second or parsed.microsecond
    time_str = parsed.strftime("%H:%M") if has_time else None
    return date_str, time_str


def list_week_candidates(settings: Settings | None = None) -> list[WeekDay]:
    """Return open tasks and non-deleted events grouped by date.

    Only rows with a parseable date column (``tasks.due_at`` /
    ``events.starts_at``) are included; rows without a date are surfaced through
    the inbox/tasks/events sections instead. Soft-deleted rows are excluded.
    Days are ordered ascending; items within a day sort by time then id.
    """

    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        task_rows = connection.execute(
            """
            SELECT id, title, due_at
            FROM tasks
            WHERE status = 'open' AND due_at IS NOT NULL
            ORDER BY id ASC
            """,
        ).fetchall()
        event_rows = connection.execute(
            """
            SELECT id, title, starts_at
            FROM events
            WHERE status != 'deleted' AND starts_at IS NOT NULL
            ORDER BY id ASC
            """,
        ).fetchall()

    grouped: dict[str, list[WeekItem]] = {}
    for row in task_rows:
        split = _date_and_time_from_iso(row["due_at"])
        if split is None:
            continue
        date_str, time_str = split
        grouped.setdefault(date_str, []).append(
            WeekItem(type="task", id=row["id"], title=row["title"], time=time_str)
        )
    for row in event_rows:
        split = _date_and_time_from_iso(row["starts_at"])
        if split is None:
            continue
        date_str, time_str = split
        grouped.setdefault(date_str, []).append(
            WeekItem(type="event", id=row["id"], title=row["title"], time=time_str)
        )

    week: list[WeekDay] = []
    for date_str in sorted(grouped):
        items = sorted(
            grouped[date_str],
            key=lambda item: (item.time is None, item.time or "", item.id),
        )
        week.append(WeekDay(date=date_str, items=items))
    return week


# ----- Phase 6: focus sessions (one-thing) -----


_FOCUS_SELECT = (
    "SELECT id, kind, target_type, target_id, status, started_at, ended_at, "
    "interval_seconds, note, last_check_in_at FROM focus_sessions WHERE id = ?"
)


def _focus_session_from_row(row: sqlite3.Row) -> FocusSessionResponse:
    return FocusSessionResponse(
        id=row["id"],
        kind=row["kind"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        status=row["status"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        interval_seconds=row["interval_seconds"],
        note=row["note"],
        last_check_in_at=row["last_check_in_at"],
    )


def _resolve_focus_target(
    connection: sqlite3.Connection,
    target_type: str,
    target_id: int,
) -> FocusTarget:
    """Return a resolved focus target, raising if missing or soft-deleted."""

    if target_type == "task":
        row = connection.execute(
            "SELECT id, title, status FROM tasks WHERE id = ?",
            (target_id,),
        ).fetchone()
        if row is None or row["status"] == "deleted":
            raise FocusTargetNotFoundError(f"task {target_id} not found")
        return FocusTarget(type="task", id=row["id"], title=row["title"])

    if target_type == "event":
        row = connection.execute(
            "SELECT id, title, status FROM events WHERE id = ?",
            (target_id,),
        ).fetchone()
        if row is None or row["status"] == "deleted":
            raise FocusTargetNotFoundError(f"event {target_id} not found")
        return FocusTarget(type="event", id=row["id"], title=row["title"])

    if target_type == "inbox_item":
        row = connection.execute(
            "SELECT id, text, status FROM inbox_items WHERE id = ?",
            (target_id,),
        ).fetchone()
        if row is None or row["status"] == "deleted":
            raise FocusTargetNotFoundError(f"inbox_item {target_id} not found")
        return FocusTarget(type="inbox_item", id=row["id"], title=row["text"])

    raise FocusTargetNotFoundError(f"unknown target_type '{target_type}'")


def _auto_end_focus_for_target(
    connection: sqlite3.Connection,
    timestamp: str,
    target_type: str,
    target_id: int,
) -> int | None:
    """End any active focus session pointing at the given (now-deleted) target.

    Writes an ``auto_end_focus`` action so the trail explains the transition.
    Returns the focus_sessions.id ended, or None if there was nothing to end.
    Caller must already hold the write transaction.
    """

    row = connection.execute(
        """
        SELECT id, kind, target_type, target_id, status, started_at, ended_at,
               interval_seconds, note, last_check_in_at
        FROM focus_sessions
        WHERE status = 'active' AND target_type = ? AND target_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (target_type, target_id),
    ).fetchone()
    if row is None:
        return None
    before = _focus_session_from_row(row)
    connection.execute(
        "UPDATE focus_sessions SET status = 'ended', ended_at = ? WHERE id = ?",
        (timestamp, before.id),
    )
    updated = connection.execute(_FOCUS_SELECT, (before.id,)).fetchone()
    after = _focus_session_from_row(updated) if updated is not None else before
    connection.execute(
        """
        INSERT INTO actions
          (action_type, target_type, target_id, before_json, after_json, created_at)
        VALUES ('auto_end_focus', 'focus_session', ?, ?, ?, ?)
        """,
        (
            before.id,
            json.dumps({"focus_session": before.model_dump()}),
            json.dumps(
                {
                    "focus_session": after.model_dump(),
                    "reason": "target_deleted",
                    "target_type": target_type,
                    "target_id": target_id,
                }
            ),
            timestamp,
        ),
    )
    return before.id


def _active_focus_row(
    connection: sqlite3.Connection, kind: str = "focus"
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT id, kind, target_type, target_id, status, started_at, ended_at,
               interval_seconds, note, last_check_in_at
        FROM focus_sessions
        WHERE kind = ? AND status = 'active'
        ORDER BY id DESC
        LIMIT 1
        """,
        (kind,),
    ).fetchone()


def get_active_focus_session(
    kind: str = "focus", settings: Settings | None = None
) -> FocusSessionResponse | None:
    """Return the single active focus_sessions row of the given kind, if any."""

    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        row = _active_focus_row(connection, kind)
    return _focus_session_from_row(row) if row is not None else None


def get_active_focus_with_target(
    kind: str = "focus", settings: Settings | None = None
) -> tuple[FocusSessionResponse, FocusTarget | None] | None:
    """Return the active session plus its resolved target.

    If the active session's target has been deleted out from under it (e.g. a
    direct DB edit, or a code path that bypasses ``delete_task``/``delete_event``),
    the session is auto-ended here so the read surface is never stuck pointing
    at a ghost target. ``None`` is returned in that case.
    """

    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = _active_focus_row(connection, kind)
        if row is None:
            return None
        session = _focus_session_from_row(row)
        if session.target_type is None or session.target_id is None:
            return session, None
        try:
            target = _resolve_focus_target(
                connection, session.target_type, session.target_id
            )
        except FocusTargetNotFoundError:
            _auto_end_focus_for_target(
                connection, _now_iso(), session.target_type, session.target_id
            )
            return None
    return session, target


def start_focus_session(
    target_type: str,
    target_id: int,
    note: str | None = None,
    replace: bool = False,
    settings: Settings | None = None,
) -> tuple[FocusSessionResponse, FocusTarget, int]:
    """Start a single active focus-kind session, with optional replace semantics.

    Returns ``(session, target, action_id)``. Raises ``FocusSessionConflictError``
    when an active focus session already exists and ``replace`` is False, and
    ``FocusTargetNotFoundError`` when the target row is missing or soft-deleted.

    TODO(undo): the inverse for ``start_focus`` / ``stop_focus`` / ``replace_focus``
    actions is not yet wired into ``app.undo``. Action rows are written so the
    trail stays auditable; undo integration lands in a later slice.
    """

    timestamp = _now_iso()
    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        target = _resolve_focus_target(connection, target_type, target_id)
        existing_row = _active_focus_row(connection, "focus")
        replaced_existing: FocusSessionResponse | None = None
        if existing_row is not None:
            existing = _focus_session_from_row(existing_row)
            if not replace:
                raise FocusSessionConflictError(existing)
            connection.execute(
                """
                UPDATE focus_sessions
                SET status = 'ended', ended_at = ?
                WHERE id = ?
                """,
                (timestamp, existing.id),
            )
            replaced_existing = existing

        cursor = connection.execute(
            """
            INSERT INTO focus_sessions
              (kind, target_type, target_id, status, started_at, note)
            VALUES ('focus', ?, ?, 'active', ?, ?)
            """,
            (target.type, target.id, timestamp, note),
        )
        session_id = cursor.lastrowid
        row = connection.execute(_FOCUS_SELECT, (session_id,)).fetchone()
        if row is None:
            raise RuntimeError("failed to load focus session after insert")
        session = _focus_session_from_row(row)

        action_type = "replace_focus" if replaced_existing is not None else "start_focus"
        before_payload = (
            {"focus_session": replaced_existing.model_dump()}
            if replaced_existing is not None
            else None
        )
        after_payload = {
            "focus_session": session.model_dump(),
            "target": target.model_dump(),
        }
        action_cursor = connection.execute(
            """
            INSERT INTO actions
              (action_type, target_type, target_id, before_json, after_json, created_at)
            VALUES (?, 'focus_session', ?, ?, ?, ?)
            """,
            (
                action_type,
                session.id,
                json.dumps(before_payload) if before_payload is not None else None,
                json.dumps(after_payload),
                timestamp,
            ),
        )
        action_id = action_cursor.lastrowid

    return session, target, action_id


def stop_focus_session(
    settings: Settings | None = None,
) -> tuple[FocusSessionResponse | None, int | None]:
    """End the active focus-kind session. Idempotent: returns (None, None) if none.

    TODO(undo): see :func:`start_focus_session`.
    """

    timestamp = _now_iso()
    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = _active_focus_row(connection, "focus")
        if row is None:
            return None, None
        before = _focus_session_from_row(row)
        connection.execute(
            "UPDATE focus_sessions SET status = 'ended', ended_at = ? WHERE id = ?",
            (timestamp, before.id),
        )
        updated_row = connection.execute(_FOCUS_SELECT, (before.id,)).fetchone()
        assert updated_row is not None
        session = _focus_session_from_row(updated_row)
        cursor = connection.execute(
            """
            INSERT INTO actions
              (action_type, target_type, target_id, before_json, after_json, created_at)
            VALUES ('stop_focus', 'focus_session', ?, ?, ?, ?)
            """,
            (
                session.id,
                json.dumps({"focus_session": before.model_dump()}),
                json.dumps({"focus_session": session.model_dump()}),
                timestamp,
            ),
        )
        action_id = cursor.lastrowid

    return session, action_id


def get_dashboard(
    settings: Settings | None = None,
    recent_actions_limit: int = RECENT_ACTIONS_DEFAULT_LIMIT,
) -> DashboardResponse:
    """Compose the read-only dashboard payload from existing read helpers.

    This function never mutates rows: it only reads from inbox, tasks, events,
    and the action log. Raw before/after snapshots are not included.
    """

    today = get_today_summary(settings=settings)
    inbox_items = list_inbox_items(settings=settings)
    tasks = list_tasks(settings=settings)
    events = list_events(settings=settings)
    week = list_week_candidates(settings=settings)
    recent_actions = list_recent_actions(
        limit=recent_actions_limit, settings=settings
    )

    counts = DashboardCounts(
        open_tasks=today.open_tasks_count,
        open_inbox=today.inbox_count,
        upcoming_events=len(events),
    )
    return DashboardResponse(
        today=DashboardToday(
            message=today.message,
            one_thing=today.one_thing,
            counts=counts,
        ),
        inbox=inbox_items,
        tasks=tasks,
        events=events,
        week=week,
        recent_actions=recent_actions,
    )


# ----- Phase 6: task breakdown -----


def list_task_children(
    parent_task_id: int, settings: Settings | None = None
) -> list[TaskResponse]:
    """Return children of ``parent_task_id`` in creation order.

    Soft-deleted children are included so the caller can render a recovery view;
    callers can filter by ``status``.
    """

    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        parent = connection.execute(_TASK_SELECT, (parent_task_id,)).fetchone()
        if parent is None:
            raise TaskNotFoundError(f"task {parent_task_id} not found")
        rows = connection.execute(
            """
            SELECT id, title, status, source_inbox_item_id, due_at,
                   created_at, updated_at, completed_at, parent_task_id
            FROM tasks
            WHERE parent_task_id = ?
            ORDER BY id ASC
            """,
            (parent_task_id,),
        ).fetchall()

    return [_task_from_row(row) for row in rows]


def breakdown_task(
    parent_task_id: int,
    steps: list[str],
    source: str = "manual",
    settings: Settings | None = None,
) -> tuple[TaskResponse, list[TaskResponse], int]:
    """Create 2–5 child tasks for ``parent_task_id`` and log a single action.

    The parent row itself is not mutated. The action's ``before_json`` carries
    the parent snapshot (for context) and ``after_json`` carries the parent
    snapshot plus the list of created child task snapshots and their ids, so
    ``/undo`` can soft-delete those children together.
    """

    if not 2 <= len(steps) <= 5:
        raise InvalidUpdateError("steps must contain between 2 and 5 entries")

    timestamp = _now_iso()
    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        parent_row = connection.execute(_TASK_SELECT, (parent_task_id,)).fetchone()
        if parent_row is None or parent_row["status"] == "deleted":
            raise TaskNotFoundError(f"task {parent_task_id} not found")
        if parent_row["parent_task_id"] is not None:
            raise BreakdownConflictError(
                f"task {parent_task_id} is already a child of another task"
            )

        parent = _task_from_row(parent_row)
        children: list[TaskResponse] = []
        child_ids: list[int] = []
        for step in steps:
            cursor = connection.execute(
                """
                INSERT INTO tasks
                  (title, status, source_inbox_item_id, parent_task_id,
                   created_at, updated_at)
                VALUES (?, 'open', NULL, ?, ?, ?)
                """,
                (step, parent_task_id, timestamp, timestamp),
            )
            child_id = cursor.lastrowid
            child_ids.append(child_id)
            row = connection.execute(_TASK_SELECT, (child_id,)).fetchone()
            assert row is not None
            children.append(_task_from_row(row))

        action_cursor = connection.execute(
            """
            INSERT INTO actions
              (action_type, target_type, target_id, before_json, after_json, created_at)
            VALUES ('breakdown', 'task', ?, ?, ?, ?)
            """,
            (
                parent.id,
                json.dumps({"parent": parent.model_dump()}),
                json.dumps(
                    {
                        "parent": parent.model_dump(),
                        "child_ids": child_ids,
                        "children": [child.model_dump() for child in children],
                        "source": source,
                    }
                ),
                timestamp,
            ),
        )
        action_id = action_cursor.lastrowid

    return parent, children, action_id


def suggest_breakdown_steps(
    parent_task_id: int,
    max_steps: int | None = None,
    settings: Settings | None = None,
) -> tuple[list[str], str]:
    """Return rules-only breakdown suggestions for a task.

    Read-only: writes nothing. Returns ``(steps, source)`` where ``source`` is
    always ``"rules"`` in this slice. An LLM-backed path can be added later by
    branching on ``settings.openrouter_api_key``; this slice keeps the helper
    offline and deterministic so tests stay hermetic.
    """

    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        parent_row = connection.execute(_TASK_SELECT, (parent_task_id,)).fetchone()
    if parent_row is None or parent_row["status"] == "deleted":
        raise TaskNotFoundError(f"task {parent_task_id} not found")

    title = (parent_row["title"] or "").strip() or "this task"
    template = (
        f"Outline {title}",
        f"Do the smallest part of {title}",
        f"Wrap up {title}",
    )
    steps = [step[:500] for step in template]
    if max_steps is not None:
        steps = steps[: max(2, min(max_steps, 5))]
    return steps, "rules"
