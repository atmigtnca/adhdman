"""Endpoint tests for Phase 6 one-thing focus endpoints."""

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


def _create_task(client: TestClient, text: str) -> int:
    captured = client.post("/capture", json={"text": text}).json()
    response = client.post(f"/inbox/{captured['inbox_item_id']}/promote-task")
    assert response.status_code == 201
    return response.json()["id"]


def test_focus_current_is_empty_initially(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.get("/focus/current")

    assert response.status_code == 200
    body = response.json()
    assert body["session"] is None
    assert body["target"] is None
    assert "focus" in body["message"].lower()


def test_focus_start_sets_active_session(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task_id = _create_task(client, "call dentist")
        response = client.post(
            "/focus/start", json={"target_type": "task", "target_id": task_id}
        )
        assert response.status_code == 201
        body = response.json()
        assert body["session"]["status"] == "active"
        assert body["session"]["target_id"] == task_id
        assert body["target"]["title"] == "call dentist"

        current = client.get("/focus/current").json()
        assert current["session"]["id"] == body["session"]["id"]


def test_focus_start_404_when_target_missing(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/focus/start", json={"target_type": "task", "target_id": 999}
        )

    assert response.status_code == 404


def test_focus_start_conflict_without_replace_returns_calm_payload(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first_task = _create_task(client, "first")
        second_task = _create_task(client, "second")
        first = client.post(
            "/focus/start", json={"target_type": "task", "target_id": first_task}
        ).json()
        conflict = client.post(
            "/focus/start", json={"target_type": "task", "target_id": second_task}
        )

    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert "message" in detail
    assert "existing" in detail
    assert detail["existing"]["id"] == first["session"]["id"]
    # non-shaming wording
    assert "fail" not in detail["message"].lower()
    assert "forgot" not in detail["message"].lower()


def test_focus_start_replace_swaps_session(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first_task = _create_task(client, "first")
        second_task = _create_task(client, "second")
        first = client.post(
            "/focus/start", json={"target_type": "task", "target_id": first_task}
        ).json()
        second = client.post(
            "/focus/start",
            json={
                "target_type": "task",
                "target_id": second_task,
                "replace": True,
            },
        )

    assert second.status_code == 201
    second_body = second.json()
    assert second_body["session"]["id"] != first["session"]["id"]
    assert second_body["target"]["title"] == "second"

    with sqlite3.connect(settings.resolved_database_path) as connection:
        active = connection.execute(
            "SELECT COUNT(*) FROM focus_sessions WHERE kind='focus' AND status='active'"
        ).fetchone()[0]
    assert active == 1


def test_focus_stop_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        # stop with no active session
        first = client.post("/focus/stop")
        assert first.status_code == 200
        assert first.json()["session"] is None

        task_id = _create_task(client, "task")
        client.post(
            "/focus/start", json={"target_type": "task", "target_id": task_id}
        )
        stopped = client.post("/focus/stop")
        assert stopped.status_code == 200
        assert stopped.json()["session"] is None

        # call again, still calm
        again = client.post("/focus/stop")
        assert again.status_code == 200

        current = client.get("/focus/current").json()
        assert current["session"] is None


def test_focus_start_writes_action_row(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task_id = _create_task(client, "task")
        client.post(
            "/focus/start", json={"target_type": "task", "target_id": task_id}
        )

    with sqlite3.connect(settings.resolved_database_path) as connection:
        rows = connection.execute(
            "SELECT action_type FROM actions WHERE target_type = 'focus_session'"
        ).fetchall()
    assert ("start_focus",) in rows


def test_focus_start_rejects_unknown_target_type(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/focus/start", json={"target_type": "novel", "target_id": 1}
        )

    assert response.status_code == 422
