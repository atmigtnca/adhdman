# Phase 4 TUI Command Center Implementation Plan

**Goal:** Give ADHDman a primary input surface — a Textual TUI that runs locally and talks to the FastAPI backend over `127.0.0.1`. The TUI is a thin client: it captures one-line input, renders the "one thing now" view, and exposes a small command vocabulary for inbox triage, task/event review, completion, undo, search, and datetime resolution. All persistence stays in the backend; the TUI owns presentation, input parsing, and number-selection UX.

**Architecture:** A standalone Python package `tui/` (peer to `backend/`) launched via `python -m tui` or a `make tui` target. The TUI uses Textual for layout/widgets and `httpx` (sync, in a worker thread) for backend calls. Three-pane layout: **Now** (top, "one thing"), **Log** (middle, append-only action/result history), **Input** (bottom, single-line capture + command bar). All commands map 1:1 to existing FastAPI endpoints from Phases 1–3; the TUI introduces no new server endpoints.

**Tech Stack:** Python 3.11, Textual (>=0.60), httpx, pytest, `pytest-asyncio`, Textual's `App.run_test()` snapshot harness. No new backend dependencies.

---

## Product Constraints

- Capture-first: a non-empty input line that is not a recognized command is sent verbatim to `POST /capture`. The TUI never silently drops input.
- Recovery-first: every mutating command shows the returned `action_id` in the Log pane and offers `/undo` as the next reachable command.
- One-thing principle: the Now pane shows exactly one item from `GET /today`. Lists are rendered on demand in the Log pane, never permanently in Now.
- Non-shaming tone: empty states and errors render the backend's calm messages verbatim; the TUI does not editorialize.
- Single-user only: no login screen, no profile picker, no `user_id` field anywhere in the TUI. The client cannot send a user identifier even by accident.
- Local-only: the TUI defaults to `http://127.0.0.1:8000` and refuses non-loopback hosts unless `ADHDMAN_ALLOW_REMOTE=1` is set (escape hatch for SSH-tunneled use, documented but off by default).
- Number selection over fuzzy text: when the user must pick a row, the TUI renders a numbered list and accepts `1`–`9` (or `pick 1`); free-form text never reaches a mutating endpoint as a target selector — it goes through `/search` first.

## Scope

In scope:

- Textual app with Now / Log / Input layout.
- One-line capture: any non-`/`-prefixed input is sent to `POST /capture`.
- Command set: `/today`, `/inbox`, `/tasks`, `/events`, `/done`, `/undo`, `/search`, `/resolve`, plus `/help` and `/quit`.
- Number-selection UX for ambiguous mutations (done/undo on a chosen row, picking from `/search` results).
- HTTP client wrapper with timeout, error surfacing, and a single shared base URL.
- Tests: command parser, HTTP client (against a stub), TUI smoke tests via `App.run_test()`.

Out of scope (deferred):

- SSE / live refresh — Now pane refreshes only on user action or `/today`. Phase 5+.
- Web dashboard.
- Editing tasks/events from inside the TUI beyond `/done` (PATCH stays curl-only this phase).
- Theming, mouse interaction beyond Textual defaults, multiple panes per resource.
- Packaging as a standalone binary; `python -m tui` is enough.
- Auth, accounts, remote deployment.

## Layout

```
┌────────────────────────────────────────────────────────────┐
│ Now                                                        │
│   [task #7]  call dentist                                  │
│   open tasks: 3   inbox: 2                                 │
├────────────────────────────────────────────────────────────┤
│ Log                                                        │
│   12:04  /today      → one thing: task #7                  │
│   12:05  capture     "pay rent" → inbox #14                │
│   12:05  /inbox      → 2 items (1) #13 milk  (2) #14 rent  │
│   12:06  /done 1     → task #11 done   (action #42, /undo) │
├────────────────────────────────────────────────────────────┤
│ > _                                                        │
└────────────────────────────────────────────────────────────┘
```

- **Now** (`Static` widget bound to a reactive `today_state`): re-rendered after every command that could change `/today`. Shows the `one_thing` payload and counts; renders the backend's empty-state message verbatim when nothing is waiting.
- **Log** (`RichLog` or `VerticalScroll` of `Static`): append-only, timestamped, scrollback-only. Each entry: timestamp, command (or `capture`), one-line summary, and — for mutating commands — the `action_id` plus a hint that `/undo` will revert it. The Log is the recovery surface; nothing about it should be hidden behind menus.
- **Input** (`Input` widget with prompt `>`): single line. Enter submits. Up/Down recalls history (in-memory only, not persisted).

## Command Set

All commands are case-insensitive, leading-slash required, parsed by a small `parse_command(line: str) -> Command` function. Anything that does not start with `/` is a capture.

