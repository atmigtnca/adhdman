"""Inverse-action dispatcher for ADHDman.

Phase 3 closes the recovery loop: every mutating repository function writes a
``before_json`` / ``after_json`` snapshot on the ``actions`` table, and this
module replays the inverse inside a single transaction. The dispatcher is keyed
by ``action_type``; only the types listed in :data:`REVERSIBLE_TYPES` can be
undone. Undo itself writes a new ``undo`` action row so the trail stays
auditable, and the original row's ``undone_at`` column is set so a second undo
of the same action returns 409 rather than silently re-applying the inverse.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import sqlite3
from typing import Any

from app.config import Settings
from app.db import get_connection


class UndoError(Exception):
    """Base class for undo failures."""


class ActionNotFoundError(UndoError):
    """Raised when the action row does not exist."""


class ActionAlreadyUndoneError(UndoError):
    """Raised when the action has already been undone."""


class ActionNotReversibleError(UndoError):
    """Raised when the action type has no inverse in Phase 3."""


class NoUndoableActionError(UndoError):
    """Raised when ``/undo/latest`` cannot find any reversible action."""


class UndoDisabledError(UndoError):
    """Raised when ``UNDO_ENABLED`` is False."""


class ActionConflictError(UndoError):
    """Raised when current live state has diverged from the action's after_json
    snapshot, so applying the inverse would clobber a newer change."""


REVERSIBLE_TYPES: frozenset[str] = frozenset(
    {
        "capture",
        "promote_task",
        "complete_task",
        "update_task",
        "delete_task",
        "update_event",
        "delete_event",
        "classify_task",
        "classify_event",
        "classify_inbox_fallback",
        "breakdown",
        "block_reset",
    }
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_action(connection: sqlite3.Connection, action_id: int) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT id, action_type, target_type, target_id,
               before_json, after_json, created_at, undone_at
        FROM actions
        WHERE id = ?
        """,
        (action_id,),
    ).fetchone()
    if row is None:
        raise ActionNotFoundError(f"action {action_id} not found")
    return row


