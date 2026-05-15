import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import init_db
from app.main import app
import app.main as main_module
from app.repositories import capture_to_inbox, promote_inbox_item_to_task


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )


def test_promote_inbox_item_to_task_creates_task_updates_inbox_and_logs_action(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        inbox_item = client.post("/capture", json={"text": "pay rent"}).json()
        response = client.post(f"/inbox/{inbox_item['inbox_item_id']}/promote-task")

    assert response.status_code == 201
    payload = response.json()
    assert payload["id"] > 0
    assert payload["title"] == "pay rent"
    assert payload["status"] == "open"
    assert payload["source_inbox_item_id"] == inbox_item["inbox_item_id"]
    assert payload["created_at"]
    assert payload["updated_at"]
    assert payload["completed_at"] is None

    with sqlite3.connect(settings.resolved_database_path) as connection:
        inbox_row = connection.execute(
            """
            SELECT status, promoted_to_type, promoted_to_id
            FROM inbox_items
            WHERE id = ?
            """,
            (inbox_item["inbox_item_id"],),
        ).fetchone()
        task_row = connection.execute(
            """
            SELECT id, title, status, source_inbox_item_id, completed_at
            FROM tasks
            WHERE id = ?
            """,
            (payload["id"],),
        ).fetchone()
        action_row = connection.execute(
            """
            SELECT action_type, target_type, target_id
            FROM actions
            WHERE action_type = 'promote_task'
            """
        ).fetchone()

    assert inbox_row == ("promoted", "task", payload["id"])
    assert task_row == (payload["id"], "pay rent", "open", inbox_item["inbox_item_id"], None)
    assert action_row == ("promote_task", "task", payload["id"])


def test_promote_missing_inbox_item_returns_404(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/inbox/999/promote-task")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_promote_non_open_inbox_item_returns_409(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        inbox_item = client.post("/capture", json={"text": "already promoted"}).json()
        first_response = client.post(f"/inbox/{inbox_item['inbox_item_id']}/promote-task")
        second_response = client.post(f"/inbox/{inbox_item['inbox_item_id']}/promote-task")

    assert first_response.status_code == 201
    assert second_response.status_code == 409
    assert "not open" in second_response.json()["detail"].lower()

    with sqlite3.connect(settings.resolved_database_path) as connection:
        task_count = connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE source_inbox_item_id = ?",
            (inbox_item["inbox_item_id"],),
        ).fetchone()[0]

    assert task_count == 1


def test_promote_repository_persists_task_inbox_update_and_action(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    inbox_item = capture_to_inbox("call dentist", settings)

    task = promote_inbox_item_to_task(inbox_item.id, settings)

    with sqlite3.connect(settings.resolved_database_path) as connection:
        inbox_row = connection.execute(
            "SELECT status, promoted_to_type, promoted_to_id FROM inbox_items WHERE id = ?",
            (inbox_item.id,),
        ).fetchone()
        action_count = connection.execute(
            "SELECT COUNT(*) FROM actions WHERE action_type = 'promote_task' AND target_id = ?",
            (task.id,),
        ).fetchone()[0]

    assert task.title == "call dentist"
    assert task.source_inbox_item_id == inbox_item.id
    assert inbox_row == ("promoted", "task", task.id)
    assert action_count == 1
