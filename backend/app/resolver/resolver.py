"""Top-level orchestrator for the deterministic datetime resolver."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.resolver.absolute import parse_absolute
from app.resolver.relative import parse_relative

ResolveKind = Literal["absolute", "relative", "none"]


class InvalidTimezoneError(ValueError):
    """Raised when a caller-supplied timezone name is not a valid IANA zone."""


@dataclass(frozen=True)
class ResolveResult:
    """Single resolver outcome.

    ``kind='none'`` means the resolver could not interpret the text; callers
    should treat that as "leave the original phrase intact" rather than as a
    fatal error.
    """

    starts_at: str | None
    ends_at: str | None
    kind: ResolveKind
    confidence: float
    source: Literal["rules"] = "rules"
    alternates: tuple[str, ...] = field(default_factory=tuple)


def load_timezone(tz_name: str) -> ZoneInfo:
    """Validate and load an IANA timezone name."""

    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        # ZoneInfo raises ValueError for malformed keys (NUL bytes, leading
        # slashes, ``..`` segments) before ever hitting the lookup table.
        raise InvalidTimezoneError(f"unknown timezone: {tz_name}") from exc


def resolve(text: str, *, now: datetime, tz: str | ZoneInfo) -> ResolveResult:
    """Resolve a free-form datetime phrase deterministically.

    ``now`` must be timezone-aware. ``tz`` is the caller's local timezone and
    governs both how naive timestamps are interpreted and the offset used for
    output strings.
    """

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    zone = tz if isinstance(tz, ZoneInfo) else load_timezone(tz)
    now_local = now.astimezone(zone)
    normalized = " ".join(text.strip().lower().split())

    if not normalized:
        return _none_result()

    abs_dt = parse_absolute(normalized, zone)
    if abs_dt is not None:
        return ResolveResult(
            starts_at=_iso(abs_dt.astimezone(zone)),
            ends_at=None,
            kind="absolute",
            confidence=0.99,
            alternates=(),
        )

    rel = parse_relative(normalized, now_local, zone)
    if rel is not None:
        confidence = _relative_confidence(normalized, rel)
        return ResolveResult(
            starts_at=_iso(rel.primary.astimezone(zone)),
            ends_at=None,
            kind="relative",
            confidence=confidence,
            alternates=tuple(_iso(alt.astimezone(zone)) for alt in rel.alternates),
        )

    return _none_result()


def _none_result() -> ResolveResult:
    return ResolveResult(
        starts_at=None,
        ends_at=None,
        kind="none",
        confidence=0.0,
        alternates=(),
    )


def _iso(dt: datetime) -> str:
    """Format a tz-aware datetime as an ISO8601 string with offset."""

    return dt.isoformat(timespec="seconds")


def _relative_confidence(text: str, match) -> float:
    if text.startswith("in "):
        return 0.95
    if text in {"today", "tomorrow", "yesterday", "tonight"}:
        return 0.7
    if text.startswith("next ") or text.startswith("this "):
        return 0.9 if match.time_explicit else 0.75
    # Bare weekday or weekday + time.
    return 0.85 if match.time_explicit else 0.6
