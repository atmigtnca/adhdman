"""Pydantic schemas for ADHDman API requests and responses."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    parent_task_id: int | None = None
    block_state: str | None = None


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
    focus: "FocusPanelResponse | None" = None


# ----- Phase 6: execution helpers -----


FocusKind = Literal["focus", "body_double", "survival"]
FocusTargetType = Literal["task", "event", "inbox_item"]
FocusStatus = Literal["active", "ended", "cancelled"]


class FocusSessionResponse(BaseModel):
    """Response schema for a row from the ``focus_sessions`` table."""

    id: int
    kind: FocusKind
    target_type: FocusTargetType | None = None
    target_id: int | None = None
    status: FocusStatus
    started_at: str
    ended_at: str | None = None
    interval_seconds: int | None = None
    note: str | None = None
    last_check_in_at: str | None = None


class FocusTarget(BaseModel):
    """Resolved focus target for read endpoints."""

    type: FocusTargetType
    id: int
    title: str


class FocusStartRequest(BaseModel):
    """Request body for POST /focus/start."""

    target_type: FocusTargetType
    target_id: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=500)
    replace: bool = False

    model_config = {"extra": "forbid"}


class FocusCurrentResponse(BaseModel):
    """Read-only response describing the current focus session (if any)."""

    session: FocusSessionResponse | None = None
    target: FocusTarget | None = None
    message: str


class FocusConflictResponse(BaseModel):
    """Calm structured payload returned when a focus session already exists."""

    message: str
    existing: FocusSessionResponse


class BreakdownRequest(BaseModel):
    """Request body for POST /tasks/{id}/breakdown."""

    steps: list[str] = Field(min_length=2, max_length=5)
    source: Literal["manual", "llm"] = "manual"

    model_config = {"extra": "forbid"}

    @field_validator("steps")
    @classmethod
    def _normalize_steps(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for step in value:
            text = step.strip()
            if not text:
                raise ValueError("step text must not be empty")
            if len(text) > 500:
                raise ValueError("step text must be at most 500 characters")
            normalized.append(text)
        return normalized


class BreakdownResponse(BaseModel):
    """Response for POST /tasks/{id}/breakdown."""

    parent: TaskResponse
    children: list[TaskResponse]
    action_id: int


class BreakdownSuggestRequest(BaseModel):
    """Optional knobs for POST /tasks/{id}/breakdown/suggest."""

    max_steps: int | None = Field(default=None, ge=2, le=5)

    model_config = {"extra": "forbid"}


class BreakdownSuggestResponse(BaseModel):
    """Read-only suggestions for a breakdown step list."""

    steps: list[str]
    source: Literal["rules", "llm"]
    prompt: str


StuckChoice = Literal["shrink", "swap", "skip", "park"]


class StuckRequest(BaseModel):
    """Request body for POST /stuck."""

    target_type: Literal["task"]
    target_id: int = Field(ge=1)
    choice: StuckChoice

    model_config = {"extra": "forbid"}


class StuckOption(BaseModel):
    """A single stuck-flow choice with its copy string."""

    choice: StuckChoice
    label: str


class StuckOptionsResponse(BaseModel):
    """Read-only listing of the four stuck-flow choices."""

    prompt: str
    options: list[StuckOption]


class StuckResponse(BaseModel):
    """Response for POST /stuck after applying a choice."""

    task: TaskResponse
    choice: StuckChoice
    action_id: int


class BodyDoubleStartRequest(BaseModel):
    """Request body for POST /body-double/start."""

    interval_seconds: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=500)
    target_type: FocusTargetType | None = None
    target_id: int | None = Field(default=None, ge=1)
    replace: bool = False

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _target_fields_are_paired(self) -> "BodyDoubleStartRequest":
        has_type = self.target_type is not None
        has_id = self.target_id is not None
        if has_type != has_id:
            raise ValueError("target_type and target_id must be provided together")
        return self


class BodyDoubleCurrentResponse(BaseModel):
    """Read-only response describing the active body-double session (if any)."""

    session: FocusSessionResponse | None = None
    target: FocusTarget | None = None
    message: str


class BodyDoubleConflictResponse(BaseModel):
    """Calm structured payload returned when a body-double session already exists."""

    message: str
    existing: FocusSessionResponse


class BodyDoubleCheckInResponse(BaseModel):
    """Response for POST /body-double/check-in."""

    session: FocusSessionResponse
    message: str


class MVSSuggestRequest(BaseModel):
    """Request body for POST /mvs/suggest."""

    target_type: Literal["task", "inbox_item"]
    target_id: int = Field(ge=1)

    model_config = {"extra": "forbid"}


class MVSSuggestResponse(BaseModel):
    """Read-only minimum-viable-step suggestion."""

    step: str
    source: Literal["rules", "llm"]
    prompt: str


class MVSCommitRequest(BaseModel):
    """Request body for POST /mvs/commit."""

    target_type: Literal["task", "inbox_item"]
    target_id: int = Field(ge=1)
    step: str = Field(min_length=1, max_length=500)

    model_config = {"extra": "forbid"}

    @field_validator("step")
    @classmethod
    def _normalize_step(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("step must not be empty")
        return text


class MVSCommitResponse(BaseModel):
    """Response for POST /mvs/commit: created child task and focus session."""

    task: TaskResponse
    focus: FocusSessionResponse
    task_action_id: int
    focus_action_id: int


class SurvivalToggleRequest(BaseModel):
    """Request body for POST /survival/enter and /survival/exit."""

    note: str | None = Field(default=None, max_length=500)

    model_config = {"extra": "forbid"}


class SurvivalStateResponse(BaseModel):
    """Read-only response describing whether survival mode is on."""

    active: bool
    session: FocusSessionResponse | None = None
    message: str


class FocusPanelResponse(BaseModel):
    """Combined Focus panel block for the dashboard payload."""

    session: FocusSessionResponse | None = None
    target: FocusTarget | None = None
    body_double: FocusSessionResponse | None = None
    survival: bool


class CoachNextRequest(BaseModel):
    """Read-only request for POST /coach/next."""

    now: str
    user_text: str | None = Field(default=None, max_length=2000)

    model_config = {"extra": "forbid"}


class CoachNextResponse(BaseModel):
    """Validated execution coach response."""

    mode: Literal["agenda", "stuck", "mvs", "transition", "survival", "clarification"]
    message: str = Field(max_length=240)
    tiny_step: str = Field(max_length=80)
    suggested_commands: list[str] = Field(default_factory=list, max_length=3)
    needs_confirmation: bool = False
    clarification_options: list[str] = Field(default_factory=list, max_length=4)
    source: Literal["rules", "llm"]
