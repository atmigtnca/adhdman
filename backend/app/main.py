"""FastAPI entrypoint for ADHDman backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from datetime import datetime
from dataclasses import asdict

from app.agenda import get_agenda_now as get_agenda_now_repo
from app.coach import coach_next as coach_next_repo
from app.classification import EmptyTextError, classify
from app.config import get_settings
from app.db import init_db
from app.resolver import InvalidTimezoneError, resolve as resolve_datetime
from app.llm.base import LLMProvider
from app.llm.openrouter import OpenRouterProvider
from app.repositories import (
    BodyDoubleNotActiveError,
    BodyDoubleSessionConflictError,
    BreakdownConflictError,
    EventNotFoundError,
    FocusSessionConflictError,
    FocusTargetNotFoundError,
    InboxItemNotFoundError,
    InboxItemNotOpenError,
    InvalidUpdateError,
    TaskNotFoundError,
    TaskNotOpenError,
    apply_classification_to_inbox_item,
    apply_stuck_choice as apply_stuck_choice_repo,
    breakdown_task as breakdown_task_repo,
    capture_to_inbox,
    commit_mvs_step as commit_mvs_step_repo,
    complete_task,
    delete_event as delete_event_repo,
    delete_task as delete_task_repo,
    get_active_body_double_with_target,
    get_active_focus_with_target,
    enter_survival_mode,
    exit_survival_mode,
    get_dashboard,
    get_event as get_event_repo,
    get_task as get_task_repo,
    get_survival_state,
    get_today_summary,
    list_events,
    list_inbox_items,
    list_task_children,
    list_tasks,
    promote_inbox_item_to_task,
    record_body_double_checkin as record_body_double_checkin_repo,
    start_body_double_session as start_body_double_session_repo,
    start_focus_session as start_focus_session_repo,
    stop_body_double_session as stop_body_double_session_repo,
    stop_focus_session as stop_focus_session_repo,
    suggest_breakdown_steps,
    suggest_mvs_step as suggest_mvs_step_repo,
    update_event as update_event_repo,
    update_task as update_task_repo,
)
from app.schemas import (
    BodyDoubleCheckInResponse,
    BodyDoubleConflictResponse,
    BodyDoubleCurrentResponse,
    BodyDoubleStartRequest,
    BreakdownRequest,
    BreakdownResponse,
    BreakdownSuggestRequest,
    BreakdownSuggestResponse,
    CaptureRequest,
    CaptureResponse,
    ClassifyResponse,
    CoachNextRequest,
    CoachNextResponse,
    DashboardResponse,
    EventMutationResponse,
    EventResponse,
    EventUpdateRequest,
    FocusConflictResponse,
    FocusCurrentResponse,
    FocusStartRequest,
    InboxItemResponse,
    MVSCommitRequest,
    MVSCommitResponse,
    MVSSuggestRequest,
    MVSSuggestResponse,
    ResolveRequest,
    ResolveResponse,
    ResolveResultSchema,
    SearchRequest,
    SearchResponse,
    StuckOption,
    StuckOptionsResponse,
    StuckRequest,
    StuckResponse,
    SurvivalStateResponse,
    SurvivalToggleRequest,
    TaskMutationResponse,
    TaskResponse,
    TaskUpdateRequest,
    TodayResponse,
    UndoResponse,
)
from app import copy as copy_strings
from app.search import search_candidates
from app.undo import (
    ActionAlreadyUndoneError,
    ActionConflictError,
    ActionNotFoundError,
    ActionNotReversibleError,
    NoUndoableActionError,
    UndoDisabledError,
    undo_action as undo_action_repo,
    undo_latest as undo_latest_repo,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Initialize the local SQLite schema before serving requests."""

    init_db(settings)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

