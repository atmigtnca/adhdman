from __future__ import annotations

import json

import httpx

from tui.client import TuiClient


def _mock(handler):
    return httpx.MockTransport(handler)


def _record_all(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.path,
                dict(request.url.params),
                request.content,
            )
        )
        return httpx.Response(200, json={"ok": True})

    return handler


def test_focus_client_wrappers():
    seen: list = []
    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock(_record_all(seen)))
    try:
        c.focus_current()
        c.focus_start("task", 7)
        c.focus_start("inbox_item", 3, note="n", replace=True)
        c.focus_stop()
    finally:
        c.close()
    paths = [(m, p) for (m, p, _, _) in seen]
    assert paths == [
        ("GET", "/focus/current"),
        ("POST", "/focus/start"),
        ("POST", "/focus/start"),
        ("POST", "/focus/stop"),
    ]
    body1 = json.loads(seen[1][3])
    assert body1 == {"target_type": "task", "target_id": 7, "replace": False}
    body2 = json.loads(seen[2][3])
    assert body2 == {
        "target_type": "inbox_item",
        "target_id": 3,
        "replace": True,
        "note": "n",
    }


def test_breakdown_client_wrappers():
    seen: list = []
    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock(_record_all(seen)))
    try:
        c.breakdown_suggest(12)
        c.breakdown_commit(12, ["a", "b", "c"], source="rules")
    finally:
        c.close()
    assert [(m, p) for (m, p, _, _) in seen] == [
        ("POST", "/tasks/12/breakdown/suggest"),
        ("POST", "/tasks/12/breakdown"),
    ]
    body = json.loads(seen[1][3])
    assert body == {"steps": ["a", "b", "c"], "source": "rules"}


def test_stuck_client_wrappers():
    seen: list = []
    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock(_record_all(seen)))
    try:
        c.stuck_options()
        c.stuck_options("task", 4)
        c.stuck_apply("task", 4, "shrink")
    finally:
        c.close()
    assert [(m, p) for (m, p, _, _) in seen] == [
        ("GET", "/stuck/options"),
        ("GET", "/stuck/options"),
        ("POST", "/stuck"),
    ]
    assert seen[0][2] == {"target_type": "task"}
    assert seen[1][2] == {"target_type": "task", "target_id": "4"}
    body = json.loads(seen[2][3])
    assert body == {"target_type": "task", "target_id": 4, "choice": "shrink"}


def test_body_double_client_wrappers():
    seen: list = []
    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock(_record_all(seen)))
    try:
        c.body_double_current()
        c.body_double_start()
        c.body_double_start(300, note="hi", target_type="task", target_id=5)
        c.body_double_check_in()
        c.body_double_stop()
    finally:
        c.close()
    assert [(m, p) for (m, p, _, _) in seen] == [
        ("GET", "/body-double/current"),
        ("POST", "/body-double/start"),
        ("POST", "/body-double/start"),
        ("POST", "/body-double/check-in"),
        ("POST", "/body-double/stop"),
    ]
    body1 = json.loads(seen[1][3])
    assert body1 == {"replace": False}
    body2 = json.loads(seen[2][3])
    assert body2 == {
        "replace": False,
        "interval_seconds": 300,
        "note": "hi",
        "target_type": "task",
        "target_id": 5,
    }


def test_mvs_client_wrappers():
    seen: list = []
    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock(_record_all(seen)))
    try:
        c.mvs_suggest("task", 11)
        c.mvs_commit("task", 11, "open file")
    finally:
        c.close()
    assert [(m, p) for (m, p, _, _) in seen] == [
        ("POST", "/mvs/suggest"),
        ("POST", "/mvs/commit"),
    ]
    assert json.loads(seen[0][3]) == {"target_type": "task", "target_id": 11}
    assert json.loads(seen[1][3]) == {
        "target_type": "task",
        "target_id": 11,
        "step": "open file",
    }


def test_survival_client_wrappers():
    seen: list = []
    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock(_record_all(seen)))
    try:
        c.survival_state()
        c.survival_enter()
        c.survival_enter(note="low energy")
        c.survival_exit()
    finally:
        c.close()
    assert [(m, p) for (m, p, _, _) in seen] == [
        ("GET", "/survival"),
        ("POST", "/survival/enter"),
        ("POST", "/survival/enter"),
        ("POST", "/survival/exit"),
    ]
    assert json.loads(seen[1][3]) == {}
    assert json.loads(seen[2][3]) == {"note": "low energy"}


def test_id_coercion():
    """Numeric ids passed as strings are coerced — defense in depth against
    free-form input slipping into a path or body."""
    seen: list = []
    c = TuiClient(base_url="http://127.0.0.1:8000", transport=_mock(_record_all(seen)))
    try:
        c.focus_start("task", "9")  # type: ignore[arg-type]
        c.breakdown_suggest("4")  # type: ignore[arg-type]
    finally:
        c.close()
    assert seen[0][1] == "/focus/start"
    body = json.loads(seen[0][3])
    assert body["target_id"] == 9
    assert seen[1][1] == "/tasks/4/breakdown/suggest"
