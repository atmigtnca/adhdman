import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
import app.main as main_module


def make_settings(tmp_path: Path) -> Settings:
    return Settings(DATABASE_PATH=tmp_path / "adhdman.sqlite")


def test_capture_stores_open_inbox_item_and_logs_action(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/capture", json={"text": "pay rent"})

    assert response.status_code == 201
    payload = response.json()
    assert payload["id"] > 0
    assert payload["text"] == "pay rent"
    assert payload["status"] == "open"
    assert payload["created_at"]
    assert payload["updated_at"]

    with sqlite3.connect(settings.resolved_database_path) as connection:
        inbox_row = connection.execute(
            "SELECT id, text, status FROM inbox_items WHERE id = ?",
            (payload["id"],),
        ).fetchone()
        action_row = connection.execute(
            """
            SELECT action_type, target_type, target_id, after_json
            FROM actions
            WHERE target_id = ?
            """,
            (payload["id"],),
        ).fetchone()

    assert inbox_row == (payload["id"], "pay rent", "open")
    assert action_row[:3] == ("capture", "inbox_item", payload["id"])
    action_after = json.loads(action_row[3])
    assert action_after["id"] == payload["id"]
    assert action_after["text"] == "pay rent"
    assert action_after["status"] == "open"


def test_capture_trims_text_before_storing(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/capture", json={"text": "  pay rent  "})

    assert response.status_code == 201
    assert response.json()["text"] == "pay rent"

    with sqlite3.connect(settings.resolved_database_path) as connection:
        stored_text = connection.execute("SELECT text FROM inbox_items").fetchone()[0]

    assert stored_text == "pay rent"


def test_capture_rejects_whitespace_only_text(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/capture", json={"text": "   \n\t  "})

    assert response.status_code == 422

    with sqlite3.connect(settings.resolved_database_path) as connection:
        inbox_count = connection.execute("SELECT COUNT(*) FROM inbox_items").fetchone()[0]
        action_count = connection.execute("SELECT COUNT(*) FROM actions").fetchone()[0]

    assert inbox_count == 0
    assert action_count == 0