STATIC_DIR = Path(__file__).resolve().parent / "static"
WEB_INDEX = STATIC_DIR / "web" / "index.html"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/web", include_in_schema=False)
def web_dashboard() -> FileResponse:
    """Serve the static read-only web memory dashboard shell.

    The page loads its data exclusively from read-only ``GET /dashboard``,
    ``GET /agenda/now``, and ``GET /coach/next``. No form, button, or fetch
    call in this shell may mutate state.
    """

    return FileResponse(WEB_INDEX, media_type="text/html")


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint used by local and Docker verification."""

    return {"status": "ok"}


@app.get("/agenda/now")
def get_agenda_now(now: str) -> dict:
    """Return the read-only current-action agenda recommendation."""

    try:
        agenda = get_agenda_now_repo(now=now, settings=settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid now timestamp") from exc
    return asdict(agenda)


@app.post("/coach/next", response_model=CoachNextResponse)
def coach_next(request: CoachNextRequest) -> CoachNextResponse:
    """Return a read-only execution coach message for the current agenda."""

    try:
        payload = coach_next_repo(
            now=request.now,
            user_text=request.user_text,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid now timestamp") from exc
    return CoachNextResponse(**asdict(payload))


@app.get("/coach/next", response_model=CoachNextResponse)
def coach_next_read_only(now: str) -> CoachNextResponse:
    """GET alias for read-only Web/TUI coach cards with no user text."""

    try:
        payload = coach_next_repo(now=now, settings=settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid now timestamp") from exc
    return CoachNextResponse(**asdict(payload))


@app.get("/inbox", response_model=list[InboxItemResponse])
def get_inbox() -> list[InboxItemResponse]:
    """List open inbox items oldest first."""

    return list_inbox_items(settings=settings)


def get_llm_provider() -> LLMProvider | None:
    """Return the production LLM provider, or None when no key is configured.

    Tests inject a fake via ``app.dependency_overrides[get_llm_provider]`` to
    keep the suite offline.
    """

    if not settings.openrouter_api_key:
        return None
    return OpenRouterProvider(settings)


@app.post("/capture", response_model=CaptureResponse, status_code=201)
def capture(
    request: CaptureRequest,
    provider: LLMProvider | None = Depends(get_llm_provider),
) -> CaptureResponse:
    """Capture free-form text into the inbox and run the classification pipeline.

    The original text is always stored as an inbox row first so the capture-first
    guarantee from Phase 1 is preserved. When ``CLASSIFY_ENABLED`` is False the
    endpoint behaves like Phase 1: a single inbox row plus a single ``capture``
    action are written; no classify_* action is logged.
    """

    inbox_item = capture_to_inbox(request.text, settings)
    try:
        result = classify(request.text, settings=settings, provider=provider)
    except EmptyTextError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return apply_classification_to_inbox_item(
        inbox_item,
        result.output,
        result.source,
        settings,
        persist_classification=settings.classify_enabled,
    )


@app.post("/classify", response_model=ClassifyResponse)
def classify_text(
    request: CaptureRequest,
    provider: LLMProvider | None = Depends(get_llm_provider),
) -> ClassifyResponse:
    """Classify text without persisting anything.

    Read-only counterpart to ``POST /capture`` used for tests, the future TUI
    preview, and debugging. Never writes inbox, task, event, or action rows.
    """

    try:
        result = classify(request.text, settings=settings, provider=provider)
    except EmptyTextError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ClassifyResponse(
        intent=result.output.intent,
        confidence=result.output.confidence,
        title=result.output.title,
        starts_at=result.output.starts_at,
        ends_at=result.output.ends_at,
        reason=result.output.reason,
        source=result.source,
    )


@app.post("/inbox/{inbox_item_id}/promote-task", response_model=TaskResponse, status_code=201)
def promote_inbox_item(inbox_item_id: int) -> TaskResponse:
    """Promote an open inbox item to a task."""

    try:
        return promote_inbox_item_to_task(inbox_item_id, settings)
    except InboxItemNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InboxItemNotOpenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/events", response_model=list[EventResponse])
def get_events() -> list[EventResponse]:
    """List events created by classification, earliest start first."""

    return list_events(settings=settings)


@app.get("/tasks", response_model=list[TaskResponse])
def get_tasks() -> list[TaskResponse]:
    """List open tasks oldest first."""

    return list_tasks(settings=settings)


@app.get("/today", response_model=TodayResponse)
def get_today() -> TodayResponse:
    """Return the one-thing summary for today."""

    return get_today_summary(settings=settings)


@app.get("/dashboard", response_model=DashboardResponse)
def get_dashboard_endpoint() -> DashboardResponse:
    """Return the combined read-only payload for the web memory dashboard.

    Composes ``today``, ``inbox``, ``tasks``, ``events``, a derived ``week``
    view, and a small ``recent_actions`` summary. This endpoint never mutates
    rows and never exposes raw before/after snapshots from the action log.
    """

    return get_dashboard(settings=settings)


@app.post("/resolve", response_model=ResolveResponse)
def resolve_endpoint(request: ResolveRequest) -> ResolveResponse:
    """Parse a free-form datetime phrase. Read-only; never persists anything.

    ``now`` and ``tz`` default to the current time and ``LOCAL_TIMEZONE`` so the
    same endpoint works for both client-driven and server-driven calls. The
    resolver itself is pure: callers that need reproducible output should pass
    both fields explicitly.
    """

    tz_name = request.tz or settings.local_timezone
    try:
        from app.resolver.resolver import load_timezone

        zone = load_timezone(tz_name)
    except InvalidTimezoneError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if request.now is not None:
        try:
            now_dt = datetime.fromisoformat(request.now)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"invalid 'now' timestamp: {exc}"
            ) from exc
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=zone)
    else:
        now_dt = datetime.now(tz=zone)

    result = resolve_datetime(request.text, now=now_dt, tz=zone)
    return ResolveResponse(
        resolved=ResolveResultSchema(
            starts_at=result.starts_at,
            ends_at=result.ends_at,
            kind=result.kind,
            confidence=result.confidence,
            source=result.source,
        ),
        alternates=list(result.alternates),
    )


@app.post("/search", response_model=SearchResponse)
def search_endpoint(request: SearchRequest) -> SearchResponse:
    """Return scored candidates across tasks, events, and inbox items.

    Read-only by design: no row is mutated. Callers pick a specific id from the
    response and invoke the typed ``PATCH``/``DELETE`` endpoint to actually
    change a row. This is the single rule that prevents free-form references
    like "edit the dentist thing" from silently editing the wrong row.
    """

    result = search_candidates(request.query, settings)
    return SearchResponse(**result)


@app.post("/tasks/{task_id}/done", response_model=TaskResponse)
def mark_task_done(task_id: int) -> TaskResponse:
    """Mark an open task as done."""

    try:
        task = complete_task(task_id, settings)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskNotOpenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return task


@app.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: int) -> TaskResponse:
    """Return a single task by id (includes soft-deleted rows)."""

    try:
        return get_task_repo(task_id, settings)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/tasks/{task_id}", response_model=TaskMutationResponse)
def patch_task(task_id: int, request: TaskUpdateRequest) -> TaskMutationResponse:
    """Update a task's title, status, or due_at and log a snapshot."""

    patch = request.model_dump(exclude_unset=True)
    try:
        task, action_id = update_task_repo(task_id, patch, settings)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskNotOpenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidUpdateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaskMutationResponse(task=task, action_id=action_id)


