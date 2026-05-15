"""Repository-level tests for Phase 6 task breakdown."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.config import Settings
from app.db import init_db
from app.repositories import (
    BreakdownConflictError,
    InvalidUpdateError,
    TaskNotFoundError,
    breakdown_task,
    capture_to_inbox,
    delete_task,
    list_task_children,
    promote_inbox_item_to_task,
    suggest_breakdown_steps,
)
from app.undo import undo_action


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )


def _make_task(settings: Settings, text: str = "ship phase six") -> int:
    inbox = capture_to_inbox(text, settings)
    return promote_inbox_item_to_task(inbox.id, settings).id


def test_breakdown_creates_children_with_parent_link(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings)

    parent, children, action_id = breakdown_task(
        parent_id, ["draft", "review", "ship"], settings=settings
    )

    assert parent.id == parent_id
    assert len(children) == 3
    assert [c.title for c in children] == ["draft", "review", "ship"]
    assert all(c.parent_task_id == parent_id for c in children)
    assert all(c.status == "open" for c in children)
    assert action_id > 0

    with sqlite3.connect(settings.resolved_database_path) as connection:
        rows = connection.execute(
            "SELECT id, title, parent_task_id FROM tasks WHERE parent_task_id = ?",
            (parent_id,),
        ).fetchall()
    assert len(rows) == 3


def test_breakdown_logs_single_action_with_child_ids(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings)

    _, children, action_id = breakdown_task(
        parent_id, ["a", "b"], settings=settings
    )

    with sqlite3.connect(settings.resolved_database_path) as connection:
        row = connection.execute(
            "SELECT action_type, target_type, target_id, before_json, after_json "
            "FROM actions WHERE id = ?",
            (action_id,),
        ).fetchone()

    assert row[0] == "breakdown"
    assert row[1] == "task"
    assert row[2] == parent_id
    before = json.loads(row[3])
    after = json.loads(row[4])
    assert before["parent"]["id"] == parent_id
    assert after["child_ids"] == [c.id for c in children]
    assert len(after["children"]) == 2


def test_breakdown_rejects_invalid_step_counts(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings)
    with pytest.raises(InvalidUpdateError):
        breakdown_task(parent_id, ["only"], settings=settings)
    with pytest.raises(InvalidUpdateError):
        breakdown_task(parent_id, ["a", "b", "c", "d", "e", "f"], settings=settings)


def test_breakdown_rejects_missing_or_deleted_parent(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    with pytest.raises(TaskNotFoundError):
        breakdown_task(999, ["a", "b"], settings=settings)

    parent_id = _make_task(settings)
    delete_task(parent_id, settings)
    with pytest.raises(TaskNotFoundError):
        breakdown_task(parent_id, ["a", "b"], settings=settings)


def test_breakdown_rejects_child_as_parent(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings)
    _, children, _ = breakdown_task(parent_id, ["a", "b"], settings=settings)
    with pytest.raises(BreakdownConflictError):
        breakdown_task(children[0].id, ["x", "y"], settings=settings)


def test_list_task_children_returns_creation_order(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings)
    breakdown_task(parent_id, ["one", "two", "three"], settings=settings)

    children = list_task_children(parent_id, settings)
    assert [c.title for c in children] == ["one", "two", "three"]


def test_list_task_children_includes_soft_deleted(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings)
    _, children, _ = breakdown_task(parent_id, ["one", "two"], settings=settings)
    delete_task(children[0].id, settings)

    listed = list_task_children(parent_id, settings)
    statuses = {c.id: c.status for c in listed}
    assert statuses[children[0].id] == "deleted"
    assert statuses[children[1].id] == "open"


def test_parent_soft_delete_does_not_delete_children(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings)
    _, children, _ = breakdown_task(parent_id, ["one", "two"], settings=settings)

    delete_task(parent_id, settings)

    with sqlite3.connect(settings.resolved_database_path) as connection:
        statuses = dict(
            connection.execute(
                "SELECT id, status FROM tasks WHERE parent_task_id = ?",
                (parent_id,),
            ).fetchall()
        )
    assert statuses == {children[0].id: "open", children[1].id: "open"}


def test_undo_breakdown_soft_deletes_children(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings)
    _, children, action_id = breakdown_task(
        parent_id, ["one", "two"], settings=settings
    )

    result = undo_action(action_id, settings)
    assert result["undone_action_type"] == "breakdown"

    with sqlite3.connect(settings.resolved_database_path) as connection:
        statuses = dict(
            connection.execute(
                "SELECT id, status FROM tasks WHERE id IN (?, ?)",
                (children[0].id, children[1].id),
            ).fetchall()
        )
        parent_status = connection.execute(
            "SELECT status FROM tasks WHERE id = ?", (parent_id,)
        ).fetchone()[0]
    assert statuses == {children[0].id: "deleted", children[1].id: "deleted"}
    assert parent_status == "open"


def test_suggest_breakdown_steps_uses_rules_only(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings, "call dentist")
    steps, source = suggest_breakdown_steps(parent_id, settings=settings)
    assert source == "rules"
    assert 2 <= len(steps) <= 5
    assert all("dentist" in step for step in steps)


def test_suggest_breakdown_steps_respects_max(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    parent_id = _make_task(settings, "tidy")
    steps, _ = suggest_breakdown_steps(parent_id, max_steps=2, settings=settings)
    assert len(steps) == 2


def test_suggest_breakdown_rejects_missing_task(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    with pytest.raises(TaskNotFoundError):
        suggest_breakdown_steps(404, settings=settings)
