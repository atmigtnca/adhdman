"""FastAPI entrypoint for ADHDman backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException

from datetime import datetime

from app.classification import EmptyTextError, classify
from app.config import get_settings
from app.db import init_db
from app.resolver import InvalidTimezoneError, resolve as resolve_datetime
from app.llm.base import LLMProvider
from app.llm.openrouter import OpenRouterProvider
from app.repositories import (
    InboxItemNotFoundError,
    InboxItemNotOpenError,
    TaskNotFoundError,
    TaskNotOpenError,
    apply_classification_to_inbox_item,
    capture_to_inbox,
    complete_task,
    get_today_summary,
    list_events,
    list_inbox_items,
    list_tasks,
    promote_inbox_item_to_task,
)
from app.schemas import (
    CaptureRequest,
    CaptureResponse,
    ClassifyResponse,
    EventResponse,
    InboxItemResponse,
    ResolveRequest,
    ResolveResponse,
    ResolveResultSchema,
    TaskResponse,
    TodayResponse,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Initialize the local SQLite schema before serving requests."""

    init_db(settings)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)


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


@app.post("/tasks/{task_id}/done", response_model=TaskResponse)
def mark_task_done(task_id: int) -> TaskResponse:
    """Mark an open task as done."""

    try:
        return complete_task(task_id, settings)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskNotOpenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
