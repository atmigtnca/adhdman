"""Tests for PATCH /events/{id} and DELETE /events/{id}.

Events are created through the existing /capture pipeline using inputs that
the rules-based classifier reliably tags as events.
"""

from __future__ import annotations

import json
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
        OPENROUTER_API_KEY=None,
    )


def create_event(client: TestClient, text: str) -> dict:
    response = client.post("/capture", json={"text": text})
    assert response.status_code == 201
    payload = response.json()
    created = payload["classification"]["created"]
    assert created is not None and created["type"] == "event"
    listing = client.get("/events").json()
    return next(event for event in listing if event["id"] == created["id"])


def test_patch_event_updates_title_and_logs_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        event = create_event(client, "Dentist 2026-07-04T09:00")
        response = client.patch(
            f"/events/{event['id']}", json={"title": "Dentist (rescheduled)"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["event"]["title"] == "Dentist (rescheduled)"
    # starts_at preserved.
    assert payload["event"]["starts_at"] == "2026-07-04T09:00"

    with sqlite3.connect(settings.resolved_database_path) as connection:
        before, after = connection.execute(
            "SELECT before_json, after_json FROM actions WHERE id = ?",
            (payload["action_id"],),
        ).fetchone()
    before_data = json.loads(before)
    after_data = json.loads(after)
    assert before_data["title"] == "Dentist 2026-07-04T09:00"
    assert after_data["title"] == "Dentist (rescheduled)"
    assert before_data["starts_at"] == after_data["starts_at"]


def test_patch_event_updates_times(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        event = create_event(client, "Standup 2026-06-01T10:00")
        response = client.patch(
            f"/events/{event['id']}",
            json={
                "starts_at": "2026-06-02T10:00",
                "ends_at": "2026-06-02T10:30",
            },
        )

    assert response.status_code == 200
    body = response.json()["event"]
    assert body["starts_at"] == "2026-06-02T10:00"
    assert body["ends_at"] == "2026-06-02T10:30"


def test_patch_event_empty_body_rejected(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        event = create_event(client, "Yoga 2026-05-20T07:00")
        response = client.patch(f"/events/{event['id']}", json={})

    assert response.status_code == 400


def test_patch_event_unknown_field_rejected(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        event = create_event(client, "Yoga 2026-05-20T07:00")
        response = client.patch(
            f"/events/{event['id']}", json={"status": "deleted"}
        )

    assert response.status_code == 422


def test_patch_missing_event_returns_404(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.patch(
            "/events/999", json={"title": "ghost event"}
        )

    assert response.status_code == 404


def test_delete_event_soft_deletes_and_logs_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        event = create_event(client, "Dentist 2026-07-04T09:00")
        response = client.delete(f"/events/{event['id']}")
        listing = client.get("/events").json()
        read = client.get(f"/events/{event['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["event"]["status"] == "deleted"
    assert event["id"] not in [e["id"] for e in listing]
    assert read.status_code == 200
    assert read.json()["status"] == "deleted"

    with sqlite3.connect(settings.resolved_database_path) as connection:
        action_row = connection.execute(
            "SELECT action_type, target_type, target_id, before_json, after_json "
            "FROM actions WHERE id = ?",
            (body["action_id"],),
        ).fetchone()
    assert action_row[0] == "delete_event"
    assert action_row[1] == "event"
    assert action_row[2] == event["id"]
    assert json.loads(action_row[3])["status"] == "open"
    assert json.loads(action_row[4])["status"] == "deleted"


def test_delete_already_deleted_event_returns_404(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        event = create_event(client, "Yoga 2026-05-20T07:00")
        first = client.delete(f"/events/{event['id']}")
        second = client.delete(f"/events/{event['id']}")

    assert first.status_code == 200
    assert second.status_code == 404
