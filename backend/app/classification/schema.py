"""Pydantic schema for classifier output.

Both the rules pass and the LLM pass produce instances of ``ClassifierOutput``
so downstream consumers see a single uniform shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Intent = Literal["task", "event", "inbox"]
ClassificationSource = Literal["rules", "llm", "repair", "fallback"]

REASON_MAX_LEN = 280


class ClassifierOutput(BaseModel):
    """Structured classifier result.

    Validation rules from the Phase 2 plan:
    - ``intent`` ∈ {task, event, inbox}
    - ``confidence`` ∈ [0.0, 1.0]
    - ``title`` required for ``task`` and ``event``, optional for ``inbox``
    - ``starts_at`` / ``ends_at`` apply only to ``event``; when present they
      must parse as ISO8601, and ``None`` is permitted so an event candidate
      can be emitted before the datetime has been resolved
    - ``reason`` is bounded; oversize values are truncated, not rejected
    - Unknown fields are ignored
    """

    model_config = ConfigDict(extra="ignore")

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    title: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    reason: str | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _validate_iso8601(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # ``datetime.fromisoformat`` accepts the subset of ISO8601 we emit.
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("must be ISO8601 or null") from exc
        return value

    @field_validator("reason")
    @classmethod
    def _truncate_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) > REASON_MAX_LEN:
            return value[:REASON_MAX_LEN]
        return value

    @model_validator(mode="after")
    def _enforce_intent_requirements(self) -> "ClassifierOutput":
        if self.intent in {"task", "event"} and not self.title:
            raise ValueError(f"title is required for intent={self.intent}")
        if self.intent != "event":
            # Drop accidental datetime fields on non-event candidates so the
            # output stays clean for downstream persistence.
            object.__setattr__(self, "starts_at", None)
            object.__setattr__(self, "ends_at", None)
        return self
