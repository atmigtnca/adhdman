"""Repository functions for ADHDman persistence operations."""

from datetime import UTC, datetime
import json
import sqlite3

from app.config import Settings
from app.db import get_connection
from app.schemas import InboxItemResponse


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
