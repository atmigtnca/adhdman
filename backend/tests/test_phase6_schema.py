"""Schema-level tests for Phase 6 execution helpers.

Covers the additive SQLite migration (``focus_sessions`` table,
``tasks.parent_task_id``, ``tasks.block_state``) and the Pydantic request/
response models. No endpoints are exercised here; that lands in Task 3.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.config import Settings
from app.db import init_db
from app.schemas import (
    BodyDoubleStartRequest,
    BreakdownRequest,
    BreakdownSuggestResponse,
    FocusCurrentResponse,
    FocusPanelResponse,
    FocusSessionResponse,
    FocusStartRequest,
    MVSCommitRequest,
    MVSSuggestRequest,
    StuckOptionsResponse,
    StuckRequest,
    SurvivalStateResponse,
    SurvivalToggleRequest,
)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(DATABASE_PATH=str(tmp_path / "phase6" / "adhdman.sqlite"))


def column_names(database_path: Path, table: str) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def table_names(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {row[0] for row in rows}


# ----- migration -----


def test_init_db_creates_focus_sessions_table(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    database_path = init_db(settings)

    assert "focus_sessions" in table_names(database_path)
    columns = column_names(database_path, "focus_sessions")
    expected = {
        "id",
        "kind",
        "target_type",
        "target_id",
        "status",
        "started_at",
        "ended_at",
        "interval_seconds",
        "note",
        "last_check_in_at",
    }
    assert expected.issubset(columns)


def test_init_db_adds_phase_6_task_columns(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    database_path = init_db(settings)

    task_columns = column_names(database_path, "tasks")
    assert "parent_task_id" in task_columns
    assert "block_state" in task_columns


def test_init_db_backfills_phase_6_columns_on_existing_db(tmp_path: Path) -> None:
    """A pre-Phase-6 database gains the new columns without losing data."""

    settings = make_settings(tmp_path)
    database_path = settings.resolved_database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'open',
              source_inbox_item_id INTEGER,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              completed_at TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO tasks (title, created_at, updated_at) VALUES (?, ?, ?)",
            ("legacy task", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        connection.commit()

    assert "parent_task_id" not in column_names(database_path, "tasks")
    assert "block_state" not in column_names(database_path, "tasks")

    init_db(settings)

    task_columns = column_names(database_path, "tasks")
    assert "parent_task_id" in task_columns
    assert "block_state" in task_columns

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT title, parent_task_id, block_state FROM tasks WHERE title = 'legacy task'"
        ).fetchone()
    assert row == ("legacy task", None, None)


def test_init_db_is_idempotent_for_phase_6(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    init_db(settings)  # second call must not error

    database_path = settings.resolved_database_path
    assert "focus_sessions" in table_names(database_path)
    assert {"parent_task_id", "block_state"}.issubset(column_names(database_path, "tasks"))


def test_focus_sessions_accepts_insert(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    database_path = init_db(settings)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO focus_sessions
              (kind, target_type, target_id, status, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("focus", "task", 1, "active", "2026-05-16T10:00:00+00:00"),
        )
        connection.commit()
        row = connection.execute(
            "SELECT kind, status, ended_at FROM focus_sessions"
        ).fetchone()
    assert row == ("focus", "active", None)


# ----- pydantic schemas -----


def test_focus_start_request_rejects_unknown_target() -> None:
    with pytest.raises(ValueError):
        FocusStartRequest(target_type="other", target_id=1)  # type: ignore[arg-type]


def test_focus_start_request_rejects_extra_fields() -> None:
    with pytest.raises(ValueError):
        FocusStartRequest(target_type="task", target_id=1, surprise="x")  # type: ignore[call-arg]


def test_focus_current_response_empty_payload_is_valid() -> None:
    response = FocusCurrentResponse(message="No focus session right now. That is fine.")
    assert response.session is None
    assert response.target is None


def test_focus_session_response_round_trips() -> None:
    session = FocusSessionResponse(
        id=1,
        kind="focus",
        target_type="task",
        target_id=7,
        status="active",
        started_at="2026-05-16T10:00:00+00:00",
    )
    assert session.ended_at is None
    assert session.last_check_in_at is None


def test_breakdown_request_enforces_step_count() -> None:
    with pytest.raises(ValueError):
        BreakdownRequest(steps=["only one"])
    with pytest.raises(ValueError):
        BreakdownRequest(steps=["a", "b", "c", "d", "e", "f"])


def test_breakdown_request_normalizes_whitespace() -> None:
    request = BreakdownRequest(steps=["  call ", "schedule "])
    assert request.steps == ["call", "schedule"]


def test_breakdown_request_rejects_empty_step() -> None:
    with pytest.raises(ValueError):
        BreakdownRequest(steps=["valid", "   "])


def test_breakdown_suggest_response_source_is_constrained() -> None:
    response = BreakdownSuggestResponse(
        steps=["a", "b"], source="rules", prompt="x"
    )
    assert response.source == "rules"
    with pytest.raises(ValueError):
        BreakdownSuggestResponse(
            steps=["a"], source="manual", prompt="x"  # type: ignore[arg-type]
        )


def test_stuck_request_rejects_invalid_choice() -> None:
    with pytest.raises(ValueError):
        StuckRequest(target_type="task", target_id=1, choice="explode")  # type: ignore[arg-type]


def test_stuck_options_response_lists_four_choices() -> None:
    response = StuckOptionsResponse(
        prompt="Pick one",
        options=[
            {"choice": "shrink", "label": "x"},
            {"choice": "swap", "label": "x"},
            {"choice": "skip", "label": "x"},
            {"choice": "park", "label": "x"},
        ],
    )
    assert {o.choice for o in response.options} == {"shrink", "swap", "skip", "park"}


def test_body_double_start_request_rejects_zero_interval() -> None:
    with pytest.raises(ValueError):
        BodyDoubleStartRequest(interval_seconds=0)


def test_body_double_start_request_allows_no_target() -> None:
    request = BodyDoubleStartRequest(interval_seconds=300)
    assert request.target_type is None
    assert request.target_id is None


def test_body_double_start_request_requires_paired_target_fields() -> None:
    with pytest.raises(ValueError):
        BodyDoubleStartRequest(interval_seconds=300, target_type="task")
    with pytest.raises(ValueError):
        BodyDoubleStartRequest(interval_seconds=300, target_id=1)

    request = BodyDoubleStartRequest(
        interval_seconds=300, target_type="task", target_id=1
    )
    assert request.target_type == "task"
    assert request.target_id == 1


def test_mvs_suggest_request_rejects_event_target() -> None:
    with pytest.raises(ValueError):
        MVSSuggestRequest(target_type="event", target_id=1)  # type: ignore[arg-type]


def test_mvs_commit_request_normalizes_step() -> None:
    request = MVSCommitRequest(target_type="task", target_id=1, step="  write subject  ")
    assert request.step == "write subject"


def test_survival_toggle_request_accepts_empty_body() -> None:
    request = SurvivalToggleRequest()
    assert request.note is None


def test_survival_state_response_minimal() -> None:
    state = SurvivalStateResponse(active=False, message="off")
    assert state.session is None


def test_focus_panel_response_supports_all_off() -> None:
    panel = FocusPanelResponse(survival=False)
    assert panel.session is None
    assert panel.body_double is None
    assert panel.target is None