def _row_snapshot_inbox(
    connection: sqlite3.Connection, inbox_id: int
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT id, text, status, created_at, updated_at,
               promoted_to_type, promoted_to_id
        FROM inbox_items WHERE id = ?
        """,
        (inbox_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _row_snapshot_task(
    connection: sqlite3.Connection, task_id: int
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT id, title, status, source_inbox_item_id, due_at,
               created_at, updated_at, completed_at, parent_task_id, block_state
        FROM tasks WHERE id = ?
        """,
        (task_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _row_snapshot_event(
    connection: sqlite3.Connection, event_id: int
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT id, title, starts_at, ends_at, source_inbox_item_id,
               status, created_at, updated_at
        FROM events WHERE id = ?
        """,
        (event_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _restore_inbox(
    connection: sqlite3.Connection, snapshot: dict[str, Any], timestamp: str
) -> dict[str, Any]:
    connection.execute(
        """
        UPDATE inbox_items
        SET text = ?, status = ?, created_at = ?, updated_at = ?,
            promoted_to_type = ?, promoted_to_id = ?
        WHERE id = ?
        """,
        (
            snapshot["text"],
            snapshot["status"],
            snapshot["created_at"],
            timestamp,
            snapshot.get("promoted_to_type"),
            snapshot.get("promoted_to_id"),
            snapshot["id"],
        ),
    )
    restored = _row_snapshot_inbox(connection, snapshot["id"])
    assert restored is not None
    return restored


def _restore_task(
    connection: sqlite3.Connection, snapshot: dict[str, Any], timestamp: str
) -> dict[str, Any]:
    connection.execute(
        """
        UPDATE tasks
        SET title = ?, status = ?, source_inbox_item_id = ?, due_at = ?,
            created_at = ?, updated_at = ?, completed_at = ?, block_state = ?
        WHERE id = ?
        """,
        (
            snapshot["title"],
            snapshot["status"],
            snapshot.get("source_inbox_item_id"),
            snapshot.get("due_at"),
            snapshot["created_at"],
            timestamp,
            snapshot.get("completed_at"),
            snapshot.get("block_state"),
            snapshot["id"],
        ),
    )
    restored = _row_snapshot_task(connection, snapshot["id"])
    assert restored is not None
    return restored


def _restore_event(
    connection: sqlite3.Connection, snapshot: dict[str, Any], timestamp: str
) -> dict[str, Any]:
    connection.execute(
        """
        UPDATE events
        SET title = ?, starts_at = ?, ends_at = ?, source_inbox_item_id = ?,
            status = ?, created_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            snapshot["title"],
            snapshot.get("starts_at"),
            snapshot.get("ends_at"),
            snapshot.get("source_inbox_item_id"),
            snapshot.get("status", "open"),
            snapshot["created_at"],
            timestamp,
            snapshot["id"],
        ),
    )
    restored = _row_snapshot_event(connection, snapshot["id"])
    assert restored is not None
    return restored


def _soft_delete_inbox(
    connection: sqlite3.Connection, inbox_id: int, timestamp: str
) -> dict[str, Any] | None:
    connection.execute(
        "UPDATE inbox_items SET status = 'deleted', updated_at = ? WHERE id = ?",
        (timestamp, inbox_id),
    )
    return _row_snapshot_inbox(connection, inbox_id)


def _soft_delete_task(
    connection: sqlite3.Connection, task_id: int, timestamp: str
) -> dict[str, Any] | None:
    connection.execute(
        "UPDATE tasks SET status = 'deleted', updated_at = ? WHERE id = ?",
        (timestamp, task_id),
    )
    return _row_snapshot_task(connection, task_id)


def _soft_delete_event(
    connection: sqlite3.Connection, event_id: int, timestamp: str
) -> dict[str, Any] | None:
    connection.execute(
        "UPDATE events SET status = 'deleted', updated_at = ? WHERE id = ?",
        (timestamp, event_id),
    )
    return _row_snapshot_event(connection, event_id)


def _row_diverged(
    current: dict[str, Any] | None, snapshot: dict[str, Any] | None
) -> bool:
    """Return True if any field present in ``snapshot`` differs from ``current``.

    Used to confirm the live row(s) still match what the action recorded as its
    after-state. If they don't, a newer write has touched the row and applying
    the inverse would clobber that newer state.
    """

    if snapshot is None:
        return False
    if current is None:
        return True
    for key, value in snapshot.items():
        if current.get(key) != value:
            return True
    return False


def _check_no_conflict(
    connection: sqlite3.Connection, action: sqlite3.Row
) -> None:
    """Raise :class:`ActionConflictError` if affected row(s) have diverged from
    the action's after_json snapshot."""

    action_type = action["action_type"]
    target_id = action["target_id"]
    after_json = action["after_json"]
    after = json.loads(after_json) if after_json else None

    if action_type == "capture":
        current = _row_snapshot_inbox(connection, target_id)
        if _row_diverged(current, after):
            raise ActionConflictError(
                f"inbox item {target_id} has changed since action "
                f"{action['id']} was recorded"
            )
        return

    if action_type in ("complete_task", "update_task", "delete_task"):
        current = _row_snapshot_task(connection, target_id)
        if _row_diverged(current, after):
            raise ActionConflictError(
                f"task {target_id} has changed since action "
                f"{action['id']} was recorded"
            )
        return

    if action_type in ("update_event", "delete_event"):
        current = _row_snapshot_event(connection, target_id)
        if _row_diverged(current, after):
            raise ActionConflictError(
                f"event {target_id} has changed since action "
                f"{action['id']} was recorded"
            )
        return

    if action_type == "promote_task":
        assert after is not None
        task_now = _row_snapshot_task(connection, target_id)
        if _row_diverged(task_now, after.get("task")):
            raise ActionConflictError(
                f"task {target_id} has changed since promote_task "
                f"action {action['id']} was recorded"
            )
        inbox_snapshot = after.get("inbox_item") or {}
        inbox_id = inbox_snapshot.get("id")
        if inbox_id is not None:
            inbox_now = _row_snapshot_inbox(connection, inbox_id)
            if _row_diverged(inbox_now, inbox_snapshot):
                raise ActionConflictError(
                    f"inbox item {inbox_id} has changed since promote_task "
                    f"action {action['id']} was recorded"
                )
        return

    if action_type in ("classify_task", "classify_event"):
        # after_json has no row snapshot. Detect divergence by checking the
        # created row is still pristine (updated_at == created_at) and the
        # inbox item is still promoted to this target.
        if action_type == "classify_task":
            current = _row_snapshot_task(connection, target_id)
        else:
            current = _row_snapshot_event(connection, target_id)
        if current is None:
            raise ActionConflictError(
                f"target row {target_id} for action {action['id']} is missing"
            )
        if current.get("status") == "deleted":
            raise ActionConflictError(
                f"target row {target_id} for action {action['id']} "
                f"is already deleted"
            )
        if current.get("updated_at") != current.get("created_at"):
            raise ActionConflictError(
                f"target row {target_id} has been modified since action "
                f"{action['id']} was recorded"
            )
        inbox_item_id = (after or {}).get("inbox_item_id")
        if inbox_item_id is not None:
            inbox_now = _row_snapshot_inbox(connection, inbox_item_id)
            if (
                inbox_now is None
                or inbox_now.get("status") != "promoted"
                or inbox_now.get("promoted_to_id") != target_id
            ):
                raise ActionConflictError(
                    f"inbox item {inbox_item_id} has changed since action "
                    f"{action['id']} was recorded"
                )
        return

    if action_type == "classify_inbox_fallback":
        # Pure log row: nothing to validate.
        return

    if action_type == "block_reset":
        assert after is not None, "block_reset action missing after_json"
        task_after = after.get("task") if isinstance(after, dict) else None
        current = _row_snapshot_task(connection, target_id)
        if _row_diverged(current, task_after):
            raise ActionConflictError(
                f"task {target_id} has changed since block_reset "
                f"action {action['id']} was recorded"
            )
        return

    if action_type == "breakdown":
        assert after is not None, "breakdown action missing after_json"
        for child_snapshot in after.get("children", []):
            child_id = child_snapshot.get("id")
            if child_id is None:
                continue
            current = _row_snapshot_task(connection, child_id)
            if current is None:
                raise ActionConflictError(
                    f"child task {child_id} for breakdown action "
                    f"{action['id']} is missing"
                )
            if current.get("status") == "deleted":
                # Already soft-deleted: undo can no-op past this child.
                continue
            if current.get("status") != child_snapshot.get("status"):
                raise ActionConflictError(
                    f"child task {child_id} has been completed or changed "
                    f"since breakdown action {action['id']} was recorded"
                )
            if current.get("updated_at") != child_snapshot.get("updated_at"):
                raise ActionConflictError(
                    f"child task {child_id} has been modified since "
                    f"breakdown action {action['id']} was recorded"
                )
        return


def _apply_inverse(
    connection: sqlite3.Connection,
    action: sqlite3.Row,
    timestamp: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return ``(restored_row, undo_before_payload)``.

    ``undo_before_payload`` is what to record as the ``before_json`` on the
    new ``undo`` action: the state of the affected row(s) at the moment of
    undo, so undoing the undo would be possible if a later phase wires it up.
    """

    action_type = action["action_type"]
    target_id = action["target_id"]
    before_json = action["before_json"]
    after_json = action["after_json"]
    before = json.loads(before_json) if before_json else None
    after = json.loads(after_json) if after_json else None

    if action_type == "capture":
        pre = _row_snapshot_inbox(connection, target_id)
        restored = _soft_delete_inbox(connection, target_id, timestamp)
        return {"inbox_item": restored}, {"inbox_item": pre}

    if action_type == "complete_task":
        assert before is not None, "complete_task action missing before_json"
        pre = _row_snapshot_task(connection, target_id)
        restored = _restore_task(connection, before, timestamp)
        return {"task": restored}, {"task": pre}

    if action_type == "update_task":
        assert before is not None, "update_task action missing before_json"
        pre = _row_snapshot_task(connection, target_id)
        restored = _restore_task(connection, before, timestamp)
        return {"task": restored}, {"task": pre}

    if action_type == "delete_task":
        assert before is not None, "delete_task action missing before_json"
        pre = _row_snapshot_task(connection, target_id)
        restored = _restore_task(connection, before, timestamp)
        return {"task": restored}, {"task": pre}

    if action_type == "update_event":
        assert before is not None, "update_event action missing before_json"
        pre = _row_snapshot_event(connection, target_id)
        restored = _restore_event(connection, before, timestamp)
        return {"event": restored}, {"event": pre}

    if action_type == "delete_event":
        assert before is not None, "delete_event action missing before_json"
        pre = _row_snapshot_event(connection, target_id)
        restored = _restore_event(connection, before, timestamp)
        return {"event": restored}, {"event": pre}

    if action_type == "promote_task":
        assert before is not None and after is not None, (
            "promote_task action missing snapshot"
        )
        inbox_snapshot = before
        task_id = target_id
        pre_task = _row_snapshot_task(connection, task_id)
        pre_inbox = _row_snapshot_inbox(connection, inbox_snapshot["id"])
        _soft_delete_task(connection, task_id, timestamp)
        restored_inbox = _restore_inbox(connection, inbox_snapshot, timestamp)
        return (
            {"inbox_item": restored_inbox},
            {"task": pre_task, "inbox_item": pre_inbox},
        )

    if action_type in ("classify_task", "classify_event"):
        # Phase 2 writes after_json={"inbox_item_id": ..., "<kind>_id": ...};
        # before_json is NULL because the row was freshly created. Undo here is
        # "delete the created row and restore the inbox row to open" — the
        # create-shaped semantics from the plan.
        assert after is not None, f"{action_type} action missing after_json"
        inbox_item_id = after.get("inbox_item_id")
        pre_inbox = (
            _row_snapshot_inbox(connection, inbox_item_id)
            if inbox_item_id is not None
            else None
        )
        if action_type == "classify_task":
            pre_target = _row_snapshot_task(connection, target_id)
            _soft_delete_task(connection, target_id, timestamp)
        else:
            pre_target = _row_snapshot_event(connection, target_id)
            _soft_delete_event(connection, target_id, timestamp)
        restored_inbox: dict[str, Any] | None = None
        if inbox_item_id is not None and pre_inbox is not None:
            connection.execute(
                """
                UPDATE inbox_items
                SET status = 'open', updated_at = ?,
                    promoted_to_type = NULL, promoted_to_id = NULL
                WHERE id = ?
                """,
                (timestamp, inbox_item_id),
            )
            restored_inbox = _row_snapshot_inbox(connection, inbox_item_id)
        payload_key = "task" if action_type == "classify_task" else "event"
        return (
            {payload_key: None, "inbox_item": restored_inbox},
            {payload_key: pre_target, "inbox_item": pre_inbox},
        )

    if action_type == "classify_inbox_fallback":
        # Pure log row: the inbox item was left as-is (status stayed 'open' for
        # fallback). There is no state to revert — return a successful no-op so
        # callers can still mark the action undone and keep the trail tidy.
        pre = _row_snapshot_inbox(connection, target_id)
        return {"inbox_item": pre}, {"inbox_item": pre}

    if action_type == "block_reset":
        assert before is not None, "block_reset action missing before_json"
        task_before = before.get("task") if isinstance(before, dict) else None
        assert task_before is not None, "block_reset before_json missing task"
        pre = _row_snapshot_task(connection, target_id)
        restored = _restore_task(connection, task_before, timestamp)
        return {"task": restored}, {"task": pre}

    if action_type == "breakdown":
        assert after is not None, "breakdown action missing after_json"
        child_snapshots = after.get("children", [])
        pre_children: list[dict[str, Any] | None] = []
        restored_children: list[dict[str, Any] | None] = []
        for child_snapshot in child_snapshots:
            child_id = child_snapshot.get("id")
            if child_id is None:
                continue
            pre_children.append(_row_snapshot_task(connection, child_id))
            restored_children.append(
                _soft_delete_task(connection, child_id, timestamp)
            )
        return (
            {"parent_id": target_id, "children": restored_children},
            {"parent_id": target_id, "children": pre_children},
        )

    raise ActionNotReversibleError(
        f"action_type '{action_type}' is not reversible"
    )


def _perform_undo(
    connection: sqlite3.Connection, action: sqlite3.Row
) -> tuple[int, str, int, dict[str, Any] | None]:
    _check_no_conflict(connection, action)
    timestamp = _now_iso()
    restored, undo_before = _apply_inverse(connection, action, timestamp)
    connection.execute(
        "UPDATE actions SET undone_at = ? WHERE id = ?",
        (timestamp, action["id"]),
    )
    cursor = connection.execute(
        """
        INSERT INTO actions
            (action_type, target_type, target_id, before_json, after_json, created_at)
        VALUES ('undo', 'action', ?, ?, ?, ?)
        """,
        (
            action["id"],
            json.dumps(undo_before),
            json.dumps({"undone_action_id": action["id"]}),
            timestamp,
        ),
    )
    return cursor.lastrowid, action["action_type"], action["id"], restored


def undo_action(
    action_id: int, settings: Settings | None = None
) -> dict[str, Any]:
    """Reverse a specific action and return a summary of the new undo row."""

    if settings is not None and not settings.undo_enabled:
        raise UndoDisabledError("undo is disabled")

    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        action = _load_action(connection, action_id)
        if action["undone_at"] is not None:
            raise ActionAlreadyUndoneError(
                f"action {action_id} has already been undone"
            )
        if action["action_type"] == "undo":
            raise ActionNotReversibleError(
                "undo actions cannot themselves be undone in phase 3"
            )
        if action["action_type"] not in REVERSIBLE_TYPES:
            raise ActionNotReversibleError(
                f"action_type '{action['action_type']}' is not reversible"
            )
        undo_id, original_type, original_id, restored = _perform_undo(
            connection, action
        )

    return {
        "undo_action_id": undo_id,
        "undone_action_id": original_id,
        "undone_action_type": original_type,
        "restored": restored,
    }


def undo_latest(settings: Settings | None = None) -> dict[str, Any]:
    """Reverse the most recent reversible, not-yet-undone action."""

    if settings is not None and not settings.undo_enabled:
        raise UndoDisabledError("undo is disabled")

    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT id, action_type, target_type, target_id,
                   before_json, after_json, created_at, undone_at
            FROM actions
            WHERE undone_at IS NULL
              AND action_type != 'undo'
            ORDER BY id DESC
            LIMIT 1
            """,
        ).fetchone()
        if row is None:
            raise NoUndoableActionError("no reversible action available to undo")
        if row["action_type"] not in REVERSIBLE_TYPES:
            # Surface a 409 rather than silently skipping past the newest
            # action — undoing an older action when the newest is non-reversible
            # would be confusing and could clobber state the user just changed.
            raise ActionNotReversibleError(
                f"latest action_type '{row['action_type']}' is not reversible"
            )
        undo_id, original_type, original_id, restored = _perform_undo(
            connection, row
        )

    return {
        "undo_action_id": undo_id,
        "undone_action_id": original_id,
        "undone_action_type": original_type,
        "restored": restored,
    }
