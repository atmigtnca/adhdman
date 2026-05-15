"""Offline tests for the classifier output schema."""

import pytest
from pydantic import ValidationError

from app.classification.schema import REASON_MAX_LEN, ClassifierOutput


def test_task_requires_title() -> None:
    with pytest.raises(ValidationError):
        ClassifierOutput(intent="task", confidence=0.9)


def test_event_requires_title() -> None:
    with pytest.raises(ValidationError):
        ClassifierOutput(
            intent="event",
            confidence=0.9,
            starts_at="2026-05-16T15:00:00",
        )


def test_event_permits_null_starts_at() -> None:
    # The plan states ``starts_at`` is ISO8601 or null. An event candidate
    # may be emitted before the datetime has been resolved.
    output = ClassifierOutput(
        intent="event", confidence=0.5, title="coffee tomorrow 3pm"
    )
    assert output.starts_at is None
    assert output.ends_at is None


def test_inbox_allows_missing_title() -> None:
    output = ClassifierOutput(intent="inbox", confidence=0.2)
    assert output.title is None
    assert output.starts_at is None


def test_confidence_must_be_in_unit_interval() -> None:
    with pytest.raises(ValidationError):
        ClassifierOutput(intent="inbox", confidence=1.5)
    with pytest.raises(ValidationError):
        ClassifierOutput(intent="inbox", confidence=-0.01)


def test_starts_at_must_be_iso8601_or_null() -> None:
    with pytest.raises(ValidationError):
        ClassifierOutput(
            intent="event",
            confidence=0.9,
            title="meeting",
            starts_at="next tuesday",
        )

    valid = ClassifierOutput(
        intent="event",
        confidence=0.9,
        title="meeting",
        starts_at="2026-05-16T15:00:00",
    )
    assert valid.starts_at == "2026-05-16T15:00:00"


def test_intent_literal_is_enforced() -> None:
    with pytest.raises(ValidationError):
        ClassifierOutput(intent="reminder", confidence=0.9, title="x")  # type: ignore[arg-type]


def test_unknown_fields_are_ignored() -> None:
    output = ClassifierOutput.model_validate(
        {
            "intent": "task",
            "confidence": 0.9,
            "title": "pay rent",
            "priority": "high",
            "extra": {"nested": True},
        }
    )
    assert output.title == "pay rent"
    assert not hasattr(output, "priority")


def test_oversize_reason_is_truncated_not_rejected() -> None:
    oversize = "x" * (REASON_MAX_LEN + 100)
    output = ClassifierOutput(
        intent="task", confidence=0.9, title="pay rent", reason=oversize
    )
    assert output.reason is not None
    assert len(output.reason) == REASON_MAX_LEN


def test_non_event_datetime_fields_are_dropped() -> None:
    output = ClassifierOutput(
        intent="task",
        confidence=0.9,
        title="pay rent",
        starts_at="2026-05-16T15:00:00",
        ends_at="2026-05-16T16:00:00",
    )
    assert output.starts_at is None
    assert output.ends_at is None


def test_title_whitespace_is_stripped() -> None:
    output = ClassifierOutput(intent="task", confidence=0.9, title="  pay rent  ")
    assert output.title == "pay rent"