| Command | Backend call | UX notes |
|---|---|---|
| `/today` | `GET /today` | Refreshes Now pane; logs the one-line summary. |
| `/inbox` | `GET /inbox` | Renders a numbered list in Log. The list becomes the active selection set for the next `/done`/`/undo`-style numeric pick if the next command is a bare number → treat as "promote inbox N". Use explicit `/promote N` if ambiguity bites in testing. |
| `/tasks` | `GET /tasks` | Numbered list; bare `N` after `/tasks` means "complete task N" (i.e. it routes through `/done N`). Decision: keep it explicit — bare numbers always require a preceding list command of the same resource. |
| `/events` | `GET /events` | Numbered list; read-only in Phase 4 (no `PATCH` from TUI yet). |
| `/done N` | `POST /tasks/{id}/done` | `N` indexes into the most recent `/tasks` listing in the same session. If no `/tasks` listing exists yet, the TUI runs `/tasks` first, shows the list, and prompts the user to retype `/done N`. Never auto-select. |
| `/undo` / `/undo N` | `POST /undo/latest` or `POST /undo/{action_id}` | Bare `/undo` calls `/undo/latest`. `/undo N` undoes the action whose id is `N` (taken from a Log line, not from a list index — undo is rare enough that explicit ids are clearer). |
| `/search <query>` | `POST /search` | Renders candidates as a numbered list; `pick N` (or bare `N` immediately after) routes the chosen id into the next mutating command the user types. To keep number-pick semantics simple, candidates are stored on a `last_selection` reactive and the user types e.g. `/done` (no number) to apply to the picked row. Document this clearly in `/help`. |
| `/resolve <text>` | `POST /resolve` (with `tz` from `LOCAL_TIMEZONE`) | Read-only; logs the resolved timestamp(s). Useful before manually editing an event via curl. |
| `/help` | — | Local cheat-sheet. |
| `/quit` | — | Exits the app. Ctrl+C also works. |

### Number-selection UX

The single rule: **a bare number is meaningful only in the context of the most recent listing or search result, and it never directly mutates without an explicit verb.**

State machine:

1. User runs a list command (`/inbox`, `/tasks`, `/events`, `/search …`). The TUI stores `last_listing = { kind, items: [(n, type, id, title), …] }` in app state.
2. User types a verb that needs a target (`/done`, future `/promote`, future `/cancel`):
   - If the verb is given a number (`/done 2`), the TUI looks `2` up in `last_listing` *of a compatible kind* and uses that id.
   - If the verb is given no number, the TUI uses `last_selection` (set by `pick N` after a `/search`).
   - If neither is set, the TUI logs a non-shaming hint: "Run /tasks first, then /done N." and does nothing.
3. The TUI never accepts free-form text as a target selector for any mutating call. Free-form text always goes through `/search` first.

This keeps the implementation small and matches the Phase 3 server contract that mutations require an explicit id.

## HTTP Client

`tui/client.py`:

- One `httpx.Client` constructed at app start with `base_url`, `timeout=5.0`, no auth headers ever.
- A guard at construction: parse `base_url`; if the host is not in `{127.0.0.1, localhost, ::1}` and `ADHDMAN_ALLOW_REMOTE` is not `1`, raise immediately with a clear message. This protects users from accidentally pointing a TUI at a shared server.
- Thin typed wrappers per endpoint (`get_today()`, `capture(text)`, `list_inbox()`, `list_tasks()`, `list_events()`, `complete_task(id)`, `undo_latest()`, `undo(id)`, `search(q)`, `resolve(text)`).
- All HTTP calls run inside Textual `@work(thread=True)` workers so the UI never blocks.
- Errors are caught at the worker boundary and rendered as a single Log line; the TUI never raises a stack trace into the UI.

## Configuration

Read from environment with sensible defaults; no new files unless the user opts in.

```bash
ADHDMAN_BASE_URL=http://127.0.0.1:8000
ADHDMAN_TIMEZONE=                # falls back to backend's LOCAL_TIMEZONE if unset
ADHDMAN_ALLOW_REMOTE=0           # set to 1 only when tunneling
```

`.env.example` (root) gains these placeholder lines. The TUI does **not** load `.env` itself — environment is the contract. Document a one-liner like `set -a; . ./.env; set +a` in the README.

## Module Layout

New package `tui/` peer to `backend/`:

```
tui/
  __init__.py
  __main__.py        # python -m tui entry point
  app.py             # TuiApp(App) — layout, key bindings, command dispatch
  commands.py        # parse_command(line) -> Command dataclass; verb table
  state.py           # AppState dataclass: today, last_listing, last_selection, history
  client.py          # httpx wrapper + loopback guard
  rendering.py       # pure functions: render_today(), render_listing(), render_log_line()
  tests/
    test_commands.py
    test_client.py
    test_state.py
    test_app_smoke.py
```

