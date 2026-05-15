from tui.state import AppState, Listing, ListingItem


def _tasks_listing():
    return Listing(
        kind="tasks",
        items=[
            ListingItem(kind="task", id=11, title="milk"),
            ListingItem(kind="task", id=12, title="rent"),
            ListingItem(kind="task", id=13, title="dentist"),
        ],
    )


def test_set_listing_replaces():
    s = AppState()
    s.set_listing(_tasks_listing())
    assert s.last_listing.kind == "tasks"
    s.set_listing(Listing(kind="inbox", items=[]))
    assert s.last_listing.kind == "inbox"
    assert s.last_listing.items == []


def test_resolve_done_by_index():
    s = AppState()
    s.set_listing(_tasks_listing())
    item = s.resolve_task_target(2)
    assert item is not None and item.id == 12


def test_resolve_done_out_of_range_returns_none():
    s = AppState()
    s.set_listing(_tasks_listing())
    assert s.resolve_task_target(99) is None
    assert s.resolve_task_target(0) is None


def test_resolve_done_without_listing_returns_none():
    s = AppState()
    assert s.resolve_task_target(1) is None
    assert s.resolve_task_target(None) is None


def test_done_uses_last_selection_when_index_none():
    s = AppState()
    s.set_selection(ListingItem(kind="task", id=99, title="picked"))
    item = s.resolve_task_target(None)
    assert item is not None and item.id == 99


def test_done_does_not_use_non_task_selection():
    s = AppState()
    s.set_selection(ListingItem(kind="event", id=5, title="meeting"))
    assert s.resolve_task_target(None) is None


def test_done_with_index_requires_tasks_listing():
    s = AppState()
    s.set_listing(Listing(kind="inbox", items=[ListingItem("inbox", 1, "x")]))
    assert s.resolve_task_target(1) is None


def test_listing_from_payload_handles_search_candidates():
    from tui.rendering import listing_from_payload

    payload = {
        "query": "milk",
        "candidates": [
            {"type": "task", "id": 5, "title": "buy milk", "score": 0.9},
            {"type": "inbox", "id": 12, "title": "milk run?", "score": 0.4},
        ],
        "ambiguous": False,
        "max_candidates": 5,
        "ambiguity_threshold": 0.15,
    }
    listing = listing_from_payload("search", payload)
    assert [it.id for it in listing.items] == [5, 12]
    assert listing.items[0].kind == "task"
    assert listing.items[1].kind == "inbox"


def test_history_append():
    s = AppState()
    s.push_history("a")
    s.push_history("")
    s.push_history("b")
    assert s.history == ["a", "b"]