@app.delete("/tasks/{task_id}", response_model=TaskMutationResponse)
def soft_delete_task(task_id: int) -> TaskMutationResponse:
    """Soft-delete a task by setting ``status='deleted'``; row remains readable."""

    try:
        task, action_id = delete_task_repo(task_id, settings)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return TaskMutationResponse(task=task, action_id=action_id)


@app.post(
    "/tasks/{task_id}/breakdown",
    response_model=BreakdownResponse,
    status_code=201,
)
def break_down_task(task_id: int, request: BreakdownRequest) -> BreakdownResponse:
    """Split a task into 2–5 child tasks and log a single reversible action."""

    try:
        parent, children, action_id = breakdown_task_repo(
            task_id, request.steps, source=request.source, settings=settings
        )
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BreakdownConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidUpdateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BreakdownResponse(parent=parent, children=children, action_id=action_id)


@app.get("/tasks/{task_id}/children", response_model=list[TaskResponse])
def list_children(task_id: int) -> list[TaskResponse]:
    """Return the child tasks for ``task_id`` (includes soft-deleted children)."""

    try:
        return list_task_children(task_id, settings=settings)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/tasks/{task_id}/breakdown/suggest",
    response_model=BreakdownSuggestResponse,
)
def suggest_task_breakdown(
    task_id: int, request: BreakdownSuggestRequest = BreakdownSuggestRequest()
) -> BreakdownSuggestResponse:
    """Return rules-only breakdown suggestions. Read-only; persists nothing."""

    max_steps = request.max_steps
    try:
        steps, source = suggest_breakdown_steps(task_id, max_steps, settings)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return BreakdownSuggestResponse(
        steps=steps,
        source=source,
        prompt=copy_strings.BREAKDOWN_PROMPT,
    )


@app.get("/events/{event_id}", response_model=EventResponse)
def get_event(event_id: int) -> EventResponse:
    """Return a single event by id (includes soft-deleted rows)."""

    try:
        return get_event_repo(event_id, settings)
    except EventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/events/{event_id}", response_model=EventMutationResponse)
def patch_event(event_id: int, request: EventUpdateRequest) -> EventMutationResponse:
    """Update an event's title, starts_at, or ends_at and log a snapshot."""

    patch = request.model_dump(exclude_unset=True)
    try:
        event, action_id = update_event_repo(event_id, patch, settings)
    except EventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidUpdateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EventMutationResponse(event=event, action_id=action_id)


