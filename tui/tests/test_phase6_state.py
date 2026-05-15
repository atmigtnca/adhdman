from tui.state import AppState, Listing, ListingItem, PendingBreakdown, PendingMVS


def _listing(kind: str, items: list[tuple[str, int, str]]) -> Listing:
    return Listing(
        kind=kind,
        items=[ListingItem(kind=k, id=i, title=t) for (k, i, t) in items],
    )


def test_resolve_listing_target_basic():
    s = AppState()
    s.set_listing(_listing("tasks", [("task", 1, "a"), ("task", 2, "b")]))
    item = s.resolve_listing_target(2)
    assert item is not None and item.id == 2


def test_resolve_listing_target_kind_filter():
    s = AppState()
    s.set_listing(_listing("inbox", [("inbox", 5, "x")]))
    # task-only consumers reject inbox rows even by index
    assert s.resolve_listing_target(1, allowed_kinds=("task",)) is None
    # but accept it under (task, inbox)
    item = s.resolve_listing_target(1, allowed_kinds=("task", "inbox"))
    assert item is not None and item.kind == "inbox"


def test_resolve_listing_target_out_of_range_returns_none():
    s = AppState()
    s.set_listing(_listing("tasks", [("task", 1, "a")]))
    assert s.resolve_listing_target(0) is None
    assert s.resolve_listing_target(2) is None


def test_resolve_listing_target_no_listing():
    s = AppState()
    assert s.resolve_listing_target(1) is None


def test_pending_breakdown_dataclass_round_trip():
    p = PendingBreakdown(task_id=4, task_title="x", steps=["a", "b"], source="rules")
    assert p.task_id == 4
    assert p.steps == ["a", "b"]
    assert p.source == "rules"


def test_pending_mvs_dataclass_round_trip():
    p = PendingMVS(
        target_type="task",
        target_id=7,
        target_title="t",
        step="open editor",
        source="rules",
    )
    assert p.target_type == "task"
    assert p.step == "open editor"


def test_survival_default_false():
    assert AppState().survival_active is False
