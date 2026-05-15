from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class Noop:
    pass


@dataclass(frozen=True)
class Capture:
    text: str


@dataclass(frozen=True)
class Today:
    pass


@dataclass(frozen=True)
class Inbox:
    pass


@dataclass(frozen=True)
class Tasks:
    pass


@dataclass(frozen=True)
class Events:
    pass


@dataclass(frozen=True)
class Done:
    index: int | None  # 1-based; None means "use last_selection"


@dataclass(frozen=True)
class Undo:
    action_id: int | None  # None means /undo/latest


@dataclass(frozen=True)
class Search:
    query: str


@dataclass(frozen=True)
class Pick:
    index: int


@dataclass(frozen=True)
class Resolve:
    text: str


@dataclass(frozen=True)
class Help:
    pass


@dataclass(frozen=True)
class Quit:
    pass


@dataclass(frozen=True)
class Unknown:
    raw: str


# ----- Phase 6 execution helper commands ---------------------------------


@dataclass(frozen=True)
class FocusStart:
    index: int  # 1-based index into last_listing


@dataclass(frozen=True)
class FocusStop:
    pass


@dataclass(frozen=True)
class FocusCurrent:
    pass


@dataclass(frozen=True)
class BreakdownSuggest:
    index: int  # 1-based; resolved against last_listing (tasks)


@dataclass(frozen=True)
class BreakdownCommit:
    pass


@dataclass(frozen=True)
class StuckOptions:
    pass


@dataclass(frozen=True)
class StuckApply:
    choice: str  # "shrink" | "swap" | "skip" | "park"


@dataclass(frozen=True)
class BodyDoubleStart:
    interval_seconds: int | None


@dataclass(frozen=True)
class BodyDoubleStop:
    pass


@dataclass(frozen=True)
class BodyDoubleCheckIn:
    pass


@dataclass(frozen=True)
class BodyDoubleCurrent:
    pass


@dataclass(frozen=True)
class MVSSuggest:
    index: int  # 1-based; resolved against last_listing (task or inbox)


@dataclass(frozen=True)
class MVSCommit:
    pass


@dataclass(frozen=True)
class SurvivalOn:
    pass


@dataclass(frozen=True)
class SurvivalOff:
    pass


@dataclass(frozen=True)
class SurvivalStatus:
    pass


Command = Union[
    Noop, Capture, Today, Inbox, Tasks, Events, Done, Undo, Search, Pick,
    Resolve, Help, Quit, Unknown,
    FocusStart, FocusStop, FocusCurrent,
    BreakdownSuggest, BreakdownCommit,
    StuckOptions, StuckApply,
    BodyDoubleStart, BodyDoubleStop, BodyDoubleCheckIn, BodyDoubleCurrent,
    MVSSuggest, MVSCommit,
    SurvivalOn, SurvivalOff, SurvivalStatus,
]


_STUCK_CHOICES = {"shrink", "swap", "skip", "park"}


