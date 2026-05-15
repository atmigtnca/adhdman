"""Repository functions for ADHDman persistence operations."""

from datetime import UTC, datetime
import json
import sqlite3

from app.config import Settings
from app.db import get_connection
from app.schemas import InboxItemResponse, TaskResponse


class InboxItemNotFoundError(Exception):
    """Raised when an inbox item does not exist."""


class InboxItemNotOpenError(Exception):
    """Raised when an inbox item cannot be promoted because it is not open."""


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
