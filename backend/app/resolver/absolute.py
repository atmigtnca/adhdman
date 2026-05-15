"""Absolute (ISO8601 / ``YYYY-MM-DD HH:MM``) datetime parsing."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.resolver.tokens import ISO_DATE_RE, ISO_DATETIME_RE
from app.resolver.util import safe_combine


def parse_absolute(text: str, tz: ZoneInfo) -> datetime | None:
    """Parse ``text`` as an absolute datetime expression.

    Returns a timezone-aware ``datetime`` or ``None`` if no absolute form
    matches. Naive inputs are localized to ``tz``; wall times that fall inside
    a DST gap are shifted forward via :func:`safe_combine`.
    """

    iso_match = ISO_DATETIME_RE.match(text)
    if iso_match:
        year, month, day = (int(p) for p in iso_match["date"].split("-"))
        hour = int(iso_match["h"])
        minute = int(iso_match["m"])
        second = int(iso_match["s"]) if iso_match["s"] else 0
        tz_part = iso_match["tz"]
        try:
            naive = datetime(year, month, day, hour, minute, second)
        except ValueError:
            return None
        if tz_part is None:
            return safe_combine(naive.date(), naive.time(), tz)
        return _attach_offset(naive, tz_part)

    date_match = ISO_DATE_RE.match(text)
    if date_match:
        year, month, day = (int(p) for p in date_match.groups())
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        return safe_combine(d, time(0, 0), tz)

    return None


def _attach_offset(naive: datetime, tz_token: str) -> datetime:
    """Attach an explicit ``Z`` / ``+HH:MM`` offset to a naive datetime."""

    from datetime import timedelta, timezone

    if tz_token in ("Z", "z"):
        return naive.replace(tzinfo=timezone.utc)
    sign = 1 if tz_token[0] == "+" else -1
    body = tz_token[1:].replace(":", "")
    hours = int(body[:2])
    minutes = int(body[2:4]) if len(body) >= 4 else 0
    offset = timedelta(hours=hours, minutes=minutes) * sign
    return naive.replace(tzinfo=timezone(offset))
