# Phase 6 Execution Helpers Implementation Plan

> **For Hermes:** Use subagent-driven-development style execution: implement one coherent slice at a time, review for spec compliance and public safety, then commit only verified changes.

**Goal:** Add a small, opinionated set of ADHD execution helpers on top of the Phase 1–5 capture/resolve/dashboard core. These helpers do not capture new data — they help the user actually *start*, *continue*, or *survive* when the existing surfaces (inbox, tasks, events, today) feel too heavy. The helpers are state-light, recoverable, and non-shaming.

**Architecture:** Extend the existing FastAPI backend with a thin "execution" module that operates on already-captured rows. A small `focus_sessions` table records optional, transient focus/body-double state so the TUI and web dashboard can render the same "what am I doing right now" view. The TUI gains new slash commands; the web dashboard gains a read-only "Now / Focus" panel. No LLM call is required for the deterministic helpers; the optional breakdown helper reuses the existing OpenRouter provider gated by `OPENROUTER_API_KEY` and falls back to a rules-only suggestion when the key is unset.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Pydantic, Textual (existing TUI), vanilla JS (existing web dashboard), pytest + TestClient. No new top-level dependencies.

---

## Product Constraints

- Local-first, single-user. No login, no `user_id`, no roles, no sessions belonging to "another user". `focus_sessions` rows are implicitly the local user's.
- No external sync. Body-double mode does not call any remote presence service; it is a local timer + render contract.
- Capture-first preserved. Helpers never silently drop input; if a helper rejects input, the raw text falls back to the inbox via the existing capture path.
- Recovery-first. Every mutation goes through the existing `actions` table and is reversible via `/undo`. Breakdown sub-steps are themselves regular `task` rows so they inherit edit/delete/undo.
- One-thing principle. Execution helpers always surface *one* next step; "more" is on demand, never default.
- Non-shaming tone. Empty states, survival-mode copy, and block-reset prompts must read as supportive and calm. No streaks, no scolding, no "you missed".
- Public-safe. No personal names, no real local paths, no real secrets in docs/code. `.env.example` placeholders only.
- Web stays read-only. Phase 6 may *display* focus/survival state on the web dashboard but must not add web-side mutation controls. All starts/stops happen via API or TUI.
- Local-only network posture. Bound to `127.0.0.1`; SSH tunnel/VPN/external access control only.

## Scope

In scope:

- Six execution helpers, in this implementation order:
  1. **one thing** — commit to a single next item (task or inbox item) and surface it everywhere.
  2. **breakdown** — split a task into 2–5 micro-steps stored as child tasks.
  3. **block reset** — a guided "stuck" flow that lowers the bar (shrink, swap, skip, park).
  4. **body double** — a local virtual-presence timer with check-in prompts.
  5. **mvs** — minimum viable step / session: derive the smallest defensible next action.
  6. **survival mode** — a global low-energy flag that filters everything to the bare minimum.
- New API endpoints (read + thin mutations) for each helper.
- One new table `focus_sessions` plus one new column on `tasks` for parent linkage (breakdown).
- TUI commands `/focus`, `/breakdown`, `/stuck`, `/body-double`, `/mvs`, `/survival`.
- Web dashboard: read-only "Focus" panel that reflects current focus/body-double/survival state.
- Pytest coverage for each helper including non-shaming tone smoke checks.

Out of scope:

- Remote/cloud body-double partners. Body double in Phase 6 is local timers only.
- Push notifications, sound, OS-level alerts.
- Multi-user "shared focus".
- Gamification, streaks, XP, badges.
- Calendar sync.
- Auth/account/session/user systems.
- Web-side mutation controls.
- Heavy LLM planning. Breakdown LLM call is optional and bounded.

## Data Model Changes

New table `focus_sessions`:

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `kind` | TEXT NOT NULL | one of `focus`, `body_double`, `survival` |
| `target_type` | TEXT NULL | `task`, `event`, `inbox_item`, or NULL (survival has no target) |
| `target_id` | INTEGER NULL | row id in target table; nullable for `survival` |
| `status` | TEXT NOT NULL | `active`, `ended`, `cancelled` |
| `started_at` | TEXT NOT NULL | ISO timestamp |
| `ended_at` | TEXT NULL | ISO timestamp |
| `interval_seconds` | INTEGER NULL | body-double check-in cadence; NULL for other kinds |
| `note` | TEXT NULL | optional non-shaming free text, capped length |

