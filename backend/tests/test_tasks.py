import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import init_db
from app.main import app
import app.main as main_module
from app.repositories import capture_to_inbox, complete_task, list_tasks, promote_inbox_item_to_task


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )


def create_task_via_api(client: TestClient, text: str) -> dict:
    inbox_item = client.post("/capture", json={"text": text}).json()
    response = client.post(f"/inbox/{inbox_item['inbox_item_id']}/promote-task")
    assert response.status_code == 201
    return response.json()


def test_get_tasks_returns_open_tasks_oldest_first(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first_task = create_task_via_api(client, "first task")
        second_task = create_task_via_api(client, "second task")
        done_response = client.post(f"/tasks/{first_task['id']}/done")
        response = client.get("/tasks")

    assert done_response.status_code == 200
    assert response.status_code == 200
    assert [task["id"] for task in response.json()] == [second_task["id"]]
    assert response.json()[0]["title"] == "second task"
    assert response.json()[0]["status"] == "open"


def test_get_tasks_orders_open_tasks_oldest_first(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first_task = create_task_via_api(client, "first task")
        second_task = create_task_via_api(client, "second task")
        response = client.get("/tasks")

    assert response.status_code == 200
    assert [task["id"] for task in response.json()] == [first_task["id"], second_task["id"]]
    assert [task["title"] for task in response.json()] == ["first task", "second task"]


def test_mark_task_done_updates_task_sets_completed_at_and_logs_action(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task = create_task_via_api(client, "pay rent")
        response = client.post(f"/tasks/{task['id']}/done")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == task["id"]
    assert payload["title"] == "pay rent"
    assert payload["status"] == "done"
    assert payload["completed_at"] is not None

    with sqlite3.connect(settings.resolved_database_path) as connection:
        task_row = connection.execute(
            "SELECT status, completed_at FROM tasks WHERE id = ?",
            (task["id"],),
        ).fetchone()
        action_row = connection.execute(
            """
            SELECT action_type, target_type, target_id
            FROM actions
            WHERE action_type = 'complete_task'
            """
        ).fetchone()

    assert task_row[0] == "done"
    assert task_row[1] == payload["completed_at"]
    assert action_row == ("complete_task", "task", task["id"])


def test_mark_missing_task_done_returns_404(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/tasks/999/done")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_mark_non_open_task_done_returns_409(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        task = create_task_via_api(client, "already done")
        first_response = client.post(f"/tasks/{task['id']}/done")
        second_response = client.post(f"/tasks/{task['id']}/done")

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert "not open" in second_response.json()["detail"].lower()

    with sqlite3.connect(settings.resolved_database_path) as connection:
        action_count = connection.execute(
            "SELECT COUNT(*) FROM actions WHERE action_type = 'complete_task' AND target_id = ?",
            (task["id"],),
        ).fetchone()[0]

    assert action_count == 1


def test_task_repository_lists_and_completes_tasks(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    inbox_item = capture_to_inbox("call dentist", settings)
    task = promote_inbox_item_to_task(inbox_item.id, settings)

    open_tasks_before = list_tasks(settings=settings)
    completed_task = complete_task(task.id, settings)
    open_tasks_after = list_tasks(settings=settings)

    assert [open_task.id for open_task in open_tasks_before] == [task.id]
    assert completed_task.status == "done"
    assert completed_task.completed_at is not None
    assert open_tasks_after == []

    with sqlite3.connect(settings.resolved_database_path) as connection:
        action_count = connection.execute(
            "SELECT COUNT(*) FROM actions WHERE action_type = 'complete_task' AND target_id = ?",
            (task.id,),
        ).fetchone()[0]

    assert action_count == 1
