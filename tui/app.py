from __future__ import annotations

import os
import re
from typing import Any, Callable

from textual.app import App, ComposeResult
from textual.widgets import Input, RichLog, Static

from tui.client import ClientError, TuiClient
from tui.commands import (
    BodyDoubleCheckIn,
    BodyDoubleCurrent,
    BodyDoubleStart,
    BodyDoubleStop,
    BreakdownCommit,
    BreakdownSuggest,
    Capture,
    Command,
    Done,
    Events,
    FocusCurrent,
    FocusStart,
    FocusStop,
    HELP_TEXT,
    Help,
    Inbox,
    MVSCommit,
    MVSSuggest,
    Noop,
    Pick,
    Quit,
    Resolve,
    Search,
    StuckApply,
    StuckOptions,
    SurvivalOff,
    SurvivalOn,
    SurvivalStatus,
    Tasks,
    Today,
    Undo,
    Unknown,
    parse_command,
)
from tui.rendering import (
    EMPTY_TODAY,
    listing_from_payload,
    render_agenda,
    render_coach,
    render_listing,
    render_log_line,
)
from tui.state import AppState, PendingBreakdown, PendingMVS


BANNER = "ADHDman TUI — local-only. /도움말 for commands. /종료 to exit."

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
        yield Input(placeholder="> 입력하거나 /명령어", id="input")

    def on_mount(self) -> None:
        self.log_line("system", BANNER)
        self.log_line("system", "명령어 목록은 /도움말 로 볼 수 있어.")

    # ---------- helpers ----------
    def log_line(self, verb: str, summary: str, action_id: int | None = None) -> None:
        log = self.query_one("#log", RichLog)
        log.write(render_log_line(verb, summary, action_id))

    def set_now(self, payload: Any) -> None:
        self.state.today = payload if isinstance(payload, dict) else None
        self.query_one("#now", Static).update(render_agenda(payload))

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
            self.log_line("?", f"unknown command {cmd.raw!r} — try /도움말")
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
        # Phase 6 helpers with local-state preconditions ----------------
        if isinstance(cmd, FocusStart):
            target = self.state.resolve_listing_target(
                cmd.index, allowed_kinds=("task", "event", "inbox")
            )
            if target is None:
                self.log_line(
                    "/focus",
                    "Run /tasks, /events, or /inbox first, then /focus N.",
                )
                return
            target_type = _focus_target_type(target.kind)
            tid = target.id
            self._dispatch_async(lambda: self._do_focus_start(target_type, tid))
            return
        if isinstance(cmd, BreakdownSuggest):
            target = self.state.resolve_listing_target(
                cmd.index, allowed_kinds=("task",)
            )
            if target is None:
                self.log_line(
                    "/breakdown",
                    "Run /tasks first, then /breakdown N (task only).",
                )
                return
            tid = target.id
            title = target.title
            self._dispatch_async(lambda: self._do_breakdown_suggest(tid, title))
            return
        if isinstance(cmd, BreakdownCommit):
            pending = self.state.pending_breakdown
            if pending is None:
                self.log_line(
                    "/breakdown",
                    "No suggestion to commit — run /breakdown N first.",
                )
                return
            self._dispatch_async(lambda: self._do_breakdown_commit(pending))
            return
        if isinstance(cmd, StuckApply):
            target = self.state.last_selection
            if target is None or target.kind != "task":
                self.log_line(
                    "/stuck",
                    "Pick a task first (/tasks then /pick N), then /stuck CHOICE.",
                )
                return
            tid = target.id
            choice = cmd.choice
            self._dispatch_async(lambda: self._do_stuck_apply(tid, choice))
            return
        if isinstance(cmd, MVSSuggest):
            target = self.state.resolve_listing_target(
                cmd.index, allowed_kinds=("task", "inbox")
            )
            if target is None:
                self.log_line(
                    "/mvs",
                    "Run /tasks or /inbox first, then /mvs N (task or inbox).",
                )
                return
            target_type = "inbox_item" if target.kind == "inbox" else "task"
            tid = target.id
            title = target.title
            self._dispatch_async(
                lambda: self._do_mvs_suggest(target_type, tid, title)
            )
            return
        if isinstance(cmd, MVSCommit):
            pending = self.state.pending_mvs
            if pending is None:
                self.log_line(
                    "/mvs", "No suggestion to commit — run /mvs N first."
                )
                return
            self._dispatch_async(lambda: self._do_mvs_commit(pending))
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
                agenda = self.client.get_agenda_now()
                coach = self.client.get_coach_next()
                self.call_from_thread(self.set_now, agenda)
                coach_text = render_coach(coach)
                if coach_text:
                    for line in coach_text.splitlines():
                        self.call_from_thread(self.log_line, "/coach", line)
                self.call_from_thread(self.log_line, "/today", "Agenda refreshed")
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
            if isinstance(cmd, FocusCurrent):
                payload = self.client.focus_current()
                self.call_from_thread(
                    self.log_line, "/focus", _summarize_focus(payload)
                )
                return
            if isinstance(cmd, FocusStop):
                payload = self.client.focus_stop()
                self.call_from_thread(
                    self.log_line, "/focus", _summarize_focus_stop(payload)
                )
                return
            if isinstance(cmd, StuckOptions):
                payload = self.client.stuck_options()
                self.call_from_thread(self._show_stuck_options, payload)
                return
            if isinstance(cmd, BodyDoubleStart):
                payload = self.client.body_double_start(cmd.interval_seconds)
                self.call_from_thread(
                    self.log_line,
                    "/body-double",
                    _summarize_body_double(payload),
                )
                return
            if isinstance(cmd, BodyDoubleCheckIn):
                payload = self.client.body_double_check_in()
                self.call_from_thread(
                    self.log_line,
                    "/body-double",
                    _summarize_body_double(payload, default="check-in"),
                )
                return
            if isinstance(cmd, BodyDoubleStop):
                payload = self.client.body_double_stop()
                self.call_from_thread(
                    self.log_line,
                    "/body-double",
                    _summarize_body_double(payload, default="stopped"),
                )
                return
            if isinstance(cmd, BodyDoubleCurrent):
                payload = self.client.body_double_current()
                self.call_from_thread(
                    self.log_line,
                    "/body-double",
                    _summarize_body_double(payload, default="no body-double session"),
                )
                return
            if isinstance(cmd, SurvivalOn):
                payload = self.client.survival_enter()
                self.call_from_thread(self._apply_survival, payload)
                return
            if isinstance(cmd, SurvivalOff):
                payload = self.client.survival_exit()
                self.call_from_thread(self._apply_survival, payload)
                return
            if isinstance(cmd, SurvivalStatus):
                payload = self.client.survival_state()
                self.call_from_thread(self._apply_survival, payload)
                return
        except ClientError as exc:
            self.call_from_thread(self.log_line, "error", str(exc))

    # ---------- Phase 6 helpers ----------
    def _do_focus_start(self, target_type: str, target_id: int) -> None:
        try:
            payload = self.client.focus_start(target_type, target_id)
        except ClientError as exc:
            self.call_from_thread(self.log_line, "error", str(exc))
            return
        self.call_from_thread(self.log_line, "/focus", _summarize_focus(payload))
        self._refresh_now_in_thread()

    def _do_breakdown_suggest(self, task_id: int, title: str) -> None:
        try:
            payload = self.client.breakdown_suggest(task_id)
        except ClientError as exc:
            self.call_from_thread(self.log_line, "error", str(exc))
            return
        steps, source = _extract_breakdown_steps(payload)
        if not steps:
            self.call_from_thread(
                self.log_line, "/breakdown", "No suggestion available."
            )
            return
        pending = PendingBreakdown(
            task_id=task_id, task_title=title, steps=steps, source=source
        )
        self.call_from_thread(self._record_pending_breakdown, pending)

    def _record_pending_breakdown(self, pending: PendingBreakdown) -> None:
        self.state.pending_breakdown = pending
        self.log_line(
            "/breakdown",
            f"suggestion for task #{pending.task_id} {pending.task_title!r} "
            f"(source={pending.source}). /breakdown commit to persist.",
        )
        for i, step in enumerate(pending.steps, start=1):
            self.log_line("/breakdown", f"  ({i}) {step}")

    def _do_breakdown_commit(self, pending: PendingBreakdown) -> None:
        try:
            payload = self.client.breakdown_commit(
                pending.task_id, pending.steps, source=pending.source
            )
        except ClientError as exc:
            self.call_from_thread(self.log_line, "error", str(exc))
            return
        self.call_from_thread(self._clear_pending_breakdown)
        action_id = payload.get("action_id") if isinstance(payload, dict) else None
        children = payload.get("children") if isinstance(payload, dict) else None
        count = len(children) if isinstance(children, list) else len(pending.steps)
        self.call_from_thread(
            self.log_line,
            "/breakdown",
            f"persisted {count} child task(s) under #{pending.task_id}",
            action_id,
        )

    def _clear_pending_breakdown(self) -> None:
        self.state.pending_breakdown = None

    def _do_stuck_apply(self, task_id: int, choice: str) -> None:
        try:
            payload = self.client.stuck_apply("task", task_id, choice)
        except ClientError as exc:
            self.call_from_thread(self.log_line, "error", str(exc))
            return
        action_id = payload.get("action_id") if isinstance(payload, dict) else None
        self.call_from_thread(
            self.log_line,
            "/stuck",
            f"applied {choice!r} to task #{task_id}",
            action_id,
        )
        self._refresh_now_in_thread()

    def _show_stuck_options(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            self.log_line("/stuck", "(no options)")
            return
        prompt = payload.get("prompt")
        if isinstance(prompt, str) and prompt:
            self.log_line("/stuck", prompt)
        for opt in payload.get("options") or []:
            if isinstance(opt, dict):
                choice = opt.get("choice")
                label = opt.get("label", "")
                if choice:
                    self.log_line("/stuck", f"  {choice}: {label}")

    def _do_mvs_suggest(
        self, target_type: str, target_id: int, title: str
    ) -> None:
        try:
            payload = self.client.mvs_suggest(target_type, target_id)
        except ClientError as exc:
            self.call_from_thread(self.log_line, "error", str(exc))
            return
        step = payload.get("step") if isinstance(payload, dict) else None
        source = (
            payload.get("source") if isinstance(payload, dict) else None
        ) or "rules"
        if not isinstance(step, str) or not step:
            self.call_from_thread(
                self.log_line, "/mvs", "No suggestion available."
            )
            return
        pending = PendingMVS(
            target_type=target_type,
            target_id=target_id,
            target_title=title,
            step=step,
            source=source,
        )
        self.call_from_thread(self._record_pending_mvs, pending)

    def _record_pending_mvs(self, pending: PendingMVS) -> None:
        self.state.pending_mvs = pending
        self.log_line(
            "/mvs",
            f"suggestion for {pending.target_type} #{pending.target_id} "
            f"{pending.target_title!r} (source={pending.source}). "
            "/mvs commit to persist + focus.",
        )
        self.log_line("/mvs", f"  -> {pending.step}")

    def _do_mvs_commit(self, pending: PendingMVS) -> None:
        try:
            payload = self.client.mvs_commit(
                pending.target_type, pending.target_id, pending.step
            )
        except ClientError as exc:
            self.call_from_thread(self.log_line, "error", str(exc))
            return
        self.call_from_thread(self._clear_pending_mvs)
        task = payload.get("task") if isinstance(payload, dict) else None
        new_id = task.get("id") if isinstance(task, dict) else None
        task_action_id = (
            payload.get("task_action_id") if isinstance(payload, dict) else None
        )
        self.call_from_thread(
            self.log_line,
            "/mvs",
            f"committed step as task #{new_id} and started focus",
            task_action_id,
        )
        self._refresh_now_in_thread()

    def _clear_pending_mvs(self) -> None:
        self.state.pending_mvs = None

    def _apply_survival(self, payload: Any) -> None:
        active = bool(
            isinstance(payload, dict) and payload.get("active")
        )
        self.state.survival_active = active
        msg = (
            payload.get("message")
            if isinstance(payload, dict) and isinstance(payload.get("message"), str)
            else None
        )
        tag = "[survival] " if active else ""
        line = msg or ("survival on" if active else "survival off")
        self.log_line("/survival", f"{tag}{line}")

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


def _focus_target_type(listing_kind: str) -> str:
    """Map a Listing item kind to the backend's focus target_type."""
    if listing_kind == "inbox":
        return "inbox_item"
    return listing_kind


def _extract_breakdown_steps(payload: Any) -> tuple[list[str], str]:
    if not isinstance(payload, dict):
        return [], "rules"
    raw = payload.get("steps") or []
    steps = [s for s in raw if isinstance(s, str) and s]
    source = payload.get("source") if isinstance(payload.get("source"), str) else "rules"
    return steps, source or "rules"


def _summarize_focus(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "no focus session"
    session = payload.get("session")
    if not isinstance(session, dict):
        msg = payload.get("message")
        return msg if isinstance(msg, str) and msg else "no focus session"
    target = payload.get("target") or {}
    ttype = session.get("target_type") or (
        target.get("kind") if isinstance(target, dict) else None
    ) or "?"
    tid = session.get("target_id") or (
        target.get("id") if isinstance(target, dict) else None
    )
    title = target.get("title") if isinstance(target, dict) else None
    head = f"focus on {ttype} #{tid}"
    if title:
        head += f" {title!r}"
    return head


def _summarize_focus_stop(payload: Any) -> str:
    if isinstance(payload, dict):
        msg = payload.get("message")
        if isinstance(msg, str) and msg:
            return msg
    return "focus stopped"


def _summarize_body_double(payload: Any, *, default: str = "body-double") -> str:
    if not isinstance(payload, dict):
        return default
    session = payload.get("session")
    msg = payload.get("message") if isinstance(payload.get("message"), str) else None
    if isinstance(session, dict):
        interval = session.get("interval_seconds")
        status = session.get("status")
        head = f"{status or 'active'} @ {interval}s"
        return f"{msg + ' — ' if msg else ''}{head}"
    return msg or default


def main() -> None:
    app = TuiApp()
    try:
        app.run()
    finally:
        app.client.close()
