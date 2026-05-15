"""Tests for the read-only candidate search layer (`POST /search`).

Search must never mutate state and must surface ambiguity rather than auto-pick
when two rows score close together.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.main import app
from app.repositories import capture_to_inbox, promote_inbox_item_to_task
from app.search import search_candidates


def make_settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        OPENROUTER_API_KEY=None,
        **overrides,
    )


def _init_db(settings: Settings) -> None:
    from app.db import init_db

    init_db(settings)


def test_substring_match_returns_best_first(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _init_db(settings)
    inbox_a = capture_to_inbox("call dentist about checkup", settings)
    promote_inbox_item_to_task(inbox_a.id, settings)
    capture_to_inbox("buy milk", settings)

    result = search_candidates("dentist", settings, now=datetime.now(UTC))

    assert result["candidates"], "expected at least one candidate"
    assert "dentist" in result["candidates"][0]["title"].lower()
    assert all(c["score"] > 0 for c in result["candidates"])


def test_inbox_items_are_searchable(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _init_db(settings)
    capture_to_inbox("unique-marker-xyz pondering something", settings)

    result = search_candidates("unique-marker-xyz", settings)
    types = {c["type"] for c in result["candidates"]}
    assert "inbox" in types


def test_empty_query_rejected(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _init_db(settings)
    with pytest.raises(ValueError):
        search_candidates("   ", settings)


def test_no_match_returns_empty_candidates(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _init_db(settings)
    capture_to_inbox("buy milk", settings)

    result = search_candidates("zzz-nothing-matches", settings)
    assert result["candidates"] == []
    assert result["ambiguous"] is False


def test_max_candidates_bound_respected(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, SEARCH_MAX_CANDIDATES=2)
    _init_db(settings)
    for i in range(5):
        capture_to_inbox(f"dentist appointment number {i}", settings)

    result = search_candidates("dentist", settings)
    assert len(result["candidates"]) == 2
    assert result["max_candidates"] == 2


def test_ambiguous_flag_when_top_scores_close(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, SEARCH_AMBIGUITY_THRESHOLD=0.5)
    _init_db(settings)
    capture_to_inbox("dentist visit", settings)
    capture_to_inbox("dentist call", settings)

    result = search_candidates("dentist", settings)
    assert len(result["candidates"]) >= 2
    assert result["ambiguous"] is True


def test_search_endpoint_returns_candidates(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        client.post("/capture", json={"text": "call dentist"})
        response = client.post("/search", json={"query": "dentist"})

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "dentist"
    assert body["candidates"]
    top = body["candidates"][0]
    assert "dentist" in top["title"].lower()
    assert 0.0 <= top["score"] <= 1.0
    assert "ambiguous" in body
    assert "max_candidates" in body
    assert "ambiguity_threshold" in body


def test_search_endpoint_does_not_mutate(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        client.post("/capture", json={"text": "call dentist"})
        before_inbox = client.get("/inbox").json()
        before_tasks = client.get("/tasks").json()
        before_events = client.get("/events").json()

        client.post("/search", json={"query": "dentist"})

        after_inbox = client.get("/inbox").json()
        after_tasks = client.get("/tasks").json()
        after_events = client.get("/events").json()

    assert before_inbox == after_inbox
    assert before_tasks == after_tasks
    assert before_events == after_events


def test_search_endpoint_rejects_empty_query(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/search", json={"query": "   "})

    assert response.status_code == 422


def test_search_endpoint_rejects_unknown_fields(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/search", json={"query": "dentist", "user_id": 1}
        )

    assert response.status_code == 422


def test_search_excludes_soft_deleted_rows(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        capture = client.post("/capture", json={"text": "call dentist now"}).json()
        task_id = capture["classification"]["created"]["id"]
        client.delete(f"/tasks/{task_id}")

        response = client.post("/search", json={"query": "dentist"})

    assert response.status_code == 200
    ids_by_type = {(c["type"], c["id"]) for c in response.json()["candidates"]}
    assert ("task", task_id) not in ids_by_type
