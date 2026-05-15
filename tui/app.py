from __future__ import annotations

import os
import re
from typing import Any, Callable

from textual.app import App, ComposeResult
from textual.widgets import Input, RichLog, Static

from tui.client import ClientError, TuiClient
from tui.commands import (
    Capture,
    Command,
    Done,
    Events,
    HELP_TEXT,
    Help,
    Inbox,
    Noop,
    Pick,
    Quit,
    Resolve,
    Search,
    Tasks,
    Today,
    Undo,
    Unknown,
    parse_command,
)
from tui.rendering import (
    EMPTY_TODAY,
    listing_from_payload,
    render_listing,
    render_log_line,
    render_today,
)
from tui.state import AppState


BANNER = "ADHDman TUI — local-only. /help for commands. /quit to exit."

_BARE_NUMBER_RE = re.compile(r"^\s*(\d+)\s*$")
_PICK_RE = re.compile(r"^\s*pick\s+(\d+)\s*$", re.IGNORECASE)


class TuiApp(App):
    """Textual app: Now / Log / Input three-pane layout."""

    CSS = """
    Screen { layout: vertical; }
    #now { height: 5; border: solid $accent; padding: 0 1; }
    #log { height: 1fr; border: solid $primary; padding: 0 1; }
    #input { dock: bottom; height: 3; }
    """

    BINDINGS = [("ctrl+c", "quit", "Quit")]

    def __init__(
        self,
        client: TuiClient | None = None,
        *,
        timezone: str | None = None,
    ) -> None:
        super().__init__()
        self.client = client or TuiClient()
        self.state = AppState()
        env_tz = os.environ.get("ADHDMAN_TIMEZONE", "").strip()
        self.timezone = timezone if timezone is not None else (env_tz or None)

    def compose(self) -> ComposeResult:
        yield Static(EMPTY_TODAY, id="now")
        yield RichLog(id="log", highlight=False, markup=False, wrap=True)
        yield Input(placeholder="> capture or /command", id="input")

    def on_mount(self) -> None:
        self.log_line("system", BANNER)
        self.log_line("system", "type /help for the command list.")

    # ---------- helpers ----------
    def log_line(self, verb: str, summary: str, action_id: int | None = None) -> None:
        log = self.query_one("#log", RichLog)
        log.write(render_log_line(verb, summary, action_id))

    def set_now(self, payload: Any) -> None:
        self.state.today = payload if isinstance(payload, dict) else None
        self.query_one("#now", Static).update(render_today(payload))

    # ---------- input ----------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value
        event.input.value = ""
        self.state.push_history(line)
        cmd = self._parse_with_listing_context(line)
        self.dispatch(cmd)

    def _parse_with_listing_context(self, line: str) -> Command:
        """Bare numbers and non-slash 'pick N' route to Pick when a listing exists."""
        if line and self.state.last_listing is not None:
            m = _BARE_NUMBER_RE.match(line) or _PICK_RE.match(line)
            if m:
                return Pick(index=int(m.group(1)))
        return parse_command(line)

    def dispatch(self, cmd: Command) -> None:
        if isinstance(cmd, Noop):
            return
        if isinstance(cmd, Help):
            for hline in HELP_TEXT.splitlines():
                self.log_line("help", hline)
            return
        if isinstance(cmd, Quit):
            self.exit()
            return
        if isinstance(cmd, Unknown):
            self.log_line("?", f"unknown command {cmd.raw!r} — try /help")
            return
        # Pick is a pure state mutation — no HTTP, no worker needed.
        if isinstance(cmd, Pick):
            self._handle_pick(cmd)
            return
        # Done with a missing target is a state check — no HTTP either.
        if isinstance(cmd, Done):
            target = self.state.resolve_task_target(cmd.index)
            if target is None:
                self.log_line("/done", "Run /tasks first, then /done N.")
                return
            self._dispatch_async(lambda: self._do_done(target.id))
            return
        # All remaining commands hit the network — run off the UI thread.
        self._dispatch_async(lambda: self._run_network(cmd))

    # ---------- worker plumbing ----------
    def _dispatch_async(self, fn: Callable[[], None]) -> None:
        """Run a blocking call on a worker thread without blocking the UI.

        The callable should perform any HTTP work and use ``self.call_from_thread``
        to push results back into the UI.
        """
        self.run_worker(fn, thread=True, exclusive=False)

    def _handle_pick(self, cmd: Pick) -> None:
        listing = self.state.last_listing
        if listing is None:
            self.log_line("/pick", "No listing yet — run /search or /tasks first.")
            return
        item = listing.resolve(cmd.index)
        if item is None:
            self.log_line("/pick", f"index {cmd.index} out of range")
            return
        self.state.set_selection(item)
        self.log_line("/pick", f"selected {item.kind} #{item.id} {item.title}")

    def _run_network(self, cmd: Command) -> None:
        """Worker-thread entry point. Bridges results back to the UI thread."""
        try:
            if isinstance(cmd, Capture):
                payload = self.client.capture(cmd.text)
                self.call_from_thread(
                    self.log_line, "capture", _summarize_capture(cmd.text, payload)
                )
                return
            if isinstance(cmd, Today):
                payload = self.client.get_today()
                self.call_from_thread(self.set_now, payload)
                self.call_from_thread(self.log_line, "/today", "Now refreshed")
                return
            if isinstance(cmd, Inbox):
                payload = self.client.list_inbox()
                self.call_from_thread(self._show_listing, "inbox", payload)
                return
            if isinstance(cmd, Tasks):
                payload = self.client.list_tasks()
                self.call_from_thread(self._show_listing, "tasks", payload)
                return
            if isinstance(cmd, Events):
                payload = self.client.list_events()
                self.call_from_thread(self._show_listing, "events", payload)
                return
            if isinstance(cmd, Undo):
                payload = (
                    self.client.undo_latest()
                    if cmd.action_id is None
                    else self.client.undo(cmd.action_id)
                )
                self.call_from_thread(self.log_line, "/undo", _summarize_undo(payload))
                self._refresh_now_in_thread()
                return
            if isinstance(cmd, Search):
                payload = self.client.search(cmd.query)
                self.call_from_thread(self._show_search, payload)
                return
            if isinstance(cmd, Resolve):
                payload = self.client.resolve(cmd.text, tz=self.timezone)
                self.call_from_thread(
                    self.log_line, "/resolve", _summarize_resolve(payload)
                )
                return
        except ClientError as exc:
            self.call_from_thread(self.log_line, "error", str(exc))

    def _do_done(self, task_id: int) -> None:
        try:
            self.client.complete_task(task_id)
        except ClientError as exc:
            self.call_from_thread(self.log_line, "error", str(exc))
            return
        # /tasks/{id}/done returns a TaskResponse, not an action id. Don't
        # mislabel the task id as an action id; just log the completion.
        self.call_from_thread(self.log_line, "/done", f"task #{task_id} done")
        self._refresh_now_in_thread()

    def _refresh_now_in_thread(self) -> None:
        try:
            payload = self.client.get_today()
        except ClientError as exc:
            self.call_from_thread(self.log_line, "error", str(exc))
            return
        self.call_from_thread(self.set_now, payload)

    def _show_listing(self, kind: str, payload: Any) -> None:
        listing = listing_from_payload(kind, payload)
        self.state.set_listing(listing)
        rendered = render_listing(listing)
        for ln in rendered.splitlines():
            self.log_line(f"/{kind}", ln)

    def _show_search(self, payload: Any) -> None:
        listing = listing_from_payload("search", payload)
        self.state.set_listing(listing)
        for ln in render_listing(listing).splitlines():
            self.log_line("/search", ln)


