from __future__ import annotations

import json

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
async def test_focus_n_resolves_index_to_task_target():
    calls: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.url.path == "/tasks":
            return httpx.Response(
                200,
                json=[{"id": 11, "title": "milk"}, {"id": 12, "title": "rent"}],
            )
        if request.url.path == "/focus/start":
            return httpx.Response(
                201,
                json={
                    "session": {
                        "id": 1,
                        "kind": "focus",
                        "target_type": "task",
                        "target_id": 12,
                        "status": "active",
                    },
                    "target": {"kind": "task", "id": 12, "title": "rent"},
                    "message": "Focus is set.",
                },
            )
        if request.url.path == "/today":
            return httpx.Response(200, json={})
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
        inp.value = "/focus 2"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
    c.close()

    starts = [c for c in calls if c[1] == "/focus/start"]
    assert len(starts) == 1
    body = json.loads(starts[0][2])
    assert body == {"target_type": "task", "target_id": 12, "replace": False}


@pytest.mark.asyncio
async def test_focus_without_listing_does_not_call_backend():
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    from textual.widgets import Input

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "/focus 1"
        await inp.action_submit()
        await pilot.pause()
    c.close()
    assert "/focus/start" not in calls


@pytest.mark.asyncio
async def test_breakdown_suggest_then_commit_flow():
    calls: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.url.path == "/tasks":
            return httpx.Response(200, json=[{"id": 21, "title": "ship doc"}])
        if request.url.path == "/tasks/21/breakdown/suggest":
            return httpx.Response(
                200,
                json={"steps": ["outline", "draft", "send"], "source": "rules"},
            )
        if request.url.path == "/tasks/21/breakdown":
            return httpx.Response(
                201,
                json={
                    "parent": {"id": 21},
                    "children": [{"id": 22}, {"id": 23}, {"id": 24}],
                    "action_id": 99,
                },
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
        inp.value = "/breakdown 1"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.state.pending_breakdown is not None
        assert app.state.pending_breakdown.task_id == 21
        assert app.state.pending_breakdown.steps == ["outline", "draft", "send"]
        inp.value = "/breakdown commit"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.state.pending_breakdown is None
    c.close()
    paths = [p for (_, p, _) in calls]
    assert "/tasks/21/breakdown/suggest" in paths
    assert "/tasks/21/breakdown" in paths


@pytest.mark.asyncio
async def test_breakdown_commit_without_pending_does_not_call_backend():
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    from textual.widgets import Input

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "/breakdown commit"
        await inp.action_submit()
        await pilot.pause()
    c.close()
    assert calls == []


@pytest.mark.asyncio
async def test_stuck_apply_uses_last_selection():
    calls: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.url.path == "/tasks":
            return httpx.Response(200, json=[{"id": 7, "title": "draft"}])
        if request.url.path == "/stuck":
            return httpx.Response(
                200,
                json={
                    "task": {"id": 7, "block_state": "parked"},
                    "choice": "park",
                    "action_id": 55,
                },
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
        inp.value = "1"  # pick #7
        await inp.action_submit()
        await pilot.pause()
        inp.value = "/stuck park"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
    c.close()
    stuck_calls = [c for c in calls if c[1] == "/stuck"]
    assert len(stuck_calls) == 1
    body = json.loads(stuck_calls[0][2])
    assert body == {"target_type": "task", "target_id": 7, "choice": "park"}


@pytest.mark.asyncio
async def test_stuck_without_selection_does_not_call_backend():
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    from textual.widgets import Input

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "/stuck shrink"
        await inp.action_submit()
        await pilot.pause()
    c.close()
    assert "/stuck" not in calls


@pytest.mark.asyncio
async def test_mvs_suggest_then_commit_flow():
    calls: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.url.path == "/tasks":
            return httpx.Response(200, json=[{"id": 8, "title": "call dentist"}])
        if request.url.path == "/mvs/suggest":
            return httpx.Response(
                200, json={"step": "open contacts", "source": "rules"}
            )
        if request.url.path == "/mvs/commit":
            return httpx.Response(
                201,
                json={
                    "task": {"id": 19},
                    "focus": {"id": 4},
                    "task_action_id": 77,
                    "focus_action_id": 78,
                },
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
        inp.value = "/mvs 1"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.state.pending_mvs is not None
        assert app.state.pending_mvs.step == "open contacts"
        inp.value = "/mvs commit"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.state.pending_mvs is None
    c.close()
    commit_calls = [c for c in calls if c[1] == "/mvs/commit"]
    body = json.loads(commit_calls[0][2])
    assert body == {"target_type": "task", "target_id": 8, "step": "open contacts"}


@pytest.mark.asyncio
async def test_body_double_start_check_in_stop():
    calls: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        return httpx.Response(
            200,
            json={
                "session": {"id": 3, "status": "active", "interval_seconds": 300},
                "message": "ok",
            },
        )

    from textual.widgets import Input

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        for line in ("/body-double 300", "/body-double check-in", "/body-double stop"):
            inp.value = line
            await inp.action_submit()
            await app.workers.wait_for_complete()
            await pilot.pause()
    c.close()
    paths = [p for (_, p, _) in calls]
    assert paths == [
        "/body-double/start",
        "/body-double/check-in",
        "/body-double/stop",
    ]
    body = json.loads(calls[0][2])
    assert body == {"replace": False, "interval_seconds": 300}


@pytest.mark.asyncio
async def test_survival_on_off_updates_state():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/survival/enter":
            return httpx.Response(
                201,
                json={"active": True, "session": {"id": 9}, "message": "Survival mode on."},
            )
        if request.url.path == "/survival/exit":
            return httpx.Response(
                200,
                json={"active": False, "session": None, "message": "Survival off."},
            )
        return httpx.Response(200, json={})

    from textual.widgets import Input

    c = _mock_client(handler)
    app = TuiApp(client=c)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", Input)
        inp.value = "/survival on"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.state.survival_active is True
        inp.value = "/survival off"
        await inp.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.state.survival_active is False
    c.close()
