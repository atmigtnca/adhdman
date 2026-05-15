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
    created_at: str
    updated_at: str


class TaskResponse(BaseModel):
    """Response schema for a task."""

    id: int
    title: str
    status: str
    source_inbox_item_id: int | None
    created_at: str
    updated_at: str
    completed_at: str | None


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
