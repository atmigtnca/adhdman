"""Deterministic, offline datetime resolver for free-form text.

The resolver maps natural-language datetime phrases to timezone-aware ISO8601
strings. It is pure: every call is determined by ``(text, now, tz)`` and never
touches the network, the database, or the wall clock.
"""

from app.resolver.resolver import (
    InvalidTimezoneError,
    ResolveResult,
    resolve,
)

__all__ = ["InvalidTimezoneError", "ResolveResult", "resolve"]
