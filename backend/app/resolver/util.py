"""Shared helpers for the deterministic resolver."""

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo


def safe_combine(d: date, t: time, tz: ZoneInfo) -> datetime:
    """Combine a date+time in ``tz`` while skipping over DST gaps.

    Spring-forward transitions create wall times that do not exist locally
    (e.g. 2026-03-08 02:30 in America/Los_Angeles). Naively attaching ``tz`` to
    such a time yields a datetime whose ``isoformat()`` advertises an offset
    that no clock will ever show. We instead snap forward to the same absolute
    instant expressed with the post-transition offset, so the emitted ISO
    string always corresponds to a real local moment.
    """

    naive = datetime.combine(d, t)
    aware = naive.replace(tzinfo=tz)
    roundtrip = aware.astimezone(timezone.utc).astimezone(tz)
    if roundtrip.replace(tzinfo=None) != naive:
        return roundtrip
    return aware
