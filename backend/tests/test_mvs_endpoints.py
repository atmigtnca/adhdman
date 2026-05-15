"""Endpoint tests for Phase 6 minimum viable step (MVS) endpoints."""

from __future__ import annotations

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


def _capture(client: TestClient, text: str) -> int:
    return client.post("/capture", json={"text": text}).json()["inbox_item_id"]


def _create_task(client: TestClient, text: str = "ship phase six") -> int:
    inbox_id = _capture(client, text)
    response = client.post(f"/inbox/{inbox_id}/promote-task")
    assert response.status_code == 201
    return response.json()["id"]


def test_mvs_suggest_for_task_is_read_only(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task_id = _create_task(client, "tidy desk")
        before = _task_count(settings)
        response = client.post(
            "/mvs/suggest", json={"target_type": "task", "target_id": task_id}
        )
        after = _task_count(settings)

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "rules"
    assert "tidy desk" in body["step"]
    assert "prompt" in body and body["prompt"]
    assert before == after


def test_mvs_suggest_for_inbox_item(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        inbox_id = _capture(client, "call vet")
        response = client.post(
            "/mvs/suggest", json={"target_type": "inbox_item", "target_id": inbox_id}
        )

    assert response.status_code == 200
    assert "call vet" in response.json()["step"]


def test_mvs_suggest_404_for_missing_target(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/mvs/suggest", json={"target_type": "task", "target_id": 999}
        )
    assert response.status_code == 404


def test_mvs_suggest_rejects_unknown_target_type(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/mvs/suggest", json={"target_type": "event", "target_id": 1}
        )
    assert response.status_code == 422


def test_mvs_commit_for_task_creates_child_and_focus(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        parent_id = _create_task(client, "ship phase six")
        response = client.post(
            "/mvs/commit",
            json={
                "target_type": "task",
                "target_id": parent_id,
                "step": "open the editor",
            },
        )
        current = client.get("/focus/current")

    assert response.status_code == 201
    body = response.json()
    assert body["task"]["parent_task_id"] == parent_id
    assert body["task"]["title"] == "open the editor"
    assert body["focus"]["status"] == "active"
    assert body["focus"]["target_id"] == body["task"]["id"]
    assert body["task_action_id"] > 0
    assert body["focus_action_id"] > body["task_action_id"]

    cur = current.json()
    assert cur["session"]["id"] == body["focus"]["id"]
    assert cur["target"]["id"] == body["task"]["id"]


def test_mvs_commit_for_inbox_item_promotes_and_focuses(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        inbox_id = _capture(client, "wedding gift")
        response = client.post(
            "/mvs/commit",
            json={
                "target_type": "inbox_item",
                "target_id": inbox_id,
                "step": "browse for ideas two minutes",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["task"]["title"] == "browse for ideas two minutes"
    assert body["task"]["parent_task_id"] is not None
    assert body["focus"]["target_id"] == body["task"]["id"]

    with sqlite3.connect(settings.resolved_database_path) as connection:
        connection.row_factory = sqlite3.Row
        inbox_row = connection.execute(
            "SELECT status, promoted_to_id FROM inbox_items WHERE id = ?",
            (inbox_id,),
        ).fetchone()
        parent_row = connection.execute(
            "SELECT title FROM tasks WHERE id = ?",
            (body["task"]["parent_task_id"],),
        ).fetchone()
    assert inbox_row["status"] == "promoted"
    assert inbox_row["promoted_to_id"] == body["task"]["parent_task_id"]
    assert parent_row["title"] == "wedding gift"


def test_mvs_commit_replaces_existing_focus(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first_id = _create_task(client, "first")
        second_id = _create_task(client, "second")
        client.post(
            "/focus/start", json={"target_type": "task", "target_id": first_id}
        )
        response = client.post(
            "/mvs/commit",
            json={
                "target_type": "task",
                "target_id": second_id,
                "step": "do smallest part",
            },
        )

    assert response.status_code == 201
    with sqlite3.connect(settings.resolved_database_path) as connection:
        active = connection.execute(
            "SELECT COUNT(*) FROM focus_sessions WHERE kind='focus' AND status='active'"
        ).fetchone()[0]
    assert active == 1


def test_mvs_commit_rejects_promoted_inbox(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        inbox_id = _capture(client, "already moved")
        client.post(f"/inbox/{inbox_id}/promote-task")
        response = client.post(
            "/mvs/commit",
            json={
                "target_type": "inbox_item",
                "target_id": inbox_id,
                "step": "do the thing",
            },
        )
    assert response.status_code == 409


def test_mvs_commit_404_for_missing_target(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/mvs/commit",
            json={"target_type": "task", "target_id": 999, "step": "ghost"},
        )
    assert response.status_code == 404


def test_mvs_commit_rejects_child_target(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        parent_id = _create_task(client)
        first = client.post(
            "/mvs/commit",
            json={"target_type": "task", "target_id": parent_id, "step": "one"},
        )
        child_id = first.json()["task"]["id"]
        second = client.post(
            "/mvs/commit",
            json={"target_type": "task", "target_id": child_id, "step": "deeper"},
        )
    assert second.status_code == 409


def test_mvs_commit_then_undo_soft_deletes_child(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        parent_id = _create_task(client)
        commit = client.post(
            "/mvs/commit",
            json={"target_type": "task", "target_id": parent_id, "step": "tiny"},
        )
        child_id = commit.json()["task"]["id"]
        task_action_id = commit.json()["task_action_id"]
        undo = client.post(f"/undo/{task_action_id}")

    assert undo.status_code == 200
    assert undo.json()["undone_action_type"] == "mvs_create_child"
    with sqlite3.connect(settings.resolved_database_path) as connection:
        status = connection.execute(
            "SELECT status FROM tasks WHERE id = ?", (child_id,)
        ).fetchone()[0]
        focus_status = connection.execute(
            """
            SELECT status FROM focus_sessions
            WHERE kind='focus' ORDER BY id DESC LIMIT 1
            """,
        ).fetchone()[0]
    assert status == "deleted"
    assert focus_status == "ended"


def test_mvs_commit_validates_empty_step(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        parent_id = _create_task(client)
        response = client.post(
            "/mvs/commit",
            json={"target_type": "task", "target_id": parent_id, "step": "   "},
        )
    assert response.status_code == 422


def _task_count(settings: Settings) -> int:
    with sqlite3.connect(settings.resolved_database_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
