from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 15.0


class ClientError(Exception):
    """Surfaced error from a backend call. Carries a calm, single-line message."""


class RemoteHostRefused(ClientError):
    """Raised at construction if base_url points at a non-loopback host
    and ADHDMAN_ALLOW_REMOTE is not set to '1'."""


def _check_loopback(base_url: str, allow_remote: bool) -> None:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise RemoteHostRefused(
            f"base_url {base_url!r} has no host; refusing to start."
        )
    if host in LOOPBACK_HOSTS:
        return
    if allow_remote:
        return
    raise RemoteHostRefused(
        f"refusing non-loopback host {host!r}; "
        "set ADHDMAN_ALLOW_REMOTE=1 to override (SSH tunnel use only)."
    )


class TuiClient:
    """Thin httpx wrapper for the ADHDman backend.

    Constructed once at app start. Every mutating wrapper takes an int id;
    free-form text never flows into a mutating endpoint as a target.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
        allow_remote: bool | None = None,
    ) -> None:
        resolved_url = base_url or os.environ.get("ADHDMAN_BASE_URL", DEFAULT_BASE_URL)
        if allow_remote is None:
            allow_remote = os.environ.get("ADHDMAN_ALLOW_REMOTE") == "1"
        _check_loopback(resolved_url, allow_remote)
        self.base_url = resolved_url
        self._client = httpx.Client(
            base_url=resolved_url, timeout=timeout, transport=transport
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TuiClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise ClientError("backend timed out")
        except httpx.HTTPError as exc:
            raise ClientError(f"connection error: {exc.__class__.__name__}")
        if resp.status_code >= 400:
            msg = _extract_message(resp)
            raise ClientError(f"{resp.status_code}: {msg}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # Read endpoints -------------------------------------------------
    def get_today(self) -> Any:
        return self._request("GET", "/today")

    def get_agenda_now(self, *, now: str | None = None) -> Any:
        params = {"now": now or self._now_iso()}
        return self._request("GET", "/agenda/now", params=params)

    def get_coach_next(self, *, now: str | None = None) -> Any:
        params = {"now": now or self._now_iso()}
        return self._request("GET", "/coach/next", params=params)

    def list_inbox(self) -> Any:
        return self._request("GET", "/inbox")

    def list_tasks(self) -> Any:
        return self._request("GET", "/tasks")

    def list_events(self) -> Any:
        return self._request("GET", "/events")

    # Capture --------------------------------------------------------
    def capture(self, text: str) -> Any:
        return self._request("POST", "/capture", json={"text": text})

    # Mutations — id-only --------------------------------------------
    def complete_task(self, task_id: int) -> Any:
        return self._request("POST", f"/tasks/{int(task_id)}/done")

    def delete_task(self, task_id: int) -> Any:
        return self._request("DELETE", f"/tasks/{int(task_id)}")

    def delete_event(self, event_id: int) -> Any:
        return self._request("DELETE", f"/events/{int(event_id)}")

    def undo_latest(self) -> Any:
        return self._request("POST", "/undo/latest")

    def undo(self, action_id: int) -> Any:
        return self._request("POST", f"/undo/{int(action_id)}")

    # Search / resolve -----------------------------------------------
    def search(self, query: str) -> Any:
        return self._request("POST", "/search", json={"query": query})

    def resolve(self, text: str, tz: str | None = None) -> Any:
        payload: dict[str, Any] = {"text": text}
        if tz:
            payload["tz"] = tz
        return self._request("POST", "/resolve", json=payload)

    # Phase 6 — execution helpers -----------------------------------
    def focus_current(self) -> Any:
        return self._request("GET", "/focus/current")

    def focus_start(
        self,
        target_type: str,
        target_id: int,
        *,
        note: str | None = None,
        replace: bool = False,
    ) -> Any:
        payload: dict[str, Any] = {
            "target_type": target_type,
            "target_id": int(target_id),
            "replace": bool(replace),
        }
        if note is not None:
            payload["note"] = note
        return self._request("POST", "/focus/start", json=payload)

    def focus_stop(self) -> Any:
        return self._request("POST", "/focus/stop")

    def breakdown_suggest(self, task_id: int) -> Any:
        return self._request(
            "POST", f"/tasks/{int(task_id)}/breakdown/suggest", json={}
        )

    def breakdown_commit(
        self, task_id: int, steps: list[str], *, source: str = "manual"
    ) -> Any:
        payload = {"steps": list(steps), "source": source}
        return self._request(
            "POST", f"/tasks/{int(task_id)}/breakdown", json=payload
        )

    def stuck_options(
        self,
        target_type: str = "task",
        target_id: int | None = None,
    ) -> Any:
        params: dict[str, Any] = {"target_type": target_type}
        if target_id is not None:
            params["target_id"] = int(target_id)
        return self._request("GET", "/stuck/options", params=params)

    def stuck_apply(self, target_type: str, target_id: int, choice: str) -> Any:
        payload = {
            "target_type": target_type,
            "target_id": int(target_id),
            "choice": choice,
        }
        return self._request("POST", "/stuck", json=payload)

    def body_double_current(self) -> Any:
        return self._request("GET", "/body-double/current")

    def body_double_start(
        self,
        interval_seconds: int | None = None,
        *,
        note: str | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        replace: bool = False,
    ) -> Any:
        payload: dict[str, Any] = {"replace": bool(replace)}
        if interval_seconds is not None:
            payload["interval_seconds"] = int(interval_seconds)
        if note is not None:
            payload["note"] = note
        if target_type is not None:
            payload["target_type"] = target_type
        if target_id is not None:
            payload["target_id"] = int(target_id)
        return self._request("POST", "/body-double/start", json=payload)

    def body_double_check_in(self) -> Any:
        return self._request("POST", "/body-double/check-in")

    def body_double_stop(self) -> Any:
        return self._request("POST", "/body-double/stop")

    def mvs_suggest(self, target_type: str, target_id: int) -> Any:
        payload = {"target_type": target_type, "target_id": int(target_id)}
        return self._request("POST", "/mvs/suggest", json=payload)

    def mvs_commit(self, target_type: str, target_id: int, step: str) -> Any:
        payload = {
            "target_type": target_type,
            "target_id": int(target_id),
            "step": step,
        }
        return self._request("POST", "/mvs/commit", json=payload)

    def survival_state(self) -> Any:
        return self._request("GET", "/survival")

    def survival_enter(self, note: str | None = None) -> Any:
        payload: dict[str, Any] = {}
        if note is not None:
            payload["note"] = note
        return self._request("POST", "/survival/enter", json=payload)

    def survival_exit(self) -> Any:
        return self._request("POST", "/survival/exit", json={})


def _extract_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text.strip() or resp.reason_phrase
    if isinstance(data, dict):
        for key in ("detail", "message", "error"):
            val = data.get(key)
            if isinstance(val, str) and val:
                return val
    return resp.reason_phrase or "error"
