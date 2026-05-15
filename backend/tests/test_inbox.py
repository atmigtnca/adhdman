import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import init_db
from app.main import app
import app.main as main_module
from app.repositories import capture_to_inbox


def make_settings(tmp_path: Path) -> Settings:
    return Settings(DATABASE_PATH=tmp_path / "adhdman.sqlite")


def test_get_inbox_returns_captured_open_items_oldest_first(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first = client.post("/capture", json={"text": "first thought"}).json()
        second = client.post("/capture", json={"text": "second thought"}).json()
        response = client.get("/inbox")

    assert response.status_code == 200
    assert response.json() == [first, second]


def test_get_inbox_excludes_promoted_items_by_default(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        promoted = client.post("/capture", json={"text": "already promoted"}).json()
        open_item = client.post("/capture", json={"text": "still open"}).json()

        with sqlite3.connect(settings.resolved_database_path) as connection:
            connection.execute(
                """
                UPDATE inbox_items
                SET status = 'promoted', promoted_to_type = 'task', promoted_to_id = 1
                WHERE id = ?
                """,
                (promoted["id"],),
            )

        response = client.get("/inbox")

    assert response.status_code == 200
    assert response.json() == [open_item]


def test_list_inbox_items_repository_defaults_to_open_oldest_first(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    init_db(settings)
    first = capture_to_inbox("first", settings)
    promoted = capture_to_inbox("promoted", settings)
    second = capture_to_inbox("second", settings)

    with sqlite3.connect(settings.resolved_database_path) as connection:
        connection.execute(
            "UPDATE inbox_items SET status = 'promoted' WHERE id = ?",
            (promoted.id,),
        )

    from app.repositories import list_inbox_items

    items = list_inbox_items(settings=settings)

    assert [item.id for item in items] == [first.id, second.id]
    assert [item.status for item in items] == ["open", "open"]
