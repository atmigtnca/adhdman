"""Pydantic schemas for ADHDman API requests and responses."""

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