Constraints:

- Only one `active` row per `kind` at a time (enforced in repository, not just at DB level, so the violation can be surfaced calmly).
- Soft-deleted target rows must auto-end any referencing `focus_sessions` row.

New column on `tasks`:

- `parent_task_id INTEGER NULL` — set when a task was created via `/breakdown`. Foreign key to `tasks(id)`. Soft-deleting the parent does **not** soft-delete children (recoverable, non-shaming behavior).

Migration approach:

- A single additive migration step in `backend/app/db.py` (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN` guarded by introspection). Existing rows continue to work; no destructive changes.

## Proposed Endpoints

All endpoints are local-only and follow existing conventions (no `user_id`, no auth headers). Mutating endpoints write `actions` rows so `/undo` works uniformly.

### One Thing

- `GET /focus/current` — read-only. Returns the active focus session (if any), the resolved target row, and a calm empty payload otherwise.
- `POST /focus/start` — body `{ "target_type": "task" | "inbox_item" | "event", "target_id": int, "note": str | null }`. Sets the single active focus session. If another is active, returns a 409-style structured payload describing the existing one rather than silently replacing it. Caller may pass `"replace": true` to explicitly swap.
- `POST /focus/stop` — ends the active focus session. Idempotent.

### Breakdown

- `POST /tasks/{id}/breakdown` — body `{ "steps": ["...", "..."], "source": "manual" | "llm" }`. Creates 2–5 child tasks with `parent_task_id = id` in given order; logs a single `breakdown` action whose `before/after` snapshot captures the parent + created child ids so `/undo` removes the children together.
- `GET /tasks/{id}/children` — read-only list of child tasks (open + soft-deleted included with a `deleted` flag) for rendering.
- `POST /tasks/{id}/breakdown/suggest` — read-only. Returns proposed 2–5 step titles without writing anything. Uses rules-only heuristics when `OPENROUTER_API_KEY` is unset; otherwise calls the existing LLM provider with a small, capped prompt. Suggestions are *suggestions only*; the user must POST to `/breakdown` to commit.

LLM safety rules (reuse Phase 2 conventions):

- Hard timeout (`LLM_TIMEOUT_SECONDS`).
- Bounded output length.
- Reject suggestions that include URLs, emails, or sensitive-looking strings; on rejection fall back to rules.
- No raw provider response stored anywhere user-visible.

### Block Reset

- `POST /stuck` — body `{ "target_type", "target_id", "choice": "shrink" | "swap" | "skip" | "park" }`. Effects:
  - `shrink`: marks the task as "needs breakdown" by setting a `block_state` field on the task (new TEXT column, nullable, soft state); TUI/web surface a prompt to run `/breakdown`.
  - `swap`: ends current focus session and clears `block_state`; user picks a new target via existing list/search.
  - `skip`: leaves the task open but pushes `due_at` forward by 1 day if set, else leaves untouched; logs an action.
  - `park`: sets `block_state = "parked"`. Parked tasks are hidden from `/today` and survival-mode lists but remain queryable.
- `GET /stuck/options?target_type=...&target_id=...` — read-only. Returns the four choices with the calm, non-shaming copy strings, so the TUI and web render identical text.

`block_state` column on `tasks`: nullable TEXT, values: `null | "needs_breakdown" | "parked"`. Survives across sessions; clears on completion or explicit re-open.

### Body Double

- `POST /body-double/start` — body `{ "interval_seconds": int, "note": str | null, "target_type": str | null, "target_id": int | null }`. Validates `interval_seconds` is within `BODY_DOUBLE_MIN_INTERVAL` (default 60) and `BODY_DOUBLE_MAX_INTERVAL` (default 1800).
- `POST /body-double/check-in` — caller sends a heartbeat; backend records `last_check_in_at` (on the focus_sessions row, in `note` or a new column `last_check_in_at TEXT NULL`). The TUI uses this for "still here?" prompts. No external call.
- `POST /body-double/stop` — ends the session.
- `GET /body-double/current` — read-only state for renderers.

### Minimum Viable Step (MVS)

- `POST /mvs/suggest` — body `{ "target_type": "task" | "inbox_item", "target_id": int }`. Read-only: returns one suggested micro-step (string) derived from the row title and (optionally) any existing child tasks. Rules-first; LLM optional and gated by `OPENROUTER_API_KEY`.
- `POST /mvs/commit` — body `{ "target_type", "target_id", "step": str }`. Convenience wrapper that:
  1. creates a single child task under the target (if the target is a task) with that step text, and
  2. starts a focus session on that child task.
  Logs both actions so `/undo` reverts the focus session and then the child task in two `/undo` calls (or `/undo` could batch in a future phase — out of scope here).

### Survival Mode

- `POST /survival/enter` — body `{ "note": str | null }`. Sets the global survival flag (a single-row mirror in `focus_sessions` with `kind="survival"`). While active:
  - `/today` and `/dashboard` return at most one task and at most one event.
  - Inbox triage suggestions are paused (capture still works; classification still writes inbox rows).
  - Body-double prompts soften their copy (the TUI uses the supplied copy strings).
- `POST /survival/exit` — clears the flag.
- `GET /survival` — read-only current state.

Survival mode is a *filter*, not a deletion. It changes what is *shown*, never what exists.

## Non-shaming Copy Library

To keep tone consistent across TUI and web, ship a small Python module `backend/app/copy.py` exporting frozen string constants. Examples (final wording subject to review during implementation):

- Block reset prompt: "Stuck is information, not failure. Pick one: shrink, swap, skip, park."
- Survival entry: "Survival mode on. We will show one task and one event. Everything else is safe."
- Body-double check-in: "Still here. Want to keep going, pause, or wrap up?"
- Empty focus state: "No focus session right now. That is fine."

Rules for this module:

- No second-person blame ("you forgot", "you missed").
- No streak/quantity language ("you only finished 1 of 5").
- No urgency punctuation pile-ups.
- Strings are unit-tested for forbidden tokens (`forgot`, `failed`, `lazy`, `only`, etc.) via a small lint test.

## TUI Interactions

New slash commands (Phase 4 patterns; map 1:1 to endpoints):

| Command | Backend call | UX notes |
|---|---|---|
| `/focus N` | `POST /focus/start` | `N` indexes into the most recent `/tasks`/`/inbox`/`/events` listing in the same session. Without `N`, prints the current focus session via `GET /focus/current`. |
| `/focus stop` | `POST /focus/stop` | Idempotent. |
| `/breakdown N` | `POST /tasks/{id}/breakdown/suggest` then prompt | Renders suggested steps as a numbered list; the user types `/breakdown commit` to persist, or edits inline before confirming. |
| `/stuck` | `GET /stuck/options` then prompt | Renders the four choices from the copy library; user types `shrink`/`swap`/`skip`/`park`. Applies to the current focus session if one is active, otherwise prompts for a target. |
| `/body-double <seconds>` | `POST /body-double/start` | Defaults `seconds` to a Phase-6 env value `BODY_DOUBLE_DEFAULT_INTERVAL`. The TUI runs a foreground timer; on each tick it calls `POST /body-double/check-in` and prints the calm prompt. `Enter` continues, `/body-double stop` ends. |
| `/mvs N` | `POST /mvs/suggest` then prompt | Renders one suggestion; `/mvs commit` writes it. |
| `/survival on` / `/survival off` | `POST /survival/enter` / `/exit` | Toggles. Refreshes Now pane and lists. |

UX rules:

- Number-pick discipline from Phase 4 is preserved: free-form text never reaches a mutation endpoint as a target selector.
- Survival mode visibly tags the TUI header (`[survival]`) so the user always knows the filter is on.
- Body-double timer runs in a Textual worker, not the event loop; stopping the TUI cleanly ends the session via `/body-double/stop`.

## Web Dashboard Interactions (read-only)

Add one section to the existing `/dashboard` payload and the `/web` page:

```json
{
  "focus": {
    "session": {
      "id": 4,
      "kind": "focus",
      "target": { "type": "task", "id": 7, "title": "call dentist" },
      "started_at": "...",
      "note": null
    },
    "body_double": null,
    "survival": false
  }
}
```

The web page renders this as a "Focus" panel directly under "Now":

- If `survival` is true, the page tags the header `Survival mode` and the Tasks/Events sections render at most one row each, matching the backend filter.
- If a focus session is active, the panel shows the target title and started-at time.
- If a body-double session is active, the panel shows the cadence and last check-in time.
- No start/stop controls. The page links to the docs for how to start one from the TUI/API.

The static JS must continue to use only `GET /dashboard`. Static tests must continue to forbid `POST`/`PATCH`/`DELETE`/`/done`/`/undo`/`/promote` strings in `web.js`.

## Configuration

Additions to `.env.example` (placeholders only):

```bash
BODY_DOUBLE_DEFAULT_INTERVAL=300
BODY_DOUBLE_MIN_INTERVAL=60
BODY_DOUBLE_MAX_INTERVAL=1800
BREAKDOWN_MIN_STEPS=2
BREAKDOWN_MAX_STEPS=5
SURVIVAL_MAX_TASKS=1
SURVIVAL_MAX_EVENTS=1
```

Existing `OPENROUTER_API_KEY`, `LLM_TIMEOUT_SECONDS`, and `CLASSIFY_ENABLED` are reused; no new secrets.

## Implementation Tasks

### Task 1: Schema and copy library scaffolding

**Files:**

- Modify: `backend/app/db.py` (additive migration for `focus_sessions`, `tasks.parent_task_id`, `tasks.block_state`)
- Create: `backend/app/copy.py` (frozen non-shaming strings)
- Modify: `backend/app/schemas.py` (FocusSession, BreakdownRequest/Response, StuckRequest, BodyDoubleStart, MVSSuggest/Commit, SurvivalToggle, FocusPanel)
- Test: `backend/tests/test_phase6_schema.py`, `backend/tests/test_copy.py`

**Steps:**

1. Add `focus_sessions` table and new columns with introspection guards.
2. Add Pydantic models for every new request/response.
3. Add `copy.py` with frozen strings; add a lint test that scans them for forbidden tokens.
4. Run:

```bash
python -m pytest backend/tests/test_phase6_schema.py backend/tests/test_copy.py -q
```

### Task 2: Repositories

**Files:**

- Modify: `backend/app/repositories.py`
- Test: `backend/tests/test_focus_repo.py`, `backend/tests/test_breakdown_repo.py`

**Steps:**

1. Implement `start_focus_session`, `stop_focus_session`, `get_active_focus_session(kind)`, with single-active-per-kind enforcement.
2. Implement `breakdown_task(parent_id, steps)` returning new child task ids; create one `breakdown` action with full before/after snapshot.
3. Implement `set_block_state(task_id, state)` writing an action row.
4. Implement `enter_survival_mode` / `exit_survival_mode` (active focus_sessions row of kind=`survival`).
5. Implement `record_body_double_checkin(session_id)`.
6. Tests cover: single-active invariant, soft-deleted targets auto-end sessions, undoing a breakdown removes children, survival enter/exit idempotency.

### Task 3: Endpoints

**Files:**

- Modify: `backend/app/main.py`
- Test: `backend/tests/test_focus_endpoints.py`, `backend/tests/test_breakdown_endpoints.py`, `backend/tests/test_stuck_endpoints.py`, `backend/tests/test_body_double_endpoints.py`, `backend/tests/test_mvs_endpoints.py`, `backend/tests/test_survival_endpoints.py`

**Steps:**

1. Wire each endpoint from the "Proposed Endpoints" section, in scope order.
2. Reuse existing error mapping (`HTTPException` with structured detail).
3. Add TestClient tests for happy path, conflict path (e.g. starting a second focus session without `replace`), and post-call DB invariants.
4. Verify every mutating endpoint writes exactly one `actions` row and that `POST /undo/latest` reverses the visible state change.
5. Run:

```bash
python -m pytest backend/tests -q
```

### Task 4: Dashboard payload integration

**Files:**

- Modify: `backend/app/repositories.py` (`get_dashboard`)
- Modify: `backend/app/main.py`
- Modify: `backend/app/static/web/index.html`, `web.css`, `web.js`
- Test: `backend/tests/test_dashboard.py`, `backend/tests/test_web_static.py`

**Steps:**

1. Extend `get_dashboard` to include the `focus` block described above.
2. When survival is active, clamp `tasks` and `events` arrays in `get_dashboard` to `SURVIVAL_MAX_TASKS` and `SURVIVAL_MAX_EVENTS`.
3. Render the Focus panel in `web.js` using `textContent`; never `innerHTML` for target titles or notes.
4. Static tests still forbid mutation strings in `web.js`.
5. Run targeted dashboard tests.

### Task 5: TUI commands

**Files:**

- Modify: `tui/commands.py`, `tui/client.py`, `tui/app.py`, `tui/rendering.py`, `tui/state.py`
- Test: `tui/tests/test_phase6_commands.py`, `tui/tests/test_phase6_app.py`

**Steps:**

1. Add command parsing for `/focus`, `/focus stop`, `/breakdown`, `/breakdown commit`, `/stuck`, `/body-double`, `/body-double stop`, `/mvs`, `/mvs commit`, `/survival on`, `/survival off`.
2. Add a non-blocking timer worker for `/body-double` that calls `/body-double/check-in` and renders the calm prompt; stopping the app cleanly stops the session.
3. Add `[survival]` header tag wired to a reactive in `tui/state.py`.
4. Tests cover parse → call → render for each command using a stubbed HTTP client.

### Task 6: Documentation

**Files:**

- Modify: `README.md` (add a `Phase 6 Execution Helpers` section with API examples and the non-shaming tone note)
- Modify: `.env.example` (placeholders only)

**Steps:**

1. Document new endpoints with `curl` examples and short explanations.
2. State that the helpers are local-only, single-user, and never call remote presence services.
3. Reaffirm: do not expose to the public internet; bind to `127.0.0.1` or use SSH tunnel/VPN/external access control.
4. Do not include real personal text in examples.

## Test Plan

Per slice:

```bash
python -m pytest backend/tests/test_phase6_schema.py backend/tests/test_copy.py -q
python -m pytest backend/tests/test_focus_repo.py backend/tests/test_breakdown_repo.py -q
python -m pytest backend/tests/test_focus_endpoints.py backend/tests/test_breakdown_endpoints.py \
                 backend/tests/test_stuck_endpoints.py backend/tests/test_body_double_endpoints.py \
                 backend/tests/test_mvs_endpoints.py backend/tests/test_survival_endpoints.py -q
python -m pytest backend/tests/test_dashboard.py backend/tests/test_web_static.py -q
python -m pytest tui/tests -q
```

Full verification before commit/push:

```bash
python -m pytest backend/tests tui/tests -q
python -m ruff check backend/app backend/tests tui
```

Smoke check, only after tests are green:

```bash
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
curl -s -X POST http://127.0.0.1:8000/focus/start \
  -H 'Content-Type: application/json' \
  -d '{"target_type":"task","target_id":1}'
curl -s http://127.0.0.1:8000/focus/current
curl -s -X POST http://127.0.0.1:8000/survival/enter -H 'Content-Type: application/json' -d '{}'
curl -s http://127.0.0.1:8000/dashboard
curl -s -X POST http://127.0.0.1:8000/survival/exit -H 'Content-Type: application/json' -d '{}'
```

If using a background server for smoke checks, stop it before finalizing.

## Review Gates

Before committing Phase 6 implementation:

1. Confirm no `user_id`, auth, accounts, sessions-belonging-to-user, roles, or permissions were added.
2. Confirm `focus_sessions` enforces single-active-per-kind in the repository layer with a calm error payload, not a 500.
3. Confirm every mutation writes an `actions` row and is reversible via `/undo`.
4. Confirm breakdown children survive parent soft-delete (recoverable) and that `/undo` of a breakdown removes the children.
5. Confirm survival mode is a filter, never a delete.
6. Confirm body-double is local-only: no network calls leave the host, no remote presence service is contacted.
7. Confirm the web dashboard added no mutation controls and `web.js` contains no `POST`/`PATCH`/`DELETE` calls.
8. Confirm the non-shaming copy lint test passes against `backend/app/copy.py`.
9. Confirm tests cover empty, happy, conflict, and undo paths for each helper.
10. Confirm public-safety scan passes (no personal names, no real local absolute paths, no real secrets/tokens in code or docs).

## Commit Boundaries

Use meaningful, non-noisy commits:

1. `docs: add phase 6 execution helpers plan`
2. `feat: add focus sessions schema and copy library`
3. `feat: add one-thing focus endpoints`
4. `feat: add task breakdown endpoints`
5. `feat: add block-reset endpoints`
6. `feat: add body-double endpoints`
7. `feat: add minimum-viable-step endpoints`
8. `feat: add survival mode filter`
9. `feat: surface focus state on web dashboard`
10. `feat: add phase 6 tui commands`
11. `docs: document phase 6 execution helpers`

Merge adjacent commits if a slice is too small to review independently. Do not inflate commit count artificially.

## Public-safety Check

Before pushing, run the repository's existing secret/private-context scan from the shell or an equivalent reviewer checklist. The non-shaming copy lint test in `backend/tests/test_copy.py` is *additional* and does not replace the public-safety scan.

Expected: no sensitive matches. Placeholder strings in `.env.example` are allowed only when they do not contain real values.
