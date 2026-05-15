"""Regex and keyword tables shared by the deterministic resolver."""

import re

WEEKDAYS: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# YYYY-MM-DD
ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# YYYY-MM-DD[ T]HH:MM[:SS] with optional offset (Z or +HH:MM / +HHMM).
ISO_DATETIME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ tT]"
    r"(?P<h>\d{2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)

# "in N (minutes|hours|days|weeks)"; singular and plural both accepted.
IN_N_RE = re.compile(
    r"^in\s+(?P<n>\d+)\s+(?P<unit>minute|minutes|hour|hours|day|days|week|weeks)$"
)

# "at HH(:MM)?(am|pm)?" — used both standalone and as a trailing clause.
TIME_RE = re.compile(
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?",
    re.IGNORECASE,
)

# Trailing time clause: optional "at " prefix, then a TIME_RE match.
TRAILING_TIME_RE = re.compile(
    r"(?:\s+at)?\s+(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?$",
    re.IGNORECASE,
)
