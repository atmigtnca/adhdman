"""Pydantic schemas for ADHDman API requests and responses."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CaptureRequest(BaseModel):
    """Request body for capturing free-form text into the inbox."""

    text: str = Field(min_length=1, max_length=10000)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        """Trim captured text and reject whitespace-only input."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("text must not be empty")
        return normalized


class CaptureClassificationCreated(BaseModel):
    """Pointer to a row created by the classification pass on capture."""

    type: Literal["task", "event"]
    id: int


class CaptureClassification(BaseModel):
    """Classification metadata returned alongside a capture response."""

    intent: Literal["task", "event", "inbox"]
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["rules", "llm", "repair", "fallback"]
    title: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    reason: str | None = None
    created: CaptureClassificationCreated | None = None


class CaptureResponse(BaseModel):
    """Response for POST /capture: original inbox row plus classification.

    The inbox row fields (``id``, ``text``, ``status``, ``created_at``,
    ``updated_at``) are kept alongside ``inbox_item_id`` so Phase 1 clients keep
    working when ``CLASSIFY_ENABLED`` is False.
    """

    id: int
    inbox_item_id: int
    text: str
    status: str
    created_at: str
    updated_at: str
    classification: CaptureClassification


class ClassifyResponse(BaseModel):
    """Response schema for the read-only /classify endpoint.

    Mirrors ``ClassifierOutput`` and adds the pipeline ``source`` so callers can
    see which stage produced the result. No persistence happens for this route.
    """

    intent: Literal["task", "event", "inbox"]
    confidence: float = Field(ge=0.0, le=1.0)
    title: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    reason: str | None = None
    source: Literal["rules", "llm", "repair", "fallback"]


class InboxItemResponse(BaseModel):
    """Response schema for an inbox item."""

    id: int
    text: str
    status: str
    created_at: str
    updated_at: str


class EventResponse(BaseModel):
    """Response schema for an event created by classification."""

    id: int
    title: str
    starts_at: str | None
    ends_at: str | None
    source_inbox_item_id: int | None
    status: str = "open"
    created_at: str
    updated_at: str


class TaskResponse(BaseModel):
    """Response schema for a task."""

    id: int
    title: str
    status: str
    source_inbox_item_id: int | None
    due_at: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None


class TaskUpdateRequest(BaseModel):
    """Request body for PATCH /tasks/{id}.

    All fields are optional; at least one must be provided. Unknown fields are
    rejected so callers cannot silently mutate columns outside the patch surface.
    """

    title: str | None = None
    status: Literal["open", "done", "cancelled"] | None = None
    due_at: str | None = None

    model_config = {"extra": "forbid"}

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("title must not be empty")
        return normalized


class EventUpdateRequest(BaseModel):
    """Request body for PATCH /events/{id}.

    All fields are optional; at least one must be provided. Unknown fields are
    rejected.
    """

    title: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None

    model_config = {"extra": "forbid"}

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("title must not be empty")
        return normalized


class TaskMutationResponse(BaseModel):
    """Response for PATCH/DELETE on a task: the post-mutation row + action id."""

    task: TaskResponse
    action_id: int


class EventMutationResponse(BaseModel):
    """Response for PATCH/DELETE on an event: the post-mutation row + action id."""

    event: EventResponse
    action_id: int


class ResolveRequest(BaseModel):
    """Request body for the deterministic /resolve endpoint."""

    text: str = Field(min_length=1, max_length=500)
    now: str | None = None
    tz: str | None = None

    @field_validator("text")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text must not be empty")
        return normalized


class ResolveResultSchema(BaseModel):
    """The single best interpretation returned by /resolve."""

    starts_at: str | None
    ends_at: str | None
    kind: Literal["absolute", "relative", "none"]
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["rules"]


class ResolveResponse(BaseModel):
    """Response body for /resolve."""

    resolved: ResolveResultSchema
    alternates: list[str]


class SearchRequest(BaseModel):
    """Request body for POST /search.

    Read-only. The endpoint never mutates rows; callers must follow up with an
    explicit PATCH/DELETE by id once they pick a candidate.
    """

    query: str = Field(min_length=1, max_length=500)

    model_config = {"extra": "forbid"}

    @field_validator("query")
    @classmethod
    def _normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        return normalized


class SearchCandidate(BaseModel):
    """A single scored candidate returned by /search."""

    type: Literal["task", "event", "inbox"]
    id: int
    title: str
    starts_at: str | None = None
    score: float = Field(ge=0.0, le=1.0)


class SearchResponse(BaseModel):
    """Scored candidates plus ambiguity metadata.

    ``ambiguous`` is True when the top two candidates' scores are within
    ``ambiguity_threshold`` — a hint that the caller should disambiguate before
    invoking a mutating endpoint.
    """

    query: str
    candidates: list[SearchCandidate]
    ambiguous: bool
    max_candidates: int
    ambiguity_threshold: float


class UndoResponse(BaseModel):
    """Response for POST /undo/{id} and POST /undo/latest.

    ``restored`` is a free-form payload describing the row(s) the inverse
    touched. Its shape depends on the original action type (e.g. ``{"task": …}``
    for ``complete_task``, ``{"inbox_item": …}`` for ``capture``). Callers
    needing typed access should re-fetch the affected resource by id.
    """

    undo_action_id: int
    undone_action_id: int
    undone_action_type: str
    restored: dict | None = None


class TodayOneThingResponse(BaseModel):
    """The single suggested item for today's summary."""

    type: Literal["task", "inbox"]
    id: int
    text: str


class TodayResponse(BaseModel):
    """Response schema for the one-thing today summary."""

    open_tasks_count: int
    inbox_count: int
    one_thing: TodayOneThingResponse | None
    message: str


class RecentActionResponse(BaseModel):
    """Read-only summary row from the action log for the dashboard.

    Excludes ``before_json`` and ``after_json`` so raw snapshots never leak into
    the web payload. Use the dedicated undo API to reverse an action.
    """

    id: int
    action_type: str
    target_type: str
    target_id: int
    created_at: str
    undone_at: str | None = None


class DashboardCounts(BaseModel):
    """Headline counts for the dashboard's Now section."""

    open_tasks: int
    open_inbox: int
    upcoming_events: int


class DashboardToday(BaseModel):
    """The Now block: one-thing focus plus calm counts."""

    message: str
    one_thing: TodayOneThingResponse | None
    counts: DashboardCounts


class WeekItem(BaseModel):
    """A task or event placed on a specific date in the week view."""

    type: Literal["task", "event"]
    id: int
    title: str
    time: str | None = None


class WeekDay(BaseModel):
    """A single day in the grouped week view."""

    date: str
    items: list[WeekItem]


class DashboardResponse(BaseModel):
    """Combined read-only payload for the web memory dashboard."""

    today: DashboardToday
    inbox: list[InboxItemResponse]
    tasks: list[TaskResponse]
    events: list[EventResponse]
    week: list[WeekDay]
    recent_actions: list[RecentActionResponse]
