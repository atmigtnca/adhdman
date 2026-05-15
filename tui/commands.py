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


Command = Union[
    Noop, Capture, Today, Inbox, Tasks, Events, Done, Undo, Search, Pick,
    Resolve, Help, Quit, Unknown,
]


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
  /help              show this help
  /quit              exit (Ctrl+C also works)
"""
