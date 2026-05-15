"""FastAPI entrypoint for ADHDman backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from datetime import datetime

from app.classification import EmptyTextError, classify
from app.config import get_settings
from app.db import init_db
from app.resolver import InvalidTimezoneError, resolve as resolve_datetime
from app.llm.base import LLMProvider
from app.llm.openrouter import OpenRouterProvider
from app.repositories import (
    EventNotFoundError,
    InboxItemNotFoundError,
    InboxItemNotOpenError,
    InvalidUpdateError,
    TaskNotFoundError,
    TaskNotOpenError,
    apply_classification_to_inbox_item,
    capture_to_inbox,
    complete_task,
    delete_event as delete_event_repo,
    delete_task as delete_task_repo,
    get_dashboard,
    get_event as get_event_repo,
    get_task as get_task_repo,
    get_today_summary,
    list_events,
    list_inbox_items,
    list_tasks,
    promote_inbox_item_to_task,
    update_event as update_event_repo,
    update_task as update_task_repo,
)
from app.schemas import (
    CaptureRequest,
    CaptureResponse,
    ClassifyResponse,
    DashboardResponse,
    EventMutationResponse,
    EventResponse,
    EventUpdateRequest,
    InboxItemResponse,
    ResolveRequest,
    ResolveResponse,
    ResolveResultSchema,
    SearchRequest,
    SearchResponse,
    TaskMutationResponse,
    TaskResponse,
    TaskUpdateRequest,
    TodayResponse,
    UndoResponse,
)
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

    The page loads its data exclusively from ``GET /dashboard``. No form,
    button, or fetch call in this shell may mutate state.
    """

    return FileResponse(WEB_INDEX, media_type="text/html")


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint used by local and Docker verification."""

    return {"status": "ok"}


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
