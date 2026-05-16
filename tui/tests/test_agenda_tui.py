from __future__ import annotations

import httpx

from tui.client import TuiClient
from tui.rendering import render_agenda, render_coach


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def test_client_get_agenda_now_hits_read_only_path_with_now_param():
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, str(request.url.query, "utf-8")))
        return httpx.Response(200, json={"now": None, "next": [], "later": [], "counts": {}})

    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock_transport(handler))
    try:
        c.get_agenda_now(now="2026-05-31T12:00:00+09:00")
    finally:
        c.close()

    assert seen == [("GET", "/agenda/now", "now=2026-05-31T12%3A00%3A00%2B09%3A00")]


def test_client_get_coach_next_hits_read_only_path_with_now_param():
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, str(request.url.query, "utf-8")))
        return httpx.Response(200, json={"mode": "agenda", "message": "go", "tiny_step": "open", "suggested_commands": [], "needs_confirmation": False, "clarification_options": [], "source": "rules"})

    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock_transport(handler))
    try:
        c.get_coach_next(now="2026-05-31T12:00:00+09:00")
    finally:
        c.close()

    assert seen == [("GET", "/coach/next", "now=2026-05-31T12%3A00%3A00%2B09%3A00")]


def test_client_get_agenda_and_coach_default_now_params_when_omitted():
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, str(request.url.query, "utf-8")))
        if request.url.path == "/agenda/now":
            return httpx.Response(200, json={"now": None, "next": [], "later": [], "counts": {}})
        return httpx.Response(200, json={"mode": "agenda", "message": "go", "tiny_step": "open", "suggested_commands": [], "needs_confirmation": False, "clarification_options": [], "source": "rules"})

    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock_transport(handler))
    try:
        c.get_agenda_now()
        c.get_coach_next()
    finally:
        c.close()

    assert seen[0][0:2] == ("GET", "/agenda/now")
    assert seen[1][0:2] == ("GET", "/coach/next")
    assert seen[0][2].startswith("now=")
    assert seen[1][2].startswith("now=")


def test_render_agenda_prioritizes_current_action_and_reason():
    payload = {
        "now": {
            "kind": "task",
            "id": 7,
            "title": "오스카모임 전까지 과제 끝내기",
            "reason": "13:00 오스카 모임 전에 끝내야 해.",
            "urgency": "before_event",
        },
        "next": [
            {"kind": "event", "id": 2, "title": "오스카 모임", "starts_at": "2026-05-31T13:00:00+09:00"},
            {"kind": "task", "id": 3, "title": "DB 과제", "due_at": "2026-05-31T23:59:00+09:00"},
        ],
        "counts": {"tasks": 3, "events": 2, "inbox": 0},
    }

    rendered = render_agenda(payload)

    assert "지금 해야 할 것" in rendered
    assert "[task #7] 오스카모임 전까지 과제 끝내기" in rendered
    assert "13:00 오스카 모임 전에 끝내야 해." in rendered
    assert "다음" in rendered
    assert "오스카 모임" in rendered
    assert "tasks: 3" in rendered


def test_render_coach_outputs_message_tiny_step_and_commands():
    rendered = render_coach(
        {
            "mode": "agenda",
            "message": "지금은 DB 과제부터 보자.",
            "tiny_step": "DB 과제 파일 열기",
            "suggested_commands": ["/집중 1", "/쪼개기 1"],
            "source": "rules",
        }
    )

    assert "코치: 지금은 DB 과제부터 보자." in rendered
    assert "2분 시작: DB 과제 파일 열기" in rendered
    assert "/집중 1" in rendered


def test_render_agenda_empty_state_is_calm():
    rendered = render_agenda({"now": None, "next": [], "later": [], "counts": {}})

    assert "지금은 비어 있어" in rendered
    assert "TUI" in rendered
