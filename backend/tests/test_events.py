"""Tests for the GET /events endpoint and classifications diagnostic table.

All tests are offline. The LLM stage is never reached because rules already
match the inputs at a high confidence; no provider is injected.
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


def test_get_events_lists_events_created_by_capture(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        first = client.post(
            "/capture", json={"text": "Dentist 2026-07-04T09:00"}
        )
        second = client.post(
            "/capture", json={"text": "Standup 2026-06-01T10:00"}
        )
        listing = client.get("/events")

    assert first.status_code == 201
    assert second.status_code == 201
    assert listing.status_code == 200

    payload = listing.json()
    assert [event["starts_at"] for event in payload] == [
        "2026-06-01T10:00",
        "2026-07-04T09:00",
    ]
    assert all(event["source_inbox_item_id"] is not None for event in payload)
    assert {event["title"] for event in payload} == {
        "Dentist 2026-07-04T09:00",
        "Standup 2026-06-01T10:00",
    }


def test_get_events_returns_empty_when_no_events(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        client.post("/capture", json={"text": "buy milk"})
        response = client.get("/events")

    assert response.status_code == 200
    assert response.json() == []


def test_capture_persists_classification_diagnostic_row(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/capture", json={"text": "buy milk"})

    assert response.status_code == 201
    inbox_item_id = response.json()["inbox_item_id"]

    with sqlite3.connect(settings.resolved_database_path) as connection:
        row = connection.execute(
            """
            SELECT inbox_item_id, intent, confidence, source, raw_response
            FROM classifications
            WHERE inbox_item_id = ?
            """,
            (inbox_item_id,),
        ).fetchone()

    assert row is not None
    assert row[0] == inbox_item_id
    assert row[1] == "task"
    assert 0.0 <= row[2] <= 1.0
    assert row[3] == "rules"
    raw = json.loads(row[4])
    assert raw["intent"] == "task"
    assert raw["title"] == "buy milk"


def test_capture_disabled_does_not_write_classification_row(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/capture", json={"text": "buy milk"})

    assert response.status_code == 201

    with sqlite3.connect(settings.resolved_database_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM classifications"
        ).fetchone()[0]

    assert count == 0


def test_classify_endpoint_does_not_write_classification_row(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/classify", json={"text": "buy milk"})

    assert response.status_code == 200

    with sqlite3.connect(settings.resolved_database_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM classifications"
        ).fetchone()[0]

    assert count == 0