`rendering.py` returns Textual `RenderableType` (or plain strings) — keeping rendering pure makes it directly testable without spinning up an `App`.

## Testing Strategy

All TUI tests run offline. The backend is never started for unit tests; instead `client.py` is mocked or pointed at `httpx.MockTransport`.

- **Command parser tests** (`test_commands.py`):
  - `/today`, `/inbox`, etc. parse to the right `Command` variants.
  - Bare text (no leading `/`) parses to `Capture(text=…)`.
  - Whitespace-only input parses to `Noop` (and the TUI ignores it).
  - `/done 2` parses with target index `2`; `/done` parses with no index.
  - Case-insensitivity: `/Today` works.
  - Unknown command (`/wat`) parses to `Unknown` and the TUI logs a non-shaming hint.

- **State tests** (`test_state.py`):
  - After `/tasks` populates `last_listing`, `/done 2` resolves to the right task id.
  - `/done 99` (out of range) returns a clear error without calling the backend.
  - `pick 1` after `/search` populates `last_selection`; subsequent verbless `/done` uses it.
  - `last_listing` is replaced (not merged) by each new listing command.

- **Client tests** (`test_client.py`):
  - Each wrapper method calls the right method+path with the right JSON.
  - The loopback guard rejects `http://example.com:8000` unless `ADHDMAN_ALLOW_REMOTE=1`.
  - HTTP error responses are surfaced as a typed `ClientError` (no stack traces leak).
  - Timeouts are caught and rendered as a non-shaming Log line.
  - `httpx.MockTransport` is used; no real network.

- **App smoke tests** (`test_app_smoke.py`) using `App.run_test()`:
  - App boots, Now pane renders the seeded `/today` payload (mocked).
  - Typing `pay rent` and pressing Enter triggers `capture(...)` and appends a Log line.
  - Typing `/tasks` followed by `/done 1` calls `complete_task(<correct id>)`.
  - `/undo` after a mutating command calls `/undo/latest` and refreshes Now.
  - These tests assert on observable widget contents, not on internal call order.

- **Negative tests**:
  - `/done 1` with no prior `/tasks` listing: no HTTP call, Log shows the prompt.
  - `/search` returning zero candidates: Log shows the empty-state message verbatim.
  - Backend returning 409 (e.g. undo on already-undone action): Log shows the backend message; Now is unchanged.

- **Regression tests**: full Phase 1 + 2 + 3 backend suite must still pass unchanged. Phase 4 introduces no backend changes.

Tests must never bind to a port and must never hit the real backend.

## Commit Boundaries

1. `docs: add phase 4 tui command center plan`
2. `feat: add tui package skeleton and entry point`
3. `feat: add tui http client with loopback guard`
4. `feat: add tui command parser and state`
5. `feat: add tui textual app with now/log/input layout`
6. `feat: wire capture and listing commands`
7. `feat: wire done, undo, search, resolve commands`
8. `feat: add tui smoke tests`
9. `docs: document tui usage and commands`

Refactor or test-only commits are allowed if they fall out naturally. Each commit must leave the full test suite (backend + tui) green.

## Review Gates

Before pushing implementation commits:

1. Run targeted tests for the changed behavior.
2. Run the full test suite (`python -m pytest backend/tests tui/tests -q`); no test may hit the network.
3. Inspect `git diff` for scope creep, accidental auth/multi-user concepts, hard-coded local paths, committed secrets, or new backend endpoints (there should be none).
4. Confirm the loopback guard is exercised by at least one test and is not bypassable through a relative URL.
5. Confirm no mutating command path can reach the backend with a free-form text target — every mutating wrapper takes an `int` id.
6. Confirm the TUI degrades gracefully when the backend is down (timeout → calm Log line, no crash).
7. Verify the Now pane never grows: rendering should always be a single-screen summary.

## Out of Scope for Phase 4

- login, auth, accounts, sessions, roles
- multi-user, `user_id`, permissions
- remote deployment, public exposure, packaging as a binary
- SSE / live refresh / websocket push
- web dashboard
- editing tasks/events from the TUI (PATCH stays out; only `/done` and `/undo` mutate)
- promotion-from-inbox UX (revisit in Phase 5 once number selection is proven)
- LLM-driven command parsing — Phase 4 parser is rule-based
- color theming, mouse-driven interactions beyond Textual defaults
- persisted command history across sessions
- multi-pane per-resource views, modal dialogs, popups
- keybinding customization
