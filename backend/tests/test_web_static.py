"""Tests for the read-only web memory dashboard static shell.

Covers:
- ``GET /web`` returns the HTML shell with the read-only badge.
- ``GET /static/web/web.css`` and ``GET /static/web/web.js`` serve the static
  assets that the shell links to.
- Public-safety scans: the static files must not contain mutation endpoint
  strings, fetch verbs other than GET, or auth/user concepts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.main import STATIC_DIR, app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with settings pinned to a temp SQLite path.

    Required because the lifespan calls ``init_db`` against the production
    database path by default, which is read-only inside the test sandbox.
    """

    settings = Settings(
        _env_file=None,
        DATABASE_PATH=tmp_path / "adhdman.sqlite",
        CLASSIFY_ENABLED=False,
    )
    monkeypatch.setattr(main_module, "settings", settings)
    with TestClient(app) as test_client:
        yield test_client

WEB_DIR = STATIC_DIR / "web"
INDEX_HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
WEB_CSS = (WEB_DIR / "web.css").read_text(encoding="utf-8")
WEB_JS = (WEB_DIR / "web.js").read_text(encoding="utf-8")
ALL_STATIC_SOURCES: dict[str, str] = {
    "index.html": INDEX_HTML,
    "web.css": WEB_CSS,
    "web.js": WEB_JS,
}


def test_web_route_returns_html_shell(client: TestClient) -> None:
    response = client.get("/web")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "ADHDman Web Memory" in body
    assert "지금 해야 할 것" in body
    assert "agenda-now-title" in body
    assert "agenda-now-reason" in body
    assert "coach-message" in body
    assert "/static/web/web.css" in body
    assert "/static/web/web.js" in body


def test_static_assets_are_served(client: TestClient) -> None:
    css = client.get("/static/web/web.css")
    js = client.get("/static/web/web.js")

    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert ".card" in css.text

    assert js.status_code == 200
    js_type = js.headers["content-type"]
    assert "javascript" in js_type
    assert "fetch" in js.text


def test_static_files_fetch_dashboard_agenda_and_coach_with_get() -> None:
    """JS must only issue read-only fetches to dashboard, agenda, and coach endpoints."""

    # No fetch options object that sets a non-GET method.
    forbidden_methods = re.compile(r"method\s*:\s*[\"'](POST|PATCH|PUT|DELETE)[\"']", re.I)
    assert not forbidden_methods.search(WEB_JS)

    # The only URLs the JS targets are read-only dashboard/agenda/coach constants.
    fetch_targets = re.findall(r"fetch\(\s*([A-Z_][A-Z0-9_]*|[\"'][^\"']+[\"'])", WEB_JS)
    assert fetch_targets, "expected at least one fetch() call"
    assert all(
        target in {
            "DASHBOARD_URL",
            "AGENDA_URL",
            "COACH_URL",
            "\"/dashboard\"",
            "'/dashboard'",
            "\"/agenda/now\"",
            "'/agenda/now'",
            "\"/coach/next\"",
            "'/coach/next'",
        }
        for target in fetch_targets
    )
    assert "/dashboard" in WEB_JS
    assert "/agenda/now" in WEB_JS
    assert "/coach/next" in WEB_JS

    assert re.search(r"DASHBOARD_URL\s*=\s*[\"']/dashboard[\"']", WEB_JS)
    assert re.search(r"AGENDA_URL\s*=\s*[\"']/agenda/now[\"']", WEB_JS)
    assert re.search(r"COACH_URL\s*=\s*[\"']/coach/next[\"']", WEB_JS)
    assert "const nowIso = now.toISOString()" in WEB_JS
    assert "encodeURIComponent(nowIso)" in WEB_JS


def test_static_files_have_no_mutation_endpoint_strings() -> None:
    """Static files must not advertise mutation endpoints or verbs."""

    forbidden_substrings = (
        "POST /capture",
        "/done",
        "/undo",
        "/promote",
    )
    forbidden_words_outside_links = (
        "DELETE",
        "PATCH",
    )
    for name, source in ALL_STATIC_SOURCES.items():
        for needle in forbidden_substrings:
            assert needle not in source, f"{name} contains forbidden string {needle!r}"
        for needle in forbidden_words_outside_links:
            assert needle not in source, f"{name} contains forbidden verb {needle!r}"


def test_static_files_have_no_auth_or_user_concepts() -> None:
    """The web shell must not introduce auth or user concepts.

    Phase 6 introduces local, non-auth "focus sessions" and "body-double
    sessions" surfaced read-only on the dashboard, so the bare word ``session``
    is no longer forbidden. The forbidden list targets auth-shaped tokens
    instead (``session_id``, ``sessionstorage``, ``set-cookie``, etc.).
    """

    forbidden = (
        "user_id",
        "login",
        "logout",
        "password",
        "cookie",
        "csrf",
        "token",
        "session_id",
        "sessionstorage",
        "set-cookie",
        "authorization",
        "auth_token",
    )
    for name, source in ALL_STATIC_SOURCES.items():
        lowered = source.lower()
        for needle in forbidden:
            assert needle not in lowered, f"{name} mentions forbidden concept {needle!r}"


def test_static_files_avoid_innerhtml_assignment() -> None:
    """User-provided text must be rendered via textContent, not innerHTML."""

    # Match assignment to innerHTML; allow `.textContent` and method calls only.
    pattern = re.compile(r"\.innerHTML\s*=")
    for name, source in ALL_STATIC_SOURCES.items():
        assert not pattern.search(source), f"{name} assigns to innerHTML"
    # textContent must actually be used to render data.
    assert ".textContent" in WEB_JS


def test_static_files_do_not_leak_local_paths_or_secrets() -> None:
    """Public-safety: no absolute home paths, no API keys."""

    forbidden_patterns = (
        re.compile(r"/home/[^\s\"']+"),
        re.compile(r"sk-[A-Za-z0-9]{8,}"),
        re.compile(r"OPENROUTER_API_KEY\s*=\s*[A-Za-z0-9]"),
    )
    for name, source in ALL_STATIC_SOURCES.items():
        for pattern in forbidden_patterns:
            assert not pattern.search(source), f"{name} matches forbidden pattern {pattern.pattern}"


def test_static_directory_only_contains_expected_files() -> None:
    """Guard against accidentally shipping unrelated files under /static."""

    found = sorted(p.name for p in Path(WEB_DIR).iterdir() if p.is_file())
    assert found == ["index.html", "web.css", "web.js"]
