import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
import app.main as main_module


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )


def test_capture_stores_open_inbox_item_and_logs_action(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/capture", json={"text": "pay rent"})

    assert response.status_code == 201
    payload = response.json()
    inbox_item_id = payload["inbox_item_id"]
    assert inbox_item_id > 0
    assert payload["classification"]["intent"] == "inbox"
    assert payload["classification"]["source"] == "fallback"
    assert payload["classification"]["created"] is None

    with sqlite3.connect(settings.resolved_database_path) as connection:
        inbox_row = connection.execute(
            "SELECT id, text, status FROM inbox_items WHERE id = ?",
            (inbox_item_id,),
        ).fetchone()
        action_rows = connection.execute(
            """
            SELECT action_type, target_type, target_id, after_json
            FROM actions
            WHERE target_id = ?
            ORDER BY id ASC
            """,
            (inbox_item_id,),
        ).fetchall()

    assert inbox_row == (inbox_item_id, "pay rent", "open")
    assert len(action_rows) == 1
    assert action_rows[0][:3] == ("capture", "inbox_item", inbox_item_id)
    action_after = json.loads(action_rows[0][3])
    assert action_after["id"] == inbox_item_id
    assert action_after["text"] == "pay rent"
    assert action_after["status"] == "open"


def test_capture_trims_text_before_storing(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/capture", json={"text": "  pay rent  "})

    assert response.status_code == 201
    inbox_item_id = response.json()["inbox_item_id"]

    with sqlite3.connect(settings.resolved_database_path) as connection:
        stored_text = connection.execute(
            "SELECT text FROM inbox_items WHERE id = ?",
            (inbox_item_id,),
        ).fetchone()[0]

    assert stored_text == "pay rent"


def _classify_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        OPENROUTER_API_KEY=None,
    )


def test_capture_auto_creates_task_for_imperative(tmp_path: Path, monkeypatch) -> None:
    settings = _classify_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/capture", json={"text": "buy milk"})

    assert response.status_code == 201
    payload = response.json()
    inbox_item_id = payload["inbox_item_id"]
    classification = payload["classification"]
    assert classification["intent"] == "task"
    assert classification["source"] == "rules"
    assert classification["created"]["type"] == "task"
    task_id = classification["created"]["id"]

    with sqlite3.connect(settings.resolved_database_path) as connection:
        inbox_row = connection.execute(
            "SELECT status, promoted_to_type, promoted_to_id FROM inbox_items WHERE id = ?",
            (inbox_item_id,),
        ).fetchone()
        task_row = connection.execute(
            "SELECT title, status, source_inbox_item_id FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        action_types = [
            row[0]
            for row in connection.execute(
                "SELECT action_type FROM actions ORDER BY id ASC"
            ).fetchall()
        ]

    assert inbox_row == ("promoted", "task", task_id)
    assert task_row == ("buy milk", "open", inbox_item_id)
    assert action_types == ["capture", "classify_task"]


def test_capture_auto_creates_event_for_iso_timestamp(tmp_path: Path, monkeypatch) -> None:
    settings = _classify_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/capture", json={"text": "Dentist 2026-07-04T09:00"}
        )

    assert response.status_code == 201
    payload = response.json()
    inbox_item_id = payload["inbox_item_id"]
    classification = payload["classification"]
    assert classification["intent"] == "event"
    assert classification["source"] == "rules"
    assert classification["starts_at"] == "2026-07-04T09:00"
    assert classification["created"]["type"] == "event"
    event_id = classification["created"]["id"]

    with sqlite3.connect(settings.resolved_database_path) as connection:
        inbox_row = connection.execute(
            "SELECT status, promoted_to_type, promoted_to_id FROM inbox_items WHERE id = ?",
            (inbox_item_id,),
        ).fetchone()
        event_row = connection.execute(
            "SELECT title, starts_at, source_inbox_item_id FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        action_types = [
            row[0]
            for row in connection.execute(
                "SELECT action_type FROM actions ORDER BY id ASC"
            ).fetchall()
        ]

    assert inbox_row == ("promoted", "event", event_id)
    assert event_row == ("Dentist 2026-07-04T09:00", "2026-07-04T09:00", inbox_item_id)
    assert action_types == ["capture", "classify_event"]


def test_capture_ambiguous_text_stays_in_inbox_with_fallback_action(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _classify_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/capture", json={"text": "groceries"})

    assert response.status_code == 201
    payload = response.json()
    inbox_item_id = payload["inbox_item_id"]
    classification = payload["classification"]
    assert classification["intent"] == "inbox"
    assert classification["source"] == "fallback"
    assert classification["created"] is None

    with sqlite3.connect(settings.resolved_database_path) as connection:
        inbox_row = connection.execute(
            "SELECT status FROM inbox_items WHERE id = ?",
            (inbox_item_id,),
        ).fetchone()
        action_types = [
            row[0]
            for row in connection.execute(
                "SELECT action_type FROM actions ORDER BY id ASC"
            ).fetchall()
        ]
        task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

    assert inbox_row == ("open",)
    assert action_types == ["capture", "classify_inbox_fallback"]
    assert task_count == 0


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