def _summarize_capture(text: str, payload: Any) -> str:
    if isinstance(payload, dict):
        ident = payload.get("id") or payload.get("inbox_id")
        kind = payload.get("kind") or payload.get("classification") or "inbox"
        if ident is not None:
            return f"{text!r} -> {kind} #{ident}"
    return f"{text!r} captured"


def _summarize_undo(payload: Any) -> str:
    if isinstance(payload, dict):
        msg = payload.get("message") or payload.get("detail")
        if isinstance(msg, str) and msg:
            return msg
        aid = payload.get("action_id") or payload.get("undone_action_id")
        if aid is not None:
            return f"undone action #{aid}"
    return "undo ok"


def _summarize_resolve(payload: Any) -> str:
    if isinstance(payload, dict):
        resolved = payload.get("resolved")
        if isinstance(resolved, dict):
            when = resolved.get("starts_at") or resolved.get("datetime")
            if when:
                return f"-> {when}"
        when = (
            payload.get("resolved_at")
            or payload.get("datetime")
            or payload.get("timestamp")
        )
        if when:
            return f"-> {when}"
        msg = payload.get("message") or payload.get("detail")
        if isinstance(msg, str) and msg:
            return msg
    return str(payload)


def main() -> None:
    app = TuiApp()
    try:
        app.run()
    finally:
        app.client.close()
