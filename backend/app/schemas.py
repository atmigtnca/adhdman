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


class InboxItemResponse(BaseModel):
    """Response schema for an inbox item."""

    id: int
    text: str
    status: str
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
