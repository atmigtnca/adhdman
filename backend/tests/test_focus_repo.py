"""Repository-level tests for Phase 6 one-thing focus sessions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.config import Settings
from app.db import get_connection, init_db
from app.repositories import (
    FocusSessionConflictError,
    FocusTargetNotFoundError,
    capture_to_inbox,
    get_active_focus_session,
    get_active_focus_with_target,
    delete_task,
    promote_inbox_item_to_task,
    start_focus_session,
    stop_focus_session,
)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )


def _make_task(settings: Settings, text: str = "call dentist") -> int:
    inbox = capture_to_inbox(text, settings)
    task = promote_inbox_item_to_task(inbox.id, settings)
    return task.id


def _action_rows(settings: Settings) -> list[tuple[str, str, int]]:
    with sqlite3.connect(settings.resolved_database_path) as connection:
        return connection.execute(
            "SELECT action_type, target_type, target_id FROM actions ORDER BY id ASC"
        ).fetchall()


def test_start_focus_creates_active_session_and_action_row(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)

    session, target, action_id = start_focus_session(
        target_type="task", target_id=task_id, settings=settings
    )

    assert session.status == "active"
    assert session.target_type == "task"
    assert session.target_id == task_id
    assert target.title == "call dentist"
    assert action_id > 0

    actions = _action_rows(settings)
    assert ("start_focus", "focus_session", session.id) in actions


def test_get_active_focus_session_returns_none_when_empty(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    assert get_active_focus_session(settings=settings) is None
    assert get_active_focus_with_target(settings=settings) is None


def test_start_focus_rejects_missing_task(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    with pytest.raises(FocusTargetNotFoundError):
        start_focus_session(target_type="task", target_id=999, settings=settings)


def test_start_focus_rejects_soft_deleted_task(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)
    with get_connection(settings) as connection:
        connection.execute(
            "UPDATE tasks SET status = 'deleted' WHERE id = ?", (task_id,)
        )
    with pytest.raises(FocusTargetNotFoundError):
        start_focus_session(target_type="task", target_id=task_id, settings=settings)


def test_start_focus_rejects_unknown_target_type(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    with pytest.raises(FocusTargetNotFoundError):
        start_focus_session(
            target_type="bogus", target_id=1, settings=settings
        )


def test_start_focus_supports_inbox_item_target(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    inbox = capture_to_inbox("buy groceries", settings)

    session, target, _ = start_focus_session(
        target_type="inbox_item", target_id=inbox.id, settings=settings
    )

    assert target.type == "inbox_item"
    assert target.title == "buy groceries"
    assert session.target_id == inbox.id


def test_second_start_without_replace_raises_conflict(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    first_task = _make_task(settings, "first")
    second_task = _make_task(settings, "second")

    first_session, _, _ = start_focus_session(
        target_type="task", target_id=first_task, settings=settings
    )

    with pytest.raises(FocusSessionConflictError) as excinfo:
        start_focus_session(
            target_type="task", target_id=second_task, settings=settings
        )
    assert excinfo.value.existing.id == first_session.id

    # Still exactly one active row, and it remains the first.
    active = get_active_focus_session(settings=settings)
    assert active is not None
    assert active.id == first_session.id


def test_replace_flag_ends_existing_and_starts_new(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    first_task = _make_task(settings, "first")
    second_task = _make_task(settings, "second")

    first_session, _, _ = start_focus_session(
        target_type="task", target_id=first_task, settings=settings
    )
    second_session, target, _ = start_focus_session(
        target_type="task",
        target_id=second_task,
        replace=True,
        settings=settings,
    )

    assert second_session.id != first_session.id
    assert target.title == "second"

    active = get_active_focus_session(settings=settings)
    assert active is not None
    assert active.id == second_session.id

    with sqlite3.connect(settings.resolved_database_path) as connection:
        rows = connection.execute(
            "SELECT id, status FROM focus_sessions ORDER BY id ASC"
        ).fetchall()
    assert rows == [(first_session.id, "ended"), (second_session.id, "active")]

    actions = [a[0] for a in _action_rows(settings)]
    assert "replace_focus" in actions


def test_stop_focus_session_idempotent(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)
    start_focus_session(target_type="task", target_id=task_id, settings=settings)

    session, action_id = stop_focus_session(settings)
    assert session is not None
    assert session.status == "ended"
    assert action_id is not None

    # second stop is a no-op
    again_session, again_action = stop_focus_session(settings)
    assert again_session is None
    assert again_action is None


def test_delete_task_auto_ends_active_focus_session(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)
    start_focus_session(target_type="task", target_id=task_id, settings=settings)

    delete_task(task_id, settings=settings)

    assert get_active_focus_session(settings=settings) is None
    with sqlite3.connect(settings.resolved_database_path) as connection:
        rows = connection.execute(
            "SELECT status, ended_at FROM focus_sessions"
        ).fetchall()
        actions = connection.execute(
            "SELECT action_type FROM actions ORDER BY id ASC"
        ).fetchall()

    assert rows[0][0] == "ended"
    assert rows[0][1] is not None
    assert ("auto_end_focus",) in actions


def test_get_active_focus_with_target_auto_ends_when_target_soft_deleted(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)
    start_focus_session(target_type="task", target_id=task_id, settings=settings)

    with get_connection(settings) as connection:
        connection.execute(
            "UPDATE tasks SET status = 'deleted' WHERE id = ?", (task_id,)
        )

    result = get_active_focus_with_target(settings=settings)
    assert result is None
    active = get_active_focus_session(settings=settings)
    assert active is None

    with sqlite3.connect(settings.resolved_database_path) as connection:
        session_status = connection.execute(
            "SELECT status, ended_at FROM focus_sessions"
        ).fetchone()
        auto_action = connection.execute(
            """
            SELECT action_type, target_type
            FROM actions
            WHERE action_type = 'auto_end_focus'
            """
        ).fetchone()

    assert session_status[0] == "ended"
    assert session_status[1] is not None
    assert auto_action == ("auto_end_focus", "focus_session")