@app.delete("/events/{event_id}", response_model=EventMutationResponse)
def soft_delete_event(event_id: int) -> EventMutationResponse:
    """Soft-delete an event by setting ``status='deleted'``; row remains readable."""

    try:
        event, action_id = delete_event_repo(event_id, settings)
    except EventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return EventMutationResponse(event=event, action_id=action_id)


@app.get("/focus/current", response_model=FocusCurrentResponse)
def focus_current() -> FocusCurrentResponse:
    """Return the active focus session (kind=focus), if any, with target."""

    result = get_active_focus_with_target("focus", settings)
    if result is None:
        return FocusCurrentResponse(message=copy_strings.EMPTY_FOCUS)
    session, target = result
    return FocusCurrentResponse(
        session=session,
        target=target,
        message="Focus is set." if target is not None else "Focus target is unavailable.",
    )


@app.post("/focus/start", response_model=FocusCurrentResponse, status_code=201)
def focus_start(request: FocusStartRequest) -> FocusCurrentResponse:
    """Start (or replace) the single active focus session."""

    try:
        session, target, _ = start_focus_session_repo(
            target_type=request.target_type,
            target_id=request.target_id,
            note=request.note,
            replace=request.replace,
            settings=settings,
        )
    except FocusTargetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FocusSessionConflictError as exc:
        payload = FocusConflictResponse(
            message=copy_strings.FOCUS_CONFLICT, existing=exc.existing
        )
        raise HTTPException(status_code=409, detail=payload.model_dump()) from exc

    return FocusCurrentResponse(session=session, target=target, message="Focus is set.")


@app.post("/focus/stop", response_model=FocusCurrentResponse)
def focus_stop() -> FocusCurrentResponse:
    """End the active focus session. Idempotent."""

    stop_focus_session_repo(settings)
    return FocusCurrentResponse(message=copy_strings.EMPTY_FOCUS)


@app.get("/body-double/current", response_model=BodyDoubleCurrentResponse)
def body_double_current() -> BodyDoubleCurrentResponse:
    """Return the active body-double session (kind=body_double), if any."""

    result = get_active_body_double_with_target(settings)
    if result is None:
        return BodyDoubleCurrentResponse(message=copy_strings.BODY_DOUBLE_EMPTY)
    session, target = result
    return BodyDoubleCurrentResponse(
        session=session, target=target, message=copy_strings.BODY_DOUBLE_START
    )


@app.post(
    "/body-double/start",
    response_model=BodyDoubleCurrentResponse,
    status_code=201,
)
def body_double_start(request: BodyDoubleStartRequest) -> BodyDoubleCurrentResponse:
    """Start (or replace) the single active body-double session.

    Local timer only. No external presence or notification service is contacted.
    """

    interval = (
        request.interval_seconds
        if request.interval_seconds is not None
        else settings.body_double_default_interval
    )
    if not (
        settings.body_double_min_interval
        <= interval
        <= settings.body_double_max_interval
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"interval_seconds must be between "
                f"{settings.body_double_min_interval} and "
                f"{settings.body_double_max_interval}"
            ),
        )

    try:
        session, target, _ = start_body_double_session_repo(
            interval_seconds=interval,
            note=request.note,
            target_type=request.target_type,
            target_id=request.target_id,
            replace=request.replace,
            settings=settings,
        )
    except FocusTargetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BodyDoubleSessionConflictError as exc:
        payload = BodyDoubleConflictResponse(
            message=copy_strings.BODY_DOUBLE_CONFLICT, existing=exc.existing
        )
        raise HTTPException(status_code=409, detail=payload.model_dump()) from exc

    return BodyDoubleCurrentResponse(
        session=session, target=target, message=copy_strings.BODY_DOUBLE_START
    )


@app.post("/body-double/check-in", response_model=BodyDoubleCheckInResponse)
def body_double_check_in() -> BodyDoubleCheckInResponse:
    """Record a heartbeat on the active body-double session."""

    try:
        session = record_body_double_checkin_repo(settings)
    except BodyDoubleNotActiveError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return BodyDoubleCheckInResponse(
        session=session, message=copy_strings.BODY_DOUBLE_CHECK_IN
    )


@app.post("/body-double/stop", response_model=BodyDoubleCurrentResponse)
def body_double_stop() -> BodyDoubleCurrentResponse:
    """End the active body-double session. Idempotent."""

    stop_body_double_session_repo(settings)
    return BodyDoubleCurrentResponse(message=copy_strings.BODY_DOUBLE_STOP)


