"""End-to-end tests for POST /resolve via FastAPI TestClient."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
import app.main as main_module


def make_settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
        **overrides,
    )


def test_resolve_with_explicit_now_and_tz(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/resolve",
            json={
                "text": "tomorrow at 3pm",
                "now": "2026-05-16T09:00:00-07:00",
                "tz": "America/Los_Angeles",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["resolved"]["starts_at"] == "2026-05-17T15:00:00-07:00"
    assert body["resolved"]["kind"] == "relative"
    assert body["resolved"]["source"] == "rules"
    assert body["alternates"] == []


def test_resolve_falls_back_to_local_timezone_setting(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, LOCAL_TIMEZONE="America/Los_Angeles")
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/resolve",
            json={
                "text": "today",
                "now": "2026-05-16T09:00:00-07:00",
            },
        )

    assert response.status_code == 200
    assert response.json()["resolved"]["starts_at"] == "2026-05-16T09:00:00-07:00"


def test_resolve_invalid_timezone_returns_400(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/resolve",
            json={"text": "today", "tz": "Not/A_Zone"},
        )

    assert response.status_code == 400
    assert "timezone" in response.json()["detail"].lower()


def test_resolve_invalid_now_returns_400(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, LOCAL_TIMEZONE="America/Los_Angeles")
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/resolve",
            json={"text": "today", "now": "not-a-timestamp"},
        )

    assert response.status_code == 400


def test_resolve_unparseable_text_returns_kind_none(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, LOCAL_TIMEZONE="America/Los_Angeles")
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/resolve",
            json={
                "text": "remind me about the dentist",
                "now": "2026-05-16T09:00:00-07:00",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["resolved"]["kind"] == "none"
    assert body["resolved"]["starts_at"] is None
    assert body["resolved"]["confidence"] == 0.0


def test_resolve_does_not_write_any_rows(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, LOCAL_TIMEZONE="America/Los_Angeles")
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        client.post(
            "/resolve",
            json={
                "text": "tomorrow at 3pm",
                "now": "2026-05-16T09:00:00-07:00",
            },
        )

        # Inbox, tasks, and events should still be empty.
        assert client.get("/inbox").json() == []
        assert client.get("/tasks").json() == []
        assert client.get("/events").json() == []


def test_resolve_malformed_timezone_returns_400(tmp_path: Path, monkeypatch) -> None:
    # ZoneInfo raises ValueError (not ZoneInfoNotFoundError) for keys with NUL
    # bytes or path-traversal segments; the endpoint must still return 400.
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/resolve",
            json={"text": "today", "tz": "../etc/passwd"},
        )

    assert response.status_code == 400
    assert "timezone" in response.json()["detail"].lower()


def test_resolve_invalid_time_returns_kind_none(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, LOCAL_TIMEZONE="America/Los_Angeles")
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/resolve",
            json={
                "text": "tomorrow at 25:99",
                "now": "2026-05-16T09:00:00-07:00",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["resolved"]["kind"] == "none"
    assert body["resolved"]["starts_at"] is None


def test_resolve_dst_gap_shifts_forward(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, LOCAL_TIMEZONE="America/Los_Angeles")
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post(
            "/resolve",
            json={
                "text": "2026-03-08 02:30",
                "now": "2026-03-07T12:00:00-08:00",
            },
        )

    assert response.status_code == 200
    assert response.json()["resolved"]["starts_at"] == "2026-03-08T03:30:00-07:00"


def test_resolve_rejects_empty_text(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main_module, "settings", settings)

    with TestClient(app) as client:
        response = client.post("/resolve", json={"text": "   "})

    assert response.status_code == 422
