"""Repository functions for ADHDman persistence operations."""

from datetime import UTC, datetime
import json
import sqlite3

from app.config import Settings
from app.db import get_connection
from app.schemas import InboxItemResponse, TaskResponse, TodayOneThingResponse, TodayResponse


class InboxItemNotFoundError(Exception):
    """Raised when an inbox item does not exist."""


class InboxItemNotOpenError(Exception):
    """Raised when an inbox item cannot be promoted because it is not open."""


class TaskNotFoundError(Exception):
    """Raised when a task does not exist."""


class TaskNotOpenError(Exception):
    """Raised when a task cannot be completed because it is not open."""


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

    return TaskResponse(
        id=row["id"],
        title=row["title"],
        status=row["status"],
        source_inbox_item_id=row["source_inbox_item_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
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


def list_tasks(status: str = "open", settings: Settings | None = None) -> list[TaskResponse]:
    """Return tasks with the requested status, ordered oldest first."""

    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, title, status, source_inbox_item_id, created_at, updated_at, completed_at
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
            SELECT id, title, status, source_inbox_item_id, created_at, updated_at, completed_at
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


def complete_task(task_id: int, settings: Settings | None = None) -> TaskResponse:
    """Mark an open task as done, set completion time, and log the action."""

    timestamp = _now_iso()
    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        # Acquire the write lock before reading so concurrent completions cannot
        # both observe the task as open and create duplicate completion actions.
        connection.execute("BEGIN IMMEDIATE")
        task_row = connection.execute(
            """
            SELECT id, title, status, source_inbox_item_id, created_at, updated_at, completed_at
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
            SELECT id, title, status, source_inbox_item_id, created_at, updated_at, completed_at
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        if updated_row is None:
            raise RuntimeError("failed to load completed task")

        task = _task_from_row(updated_row)
        connection.execute(
            """
            INSERT INTO actions (action_type, target_type, target_id, before_json, after_json, created_at)
            VALUES ('complete_task', 'task', ?, ?, ?, ?)
            """,
            (task.id, json.dumps(before_task), json.dumps(task.model_dump()), timestamp),
        )

    return task
