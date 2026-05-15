"""Pure resolver tests. ``now`` is always frozen so output is deterministic."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.resolver import InvalidTimezoneError, resolve


LA = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")
# A Saturday at 09:00 local — picked so weekday math is easy to reason about.
NOW_LA = datetime(2026, 5, 16, 9, 0, tzinfo=LA)


def test_iso_datetime_with_offset_is_absolute() -> None:
    result = resolve("2026-06-01T15:30:00-07:00", now=NOW_LA, tz=LA)

    assert result.kind == "absolute"
    assert result.confidence >= 0.95
    assert result.starts_at == "2026-06-01T15:30:00-07:00"
    assert result.alternates == ()


def test_iso_datetime_without_offset_uses_caller_tz() -> None:
    result = resolve("2026-06-01 15:30", now=NOW_LA, tz=LA)

    assert result.kind == "absolute"
    assert result.starts_at == "2026-06-01T15:30:00-07:00"


def test_iso_date_only_anchors_to_midnight_local() -> None:
    result = resolve("2026-06-01", now=NOW_LA, tz=LA)

    assert result.kind == "absolute"
    assert result.starts_at == "2026-06-01T00:00:00-07:00"


def test_today_with_default_time() -> None:
    result = resolve("today", now=NOW_LA, tz=LA)

    assert result.kind == "relative"
    assert result.starts_at == "2026-05-16T09:00:00-07:00"


def test_tomorrow_at_3pm() -> None:
    result = resolve("tomorrow at 3pm", now=NOW_LA, tz=LA)

    assert result.kind == "relative"
    assert result.starts_at == "2026-05-17T15:00:00-07:00"
    assert result.confidence >= 0.85


def test_tomorrow_3pm_without_at_word() -> None:
    result = resolve("tomorrow 3pm", now=NOW_LA, tz=LA)

    assert result.starts_at == "2026-05-17T15:00:00-07:00"


def test_tonight_uses_evening_default() -> None:
    result = resolve("tonight", now=NOW_LA, tz=LA)

    assert result.starts_at == "2026-05-16T20:00:00-07:00"


def test_tonight_with_explicit_time_overrides_default() -> None:
    result = resolve("tonight at 9:30pm", now=NOW_LA, tz=LA)

    assert result.starts_at == "2026-05-16T21:30:00-07:00"


def test_in_n_minutes() -> None:
    result = resolve("in 45 minutes", now=NOW_LA, tz=LA)

    assert result.kind == "relative"
    assert result.starts_at == "2026-05-16T09:45:00-07:00"


def test_in_n_hours_crossing_midnight() -> None:
    base = datetime(2026, 5, 16, 23, 0, tzinfo=LA)

    result = resolve("in 2 hours", now=base, tz=LA)

    assert result.starts_at == "2026-05-17T01:00:00-07:00"


def test_in_24_hours_across_dst_spring_forward() -> None:
    # 2026 US DST starts on Sunday March 8 at 02:00 local — clocks jump to 03:00.
    base = datetime(2026, 3, 7, 12, 0, tzinfo=LA)

    result = resolve("in 24 hours", now=base, tz=LA)

    # Wall-clock perspective: same hour next day, but offset shifts -08 -> -07.
    assert result.kind == "relative"
    assert result.starts_at is not None
    parsed = datetime.fromisoformat(result.starts_at)
    assert parsed - base == timedelta(hours=24)
    assert parsed.utcoffset() == timedelta(hours=-7)


def test_next_monday_advances_a_full_week_when_today_is_that_weekday() -> None:
    monday = datetime(2026, 5, 18, 9, 0, tzinfo=LA)  # Monday

    result = resolve("next monday", now=monday, tz=LA)

    assert result.starts_at == "2026-05-25T09:00:00-07:00"


def test_next_monday_when_today_is_saturday() -> None:
    result = resolve("next monday", now=NOW_LA, tz=LA)

    assert result.starts_at == "2026-05-18T09:00:00-07:00"


def test_bare_weekday_returns_primary_and_alternate() -> None:
    # Saturday -> nearest "monday" is two days out; alternate is +7.
    result = resolve("monday", now=NOW_LA, tz=LA)

    assert result.starts_at == "2026-05-18T09:00:00-07:00"
    assert result.alternates == ("2026-05-25T09:00:00-07:00",)


def test_bare_weekday_with_time() -> None:
    result = resolve("friday at 5pm", now=NOW_LA, tz=LA)

    # Saturday -> next Friday is 6 days away.
    assert result.starts_at == "2026-05-22T17:00:00-07:00"
    assert result.confidence >= 0.85


def test_no_match_returns_kind_none() -> None:
    result = resolve("dentist appointment", now=NOW_LA, tz=LA)

    assert result.kind == "none"
    assert result.confidence == 0.0
    assert result.starts_at is None
    assert result.ends_at is None


def test_empty_text_returns_none() -> None:
    result = resolve("   ", now=NOW_LA, tz=LA)

    assert result.kind == "none"


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError):
        resolve("today", now=datetime(2026, 5, 16, 9, 0), tz=LA)


def test_invalid_timezone_string_raises() -> None:
    with pytest.raises(InvalidTimezoneError):
        resolve("today", now=NOW_LA, tz="Not/A_Zone")


@pytest.mark.parametrize("bad", ["", "../etc/passwd", "Foo\x00Bar"])
def test_malformed_timezone_key_raises_invalid_timezone(bad: str) -> None:
    # ZoneInfo rejects these with ValueError before lookup; load_timezone must
    # translate that into the same InvalidTimezoneError the endpoint expects.
    with pytest.raises(InvalidTimezoneError):
        resolve("today", now=NOW_LA, tz=bad)


def test_bare_time_resolves_to_today_when_upcoming() -> None:
    result = resolve("3pm", now=NOW_LA, tz=LA)

    assert result.kind == "relative"
    assert result.starts_at == "2026-05-16T15:00:00-07:00"


def test_bare_time_with_minutes_resolves_to_today_when_upcoming() -> None:
    result = resolve("15:30", now=NOW_LA, tz=LA)

    assert result.starts_at == "2026-05-16T15:30:00-07:00"


def test_bare_time_rolls_to_tomorrow_when_already_past() -> None:
    # NOW_LA is 09:00 — "8am" today has passed, so tomorrow.
    result = resolve("8am", now=NOW_LA, tz=LA)

    assert result.starts_at == "2026-05-17T08:00:00-07:00"


def test_bare_time_equal_to_now_rolls_to_tomorrow() -> None:
    # Treat "now" itself as already passed so the user-facing time always moves
    # forward.
    result = resolve("9am", now=NOW_LA, tz=LA)

    assert result.starts_at == "2026-05-17T09:00:00-07:00"


def test_invalid_bare_time_returns_none_not_clamped() -> None:
    result = resolve("25:99", now=NOW_LA, tz=LA)

    assert result.kind == "none"
    assert result.starts_at is None


def test_invalid_trailing_time_returns_none_not_clamped() -> None:
    result = resolve("tomorrow at 25:99", now=NOW_LA, tz=LA)

    assert result.kind == "none"


def test_invalid_ampm_hour_returns_none() -> None:
    result = resolve("tomorrow at 13pm", now=NOW_LA, tz=LA)

    assert result.kind == "none"


def test_dst_spring_forward_relative_shifts_forward() -> None:
    # 2026-03-08 02:30 LA is in the spring-forward gap; resolver must not emit
    # a wall time that no clock will ever show. Shift to the same UTC instant
    # expressed with the post-transition offset (03:30 PDT).
    base = datetime(2026, 3, 7, 22, 0, tzinfo=LA)

    result = resolve("tomorrow at 2:30am", now=base, tz=LA)

    assert result.kind == "relative"
    assert result.starts_at == "2026-03-08T03:30:00-07:00"


def test_dst_spring_forward_absolute_shifts_forward() -> None:
    result = resolve("2026-03-08 02:30", now=NOW_LA, tz=LA)

    assert result.kind == "absolute"
    assert result.starts_at == "2026-03-08T03:30:00-07:00"


def test_dst_spring_forward_bare_time_shifts_forward() -> None:
    # Bare "2:30am" said the night before the transition still rolls forward to
    # tomorrow at 02:30 — which doesn't exist locally and must be shifted.
    base = datetime(2026, 3, 7, 22, 0, tzinfo=LA)

    result = resolve("2:30am", now=base, tz=LA)

    assert result.starts_at == "2026-03-08T03:30:00-07:00"


def test_resolver_is_deterministic() -> None:
    a = resolve("tomorrow at 3pm", now=NOW_LA, tz=LA)
    b = resolve("tomorrow at 3pm", now=NOW_LA, tz=LA)

    assert a == b


def test_resolver_uses_caller_tz_for_localization() -> None:
    # 09:00 in LA is 16:00 UTC; "today" in UTC should anchor to UTC midnight.
    now_utc = NOW_LA.astimezone(UTC)

    result = resolve("today", now=now_utc, tz=UTC)

    assert result.starts_at == "2026-05-16T09:00:00+00:00"
