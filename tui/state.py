from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ListingItem:
    """One row of a numbered listing.

    `kind` is the resource type ("task", "event", "inbox", "search").
    `id` is the backend row id. `title` is a short human label.
    """

    kind: str
    id: int
    title: str


@dataclass
class Listing:
    kind: str  # "tasks", "events", "inbox", "search"
    items: list[ListingItem] = field(default_factory=list)

    def resolve(self, index_1based: int) -> ListingItem | None:
        if index_1based < 1 or index_1based > len(self.items):
            return None
        return self.items[index_1based - 1]


@dataclass
class PendingBreakdown:
    """Cached `/breakdown N` suggestion awaiting `/breakdown commit`."""

    task_id: int
    task_title: str
    steps: list[str]
    source: str  # "rules" | "llm"


@dataclass
class PendingMVS:
    """Cached `/mvs N` suggestion awaiting `/mvs commit`."""

    target_type: str  # "task" | "inbox_item"
    target_id: int
    target_title: str
    step: str
    source: str  # "rules" | "llm"


@dataclass
class PendingClarification:
    """A user-facing question that needs one more answer before capture."""

    kind: str
    original: str
    subject: str


@dataclass
class AppState:
    today: dict[str, Any] | None = None
    last_listing: Listing | None = None
    last_selection: ListingItem | None = None
    history: list[str] = field(default_factory=list)  # input lines, in-memory only
    pending_breakdown: PendingBreakdown | None = None
    pending_mvs: PendingMVS | None = None
    pending_clarification: PendingClarification | None = None
    survival_active: bool = False

    def set_listing(self, listing: Listing) -> None:
        self.last_listing = listing

    def set_selection(self, item: ListingItem) -> None:
        self.last_selection = item

    def push_history(self, line: str) -> None:
        if line:
            self.history.append(line)

    def resolve_listing_target(
        self,
        index: int,
        allowed_kinds: tuple[str, ...] | None = None,
    ) -> ListingItem | None:
        """Resolve a 1-based index in the most recent listing, regardless of kind.

        If ``allowed_kinds`` is given, only items whose ``kind`` is in the
        set are returned; non-matching rows yield ``None`` so callers can
        emit a calm message rather than mutating an unsupported target.
        """
        if self.last_listing is None:
            return None
        item = self.last_listing.resolve(index)
        if item is None:
            return None
        if allowed_kinds is not None and item.kind not in allowed_kinds:
            return None
        return item

    def resolve_task_target(self, index: int | None) -> ListingItem | None:
        """Resolve a /done-style target.

        If index is given, look it up in last_listing (must be tasks).
        If index is None, use last_selection if it's a task.
        """
        if index is not None:
            if self.last_listing is None or self.last_listing.kind != "tasks":
                return None
            return self.last_listing.resolve(index)
        if self.last_selection is not None and self.last_selection.kind == "task":
            return self.last_selection
        return None
