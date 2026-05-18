from __future__ import annotations

import httpx
import pytest

from tui.app import TuiApp
from tui.client import TuiClient


def _mock_client(handler):
    return TuiClient(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_app_boots_and_renders_help():
    c = _mock_client(lambda r: httpx.Response(200, json={}))
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        await pilot.press(*"/help")
        await pilot.press("enter")
        await pilot.pause()
    c.close()


@pytest.mark.asyncio
async def test_capture_appends_log_line():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/capture":
            return httpx.Response(200, json={"id": 14, "kind": "inbox"})
        return httpx.Response(200, json={})

    from textual.widgets import Input

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "pay rent"
        await inp.action_submit()
        await pilot.pause()
    c.close()
    assert ("POST", "/capture") in calls


@pytest.mark.asyncio
async def test_unknown_command_does_not_call_backend():
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    from textual.widgets import Input

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "/wat"
        await inp.action_submit()
        await pilot.pause()
    c.close()
    assert calls == []


@pytest.mark.asyncio
async def test_capture_runs_off_ui_thread():
    """Slow backend must not block the UI input handler."""
    import threading
    import time

    ui_thread = threading.get_ident()
    seen_threads: list[int] = []
    release = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        seen_threads.append(threading.get_ident())
        # Block until the test releases us — simulates a slow backend.
        release.wait(timeout=2.0)
        return httpx.Response(200, json={"id": 1, "kind": "inbox"})

    from textual.widgets import Input

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "slow capture"
        # If the call ran on the UI thread this submit would block ~2s.
        t0 = time.monotonic()
        await inp.action_submit()
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, "submit blocked the UI thread"
        # Now let the worker complete.
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
    c.close()
    assert seen_threads, "backend was never called"
    assert ui_thread not in seen_threads, "HTTP ran on the UI thread"


@pytest.mark.asyncio
async def test_search_renders_candidates_payload():
    """Backend SearchResponse uses 'candidates', not 'items'."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={
                    "query": "dent",
                    "candidates": [
                        {"type": "task", "id": 7, "title": "call dentist", "score": 0.9},
                        {"type": "event", "id": 12, "title": "dentist visit", "score": 0.7},
                    ],
                    "ambiguous": False,
                    "max_candidates": 5,
                    "ambiguity_threshold": 0.15,
                },
            )
        return httpx.Response(200, json={})

    from textual.widgets import Input

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "/search dent"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        listing = app.state.last_listing
        assert listing is not None
        assert listing.kind == "search"
        ids = [it.id for it in listing.items]
        assert ids == [7, 12]
        assert listing.items[0].kind == "task"
    c.close()


@pytest.mark.asyncio
async def test_bare_number_after_listing_is_pick_not_capture():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/tasks":
            return httpx.Response(
                200, json=[{"id": 11, "title": "milk"}, {"id": 12, "title": "rent"}]
            )
        return httpx.Response(200, json={})

    from textual.widgets import Input

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "/tasks"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        # Bare number after a listing must not get captured.
        inp.value = "2"
        await inp.action_submit()
        await pilot.pause()
        assert app.state.last_selection is not None
        assert app.state.last_selection.id == 12
    c.close()
    assert "/capture" not in calls


@pytest.mark.asyncio
async def test_non_slash_pick_after_listing_is_pick_not_capture():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={
                    "query": "milk",
                    "candidates": [
                        {"type": "task", "id": 5, "title": "buy milk", "score": 0.9}
                    ],
                    "ambiguous": False,
                    "max_candidates": 5,
                    "ambiguity_threshold": 0.15,
                },
            )
        return httpx.Response(200, json={})

    from textual.widgets import Input

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "/search milk"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        inp.value = "pick 1"
        await inp.action_submit()
        await pilot.pause()
        assert app.state.last_selection is not None
        assert app.state.last_selection.id == 5
    c.close()
    assert "/capture" not in calls


@pytest.mark.asyncio
async def test_done_does_not_label_task_id_as_action_id():
    """`/tasks/{id}/done` returns a TaskResponse with `id` (the task id),
    not an action id. The Log line must not claim it as `action #N`."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/tasks":
            return httpx.Response(200, json=[{"id": 11, "title": "milk"}])
        if request.url.path == "/tasks/11/done":
            return httpx.Response(
                200, json={"id": 11, "title": "milk", "status": "done"}
            )
        return httpx.Response(200, json={})

    from textual.widgets import Input, Static

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "/tasks"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        inp.value = "/done 1"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        text = str(app.query_one("#status", Static).render())
    c.close()
    assert ("POST", "/tasks/11/done") in calls
    assert "완료했어: 할 일 #11" in text
    # Must not mislabel the task id as an action id.
    assert "action #11" not in text
    assert "action #" not in text


@pytest.mark.asyncio
async def test_resolve_passes_adhdman_timezone(monkeypatch):
    monkeypatch.setenv("ADHDMAN_TIMEZONE", "America/Los_Angeles")
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/resolve":
            bodies.append(request.content)
            return httpx.Response(
                200,
                json={
                    "resolved": {
                        "starts_at": "2026-05-17T15:00:00-07:00",
                        "ends_at": None,
                        "kind": "instant",
                        "confidence": 0.9,
                        "source": "test",
                    },
                    "alternates": [],
                },
            )
        return httpx.Response(200, json={})

    from textual.widgets import Input

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "/resolve tomorrow 3pm"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
    c.close()
    assert bodies, "/resolve was not called"
    import json as _json
    body = _json.loads(bodies[0])
    assert body == {"text": "tomorrow 3pm", "tz": "America/Los_Angeles"}


@pytest.mark.asyncio
async def test_today_renders_dashboard_even_when_coach_times_out():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/agenda/now":
            return httpx.Response(
                200,
                json={
                    "now": {"kind": "task", "id": 1, "title": "DB 과제", "reason": "마감이 가까워."},
                    "next": [],
                    "later": [],
                    "counts": {"tasks": 1, "events": 0, "inbox": 0},
                },
            )
        if request.url.path == "/coach/next":
            raise httpx.TimeoutException("slow", request=request)
        return httpx.Response(200, json={})

    from textual.widgets import Input, Static

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "/오늘"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        now_text = str(app.query_one("#now", Static).render())
        assert "지금 해야 할 것" in now_text
        assert "DB 과제" in now_text
        assert "코치 제안" in now_text
        assert "다음" in now_text
        assert "요약" in now_text
    c.close()
    assert calls[:2] == ["/agenda/now", "/coach/next"]


@pytest.mark.asyncio
async def test_capture_refreshes_web_memory_dashboard():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/capture":
            return httpx.Response(200, json={"id": 14, "kind": "event"})
        if request.url.path == "/agenda/now":
            return httpx.Response(
                200,
                json={
                    "now": {
                        "kind": "event",
                        "id": 14,
                        "title": "오늘 15시 회의",
                        "reason": "곧 시작되는 일정이라서 보여줘요.",
                    },
                    "next": [],
                    "later": [],
                    "counts": {"tasks": 0, "events": 1, "inbox": 0},
                },
            )
        if request.url.path == "/coach/next":
            return httpx.Response(
                200,
                json={
                    "mode": "agenda",
                    "message": "지금은 회의 준비만 보면 돼.",
                    "tiny_step": "회의 링크 확인하기",
                    "suggested_commands": ["/집중"],
                    "needs_confirmation": False,
                    "clarification_options": [],
                    "source": "rules",
                },
            )
        return httpx.Response(200, json={})

    from textual.widgets import Input, Static

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "오늘 15시 회의"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        now_text = str(app.query_one("#now", Static).render())
        assert "오늘 15시 회의" in now_text
        assert "코치 제안" in now_text
    c.close()
    assert calls[:3] == ["/capture", "/agenda/now", "/coach/next"]


def test_slash_input_change_shows_compact_command_menu_as_ui_not_log():
    app = TuiApp(client=_mock_client(lambda r: httpx.Response(200, json={})))
    seen: list[tuple[str, str]] = []
    app.log_line = lambda verb, summary, action_id=None: seen.append((verb, summary))  # type: ignore[method-assign]

    class Event:
        value = "/"

    async def run():
        from textual.widgets import Static

        async with app.run_test() as pilot:
            seen.clear()
            app.on_input_changed(Event())
            await pilot.pause()
            menu = app.query_one("#command-menu", Static)
            text = str(menu.render())
            assert menu.display is True
            assert "명령어를 고를 수 있어." in text
            assert "/오늘" in text
            assert "/집중 N" in text
            assert "/도움말" in text

    import asyncio

    asyncio.run(run())
    app.client.close()
    assert seen == []


@pytest.mark.asyncio
async def test_today_timeless_event_asks_for_time_before_capture():
    calls: list[tuple[str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.content))
        return httpx.Response(200, json={})

    from textual.widgets import Input, Static

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "오늘 병원"
        await inp.action_submit()
        await pilot.pause()

        now_text = str(app.query_one("#now", Static).render())
        assert "오늘 병원은 언제 갈 계획이야?" in now_text
        assert "정확한 시간을 알려줘" in now_text
        assert calls == []

        inp.value = "오후 3시"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()

    c.close()
    assert calls
    assert calls[0][0] == "/capture"
    assert "오늘 오후 3시 병원" in calls[0][1].decode()


@pytest.mark.asyncio
async def test_delete_event_from_listing_refreshes_dashboard_without_log_panel():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/events":
            return httpx.Response(200, json=[{"id": 21, "title": "병원", "starts_at": "2026-05-18T15:00:00+09:00"}])
        if request.url.path == "/events/21":
            return httpx.Response(200, json={"event": {"id": 21, "title": "병원", "status": "deleted"}, "action_id": 9})
        if request.url.path == "/agenda/now":
            return httpx.Response(200, json={"now": None, "next": [], "later": [], "counts": {"tasks": 0, "events": 0, "inbox": 0}})
        if request.url.path == "/coach/next":
            return httpx.Response(200, json={"message": "정리됐어.", "suggested_commands": []})
        return httpx.Response(200, json={})

    from textual.css.query import NoMatches
    from textual.widgets import Input, Static

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "/일정"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        inp.value = "/삭제 1"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        status = str(app.query_one("#status", Static).render())
        assert "삭제했어" in status
        assert "병원" in status
        try:
            app.query_one("#log")
        except NoMatches:
            pass
        else:
            raise AssertionError("log panel should not be part of the user-facing TUI")

    c.close()
    assert ("DELETE", "/events/21") in calls
    assert ("GET", "/agenda/now") in calls
