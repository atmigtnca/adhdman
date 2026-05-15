"""Tests for PATCH /tasks/{id} and DELETE /tasks/{id}.

All tests run offline: classification is disabled so the LLM path is never
exercised and tasks are created through promote-task.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.main import app


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )


def create_task(client: TestClient, text: str) -> dict:
    inbox = client.post("/capture", json={"text": text}).json()
    response = client.post(f"/inbox/{inbox['inbox_item_id']}/promote-task")
    assert response.status_code == 201
    return response.json()


def test_patch_task_updates_title_and_logs_full_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task = create_task(client, "call dentist")
        response = client.patch(
            f"/tasks/{task['id']}", json={"title": "call dentist tomorrow"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["id"] == task["id"]
    assert payload["task"]["title"] == "call dentist tomorrow"
    assert payload["task"]["status"] == "open"
    assert isinstance(payload["action_id"], int)

    with sqlite3.connect(settings.resolved_database_path) as connection:
        before, after = connection.execute(
            "SELECT before_json, after_json FROM actions WHERE id = ?",
            (payload["action_id"],),
        ).fetchone()
    before_data = json.loads(before)
    after_data = json.loads(after)
    assert before_data["title"] == "call dentist"
    assert after_data["title"] == "call dentist tomorrow"
    assert before_data["updated_at"] != after_data["updated_at"]
    assert before_data["created_at"] == after_data["created_at"]


def test_patch_task_updates_due_at_and_status(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task = create_task(client, "buy groceries")
        response = client.patch(
            f"/tasks/{task['id']}",
            json={"due_at": "2026-06-01T09:00", "status": "cancelled"},
        )

    assert response.status_code == 200
    body = response.json()["task"]
    assert body["due_at"] == "2026-06-01T09:00"
    assert body["status"] == "cancelled"


def test_patch_task_empty_body_rejected(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task = create_task(client, "tidy desk")
        response = client.patch(f"/tasks/{task['id']}", json={})

    assert response.status_code == 400
    assert "at least one" in response.json()["detail"].lower()


def test_patch_task_unknown_field_rejected(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task = create_task(client, "review pr")
        response = client.patch(
            f"/tasks/{task['id']}", json={"completed_at": "2026-06-01T09:00"}
        )

    assert response.status_code == 422


def test_patch_task_empty_title_rejected(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task = create_task(client, "ship it")
        response = client.patch(f"/tasks/{task['id']}", json={"title": "   "})

    assert response.status_code == 422


def test_patch_missing_task_returns_404(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.patch("/tasks/999", json={"title": "ghost"})

    assert response.status_code == 404


def test_delete_task_soft_deletes_and_logs_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task = create_task(client, "obsolete idea")
        response = client.delete(f"/tasks/{task['id']}")
        listing = client.get("/tasks")
        # The soft-deleted row remains addressable via GET /tasks/{id}.
        read = client.get(f"/tasks/{task['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["task"]["status"] == "deleted"
    assert isinstance(body["action_id"], int)
    assert task["id"] not in [t["id"] for t in listing.json()]
    assert read.status_code == 200
    assert read.json()["status"] == "deleted"

    with sqlite3.connect(settings.resolved_database_path) as connection:
        action_row = connection.execute(
            """
            SELECT action_type, target_type, target_id, before_json, after_json
            FROM actions WHERE id = ?
            """,
            (body["action_id"],),
        ).fetchone()
    assert action_row[0] == "delete_task"
    assert action_row[1] == "task"
    assert action_row[2] == task["id"]
    assert json.loads(action_row[3])["status"] == "open"
    assert json.loads(action_row[4])["status"] == "deleted"


def test_delete_already_deleted_task_returns_404(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task = create_task(client, "double delete")
        first = client.delete(f"/tasks/{task['id']}")
        second = client.delete(f"/tasks/{task['id']}")

    assert first.status_code == 200
    assert second.status_code == 404


def test_patch_soft_deleted_task_returns_404(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task = create_task(client, "no edits after delete")
        client.delete(f"/tasks/{task['id']}")
        response = client.patch(
            f"/tasks/{task['id']}", json={"title": "ghost edit"}
        )

    assert response.status_code == 404
