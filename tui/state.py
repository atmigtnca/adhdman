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
class AppState:
    today: dict[str, Any] | None = None
    last_listing: Listing | None = None
    last_selection: ListingItem | None = None
    history: list[str] = field(default_factory=list)  # input lines, in-memory only

    def set_listing(self, listing: Listing) -> None:
        self.last_listing = listing

    def set_selection(self, item: ListingItem) -> None:
        self.last_selection = item

    def push_history(self, line: str) -> None:
        if line:
            self.history.append(line)

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
