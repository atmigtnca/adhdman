from __future__ import annotations

from datetime import datetime
from typing import Any

from tui.state import Listing, ListingItem


EMPTY_TODAY = "nothing scheduled — capture something or run /inbox"


def render_today(payload: Any) -> str:
    """Render the Now pane content from /today payload. Single-screen only."""
    if not payload:
        return EMPTY_TODAY
    if isinstance(payload, dict):
        one = payload.get("one_thing") or payload.get("item")
        if not one:
            msg = payload.get("message")
            return msg if isinstance(msg, str) and msg else EMPTY_TODAY
        kind = one.get("kind") or one.get("type") or "item"
        ident = one.get("id")
        title = one.get("title") or one.get("text") or ""
        head = f"[{kind} #{ident}] {title}".strip()
        counts = payload.get("counts") or {}
        if isinstance(counts, dict) and counts:
            tail = "  ".join(f"{k}: {v}" for k, v in counts.items())
            return f"{head}\n{tail}"
        return head
    return str(payload)


def render_agenda(payload: Any) -> str:
    """Render the current-action agenda pane from /agenda/now payload."""
    if not isinstance(payload, dict):
        return EMPTY_TODAY
    now = payload.get("now")
    if not isinstance(now, dict) or not now:
        return "지금은 비어 있어\n떠오르는 일을 TUI에서 하나만 적어두면 돼."
    kind = now.get("kind") or "item"
    ident = now.get("id")
    title = now.get("title") or ""
    lines = ["지금 해야 할 것", f"[{kind} #{ident}] {title}".strip()]
    reason = now.get("reason")
    if isinstance(reason, str) and reason:
        lines.append(reason)
    due_at = now.get("due_at")
    starts_at = now.get("starts_at")
    if due_at:
        lines.append(f"마감 {due_at}")
    elif starts_at:
        lines.append(f"시작 {starts_at}")
    next_items = payload.get("next") or []
    if isinstance(next_items, list) and next_items:
        preview: list[str] = []
        for item in next_items[:2]:
            if isinstance(item, dict):
                preview.append(str(item.get("title") or ""))
        if preview:
            lines.append("다음: " + " / ".join(preview))
    counts = payload.get("counts") or {}
    if isinstance(counts, dict) and counts:
        lines.append("  ".join(f"{k}: {v}" for k, v in counts.items()))
    return "\n".join(lines)


def render_coach(payload: Any) -> str:
    """Render a short execution-coach message."""
    if not isinstance(payload, dict):
        return ""
    message = payload.get("message")
    tiny_step = payload.get("tiny_step")
    commands = payload.get("suggested_commands") or []
    lines: list[str] = []
    if isinstance(message, str) and message:
        lines.append("코치: " + message)
    if isinstance(tiny_step, str) and tiny_step:
        lines.append("2분 시작: " + tiny_step)
    if isinstance(commands, list) and commands:
        lines.append("추천: " + " / ".join(str(cmd) for cmd in commands[:3]))
    return "\n".join(lines)


def render_listing(listing: Listing) -> str:
    if not listing.items:
        return f"{listing.kind}: (empty)"
    lines = [f"{listing.kind}:"]
    for i, item in enumerate(listing.items, start=1):
        lines.append(f"  ({i}) #{item.id} {item.title}")
    return "\n".join(lines)


def render_log_line(verb: str, summary: str, action_id: int | None = None) -> str:
    ts = datetime.now().strftime("%H:%M:%S")
    base = f"{ts}  {verb:<10} {summary}"
    if action_id is not None:
        base += f"   (action #{action_id}, /undo)"
    return base


def listing_from_payload(kind: str, payload: Any) -> Listing:
    """Best-effort conversion of a backend list payload into a Listing.

    Accepts a list of dicts or a dict with 'items'. Each item is expected
    to have 'id' and one of 'title'/'text'/'summary'.
    """
    rows: list[Any]
    if isinstance(payload, dict):
        rows = (
            payload.get("items")
            or payload.get("candidates")
            or payload.get("results")
            or []
        )
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    item_kind = {
        "tasks": "task",
        "events": "event",
        "inbox": "inbox",
        "search": "search",
    }.get(kind, kind)
    items: list[ListingItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        if rid is None:
            continue
        title = (
            row.get("title")
            or row.get("text")
            or row.get("summary")
            or row.get("name")
            or ""
        )
        per_row_kind = row.get("kind") or row.get("type") or item_kind
        items.append(ListingItem(kind=per_row_kind, id=int(rid), title=str(title)))
    return Listing(kind=kind, items=items)