def parse_command(line: str) -> Command:
    """Parse a single user input line into a Command.

    Anything not starting with '/' (after stripping) is a Capture.
    Blank input is Noop. Unknown slash commands return Unknown.
    """
    if line is None:
        return Noop()
    stripped = line.strip()
    if not stripped:
        return Noop()
    if not stripped.startswith("/"):
        return Capture(text=stripped)

    parts = stripped.split(maxsplit=1)
    verb = parts[0][1:].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if verb == "today":
        return Today()
    if verb == "inbox":
        return Inbox()
    if verb == "tasks":
        return Tasks()
    if verb == "events":
        return Events()
    if verb == "done":
        if not rest:
            return Done(index=None)
        try:
            return Done(index=int(rest))
        except ValueError:
            return Unknown(raw=stripped)
    if verb == "undo":
        if not rest:
            return Undo(action_id=None)
        try:
            return Undo(action_id=int(rest))
        except ValueError:
            return Unknown(raw=stripped)
    if verb == "search":
        if not rest:
            return Unknown(raw=stripped)
        return Search(query=rest)
    if verb == "pick":
        try:
            return Pick(index=int(rest))
        except ValueError:
            return Unknown(raw=stripped)
    if verb == "resolve":
        if not rest:
            return Unknown(raw=stripped)
        return Resolve(text=rest)
    if verb == "help":
        return Help()
    if verb in ("quit", "exit"):
        return Quit()
    if verb == "focus":
        if not rest:
            return FocusCurrent()
        low = rest.lower()
        if low == "stop":
            return FocusStop()
        if low == "current":
            return FocusCurrent()
        try:
            return FocusStart(index=int(rest))
        except ValueError:
            return Unknown(raw=stripped)
    if verb == "breakdown":
        if not rest:
            return Unknown(raw=stripped)
        low = rest.lower()
        if low == "commit":
            return BreakdownCommit()
        try:
            return BreakdownSuggest(index=int(rest))
        except ValueError:
            return Unknown(raw=stripped)
    if verb == "stuck":
        if not rest or rest.lower() == "options":
            return StuckOptions()
        choice = rest.lower()
        if choice in _STUCK_CHOICES:
            return StuckApply(choice=choice)
        # accept "apply shrink" style for symmetry
        parts2 = rest.split(maxsplit=1)
        if len(parts2) == 2 and parts2[0].lower() == "apply":
            sub = parts2[1].strip().lower()
            if sub in _STUCK_CHOICES:
                return StuckApply(choice=sub)
        return Unknown(raw=stripped)
    if verb == "body-double":
        if not rest:
            return BodyDoubleCurrent()
        low = rest.lower()
        if low == "stop":
            return BodyDoubleStop()
        if low in ("check-in", "checkin"):
            return BodyDoubleCheckIn()
        if low == "current":
            return BodyDoubleCurrent()
        if low == "start":
            return BodyDoubleStart(interval_seconds=None)
        parts2 = rest.split(maxsplit=1)
        head = parts2[0].lower()
        tail = parts2[1].strip() if len(parts2) > 1 else ""
        if head == "start":
            if not tail:
                return BodyDoubleStart(interval_seconds=None)
            try:
                interval = int(tail)
            except ValueError:
                return Unknown(raw=stripped)
            if interval <= 0:
                return Unknown(raw=stripped)
            return BodyDoubleStart(interval_seconds=interval)
        # bare positive integer => start with that interval
        try:
            interval = int(rest)
        except ValueError:
            return Unknown(raw=stripped)
        if interval <= 0:
            return Unknown(raw=stripped)
        return BodyDoubleStart(interval_seconds=interval)
    if verb == "mvs":
        if not rest:
            return Unknown(raw=stripped)
        low = rest.lower()
        if low == "commit":
            return MVSCommit()
        parts2 = rest.split(maxsplit=1)
        head = parts2[0].lower()
        tail = parts2[1].strip() if len(parts2) > 1 else ""
        if head == "suggest":
            if not tail:
                return Unknown(raw=stripped)
            try:
                return MVSSuggest(index=int(tail))
            except ValueError:
                return Unknown(raw=stripped)
        try:
            return MVSSuggest(index=int(rest))
        except ValueError:
            return Unknown(raw=stripped)
    if verb == "survival":
        low = rest.lower()
        if not rest or low == "status":
            return SurvivalStatus()
        if low == "on":
            return SurvivalOn()
        if low == "off":
            return SurvivalOff()
        return Unknown(raw=stripped)
    return Unknown(raw=stripped)


HELP_TEXT = """\
ADHDman TUI commands (slash-prefixed). Anything else is captured verbatim.

  /today             show the one thing now
  /inbox             list inbox items
  /tasks             list open tasks
  /events            list upcoming events
  /done N            complete task N from the last /tasks listing
  /undo              undo the most recent action
  /undo ID           undo a specific action id (from the Log)
  /search <query>    search across tasks/events/inbox
  /pick N            select candidate N from the last /search
  /resolve <text>    resolve a natural-language datetime

Execution helpers (Phase 6):

  /focus             show current focus session
  /focus N           focus on item N from the last listing
  /focus stop        end the current focus session
  /breakdown N       suggest 2-5 micro-steps for task N (from last /tasks)
  /breakdown commit  persist the last suggestion as child tasks
  /stuck             show the four block-reset options
  /stuck CHOICE      apply shrink|swap|skip|park to last-selected task
  /body-double       show current body-double session
  /body-double N     start a body-double timer with N-second cadence
  /body-double check-in   record a heartbeat
  /body-double stop  end the body-double session
  /mvs N             suggest one minimum-viable step for item N
  /mvs commit        commit the suggested step and focus on it
  /survival on       enter survival mode (one task, one event)
  /survival off      exit survival mode
  /survival          show survival-mode state

  /help              show this help
  /quit              exit (Ctrl+C also works)
"""
