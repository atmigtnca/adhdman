"""Tests for the read-only POST /classify endpoint.

All tests are offline. The LLM stage is exercised through a fake provider
injected via ``app.dependency_overrides`` so no real network calls happen.
The endpoint must never write inbox, task, event, or action rows.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.llm.base import LLMError, LLMResult
from app.main import app, get_llm_provider


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        **overrides,
    )


@dataclass
class FakeProvider:
    responses: list[LLMResult | LLMError]
    available_flag: bool = True
    calls: list[tuple[str, str]] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.available_flag

    def complete(self, system_prompt: str, user_text: str) -> LLMResult | LLMError:
        self.calls.append((system_prompt, user_text))
        if not self.responses:
            raise AssertionError("FakeProvider received an unexpected extra call")
        return self.responses.pop(0)


def _row_counts(database_path: Path) -> dict[str, int]:
    with sqlite3.connect(database_path) as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("inbox_items", "tasks", "actions")
        }


def test_classify_returns_rules_result_without_persistence(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, OPENROUTER_API_KEY=None)
    monkeypatch.setattr(main_module, "settings", settings)
    app.dependency_overrides[get_llm_provider] = lambda: None

    try:
        with TestClient(app) as client:
            response = client.post("/classify", json={"text": "buy milk"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "task"
    assert payload["title"] == "buy milk"
    assert payload["source"] == "rules"
    assert payload["starts_at"] is None
    assert payload["ends_at"] is None
    assert 0.0 <= payload["confidence"] <= 1.0
    assert "reason" in payload

    assert _row_counts(settings.resolved_database_path) == {
        "inbox_items": 0,
        "tasks": 0,
        "actions": 0,
    }


def test_classify_returns_event_for_iso_timestamp(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, OPENROUTER_API_KEY=None)
    monkeypatch.setattr(main_module, "settings", settings)
    app.dependency_overrides[get_llm_provider] = lambda: None

    try:
        with TestClient(app) as client:
            response = client.post(
                "/classify", json={"text": "Dentist 2026-07-04T09:00"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "event"
    assert payload["source"] == "rules"
    assert payload["starts_at"] == "2026-07-04T09:00"

    assert _row_counts(settings.resolved_database_path) == {
        "inbox_items": 0,
        "tasks": 0,
        "actions": 0,
    }


def test_classify_uses_injected_fake_provider_when_rules_inconclusive(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, OPENROUTER_API_KEY="test-key")
    monkeypatch.setattr(main_module, "settings", settings)

    fake = FakeProvider(
        responses=[
            LLMResult(
                text='{"intent":"task","confidence":0.7,"title":"groceries","reason":"clear"}'
            )
        ]
    )
    app.dependency_overrides[get_llm_provider] = lambda: fake

    try:
        with TestClient(app) as client:
            response = client.post("/classify", json={"text": "groceries"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "task"
    assert payload["title"] == "groceries"
    assert payload["source"] == "llm"
    assert len(fake.calls) == 1

    assert _row_counts(settings.resolved_database_path) == {
        "inbox_items": 0,
        "tasks": 0,
        "actions": 0,
    }


def test_classify_falls_back_when_no_provider_and_rules_inconclusive(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, OPENROUTER_API_KEY=None)
    monkeypatch.setattr(main_module, "settings", settings)
    app.dependency_overrides[get_llm_provider] = lambda: None

    try:
        with TestClient(app) as client:
            response = client.post("/classify", json={"text": "groceries"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "inbox"
    assert payload["source"] == "fallback"

    assert _row_counts(settings.resolved_database_path) == {
        "inbox_items": 0,
        "tasks": 0,
        "actions": 0,
    }


def test_classify_disabled_short_circuits_to_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(
        tmp_path, OPENROUTER_API_KEY="test-key", CLASSIFY_ENABLED=False
    )
    monkeypatch.setattr(main_module, "settings", settings)

    fake = FakeProvider(responses=[])
    app.dependency_overrides[get_llm_provider] = lambda: fake

    try:
        with TestClient(app) as client:
            response = client.post("/classify", json={"text": "buy milk"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "inbox"
    assert payload["source"] == "fallback"
    assert fake.calls == []


def test_classify_rejects_whitespace_only_text(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, OPENROUTER_API_KEY=None)
    monkeypatch.setattr(main_module, "settings", settings)
    app.dependency_overrides[get_llm_provider] = lambda: None

    try:
        with TestClient(app) as client:
            response = client.post("/classify", json={"text": "   \n\t  "})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422

    assert _row_counts(settings.resolved_database_path) == {
        "inbox_items": 0,
        "tasks": 0,
        "actions": 0,
    }


def test_default_provider_dependency_uses_openrouter_when_key_set(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, OPENROUTER_API_KEY="prod-key")
    monkeypatch.setattr(main_module, "settings", settings)

    provider = main_module.get_llm_provider()
    assert provider is not None
    assert provider.available is True


def test_default_provider_dependency_returns_none_without_key(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, OPENROUTER_API_KEY=None)
    monkeypatch.setattr(main_module, "settings", settings)

    assert main_module.get_llm_provider() is None
