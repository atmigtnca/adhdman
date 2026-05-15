"""Endpoint tests for Phase 6 task breakdown endpoints."""

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


def _create_task(client: TestClient, text: str = "ship phase six") -> int:
    captured = client.post("/capture", json={"text": text}).json()
    response = client.post(f"/inbox/{captured['inbox_item_id']}/promote-task")
    assert response.status_code == 201
    return response.json()["id"]


def test_breakdown_endpoint_creates_children(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        parent_id = _create_task(client)
        response = client.post(
            f"/tasks/{parent_id}/breakdown",
            json={"steps": ["draft", "review", "ship"]},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["parent"]["id"] == parent_id
    assert [child["title"] for child in body["children"]] == [
        "draft",
        "review",
        "ship",
    ]
    assert all(child["parent_task_id"] == parent_id for child in body["children"])
    assert body["action_id"] > 0


def test_children_endpoint_lists_children_read_only(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        parent_id = _create_task(client, "prepare demo")
        before_count = _task_count(settings)
        client.post(
            f"/tasks/{parent_id}/breakdown",
            json={"steps": ["one", "two"]},
        )
        response = client.get(f"/tasks/{parent_id}/children")
        after_count = _task_count(settings)

    assert response.status_code == 200
    assert [child["title"] for child in response.json()] == ["one", "two"]
    assert after_count == before_count + 2


def test_breakdown_suggest_endpoint_is_read_only(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        parent_id = _create_task(client, "call dentist")
        before_count = _task_count(settings)
        response = client.post(
            f"/tasks/{parent_id}/breakdown/suggest",
            json={"max_steps": 2},
        )
        after_count = _task_count(settings)

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "rules"
    assert len(body["steps"]) == 2
    assert all("dentist" in step for step in body["steps"])
    assert after_count == before_count


def test_breakdown_endpoint_rejects_missing_or_deleted_parent(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        missing = client.post(
            "/tasks/999/breakdown", json={"steps": ["a", "b"]}
        )
        parent_id = _create_task(client)
        delete_response = client.delete(f"/tasks/{parent_id}")
        deleted = client.post(
            f"/tasks/{parent_id}/breakdown", json={"steps": ["a", "b"]}
        )

    assert missing.status_code == 404
    assert delete_response.status_code == 200
    assert deleted.status_code == 404


def test_breakdown_endpoint_rejects_invalid_step_count(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        parent_id = _create_task(client)
        response = client.post(
            f"/tasks/{parent_id}/breakdown", json={"steps": ["only"]}
        )

    assert response.status_code == 422


def test_undo_latest_after_breakdown_soft_deletes_children(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        parent_id = _create_task(client)
        breakdown = client.post(
            f"/tasks/{parent_id}/breakdown", json={"steps": ["one", "two"]}
        )
        child_ids = [child["id"] for child in breakdown.json()["children"]]
        undo = client.post("/undo/latest")

    assert breakdown.status_code == 201
    assert undo.status_code == 200
    with sqlite3.connect(settings.resolved_database_path) as connection:
        statuses = dict(
            connection.execute(
                "SELECT id, status FROM tasks WHERE id IN (?, ?)", child_ids
            ).fetchall()
        )
    assert statuses == {child_ids[0]: "deleted", child_ids[1]: "deleted"}


def _task_count(settings: Settings) -> int:
    with sqlite3.connect(settings.resolved_database_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
