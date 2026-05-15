"""Endpoint tests for Phase 6 survival mode."""

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
        SURVIVAL_MAX_TASKS=1,
        SURVIVAL_MAX_EVENTS=1,
    )


def _create_task(client: TestClient, text: str) -> int:
    captured = client.post("/capture", json={"text": text}).json()
    response = client.post(f"/inbox/{captured['inbox_item_id']}/promote-task")
    assert response.status_code == 201
    return response.json()["id"]


def _insert_event(settings: Settings, title: str, starts_at: str) -> int:
    with sqlite3.connect(settings.resolved_database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO events
              (title, starts_at, ends_at, source_inbox_item_id, status, created_at, updated_at)
            VALUES (?, ?, NULL, NULL, 'open', ?, ?)
            """,
            (title, starts_at, starts_at, starts_at),
        )
        return int(cursor.lastrowid)


def test_survival_state_is_inactive_initially(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.get("/survival")

    assert response.status_code == 200
    body = response.json()
    assert body["active"] is False
    assert body["session"] is None


def test_survival_enter_and_exit_are_idempotent(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first = client.post("/survival/enter", json={"note": "low energy"})
        second = client.post("/survival/enter", json={"note": "still low"})
        current = client.get("/survival")
        exit_first = client.post("/survival/exit", json={})
        exit_second = client.post("/survival/exit", json={})

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["session"]["id"] == second.json()["session"]["id"]
    assert current.json()["active"] is True
    assert exit_first.status_code == 200
    assert exit_first.json()["active"] is False
    assert exit_second.status_code == 200
    assert exit_second.json()["active"] is False

    with sqlite3.connect(settings.resolved_database_path) as connection:
        active = connection.execute(
            "SELECT COUNT(*) FROM focus_sessions WHERE kind='survival' AND status='active'"
        ).fetchone()[0]
        actions = connection.execute(
            "SELECT action_type FROM actions ORDER BY id"
        ).fetchall()
    assert active == 0
    assert ("enter_survival",) in actions
    assert ("exit_survival",) in actions


def test_survival_filters_dashboard_without_deleting_rows(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first_task = _create_task(client, "first task")
        second_task = _create_task(client, "second task")
        first_event = _insert_event(settings, "first event", "2026-05-16T09:00:00+00:00")
        second_event = _insert_event(settings, "second event", "2026-05-16T10:00:00+00:00")

        before = client.get("/dashboard").json()
        client.post("/survival/enter", json={})
        during = client.get("/dashboard").json()
        task_after = client.get(f"/tasks/{second_task}")
        event_after = client.get(f"/events/{second_event}")

    assert [task["id"] for task in before["tasks"]] == [first_task, second_task]
    assert [event["id"] for event in before["events"]] == [first_event, second_event]
    assert [task["id"] for task in during["tasks"]] == [first_task]
    assert [event["id"] for event in during["events"]] == [first_event]
    assert during["today"]["counts"]["open_tasks"] == 1
    assert during["today"]["counts"]["upcoming_events"] == 1
    assert during["focus"]["survival"] is True
    assert task_after.status_code == 200
    assert task_after.json()["status"] == "open"
    assert event_after.status_code == 200
    assert event_after.json()["status"] == "open"


def test_capture_still_works_during_survival_mode(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        client.post("/survival/enter", json={})
        captured = client.post("/capture", json={"text": "new thought"})
        inbox = client.get("/inbox")

    assert captured.status_code == 201
    assert captured.json()["classification"]["intent"] == "inbox"
    assert any(item["text"] == "new thought" for item in inbox.json())


def test_undoing_stale_survival_exit_conflicts_when_another_session_is_active(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        client.post("/survival/enter", json={})
        client.post("/survival/exit", json={})
        with sqlite3.connect(settings.resolved_database_path) as connection:
            action_id = connection.execute(
                """
                SELECT id FROM actions
                WHERE action_type = 'exit_survival'
                ORDER BY id DESC
                LIMIT 1
                """,
            ).fetchone()[0]
        client.post("/survival/enter", json={})
        undo_response = client.post(f"/undo/{action_id}")

    assert undo_response.status_code == 409
    with sqlite3.connect(settings.resolved_database_path) as connection:
        active_count = connection.execute(
            "SELECT COUNT(*) FROM focus_sessions WHERE kind='survival' AND status='active'"
        ).fetchone()[0]
    assert active_count == 1


def test_survival_enter_and_exit_are_undoable(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        client.post("/survival/enter", json={})
        undo_enter = client.post("/undo/latest")
        after_undo_enter = client.get("/survival")
        client.post("/survival/enter", json={})
        client.post("/survival/exit", json={})
        undo_exit = client.post("/undo/latest")
        after_undo_exit = client.get("/survival")

    assert undo_enter.status_code == 200
    assert undo_enter.json()["undone_action_type"] == "enter_survival"
    assert after_undo_enter.json()["active"] is False
    assert undo_exit.status_code == 200
    assert undo_exit.json()["undone_action_type"] == "exit_survival"
    assert after_undo_exit.json()["active"] is True


def test_survival_today_caps_task_count(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first_task = _create_task(client, "first task")
        _create_task(client, "second task")
        client.post("/survival/enter", json={})
        today = client.get("/today").json()

    assert today["open_tasks_count"] == 1
    assert today["one_thing"] == {
        "type": "task",
        "id": first_task,
        "text": "first task",
    }
    assert "Survival mode" in today["message"]
