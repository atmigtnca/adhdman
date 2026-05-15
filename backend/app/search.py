"""Deterministic, read-only candidate search across tasks, events, and inbox.

Phase 3 safe-candidate-selection layer. Free-form text never mutates a row;
it produces a scored list of candidates the caller must confirm by id through
the typed ``PATCH``/``DELETE`` endpoints.

Scoring is pure Python, offline, and deterministic for a given
``(query, rows, now)`` triple — no fuzzy library, no network call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
import sqlite3

from app.config import Settings
from app.db import get_connection


_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

_SUBSTRING_WEIGHT = 0.6
_TOKEN_WEIGHT = 0.3
_RECENCY_BOOST = 0.05
_FUTURE_PROXIMITY_BOOST = 0.05
_RECENCY_WINDOW = timedelta(days=7)
_FUTURE_WINDOW = timedelta(days=14)


def _tokenize(text: str) -> list[str]:
    return [tok for tok in _TOKEN_SPLIT.split(text.lower()) if tok]


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _score(
    title: str,
    query: str,
    query_tokens: set[str],
    updated_at: str | None,
    starts_at: str | None,
    now: datetime,
) -> float:
    title_lower = title.lower()
    score = 0.0
    if query and query in title_lower:
        score += _SUBSTRING_WEIGHT
    title_tokens = set(_tokenize(title))
    overlap_fraction = 0.0
    if query_tokens:
        overlap_fraction = len(query_tokens & title_tokens) / len(query_tokens)
        score += _TOKEN_WEIGHT * overlap_fraction

    # Recency and proximity only boost rows that already match textually,
    # so a non-matching but recently-touched row never surfaces.
    if score == 0.0:
        return 0.0

    updated = _parse_iso(updated_at)
    if updated is not None and now - updated <= _RECENCY_WINDOW and updated <= now:
        score += _RECENCY_BOOST

    starts = _parse_iso(starts_at)
    if starts is not None and abs(starts - now) <= _FUTURE_WINDOW:
        score += _FUTURE_PROXIMITY_BOOST

    return min(score, 1.0)


def search_candidates(
    query: str,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict:
    """Return scored candidates across tasks, events, and inbox items.

    Read-only: no row is ever mutated by this function. Soft-deleted rows are
    excluded. ``ambiguous`` is True when the top two candidates are within
    ``settings.search_ambiguity_threshold`` of each other — a signal that the
    caller should disambiguate before mutating.
    """

    normalized = query.strip().lower()
    if not normalized:
        raise ValueError("query must not be empty")

    if now is None:
        now = datetime.now(UTC)

    query_tokens = set(_tokenize(normalized))
    candidates: list[dict] = []

    with get_connection(settings) as connection:
        connection.row_factory = sqlite3.Row
        task_rows = connection.execute(
            "SELECT id, title, status, due_at, updated_at FROM tasks "
            "WHERE status != 'deleted'",
        ).fetchall()
        event_rows = connection.execute(
            "SELECT id, title, starts_at, ends_at, status, updated_at FROM events "
            "WHERE status != 'deleted'",
        ).fetchall()
        inbox_rows = connection.execute(
            "SELECT id, text, status, updated_at FROM inbox_items "
            "WHERE status = 'open'",
        ).fetchall()

    for row in task_rows:
        score = _score(
            row["title"], normalized, query_tokens, row["updated_at"], row["due_at"], now
        )
        if score > 0:
            candidates.append(
                {
                    "type": "task",
                    "id": row["id"],
                    "title": row["title"],
                    "starts_at": row["due_at"],
                    "score": round(score, 4),
                }
            )

    for row in event_rows:
        score = _score(
            row["title"],
            normalized,
            query_tokens,
            row["updated_at"],
            row["starts_at"],
            now,
        )
        if score > 0:
            candidates.append(
                {
                    "type": "event",
                    "id": row["id"],
                    "title": row["title"],
                    "starts_at": row["starts_at"],
                    "score": round(score, 4),
                }
            )

    for row in inbox_rows:
        score = _score(
            row["text"], normalized, query_tokens, row["updated_at"], None, now
        )
        if score > 0:
            candidates.append(
                {
                    "type": "inbox",
                    "id": row["id"],
                    "title": row["text"],
                    "starts_at": None,
                    "score": round(score, 4),
                }
            )

    # Deterministic ordering: highest score, then type, then id.
    candidates.sort(key=lambda c: (-c["score"], c["type"], c["id"]))

    limit = settings.search_max_candidates
    bounded = candidates[:limit]

    ambiguous = False
    if len(bounded) >= 2:
        gap = bounded[0]["score"] - bounded[1]["score"]
        ambiguous = gap < settings.search_ambiguity_threshold

    return {
        "query": query.strip(),
        "candidates": bounded,
        "ambiguous": ambiguous,
        "max_candidates": limit,
        "ambiguity_threshold": settings.search_ambiguity_threshold,
    }
