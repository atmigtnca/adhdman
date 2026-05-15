"""Frozen non-shaming copy strings for Phase 6 execution helpers.

These constants are shared by the API, TUI, and web dashboard so the wording
stays identical across surfaces. The strings are deliberately calm, supportive,
and free of blame/streak/quantity language. ``backend/tests/test_copy.py``
enforces the lint contract.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping


EMPTY_FOCUS: str = "No focus session right now. That is fine."

FOCUS_CONFLICT: str = (
    "A focus session is already running. Keep it, or replace it on purpose."
)

BLOCK_RESET_PROMPT: str = (
    "Stuck is information, not failure. Pick one: shrink, swap, skip, park."
)

STUCK_OPTIONS: Mapping[str, str] = MappingProxyType(
    {
        "shrink": "Shrink: break this into a smaller next step.",
        "swap": "Swap: set this aside and pick something else.",
        "skip": "Skip: push this forward by a day. It will come back.",
        "park": "Park: keep it safe and hide it from today until you return.",
    }
)

BODY_DOUBLE_START: str = "Sitting with you. We will check in at the chosen pace."

BODY_DOUBLE_CHECK_IN: str = "Still here. Want to keep going, pause, or wrap up?"

BODY_DOUBLE_STOP: str = "Session closed. Thanks for sitting together."

SURVIVAL_ENTER: str = (
    "Survival mode on. We will show one task and one event. Everything else is safe."
)

SURVIVAL_EXIT: str = "Survival mode off. The rest of your items are back when ready."

MVS_PROMPT: str = "Smallest next step. Pick it, or ask for another."

BREAKDOWN_PROMPT: str = (
    "Break this into two to five small steps. Keep each one doable in one sitting."
)


ALL_STRINGS: tuple[str, ...] = (
    EMPTY_FOCUS,
    FOCUS_CONFLICT,
    BLOCK_RESET_PROMPT,
    BODY_DOUBLE_START,
    BODY_DOUBLE_CHECK_IN,
    BODY_DOUBLE_STOP,
    SURVIVAL_ENTER,
    SURVIVAL_EXIT,
    MVS_PROMPT,
    BREAKDOWN_PROMPT,
    *STUCK_OPTIONS.values(),
)


FORBIDDEN_TOKENS: tuple[str, ...] = (
    "forgot",
    "forget",
    "failed",
    "failure to",
    "lazy",
    "only finished",
    "you missed",
    "missed it",
    "you should have",
    "should've",
    "behind schedule",
    "streak",
    "punish",
)