@app.get("/stuck/options", response_model=StuckOptionsResponse)
def stuck_options(
    target_type: str = "task", target_id: int | None = None
) -> StuckOptionsResponse:
    """Return the four block-reset choices with their non-shaming copy.

    Read-only: never writes anything. ``target_type`` / ``target_id`` are
    accepted for parity with ``POST /stuck`` and to let future targets surface
    different choice sets, but the current slice only supports tasks and the
    options list is identical regardless of target.
    """

    if target_type != "task":
        raise HTTPException(
            status_code=400,
            detail="block reset only supports target_type='task'",
        )
    if target_id is not None:
        try:
            get_task_repo(target_id, settings)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    options = [
        StuckOption(choice=choice, label=copy_strings.STUCK_OPTIONS[choice])
        for choice in ("shrink", "swap", "skip", "park")
    ]
    return StuckOptionsResponse(
        prompt=copy_strings.BLOCK_RESET_PROMPT, options=options
    )


@app.post("/stuck", response_model=StuckResponse)
def stuck(request: StuckRequest) -> StuckResponse:
    """Apply a block-reset choice to a task and log a reversible action."""

    try:
        task, action_id = apply_stuck_choice_repo(
            request.target_type, request.target_id, request.choice, settings
        )
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidUpdateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FocusTargetNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StuckResponse(task=task, choice=request.choice, action_id=action_id)


@app.get("/survival", response_model=SurvivalStateResponse)
def survival_state() -> SurvivalStateResponse:
    """Return current survival-mode state. Read-only."""

    session = get_survival_state(settings)
    if session is None:
        return SurvivalStateResponse(
            active=False, session=None, message=copy_strings.SURVIVAL_EXIT
        )
    return SurvivalStateResponse(
        active=True, session=session, message=copy_strings.SURVIVAL_ENTER
    )


@app.post("/survival/enter", response_model=SurvivalStateResponse, status_code=201)
def survival_enter(request: SurvivalToggleRequest = SurvivalToggleRequest()) -> SurvivalStateResponse:
    """Idempotently enable survival mode as a targetless local focus session."""

    session, _ = enter_survival_mode(note=request.note, settings=settings)
    return SurvivalStateResponse(
        active=True, session=session, message=copy_strings.SURVIVAL_ENTER
    )


@app.post("/survival/exit", response_model=SurvivalStateResponse)
def survival_exit(request: SurvivalToggleRequest = SurvivalToggleRequest()) -> SurvivalStateResponse:
    """Idempotently disable survival mode. The request body is accepted for symmetry."""

    _ = request
    exit_survival_mode(settings=settings)
    return SurvivalStateResponse(
        active=False, session=None, message=copy_strings.SURVIVAL_EXIT
    )


@app.post("/mvs/suggest", response_model=MVSSuggestResponse)
def mvs_suggest(request: MVSSuggestRequest) -> MVSSuggestResponse:
    """Return one rules-only minimum-viable-step suggestion. Read-only."""

    try:
        step, source = suggest_mvs_step_repo(
            request.target_type, request.target_id, settings
        )
    except FocusTargetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MVSSuggestResponse(step=step, source=source, prompt=copy_strings.MVS_PROMPT)


@app.post("/mvs/commit", response_model=MVSCommitResponse, status_code=201)
def mvs_commit(request: MVSCommitRequest) -> MVSCommitResponse:
    """Create a single child task carrying the step and start focus on it."""

    try:
        task, focus, _target, task_action_id, focus_action_id = commit_mvs_step_repo(
            request.target_type, request.target_id, request.step, settings
        )
    except FocusTargetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InboxItemNotOpenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BreakdownConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidUpdateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MVSCommitResponse(
        task=task,
        focus=focus,
        task_action_id=task_action_id,
        focus_action_id=focus_action_id,
    )


@app.post("/undo/latest", response_model=UndoResponse)
def undo_latest_endpoint() -> UndoResponse:
    """Reverse the most recent reversible action.

    Routed before ``/undo/{action_id}`` so ``latest`` is never interpreted as
    an integer path parameter.
    """

    try:
        result = undo_latest_repo(settings)
    except UndoDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NoUndoableActionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ActionNotReversibleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ActionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return UndoResponse(**result)


@app.post("/undo/{action_id}", response_model=UndoResponse)
def undo_endpoint(action_id: int) -> UndoResponse:
    """Reverse a specific action by id."""

    try:
        result = undo_action_repo(action_id, settings)
    except UndoDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ActionAlreadyUndoneError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ActionNotReversibleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ActionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return UndoResponse(**result)
