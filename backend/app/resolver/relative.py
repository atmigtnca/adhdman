"""Relative datetime parsing (``today``, ``tomorrow``, ``in 2 hours``, ...)."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.resolver.tokens import IN_N_RE, TIME_RE, TRAILING_TIME_RE, WEEKDAYS
from app.resolver.util import safe_combine

# Default time-of-day when a phrase names a day but no explicit time.
DEFAULT_TIME = time(9, 0)
TONIGHT_TIME = time(20, 0)


@dataclass(frozen=True)
class RelativeMatch:
    """Outcome of a relative-phrase parse.

    ``alternates`` carries other reasonable interpretations (e.g. "monday" can
    mean this Monday or next Monday). ``time_explicit`` is True when the phrase
    contained an explicit time token; otherwise the resolver applied a default.
    """

    primary: datetime
    alternates: tuple[datetime, ...] = ()
    time_explicit: bool = False


def parse_relative(text: str, now: datetime, tz: ZoneInfo) -> RelativeMatch | None:
    """Parse ``text`` against ``now`` (already in ``tz``)."""

    in_match = IN_N_RE.match(text)
    if in_match:
        n = int(in_match["n"])
        unit = in_match["unit"]
        delta = _unit_delta(n, unit)
        # Anchor arithmetic in UTC so DST transitions don't silently swallow or
        # add an hour: ``now + 24h`` should always advance 24 absolute hours.
        target = (now.astimezone(timezone.utc) + delta).astimezone(tz)
        return RelativeMatch(primary=target, time_explicit=True)

    split = _split_trailing_time(text)
    if split is None:
        # Text matched a time-shape but the value was out of range; treat the
        # whole phrase as unresolvable rather than silently clamping.
        return None
    head, parsed_time = split
    chosen_time = parsed_time if parsed_time is not None else DEFAULT_TIME
    explicit = parsed_time is not None
    today = now.date()

    if head == "" and parsed_time is not None:
        # Bare time like "15:30" or "3pm": today's upcoming instance, or
        # tomorrow if that time has already passed.
        candidate = safe_combine(today, parsed_time, tz)
        if candidate <= now:
            candidate = safe_combine(today + timedelta(days=1), parsed_time, tz)
        return RelativeMatch(primary=candidate, time_explicit=True)

    if head == "today":
        return RelativeMatch(
            primary=safe_combine(today, chosen_time, tz), time_explicit=explicit
        )
    if head == "tonight":
        target_time = parsed_time if parsed_time is not None else TONIGHT_TIME
        return RelativeMatch(
            primary=safe_combine(today, target_time, tz), time_explicit=explicit
        )
    if head == "tomorrow":
        return RelativeMatch(
            primary=safe_combine(today + timedelta(days=1), chosen_time, tz),
            time_explicit=explicit,
        )
    if head == "yesterday":
        return RelativeMatch(
            primary=safe_combine(today - timedelta(days=1), chosen_time, tz),
            time_explicit=explicit,
        )

    if head.startswith("next ") and head[5:] in WEEKDAYS:
        target_dow = WEEKDAYS[head[5:]]
        days_ahead = ((target_dow - today.weekday()) % 7) or 7
        return RelativeMatch(
            primary=safe_combine(today + timedelta(days=days_ahead), chosen_time, tz),
            time_explicit=explicit,
        )

    if head.startswith("this ") and head[5:] in WEEKDAYS:
        target_dow = WEEKDAYS[head[5:]]
        days_ahead = (target_dow - today.weekday()) % 7
        primary = safe_combine(today + timedelta(days=days_ahead), chosen_time, tz)
        # "this monday" said on Tuesday almost always means *next* Monday.
        alt = safe_combine(today + timedelta(days=days_ahead + 7), chosen_time, tz)
        return RelativeMatch(primary=primary, alternates=(alt,), time_explicit=explicit)

    if head in WEEKDAYS:
        target_dow = WEEKDAYS[head]
        days_ahead = (target_dow - today.weekday()) % 7
        primary = safe_combine(today + timedelta(days=days_ahead), chosen_time, tz)
        alt = safe_combine(today + timedelta(days=days_ahead + 7), chosen_time, tz)
        return RelativeMatch(primary=primary, alternates=(alt,), time_explicit=explicit)

    return None


def _split_trailing_time(text: str) -> tuple[str, time | None] | None:
    """Strip a trailing ``[at] HH(:MM)?(am|pm)?`` clause from ``text``.

    Returns ``(head, parsed_time)`` when a clause is present (or ``(text, None)``
    when not), and ``None`` when a clause was detected but the time was out of
    range. Falls through cleanly if the trailing token is purely numeric (e.g.
    a date like ``2026-05-16`` shouldn't be misread as a time).
    """

    if " " not in text and "at" not in text:
        # Bare token — could itself be a time like "3pm" or "15:30".
        bare = TIME_RE.fullmatch(text)
        if bare and (bare["ap"] or ":" in text):
            t = _to_time(int(bare["h"]), int(bare["m"]) if bare["m"] else 0, bare["ap"])
            if t is None:
                return None
            return ("", t)
        return (text, None)

    match = TRAILING_TIME_RE.search(text)
    if not match:
        return (text, None)

    # Require an explicit am/pm or minute separator so we don't clip "in 3 days".
    if not match["ap"] and not match["m"]:
        return (text, None)

    t = _to_time(int(match["h"]), int(match["m"]) if match["m"] else 0, match["ap"])
    if t is None:
        return None
    head = text[: match.start()].rstrip()
    return (head, t)


def _to_time(hour: int, minute: int, ampm: str | None) -> time | None:
    """Normalize ``(hour, minute, ampm)`` into a ``time``.

    Returns ``None`` for out-of-range values instead of silently clamping —
    callers treat that as "not a time" so the whole phrase fails to resolve.
    """

    if ampm:
        ampm = ampm.lower()
        if hour < 1 or hour > 12:
            return None
        if ampm == "am":
            if hour == 12:
                hour = 0
        else:  # pm
            if hour != 12:
                hour += 12
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return time(hour, minute)


def _unit_delta(n: int, unit: str) -> timedelta:
    if unit.startswith("minute"):
        return timedelta(minutes=n)
    if unit.startswith("hour"):
        return timedelta(hours=n)
    if unit.startswith("day"):
        return timedelta(days=n)
    if unit.startswith("week"):
        return timedelta(weeks=n)
    raise ValueError(f"unknown unit: {unit}")
