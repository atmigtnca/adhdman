"""Offline tests for the deterministic rules pass."""

import pytest

from app.classification.rules import classify_with_rules


def test_imperative_verb_classifies_as_task() -> None:
    output = classify_with_rules("pay rent")
    assert output.intent == "task"
    assert output.confidence >= 0.85
    assert output.title == "pay rent"


@pytest.mark.parametrize(
    "text",
    [
        "buy groceries",
        "call mom",
        "email Sarah about the deck",
        "send invoice",
        "book dentist",
    ],
)
def test_common_imperatives_classify_as_task(text: str) -> None:
    output = classify_with_rules(text)
    assert output.intent == "task"
    assert output.confidence >= 0.85


def test_iso_timestamp_classifies_as_event_with_start() -> None:
    output = classify_with_rules("standup 2026-05-16T09:30:00")
    assert output.intent == "event"
    assert output.confidence >= 0.85
    assert output.starts_at == "2026-05-16T09:30:00"
    assert output.title == "standup 2026-05-16T09:30:00"


def test_iso_date_only_classifies_as_event() -> None:
    output = classify_with_rules("review 2026-05-16")
    assert output.intent == "event"
    assert output.starts_at == "2026-05-16"


@pytest.mark.parametrize(
    "text",
    [
        "tomorrow 3pm coffee with Jamie",
        "at 14:00 standup",
        "meet Friday 9am",
    ],
)
def test_relative_datetime_returns_event_with_null_starts_at(text: str) -> None:
    # Datetime-like but not parseable: emit an event candidate so the LLM
    # stage can resolve the timestamp. Confidence stays below the
    # rules-accept threshold so this never short-circuits.
    output = classify_with_rules(text)
    assert output.intent == "event"
    assert output.starts_at is None
    assert output.title == text.strip()
    assert output.confidence < 0.85


@pytest.mark.parametrize(
    "text",
    [
        "meeting 2026-13-99",  # impossible month/day
        "sync 2026-02-30",  # day out of range
        "call 2026-05-16 25:99",  # hour/minute out of range
    ],
)
def test_malformed_iso_like_timestamp_falls_back_to_inbox(text: str) -> None:
    # Regex matches the ISO shape but ``datetime.fromisoformat`` rejects
    # the value. Rules must never raise — capture-first requires a
    # low-confidence inbox fallback.
    output = classify_with_rules(text)
    assert output.intent == "inbox"
    assert output.confidence < 0.85
    assert output.starts_at is None


def test_question_classifies_as_inbox() -> None:
    output = classify_with_rules("should I refactor the capture endpoint?")
    assert output.intent == "inbox"
    assert output.confidence < 0.85


def test_bare_noun_classifies_as_inbox() -> None:
    output = classify_with_rules("coffee")
    assert output.intent == "inbox"
    assert output.confidence < 0.85


def test_whitespace_only_input_is_rejected() -> None:
    with pytest.raises(ValueError):
        classify_with_rules("   \n\t  ")


def test_text_is_normalized_in_title() -> None:
    output = classify_with_rules("pay   rent\tnow")
    assert output.title == "pay rent now"


def test_imperative_question_does_not_become_task() -> None:
    output = classify_with_rules("call mom?")
    assert output.intent == "inbox"
