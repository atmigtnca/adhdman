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
    EventResponse,
    InboxItemResponse,
    TaskResponse,
    TodayOneThingResponse,
    TodayResponse,
)


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
    "created_at, updated_at, completed_at FROM tasks WHERE id = ?"
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

    return event, action_id
