"""Tests for POST /undo/{action_id} and POST /undo/latest.

All tests run offline. Tasks are created through the promote-task flow with
classification disabled so no LLM is involved; events are produced through the
deterministic rules pipeline that triggers on inputs containing absolute ISO
timestamps.
"""

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


def make_event_settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        OPENROUTER_API_KEY=None,
        **overrides,
    )


def _latest_action(settings: Settings, action_type: str) -> int:
    with sqlite3.connect(settings.resolved_database_path) as connection:
        row = connection.execute(
            "SELECT id FROM actions WHERE action_type = ? ORDER BY id DESC LIMIT 1",
            (action_type,),
        ).fetchone()
    assert row is not None
    return row[0]


def _action_row(settings: Settings, action_id: int) -> tuple:
    with sqlite3.connect(settings.resolved_database_path) as connection:
        return connection.execute(
            "SELECT action_type, undone_at FROM actions WHERE id = ?",
            (action_id,),
        ).fetchone()


def test_undo_capture_soft_deletes_inbox_item(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        inbox = client.post("/capture", json={"text": "buy milk"}).json()
        capture_action_id = _latest_action(settings, "capture")
        response = client.post(f"/undo/{capture_action_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["undone_action_id"] == capture_action_id
    assert payload["undone_action_type"] == "capture"
    assert payload["restored"]["inbox_item"]["status"] == "deleted"

    listing = TestClient(app)
    with listing as client:
        items = client.get("/inbox").json()
    assert all(item["id"] != inbox["inbox_item_id"] for item in items)
    assert _action_row(settings, capture_action_id)[1] is not None


def test_undo_complete_task_restores_status_and_completed_at(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        inbox = client.post("/capture", json={"text": "ship feature"}).json()
        task = client.post(
            f"/inbox/{inbox['inbox_item_id']}/promote-task"
        ).json()
        done = client.post(f"/tasks/{task['id']}/done")
        assert done.status_code == 200
        complete_action_id = _latest_action(settings, "complete_task")
        undo_response = client.post(f"/undo/{complete_action_id}")
        restored_task = client.get(f"/tasks/{task['id']}").json()

    assert undo_response.status_code == 200
    assert restored_task["status"] == "open"
    assert restored_task["completed_at"] is None


def test_undo_update_event_restores_columns_byte_for_byte(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_event_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        captured = client.post(
            "/capture", json={"text": "Dentist 2026-07-04T09:00"}
        ).json()
        event_id = captured["classification"]["created"]["id"]
        before = client.get(f"/events/{event_id}").json()
        patch = client.patch(
            f"/events/{event_id}",
            json={"title": "Dentist rescheduled", "starts_at": "2026-07-05T09:00"},
        ).json()
        undo_response = client.post(f"/undo/{patch['action_id']}")
        after = client.get(f"/events/{event_id}").json()

    assert undo_response.status_code == 200
    # updated_at moves forward on each write, so compare every other column.
    for key in ("id", "title", "starts_at", "ends_at", "status", "created_at"):
        assert after[key] == before[key], f"mismatch on {key}"


def test_undo_delete_task_restores_status(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        inbox = client.post("/capture", json={"text": "obsolete idea"}).json()
        task = client.post(
            f"/inbox/{inbox['inbox_item_id']}/promote-task"
        ).json()
        delete = client.delete(f"/tasks/{task['id']}").json()
        undo = client.post(f"/undo/{delete['action_id']}")
        restored = client.get(f"/tasks/{task['id']}").json()

    assert undo.status_code == 200
    assert restored["status"] == "open"


def test_undo_update_task_round_trip(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        inbox = client.post("/capture", json={"text": "draft memo"}).json()
        task = client.post(
            f"/inbox/{inbox['inbox_item_id']}/promote-task"
        ).json()
        original = client.get(f"/tasks/{task['id']}").json()
        patch = client.patch(
            f"/tasks/{task['id']}",
            json={"title": "draft memo v2", "due_at": "2026-06-01T09:00"},
        ).json()
        undo = client.post(f"/undo/{patch['action_id']}")
        restored = client.get(f"/tasks/{task['id']}").json()

    assert undo.status_code == 200
    for key in ("title", "status", "due_at", "completed_at", "created_at"):
        assert restored[key] == original[key], key


def test_undo_promote_task_restores_inbox_and_soft_deletes_task(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        inbox = client.post("/capture", json={"text": "schedule retro"}).json()
        promote = client.post(
            f"/inbox/{inbox['inbox_item_id']}/promote-task"
        ).json()
        promote_action_id = _latest_action(settings, "promote_task")
        undo = client.post(f"/undo/{promote_action_id}")
        task_after = client.get(f"/tasks/{promote['id']}").json()
        inbox_listing = client.get("/inbox").json()

    assert undo.status_code == 200
    assert task_after["status"] == "deleted"
    assert any(
        item["id"] == inbox["inbox_item_id"] and item["status"] == "open"
        for item in inbox_listing
    )


def test_undo_already_undone_returns_409(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        client.post("/capture", json={"text": "thing"})
        action_id = _latest_action(settings, "capture")
        first = client.post(f"/undo/{action_id}")
        second = client.post(f"/undo/{action_id}")

    assert first.status_code == 200
    assert second.status_code == 409
    assert "already" in second.json()["detail"].lower()


def test_undo_unknown_action_returns_404(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/undo/99999")

    assert response.status_code == 404


def test_undo_latest_picks_newest_reversible(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        client.post("/capture", json={"text": "first"})
        client.post("/capture", json={"text": "second"})
        second_capture_id = _latest_action(settings, "capture")
        response = client.post("/undo/latest")

    assert response.status_code == 200
    assert response.json()["undone_action_id"] == second_capture_id


def test_undo_latest_with_no_actions_returns_404(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/undo/latest")

    assert response.status_code == 404


def test_undo_writes_undo_action_row(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        client.post("/capture", json={"text": "audit trail"})
        action_id = _latest_action(settings, "capture")
        response = client.post(f"/undo/{action_id}")

    assert response.status_code == 200
    payload = response.json()
    with sqlite3.connect(settings.resolved_database_path) as connection:
        row = connection.execute(
            "SELECT action_type, target_type, target_id FROM actions WHERE id = ?",
            (payload["undo_action_id"],),
        ).fetchone()
    assert row == ("undo", "action", action_id)


def test_undo_disabled_returns_409(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, UNDO_ENABLED=False)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        client.post("/capture", json={"text": "no undo"})
        action_id = _latest_action(settings, "capture")
        response = client.post(f"/undo/{action_id}")
        latest = client.post("/undo/latest")

    assert response.status_code == 409
    assert latest.status_code == 409


def test_undo_latest_non_reversible_blocks_older_undo(
    tmp_path: Path, monkeypatch
) -> None:
    """If the newest non-undo action is a non-reversible type, /undo/latest must
    return 409 instead of silently skipping back to an older reversible action.
    """

    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        client.post("/capture", json={"text": "older reversible"})
        # Inject a synthetic non-reversible action as the newest row so the
        # selector cannot fall back to an earlier capture.
        with sqlite3.connect(settings.resolved_database_path) as connection:
            connection.execute(
                """
                INSERT INTO actions
                    (action_type, target_type, target_id,
                     before_json, after_json, created_at)
                VALUES ('phase_4_only', 'inbox_item', 1,
                        NULL, '{}', '2030-01-01T00:00:00+00:00')
                """
            )
            connection.commit()
        response = client.post("/undo/latest")

    assert response.status_code == 409
    assert "not reversible" in response.json()["detail"].lower()


def test_undo_update_task_rejected_when_row_diverged(
    tmp_path: Path, monkeypatch
) -> None:
    """Update A->B, then B->C; undoing the first update must 409 rather than
    restore A on top of C and clobber the newer state."""

    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        inbox = client.post("/capture", json={"text": "title A"}).json()
        task = client.post(
            f"/inbox/{inbox['inbox_item_id']}/promote-task"
        ).json()
        first = client.patch(
            f"/tasks/{task['id']}", json={"title": "title B"}
        ).json()
        client.patch(
            f"/tasks/{task['id']}", json={"title": "title C"}
        ).json()
        response = client.post(f"/undo/{first['action_id']}")
        current = client.get(f"/tasks/{task['id']}").json()

    assert response.status_code == 409
    assert current["title"] == "title C"


def test_undo_promote_task_rejected_when_created_task_modified(
    tmp_path: Path, monkeypatch
) -> None:
    """If the promoted task is patched after creation, undoing the promote must
    409 — the inverse would clobber the user's newer edit on the task."""

    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        inbox = client.post("/capture", json={"text": "schedule retro"}).json()
        task = client.post(
            f"/inbox/{inbox['inbox_item_id']}/promote-task"
        ).json()
        promote_action_id = _latest_action(settings, "promote_task")
        client.patch(
            f"/tasks/{task['id']}", json={"title": "renamed"}
        )
        response = client.post(f"/undo/{promote_action_id}")
        task_after = client.get(f"/tasks/{task['id']}").json()

    assert response.status_code == 409
    assert task_after["status"] == "open"
    assert task_after["title"] == "renamed"


def test_undo_of_undo_action_rejected(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        client.post("/capture", json={"text": "no double"})
        action_id = _latest_action(settings, "capture")
        first = client.post(f"/undo/{action_id}")
        undo_id = first.json()["undo_action_id"]
        second = client.post(f"/undo/{undo_id}")

    assert second.status_code == 409
