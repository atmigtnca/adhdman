from __future__ import annotations

import json

import httpx
import pytest

from tui.client import ClientError, RemoteHostRefused, TuiClient


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def test_loopback_allowed_by_default():
    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock_transport(lambda r: httpx.Response(200, json={})))
    c.close()
    c = TuiClient(base_url="http://localhost:8000", transport=_mock_transport(lambda r: httpx.Response(200, json={})))
    c.close()


def test_remote_host_rejected():
    with pytest.raises(RemoteHostRefused):
        TuiClient(base_url="http://example.com:8000", allow_remote=False)


def test_remote_host_allowed_with_flag():
    c = TuiClient(
        base_url="http://example.com:8000",
        allow_remote=True,
        transport=_mock_transport(lambda r: httpx.Response(200, json={})),
    )
    c.close()


def test_remote_host_allowed_via_env(monkeypatch):
    monkeypatch.setenv("ADHDMAN_ALLOW_REMOTE", "1")
    c = TuiClient(
        base_url="http://example.com:8000",
        transport=_mock_transport(lambda r: httpx.Response(200, json={})),
    )
    c.close()


def test_relative_base_url_rejected():
    with pytest.raises(RemoteHostRefused):
        TuiClient(base_url="/api", allow_remote=False)


def test_wrappers_hit_expected_paths():
    seen: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.content))
        return httpx.Response(200, json={"ok": True})

    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock_transport(handler))
    try:
        c.get_today()
        c.list_inbox()
        c.list_tasks()
        c.list_events()
        c.capture("buy milk")
        c.complete_task(7)
        c.undo_latest()
        c.undo(42)
        c.search("milk")
        c.resolve("tomorrow 3pm", tz="UTC")
    finally:
        c.close()

    methods_paths = [(m, p) for (m, p, _) in seen]
    assert methods_paths == [
        ("GET", "/today"),
        ("GET", "/inbox"),
        ("GET", "/tasks"),
        ("GET", "/events"),
        ("POST", "/capture"),
        ("POST", "/tasks/7/done"),
        ("POST", "/undo/latest"),
        ("POST", "/undo/42"),
        ("POST", "/search"),
        ("POST", "/resolve"),
    ]
    capture_body = json.loads(seen[4][2])
    assert capture_body == {"text": "buy milk"}
    resolve_body = json.loads(seen[9][2])
    assert resolve_body == {"text": "tomorrow 3pm", "tz": "UTC"}


def test_http_error_surfaced_as_client_error():
    def handler(request):
        return httpx.Response(409, json={"detail": "already undone"})

    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock_transport(handler))
    try:
        with pytest.raises(ClientError) as ei:
            c.undo_latest()
        assert "409" in str(ei.value)
        assert "already undone" in str(ei.value)
    finally:
        c.close()


def test_timeout_surfaced_as_client_error():
    def handler(request):
        raise httpx.TimeoutException("slow", request=request)

    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock_transport(handler))
    try:
        with pytest.raises(ClientError) as ei:
            c.get_today()
        assert "timed out" in str(ei.value)
    finally:
        c.close()


def test_complete_task_coerces_int():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock_transport(handler))
    try:
        c.complete_task("12")  # type: ignore[arg-type]
    finally:
        c.close()
    assert seen == ["/tasks/12/done"]
