"""Repository-level tests for Phase 6 block-reset / stuck flow."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.config import Settings
from app.db import init_db
from app.repositories import (
    InvalidUpdateError,
    TaskNotFoundError,
    apply_stuck_choice,
    capture_to_inbox,
    complete_task,
    get_today_summary,
    promote_inbox_item_to_task,
    start_focus_session,
    update_task,
)
from app.undo import undo_action


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )


def _make_task(settings: Settings, text: str = "stuck task") -> int:
    inbox = capture_to_inbox(text, settings)
    return promote_inbox_item_to_task(inbox.id, settings).id


def _task_row(settings: Settings, task_id: int) -> sqlite3.Row:
    with sqlite3.connect(settings.resolved_database_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, status, due_at, block_state FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    assert row is not None
    return row


def test_shrink_marks_task_needs_breakdown_and_undo_restores(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)

    task, action_id = apply_stuck_choice("task", task_id, "shrink", settings)

    assert task.block_state == "needs_breakdown"
    undo_action(action_id, settings)
    assert _task_row(settings, task_id)["block_state"] is None


def test_park_hides_task_from_today_and_undo_restores(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings, "visible task")

    before = get_today_summary(settings)
    assert before.one_thing is not None
    assert before.one_thing.id == task_id

    _, action_id = apply_stuck_choice("task", task_id, "park", settings)

    parked = get_today_summary(settings)
    assert parked.open_tasks_count == 0
    assert parked.one_thing is None
    assert _task_row(settings, task_id)["block_state"] == "parked"

    undo_action(action_id, settings)
    restored = get_today_summary(settings)
    assert restored.open_tasks_count == 1
    assert restored.one_thing is not None
    assert restored.one_thing.id == task_id


def test_skip_pushes_due_at_by_one_day_when_present(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)
    with sqlite3.connect(settings.resolved_database_path) as connection:
        connection.execute(
            "UPDATE tasks SET due_at = ? WHERE id = ?",
            ("2026-05-16T09:00:00+00:00", task_id),
        )

    task, action_id = apply_stuck_choice("task", task_id, "skip", settings)

    assert task.due_at == "2026-05-17T09:00:00+00:00"
    undo_action(action_id, settings)
    assert _task_row(settings, task_id)["due_at"] == "2026-05-16T09:00:00+00:00"


def test_skip_leaves_missing_due_at_missing(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)

    task, _ = apply_stuck_choice("task", task_id, "skip", settings)

    assert task.due_at is None
    assert task.status == "open"


def test_swap_clears_block_state_without_changing_focus(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)
    apply_stuck_choice("task", task_id, "park", settings)
    focus, _, _ = start_focus_session("task", task_id, settings=settings)

    task, action_id = apply_stuck_choice("task", task_id, "swap", settings)

    assert task.block_state is None
    with sqlite3.connect(settings.resolved_database_path) as connection:
        focus_status = connection.execute(
            "SELECT status FROM focus_sessions WHERE id = ?", (focus.id,)
        ).fetchone()[0]
        action_types = [
            row[0]
            for row in connection.execute(
                "SELECT action_type FROM actions ORDER BY id ASC"
            ).fetchall()
        ]
    assert focus_status == "active"
    assert "stop_focus" not in action_types
    assert "block_reset" in action_types
    undo_action(action_id, settings)
    assert _task_row(settings, task_id)["block_state"] == "parked"


def test_completion_and_reopen_clear_block_state(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    done_task_id = _make_task(settings, "finish me")
    reopen_task_id = _make_task(settings, "reopen me")

    apply_stuck_choice("task", done_task_id, "park", settings)
    completed = complete_task(done_task_id, settings=settings)

    apply_stuck_choice("task", reopen_task_id, "park", settings)
    reopened, _ = update_task(reopen_task_id, {"status": "open"}, settings=settings)

    assert completed.block_state is None
    assert _task_row(settings, done_task_id)["block_state"] is None
    assert reopened.block_state is None
    assert _task_row(settings, reopen_task_id)["block_state"] is None


def test_stuck_rejects_non_task_target_and_missing_task(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)

    with pytest.raises(Exception):
        apply_stuck_choice("event", 1, "shrink", settings)
    with pytest.raises(TaskNotFoundError):
        apply_stuck_choice("task", 999, "shrink", settings)
    task_id = _make_task(settings)
    with pytest.raises(InvalidUpdateError):
        apply_stuck_choice("task", task_id, "explode", settings)


def test_block_reset_action_has_before_after_snapshots(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)

    _, action_id = apply_stuck_choice("task", task_id, "shrink", settings)

    with sqlite3.connect(settings.resolved_database_path) as connection:
        row = connection.execute(
            "SELECT action_type, before_json, after_json FROM actions WHERE id = ?",
            (action_id,),
        ).fetchone()
    assert row[0] == "block_reset"
    assert '"choice": "shrink"' in row[1]
    assert '"needs_breakdown"' in row[2]
