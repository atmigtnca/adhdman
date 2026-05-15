"""Repository-level tests for Phase 6 body-double sessions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.config import Settings
from app.db import get_connection, init_db
from app.repositories import (
    BodyDoubleNotActiveError,
    BodyDoubleSessionConflictError,
    FocusTargetNotFoundError,
    capture_to_inbox,
    delete_task,
    get_active_body_double_with_target,
    get_active_focus_session,
    promote_inbox_item_to_task,
    record_body_double_checkin,
    start_body_double_session,
    stop_body_double_session,
    start_focus_session,
)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )


def _make_task(settings: Settings, text: str = "deep work") -> int:
    inbox = capture_to_inbox(text, settings)
    task = promote_inbox_item_to_task(inbox.id, settings)
    return task.id


def _action_types(settings: Settings) -> list[str]:
    with sqlite3.connect(settings.resolved_database_path) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT action_type FROM actions ORDER BY id ASC"
            ).fetchall()
        ]


def test_start_body_double_no_target_creates_active_session(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)

    session, target, action_id = start_body_double_session(
        interval_seconds=120, settings=settings
    )

    assert session.kind == "body_double"
    assert session.status == "active"
    assert session.interval_seconds == 120
    assert session.target_type is None
    assert session.target_id is None
    assert session.last_check_in_at is not None
    assert target is None
    assert action_id > 0
    assert "start_body_double" in _action_types(settings)


def test_start_body_double_with_task_target_resolves_title(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)

    session, target, _ = start_body_double_session(
        interval_seconds=300,
        target_type="task",
        target_id=task_id,
        settings=settings,
    )

    assert session.target_type == "task"
    assert session.target_id == task_id
    assert target is not None
    assert target.title == "deep work"


def test_start_body_double_rejects_unpaired_target(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    with pytest.raises(FocusTargetNotFoundError):
        start_body_double_session(
            interval_seconds=120, target_type="task", settings=settings
        )


def test_start_body_double_rejects_missing_target(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    with pytest.raises(FocusTargetNotFoundError):
        start_body_double_session(
            interval_seconds=120,
            target_type="task",
            target_id=999,
            settings=settings,
        )


def test_second_start_without_replace_raises_conflict(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    first, _, _ = start_body_double_session(interval_seconds=120, settings=settings)

    with pytest.raises(BodyDoubleSessionConflictError) as excinfo:
        start_body_double_session(interval_seconds=200, settings=settings)
    assert excinfo.value.existing.id == first.id

    with sqlite3.connect(settings.resolved_database_path) as connection:
        active = connection.execute(
            "SELECT COUNT(*) FROM focus_sessions WHERE kind='body_double' AND status='active'"
        ).fetchone()[0]
    assert active == 1


def test_replace_flag_ends_existing_and_starts_new(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    first, _, _ = start_body_double_session(interval_seconds=120, settings=settings)
    second, _, _ = start_body_double_session(
        interval_seconds=240, replace=True, settings=settings
    )

    assert second.id != first.id
    assert second.interval_seconds == 240
    assert "replace_body_double" in _action_types(settings)

    with sqlite3.connect(settings.resolved_database_path) as connection:
        rows = connection.execute(
            "SELECT id, status FROM focus_sessions ORDER BY id ASC"
        ).fetchall()
    assert rows == [(first.id, "ended"), (second.id, "active")]


def test_focus_and_body_double_are_independent_kinds(tmp_path: Path) -> None:
    """Starting a body-double session must not collide with a focus session."""

    from app.repositories import start_focus_session

    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)

    start_focus_session(target_type="task", target_id=task_id, settings=settings)
    body, _, _ = start_body_double_session(interval_seconds=120, settings=settings)

    assert body.status == "active"
    # focus is also still active
    focus = get_active_focus_session(settings=settings)
    assert focus is not None
    assert focus.status == "active"


def test_stop_body_double_is_idempotent(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    start_body_double_session(interval_seconds=120, settings=settings)

    session, action_id = stop_body_double_session(settings)
    assert session is not None
    assert session.status == "ended"
    assert action_id is not None

    again_session, again_action = stop_body_double_session(settings)
    assert again_session is None
    assert again_action is None


def test_check_in_updates_last_check_in_at(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    session, _, _ = start_body_double_session(interval_seconds=120, settings=settings)
    original = session.last_check_in_at

    updated = record_body_double_checkin(settings)
    assert updated.id == session.id
    assert updated.last_check_in_at is not None
    # Either strictly later, or at least preserved as a non-empty string. Some
    # CI runs may have sub-ms timing collisions; both monotonic and equal are
    # acceptable, but the column must be populated and an audit row written.
    assert updated.last_check_in_at >= (original or "")
    assert "body_double_checkin" in _action_types(settings)


def test_check_in_without_active_raises(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    with pytest.raises(BodyDoubleNotActiveError):
        record_body_double_checkin(settings)


def test_get_active_body_double_with_target_returns_none_when_empty(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    assert get_active_body_double_with_target(settings=settings) is None


def test_get_active_body_double_with_target_auto_ends_when_target_deleted(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)
    start_body_double_session(
        interval_seconds=120,
        target_type="task",
        target_id=task_id,
        settings=settings,
    )

    with get_connection(settings) as connection:
        connection.execute(
            "UPDATE tasks SET status = 'deleted' WHERE id = ?", (task_id,)
        )

    assert get_active_body_double_with_target(settings=settings) is None
    with sqlite3.connect(settings.resolved_database_path) as connection:
        status = connection.execute(
            "SELECT status FROM focus_sessions WHERE kind='body_double'"
        ).fetchone()[0]
    assert status == "ended"
    assert "auto_end_focus" in _action_types(settings)


def test_get_active_body_double_auto_end_does_not_end_focus_same_target(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)
    focus, _, _ = start_focus_session(
        target_type="task", target_id=task_id, settings=settings
    )
    start_body_double_session(
        interval_seconds=120,
        target_type="task",
        target_id=task_id,
        settings=settings,
    )

    with get_connection(settings) as connection:
        connection.execute(
            "UPDATE tasks SET status = 'deleted' WHERE id = ?", (task_id,)
        )

    assert get_active_body_double_with_target(settings=settings) is None
    with sqlite3.connect(settings.resolved_database_path) as connection:
        rows = connection.execute(
            "SELECT id, kind, status FROM focus_sessions ORDER BY id ASC"
        ).fetchall()
    assert (focus.id, "focus", "active") in rows
    assert any(kind == "body_double" and status == "ended" for _, kind, status in rows)


def test_delete_task_auto_ends_active_body_double(tmp_path: Path) -> None:
    """Soft-deleting the target task auto-ends a body-double session."""

    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)
    start_body_double_session(
        interval_seconds=120,
        target_type="task",
        target_id=task_id,
        settings=settings,
    )

    delete_task(task_id, settings=settings)

    assert get_active_body_double_with_target(settings=settings) is None
