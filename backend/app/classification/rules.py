"""Deterministic rules-based classification pass.

This pass is fast, offline, and free of side effects. It only emits
high-confidence outputs when the shape is unambiguous; anything else falls
through to ``inbox`` at low confidence so the pipeline can decide whether to
defer to the LLM stage or to the inbox fallback.
"""

from __future__ import annotations

import re
from datetime import datetime

from .schema import ClassifierOutput


IMPERATIVE_VERBS: frozenset[str] = frozenset(
    {
        "buy",
        "pay",
        "email",
        "call",
        "fix",
        "write",
        "read",
        "send",
        "book",
        "schedule",
    }
)

# Datetime-like phrase detectors. Each pattern targets a distinct shape so
# matches stay explainable. All patterns are anchored case-insensitively.
_RELATIVE_DAY = r"(?:today|tonight|tomorrow|tmrw|mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_CLOCK_TIME = r"(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2})"
_ISO_TIMESTAMP = r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?"

_DATETIME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b{_RELATIVE_DAY}\b\s+(?:at\s+)?{_CLOCK_TIME}\b", re.IGNORECASE),
    re.compile(rf"\bat\s+{_CLOCK_TIME}\b", re.IGNORECASE),
    re.compile(rf"\b{_ISO_TIMESTAMP}\b", re.IGNORECASE),
    re.compile(rf"\b{_RELATIVE_DAY}\b", re.IGNORECASE),
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _has_datetime(text: str) -> bool:
    return any(pat.search(text) for pat in _DATETIME_PATTERNS)


def _starts_with_imperative(text: str) -> bool:
    first = text.split(maxsplit=1)[0] if text else ""
    return first.lower().strip(",.:;!?") in IMPERATIVE_VERBS


def _looks_like_question(text: str) -> bool:
    if text.endswith("?"):
        return True
    lowered = text.lower()
    return lowered.startswith(
        ("what", "why", "how", "when", "where", "who", "should", "could", "would", "do ", "does ", "did ", "is ", "are ")
    )


def classify_with_rules(text: str) -> ClassifierOutput:
    """Run the deterministic rules pass.

    Returns a ``ClassifierOutput`` whose ``confidence`` reflects how sure the
    rules are. The pipeline interprets values at or above its accept threshold
    as a short-circuit; lower values mean "fall through to the next stage".
    """

    normalized = _normalize(text)
    if not normalized:
        raise ValueError("text must not be empty")

    has_dt = _has_datetime(normalized)
    iso_match = re.search(_ISO_TIMESTAMP, normalized)
    starts_imperative = _starts_with_imperative(normalized)
    is_question = _looks_like_question(normalized)

    if iso_match:
        candidate = iso_match.group(0).replace(" ", "T")
        try:
            datetime.fromisoformat(candidate)
        except ValueError:
            # The regex matched an ISO-like shape (e.g. "2026-13-99") that
            # is not actually a real timestamp. Never raise — preserve the
            # capture by deferring to the inbox at low confidence.
            return ClassifierOutput(
                intent="inbox",
                confidence=0.3,
                title=normalized,
                reason="rules: malformed ISO-like timestamp, deferring to inbox",
            )
        return ClassifierOutput(
            intent="event",
            confidence=0.9,
            title=normalized,
            starts_at=candidate,
            reason="rules: ISO8601 timestamp detected",
        )

    if has_dt:
        # Datetime-like phrase (e.g. "tomorrow 3pm", "at 14:00") but no
        # parseable timestamp. Emit an event candidate with ``starts_at``
        # null so the LLM stage can attempt the date extraction; confidence
        # stays below the rules-accept threshold.
        return ClassifierOutput(
            intent="event",
            confidence=0.5,
            title=normalized,
            starts_at=None,
            reason="rules: datetime-like phrase without parseable timestamp",
        )

    if starts_imperative and not is_question:
        return ClassifierOutput(
            intent="task",
            confidence=0.88,
            title=normalized,
            reason="rules: leading imperative verb",
        )

    return ClassifierOutput(
        intent="inbox",
        confidence=0.3,
        title=normalized,
        reason="rules: ambiguous shape, deferring to inbox/LLM",
    )
