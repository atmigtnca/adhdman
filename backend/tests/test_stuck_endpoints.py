"""Endpoint tests for Phase 6 block-reset / stuck flow."""

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


def _create_task(client: TestClient, text: str = "stuck task") -> int:
    captured = client.post("/capture", json={"text": text}).json()
    response = client.post(f"/inbox/{captured['inbox_item_id']}/promote-task")
    assert response.status_code == 201
    return response.json()["id"]


def test_stuck_options_are_read_only_and_non_shaming(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task_id = _create_task(client)
        before_count = _action_count(settings)
        response = client.get(f"/stuck/options?target_type=task&target_id={task_id}")
        after_count = _action_count(settings)

    assert response.status_code == 200
    body = response.json()
    assert body["prompt"]
    lowered_prompt = body["prompt"].lower()
    for token in ("forgot", "lazy", "you missed", "your fault"):
        assert token not in lowered_prompt
    assert [option["choice"] for option in body["options"]] == [
        "shrink",
        "swap",
        "skip",
        "park",
    ]
    assert after_count == before_count


def test_stuck_shrink_endpoint_sets_block_state(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task_id = _create_task(client)
        response = client.post(
            "/stuck",
            json={"target_type": "task", "target_id": task_id, "choice": "shrink"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["choice"] == "shrink"
    assert body["task"]["block_state"] == "needs_breakdown"
    assert body["action_id"] > 0


def test_stuck_park_hides_from_today(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task_id = _create_task(client, "park me")
        before = client.get("/today").json()
        response = client.post(
            "/stuck",
            json={"target_type": "task", "target_id": task_id, "choice": "park"},
        )
        after = client.get("/today").json()

    assert before["open_tasks_count"] == 1
    assert response.status_code == 200
    assert response.json()["task"]["block_state"] == "parked"
    assert after["open_tasks_count"] == 0
    assert after["one_thing"] is None


def test_stuck_skip_endpoint_keeps_task_open(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task_id = _create_task(client)
        response = client.post(
            "/stuck",
            json={"target_type": "task", "target_id": task_id, "choice": "skip"},
        )

    assert response.status_code == 200
    assert response.json()["task"]["status"] == "open"


def test_stuck_swap_clears_block_state_without_stopping_focus(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task_id = _create_task(client)
        focus = client.post(
            "/focus/start", json={"target_type": "task", "target_id": task_id}
        ).json()
        response = client.post(
            "/stuck",
            json={"target_type": "task", "target_id": task_id, "choice": "swap"},
        )
        current = client.get("/focus/current").json()

    assert response.status_code == 200
    assert current["session"]["id"] == focus["session"]["id"]
    with sqlite3.connect(settings.resolved_database_path) as connection:
        focus_status = connection.execute(
            "SELECT status FROM focus_sessions WHERE id = ?",
            (focus["session"]["id"],),
        ).fetchone()[0]
    assert focus_status == "active"


def test_stuck_endpoint_rejects_bad_target_or_choice(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        missing = client.post(
            "/stuck",
            json={"target_type": "task", "target_id": 999, "choice": "park"},
        )
        bad_choice = client.post(
            "/stuck",
            json={"target_type": "task", "target_id": 999, "choice": "explode"},
        )
        bad_target = client.get("/stuck/options?target_type=event&target_id=1")

    assert missing.status_code == 404
    assert bad_choice.status_code == 422
    assert bad_target.status_code == 400


def test_undo_latest_reverts_block_reset(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task_id = _create_task(client)
        client.post(
            "/stuck",
            json={"target_type": "task", "target_id": task_id, "choice": "park"},
        )
        undo = client.post("/undo/latest")
        today = client.get("/today").json()

    assert undo.status_code == 200
    assert today["open_tasks_count"] == 1


def _action_count(settings: Settings) -> int:
    with sqlite3.connect(settings.resolved_database_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
