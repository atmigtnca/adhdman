from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

import app.main as main_module
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


def test_get_agenda_now_returns_current_action_card_payload(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    ids = seed_oscar_db_cpp_kcc(settings)
    monkeypatch.setattr(main_module, "settings", settings)

    before_actions = action_count(settings)
    with TestClient(app) as client:
        response = client.get("/agenda/now", params={"now": "2026-05-31T12:00:00+09:00"})
    after_actions = action_count(settings)

    assert response.status_code == 200
    payload = response.json()
    assert payload["now"]["kind"] == "task"
    assert payload["now"]["id"] == ids["before_oscar"]
    assert payload["now"]["title"] == "오스카모임 전까지 과제 끝내기"
    assert payload["now"]["urgency"] == "before_event"
    assert "오스카 모임" in payload["now"]["reason"]
    assert payload["next"][0]["kind"] == "event"
    assert payload["next"][1]["id"] == ids["db"]
    assert ids["kcc"] in [item["id"] for item in payload["later"]]
    assert payload["counts"] == {"tasks": 3, "events": 2, "inbox": 0}
    assert after_actions == before_actions


def test_get_agenda_now_requires_valid_iso_now(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    seed_oscar_db_cpp_kcc(settings)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.get("/agenda/now", params={"now": "not-a-date"})

    assert response.status_code == 400
    assert "invalid" in response.json()["detail"].lower()
