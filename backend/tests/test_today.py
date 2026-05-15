from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import init_db
from app.main import app
import app.main as main_module
from app.repositories import capture_to_inbox, complete_task, get_today_summary, promote_inbox_item_to_task


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )


def test_get_today_returns_oldest_open_task_when_tasks_exist(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first_inbox = client.post("/capture", json={"text": "pay rent"}).json()
        second_inbox = client.post("/capture", json={"text": "wash dishes"}).json()
        first_task = client.post(f"/inbox/{first_inbox['inbox_item_id']}/promote-task").json()
        client.post(f"/inbox/{second_inbox['inbox_item_id']}/promote-task")
        open_inbox = client.post("/capture", json={"text": "random thought"}).json()
        response = client.get("/today")

    assert response.status_code == 200
    assert response.json() == {
        "open_tasks_count": 2,
        "inbox_count": 1,
        "one_thing": {"type": "task", "id": first_task["id"], "text": "pay rent"},
        "message": "One thing is ready.",
    }
    assert open_inbox["inbox_item_id"] > 0


def test_get_today_falls_back_to_oldest_open_inbox_item(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first = client.post("/capture", json={"text": "first thought"}).json()
        client.post("/capture", json={"text": "second thought"})
        response = client.get("/today")

    assert response.status_code == 200
    assert response.json() == {
        "open_tasks_count": 0,
        "inbox_count": 2,
        "one_thing": {"type": "inbox", "id": first["inbox_item_id"], "text": "first thought"},
        "message": "One thing is ready.",
    }


def test_get_today_returns_non_shaming_empty_message_when_nothing_waits(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.get("/today")

    assert response.status_code == 200
    assert response.json() == {
        "open_tasks_count": 0,
        "inbox_count": 0,
        "one_thing": None,
        "message": "Nothing is waiting right now. You can capture the next thought when it appears.",
    }


def test_today_summary_repository_ignores_done_tasks_and_promoted_inbox(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    done_source = capture_to_inbox("done task", settings)
    done_task = promote_inbox_item_to_task(done_source.id, settings)
    complete_task(done_task.id, settings)
    open_inbox = capture_to_inbox("open inbox", settings)

    summary = get_today_summary(settings=settings)

    assert summary.open_tasks_count == 0
    assert summary.inbox_count == 1
    assert summary.one_thing is not None
    assert summary.one_thing.type == "inbox"
    assert summary.one_thing.id == open_inbox.id
    assert summary.one_thing.text == "open inbox"
