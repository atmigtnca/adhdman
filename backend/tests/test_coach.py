from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

import app.main as main_module
from app.coach import coach_next
from app.config import Settings
from app.main import app
from tests.test_agenda import seed_oscar_db_cpp_kcc


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )


def action_count(settings: Settings) -> int:
    with sqlite3.connect(settings.resolved_database_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM actions").fetchone()[0])


def test_coach_next_fallback_returns_short_execution_coach_message(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    ids = seed_oscar_db_cpp_kcc(settings)

    payload = coach_next(
        now="2026-05-31T12:00:00+09:00",
        user_text="시작이 안 돼 너무 커",
        settings=settings,
    )

    assert payload.mode == "stuck"
    assert payload.source == "rules"
    assert len(payload.message) <= 240
    assert "하나" in payload.message or "2분" in payload.message
    assert payload.tiny_step
    assert len(payload.tiny_step) <= 80
    assert payload.suggested_commands
    assert len(payload.suggested_commands) <= 3
    assert any("쪼개기" in cmd or "최소" in cmd for cmd in payload.suggested_commands)
    assert payload.needs_confirmation is False
    assert str(ids["before_oscar"]) not in payload.message


def test_coach_next_mvs_for_deadline_pressure_after_event(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    seed_oscar_db_cpp_kcc(settings)
    with sqlite3.connect(settings.resolved_database_path) as connection:
        connection.execute("UPDATE tasks SET status = 'done' WHERE title LIKE '오스카모임%'")

    payload = coach_next(
        now="2026-05-31T22:30:00+09:00",
        user_text="망했다 시간이 없어",
        settings=settings,
    )

    assert payload.mode == "mvs"
    assert "60점" in payload.message or "제출" in payload.message
    assert any("최소" in cmd or "mvs" in cmd.lower() for cmd in payload.suggested_commands)


def test_coach_endpoint_is_read_only_and_schema_valid(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    seed_oscar_db_cpp_kcc(settings)
    monkeypatch.setattr(main_module, "settings", settings)

    before_actions = action_count(settings)
    with TestClient(app) as client:
        response = client.post(
            "/coach/next",
            json={"now": "2026-05-31T12:00:00+09:00", "user_text": "못하겠어"},
        )
    after_actions = action_count(settings)

    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "mode",
        "message",
        "tiny_step",
        "suggested_commands",
        "needs_confirmation",
        "clarification_options",
        "source",
    }
    assert data["mode"] == "stuck"
    assert data["source"] == "rules"
    assert after_actions == before_actions


def test_coach_get_endpoint_is_read_only_for_web_and_tui(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    seed_oscar_db_cpp_kcc(settings)
    monkeypatch.setattr(main_module, "settings", settings)

    before_actions = action_count(settings)
    with TestClient(app) as client:
        response = client.get(
            "/coach/next",
            params={"now": "2026-05-31T12:00:00+09:00"},
        )
    after_actions = action_count(settings)

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "agenda"
    assert data["source"] == "rules"
    assert after_actions == before_actions


def test_coach_endpoint_rejects_invalid_now(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    seed_oscar_db_cpp_kcc(settings)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/coach/next", json={"now": "bad"})

    assert response.status_code == 400
