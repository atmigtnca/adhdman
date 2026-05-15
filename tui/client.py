from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


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
        timeout: float = 5.0,
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

    # Read endpoints -------------------------------------------------
    def get_today(self) -> Any:
        return self._request("GET", "/today")

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
