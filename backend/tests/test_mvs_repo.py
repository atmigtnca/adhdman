"""Repository-level tests for Phase 6 minimum viable step (MVS)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.config import Settings
from app.db import init_db
from app.repositories import (
    FocusTargetNotFoundError,
    InboxItemNotOpenError,
    capture_to_inbox,
    commit_mvs_step,
    delete_task,
    promote_inbox_item_to_task,
    suggest_mvs_step,
)
from app.undo import ActionConflictError, undo_action, undo_latest


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )


def _make_task(settings: Settings, text: str = "call dentist") -> int:
    inbox = capture_to_inbox(text, settings)
    return promote_inbox_item_to_task(inbox.id, settings).id


def test_suggest_mvs_step_for_task_is_rules_only(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings, "tidy desk")
    step, source = suggest_mvs_step("task", task_id, settings)
    assert source == "rules"
    assert "tidy desk" in step
    assert len(step) <= 500


def test_suggest_mvs_step_for_inbox_item(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    inbox = capture_to_inbox("call vet", settings)
    step, source = suggest_mvs_step("inbox_item", inbox.id, settings)
    assert source == "rules"
    assert "call vet" in step


def test_suggest_mvs_step_rejects_missing_target(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    with pytest.raises(FocusTargetNotFoundError):
        suggest_mvs_step("task", 999, settings)
    with pytest.raises(FocusTargetNotFoundError):
        suggest_mvs_step("inbox_item", 999, settings)


def test_suggest_mvs_step_rejects_deleted_task(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    task_id = _make_task(settings)
    delete_task(task_id, settings)
    with pytest.raises(FocusTargetNotFoundError):
        suggest_mvs_step("task", task_id, settings)


def test_commit_mvs_step_creates_child_and_starts_focus(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings, "ship phase six")

    child, focus, target, task_action_id, focus_action_id = commit_mvs_step(
        "task", parent_id, "open the editor", settings
    )

    assert child.parent_task_id == parent_id
    assert child.title == "open the editor"
    assert focus.status == "active"
    assert focus.target_id == child.id
    assert target.id == child.id
    assert task_action_id > 0 and focus_action_id > task_action_id

    with sqlite3.connect(settings.resolved_database_path) as connection:
        action_types = [
            row[0]
            for row in connection.execute(
                "SELECT action_type FROM actions ORDER BY id ASC"
            ).fetchall()
        ]
        active_focus = connection.execute(
            "SELECT COUNT(*) FROM focus_sessions WHERE kind='focus' AND status='active'"
        ).fetchone()[0]
    assert "mvs_create_child" in action_types
    assert action_types[-1] == "start_focus"
    assert active_focus == 1


def test_commit_mvs_step_replaces_existing_focus(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    first_task = _make_task(settings, "first")
    second_task = _make_task(settings, "second")
    # Seed a focus on the first task so commit must swap calmly.
    from app.repositories import start_focus_session

    start_focus_session("task", first_task, settings=settings)
    child, focus, _target, _t_act, _f_act = commit_mvs_step(
        "task", second_task, "do the smallest part", settings
    )

    with sqlite3.connect(settings.resolved_database_path) as connection:
        active = connection.execute(
            """
            SELECT id, target_id FROM focus_sessions
            WHERE kind='focus' AND status='active'
            """,
        ).fetchall()
        replace_actions = connection.execute(
            "SELECT COUNT(*) FROM actions WHERE action_type='replace_focus'"
        ).fetchone()[0]
    assert len(active) == 1
    assert active[0][1] == child.id
    assert replace_actions == 1
    assert focus.target_id == child.id


def test_commit_mvs_step_rejects_child_target(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings)
    child, _focus, _target, _t_act, _f_act = commit_mvs_step(
        "task", parent_id, "step one", settings
    )
    from app.repositories import BreakdownConflictError

    with pytest.raises(BreakdownConflictError):
        commit_mvs_step("task", child.id, "deeper step", settings)


def test_commit_mvs_step_promotes_inbox_item(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    inbox = capture_to_inbox("wedding gift", settings)

    child, focus, _target, _t_act, _f_act = commit_mvs_step(
        "inbox_item", inbox.id, "browse for ideas two minutes", settings
    )

    assert child.parent_task_id is not None
    assert focus.target_id == child.id

    with sqlite3.connect(settings.resolved_database_path) as connection:
        connection.row_factory = sqlite3.Row
        inbox_row = connection.execute(
            "SELECT status, promoted_to_type, promoted_to_id FROM inbox_items WHERE id = ?",
            (inbox.id,),
        ).fetchone()
        parent_row = connection.execute(
            "SELECT id, title, source_inbox_item_id FROM tasks WHERE id = ?",
            (child.parent_task_id,),
        ).fetchone()
        action_types = [
            r[0]
            for r in connection.execute(
                "SELECT action_type FROM actions ORDER BY id ASC"
            ).fetchall()
        ]
    assert inbox_row["status"] == "promoted"
    assert inbox_row["promoted_to_id"] == parent_row["id"]
    assert parent_row["title"] == "wedding gift"
    assert parent_row["source_inbox_item_id"] == inbox.id
    assert "promote_task" in action_types
    assert "mvs_create_child" in action_types
    assert action_types[-1] == "start_focus"


def test_commit_mvs_step_rejects_promoted_inbox(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    inbox = capture_to_inbox("already moved", settings)
    promote_inbox_item_to_task(inbox.id, settings)
    with pytest.raises(InboxItemNotOpenError):
        commit_mvs_step("inbox_item", inbox.id, "do the thing", settings)


def test_commit_mvs_step_rejects_missing_target(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    with pytest.raises(FocusTargetNotFoundError):
        commit_mvs_step("task", 999, "ghost", settings)
    with pytest.raises(FocusTargetNotFoundError):
        commit_mvs_step("inbox_item", 999, "ghost", settings)


def test_undo_mvs_create_child_soft_deletes_child_and_ends_focus(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings)
    child, focus, _target, child_action_id, _focus_action_id = commit_mvs_step(
        "task", parent_id, "smallest step", settings
    )

    result = undo_action(child_action_id, settings)
    assert result["undone_action_type"] == "mvs_create_child"

    with sqlite3.connect(settings.resolved_database_path) as connection:
        status = connection.execute(
            "SELECT status FROM tasks WHERE id = ?", (child.id,)
        ).fetchone()[0]
        focus_row = connection.execute(
            "SELECT status FROM focus_sessions WHERE id = ?", (focus.id,)
        ).fetchone()
        auto_end = connection.execute(
            "SELECT COUNT(*) FROM actions WHERE action_type='auto_end_focus'"
        ).fetchone()[0]
    assert status == "deleted"
    assert focus_row[0] == "ended"
    assert auto_end == 1


def test_undo_latest_after_mvs_first_stops_focus_then_deletes_child(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings)
    child, focus, _target, _child_action_id, _focus_action_id = commit_mvs_step(
        "task", parent_id, "smallest step", settings
    )

    first = undo_latest(settings)
    second = undo_latest(settings)

    assert first["undone_action_type"] == "start_focus"
    assert second["undone_action_type"] == "mvs_create_child"
    with sqlite3.connect(settings.resolved_database_path) as connection:
        child_status = connection.execute(
            "SELECT status FROM tasks WHERE id = ?", (child.id,)
        ).fetchone()[0]
        focus_status = connection.execute(
            "SELECT status FROM focus_sessions WHERE id = ?", (focus.id,)
        ).fetchone()[0]
    assert child_status == "deleted"
    assert focus_status == "ended"


def test_undo_promote_parent_conflicts_while_mvs_child_exists(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    inbox = capture_to_inbox("wedding gift", settings)
    child, _focus, _target, child_action_id, _focus_action_id = commit_mvs_step(
        "inbox_item", inbox.id, "browse for ideas", settings
    )

    with sqlite3.connect(settings.resolved_database_path) as connection:
        promote_action_id = connection.execute(
            "SELECT id FROM actions WHERE action_type = 'promote_task'"
        ).fetchone()[0]

    with pytest.raises(ActionConflictError):
        undo_action(promote_action_id, settings)

    undo_action(child_action_id, settings)
    result = undo_action(promote_action_id, settings)
    assert result["undone_action_type"] == "promote_task"
    assert child.id > 0


def test_mvs_create_child_action_payload_shape(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings)
    child, _focus, _target, child_action_id, _focus_action_id = commit_mvs_step(
        "task", parent_id, "step text", settings
    )

    with sqlite3.connect(settings.resolved_database_path) as connection:
        row = connection.execute(
            "SELECT before_json, after_json, target_id, target_type "
            "FROM actions WHERE id = ?",
            (child_action_id,),
        ).fetchone()

    before = json.loads(row[0])
    after = json.loads(row[1])
    assert row[2] == child.id
    assert row[3] == "task"
    assert before["parent_task_id"] == parent_id
    assert after["child"]["id"] == child.id
    assert after["parent_task_id"] == parent_id
