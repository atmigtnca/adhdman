"""Endpoint tests for Phase 6 body-double endpoints."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.main import app


def make_settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
        **overrides,
    )


def _create_task(client: TestClient, text: str) -> int:
    captured = client.post("/capture", json={"text": text}).json()
    response = client.post(f"/inbox/{captured['inbox_item_id']}/promote-task")
    assert response.status_code == 201
    return response.json()["id"]


def test_body_double_current_is_empty_initially(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.get("/body-double/current")

    assert response.status_code == 200
    body = response.json()
    assert body["session"] is None
    assert body["target"] is None
    assert "body-double" in body["message"].lower()


def test_body_double_start_with_explicit_interval(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/body-double/start", json={"interval_seconds": 120}
        )

    assert response.status_code == 201
    body = response.json()
    assert body["session"]["status"] == "active"
    assert body["session"]["interval_seconds"] == 120
    assert body["session"]["kind"] == "body_double"
    assert body["target"] is None


def test_body_double_start_defaults_interval_from_settings(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, BODY_DOUBLE_DEFAULT_INTERVAL=240)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/body-double/start", json={})

    assert response.status_code == 201
    assert response.json()["session"]["interval_seconds"] == 240


def test_body_double_start_rejects_interval_below_min(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/body-double/start", json={"interval_seconds": 5}
        )

    assert response.status_code == 400
    assert "between" in response.json()["detail"]


def test_body_double_start_rejects_interval_above_max(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/body-double/start", json={"interval_seconds": 9999}
        )

    assert response.status_code == 400


def test_body_double_start_with_task_target_resolves_title(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task_id = _create_task(client, "draft report")
        response = client.post(
            "/body-double/start",
            json={
                "interval_seconds": 120,
                "target_type": "task",
                "target_id": task_id,
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["target"]["title"] == "draft report"
    assert body["session"]["target_id"] == task_id


def test_body_double_start_404_when_target_missing(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/body-double/start",
            json={
                "interval_seconds": 120,
                "target_type": "task",
                "target_id": 999,
            },
        )

    assert response.status_code == 404


def test_body_double_start_conflict_without_replace(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first = client.post(
            "/body-double/start", json={"interval_seconds": 120}
        ).json()
        conflict = client.post(
            "/body-double/start", json={"interval_seconds": 240}
        )

    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["existing"]["id"] == first["session"]["id"]
    assert "fail" not in detail["message"].lower()
    assert "forgot" not in detail["message"].lower()


def test_body_double_start_replace_swaps_session(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first = client.post(
            "/body-double/start", json={"interval_seconds": 120}
        ).json()
        second = client.post(
            "/body-double/start",
            json={"interval_seconds": 240, "replace": True},
        )

    assert second.status_code == 201
    second_body = second.json()
    assert second_body["session"]["id"] != first["session"]["id"]
    assert second_body["session"]["interval_seconds"] == 240

    with sqlite3.connect(settings.resolved_database_path) as connection:
        active = connection.execute(
            "SELECT COUNT(*) FROM focus_sessions "
            "WHERE kind='body_double' AND status='active'"
        ).fetchone()[0]
    assert active == 1


def test_check_in_updates_last_check_in_at(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        started = client.post(
            "/body-double/start", json={"interval_seconds": 120}
        ).json()
        original = started["session"]["last_check_in_at"]
        response = client.post("/body-double/check-in")

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["id"] == started["session"]["id"]
    assert body["session"]["last_check_in_at"] is not None
    assert body["session"]["last_check_in_at"] >= (original or "")


def test_check_in_404_when_no_active_session(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/body-double/check-in")

    assert response.status_code == 404


def test_stop_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first = client.post("/body-double/stop")
        assert first.status_code == 200
        assert first.json()["session"] is None

        client.post("/body-double/start", json={"interval_seconds": 120})
        stopped = client.post("/body-double/stop")
        assert stopped.status_code == 200

        again = client.post("/body-double/stop")
        assert again.status_code == 200

        current = client.get("/body-double/current").json()
        assert current["session"] is None


def test_start_writes_action_row(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        client.post("/body-double/start", json={"interval_seconds": 120})

    with sqlite3.connect(settings.resolved_database_path) as connection:
        rows = connection.execute(
            "SELECT action_type FROM actions WHERE target_type = 'focus_session'"
        ).fetchall()
    assert ("start_body_double",) in rows


def test_rejects_unknown_fields(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/body-double/start",
            json={"interval_seconds": 120, "bogus": True},
        )

    assert response.status_code == 422
