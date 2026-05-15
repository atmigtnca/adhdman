# Phase 3 Resolver & Undo Implementation Plan

**Goal:** Make existing tasks and events safely editable, deletable, and reversible. Phase 1 captures and Phase 2 classifies; Phase 3 closes the recovery loop by (a) resolving free-form datetime phrases into structured timestamps, (b) updating and deleting tasks/events through a safe candidate-selection flow, and (c) restoring any mutating action from the action log via a single undo endpoint.

**Architecture:** Add a deterministic datetime resolver in `backend/app/resolver/`, extend repositories with `update_*` / `delete_*` functions that always write a full `before_json` / `after_json` action snapshot, add a candidate-selection layer that turns ambiguous references (e.g. "the dentist thing", "tomorrow's meeting") into a *list of candidates* the caller must confirm before mutation, and expose `POST /undo/{action_id}` and `POST /undo/latest` that replay action rows in reverse. Local-first, single-user. No auth, no accounts, no `user_id`, no external calendar sync, no public exposure.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, SQLite, `zoneinfo` from the standard library, pytest. No new third-party runtime dependencies beyond what Phase 1/2 introduced.

---

## Product Constraints

- Capture-first: resolver failures fall back to leaving the original text intact on the inbox item; nothing is dropped.
- Recovery-first: every update and delete writes a complete `before_json` snapshot so undo can restore byte-for-byte.
- Safe candidate selection: ambiguous references never silently mutate. The API returns candidates and requires an explicit `target_id` on a second call.
- One-thing principle: resolver returns one best interpretation plus optional alternates; mutation endpoints act on exactly one row.
- Non-shaming tone: "did you mean…" prompts stay calm; undo confirmations are neutral.
- Single-user only: no auth, no accounts, no `user_id`, no roles, no sessions.
- Local-first only: resolver is fully offline and deterministic. No network calls in this phase.
- Determinism: resolver output for a given `(text, now, tz)` triple is reproducible across runs.

## Phase 3 API Target

Phase 3 adds new endpoints and extends existing task/event resources. Phase 1 and Phase 2 endpoints are unchanged.

```text
POST   /resolve                         # parse free-form datetime text, no persistence
GET    /tasks/{id}                      # new: read a single task
PATCH  /tasks/{id}                      # update title / status / due_at
DELETE /tasks/{id}                      # soft-delete (status='deleted')
GET    /events/{id}                     # new: read a single event
PATCH  /events/{id}                     # update title / starts_at / ends_at
DELETE /events/{id}                     # soft-delete
POST   /search                          # candidate selection by free-form query
POST   /undo/{action_id}                # revert a specific action
POST   /undo/latest                     # revert most recent reversible action
GET    /actions                         # list recent actions (read-only, for trust)
```

`POST /resolve` shape:

```json
{
  "text": "tomorrow 3pm",
  "now": "2026-05-16T09:00:00-07:00",
  "tz": "America/Los_Angeles"
}
```

Returns:

```json
{
  "resolved": {
    "starts_at": "2026-05-17T15:00:00-07:00",
    "ends_at": null,
    "kind": "absolute",
    "confidence": 0.95,
    "source": "rules"
  },
  "alternates": []
}
```

`POST /search` returns candidates only — never mutates:

```json
{
  "query": "dentist",
  "candidates": [
    { "type": "event", "id": 12, "title": "dentist checkup", "starts_at": "2026-05-20T10:00:00-07:00", "score": 0.88 },
    { "type": "task",  "id":  7, "title": "call dentist",     "starts_at": null, "score": 0.61 }
  ]
}
```

Callers then invoke `PATCH /tasks/7` or `PATCH /events/12` with the explicit id.

## Datetime Resolver

Module: `backend/app/resolver/` (new):

- `tokens.py` — small regex/keyword tables for `today`, `tomorrow`, `yesterday`, weekdays (`monday`..`sunday`), `next <weekday>`, `this <weekday>`, `in N (minutes|hours|days|weeks)`, `at HH(:MM)?(am|pm)?`, bare ISO8601, `YYYY-MM-DD`, `HH:MM`.
- `relative.py` — applies relative offsets against `now` in the caller-provided timezone.
- `absolute.py` — parses ISO and `YYYY-MM-DD[ T]HH:MM[:SS]`.
- `resolver.py` — orchestrates: normalize → try absolute → try relative → fail. Returns a `ResolveResult` with `starts_at`, `ends_at`, `kind ∈ {absolute,relative,none}`, `confidence`, `source='rules'`, and an optional list of `alternates` when input is ambiguous (e.g. `monday` could mean this or next Monday — pick nearer-future as primary, include the other as alternate).
- All outputs are timezone-aware ISO8601 strings. `now` and `tz` are required inputs; the API uses `Settings.LOCAL_TIMEZONE` as default when omitted.

Resolver is **pure and offline**. No LLM call in Phase 3. (A later phase may add an LLM resolver behind the same protocol.)

Failure mode: when no token matches, return `kind='none'`, `confidence=0.0`, and leave timestamps `null`. Callers (e.g. `PATCH /events/{id}`) treat this as a 400 with a non-shaming message rather than silently overwriting.

## Safe Candidate Selection

Two-step pattern for any reference that is not a literal id:

1. Caller sends free-form text to `POST /search`.
2. Server scores `tasks` and `events` by:
   - case-insensitive substring on title (highest weight),
   - token overlap on title,
   - small recency boost for items updated in the last 7 days,
   - small future-proximity boost for events with `starts_at` within ±14 days of `now`.
3. Returns up to `SEARCH_MAX_CANDIDATES` (default 5) candidates with `score ∈ [0,1]` ordered descending.
4. Caller picks a specific `id` and calls the typed `PATCH` / `DELETE` endpoint.

The server **never** auto-selects when more than one candidate exists above an ambiguity threshold. A single high-confidence match is still returned through `/search`; mutation always requires an explicit id. This is the single rule that prevents "edit the dentist thing" from silently editing the wrong row.

Scoring is deterministic and offline. No fuzzy library dependency in Phase 3; plain Python is sufficient at single-user scale.

## Update / Delete Semantics

Common to all mutating endpoints:

- Load the current row inside a transaction.
- Build `before_json` from the full row (all columns, including timestamps).
- Apply the patch; reject unknown fields.
- Build `after_json` from the post-update row.
- Write an `actions` row with the appropriate `action_type` and both snapshots.
- Commit.

Per resource:

- `PATCH /tasks/{id}` accepts `title`, `status` (`open|done|cancelled`), `due_at` (nullable ISO8601). Reject empty `title`. Action types: `update_task`, plus the existing `complete_task` for status→done transitions (keep Phase 1 behavior to avoid duplicate logging).
- `DELETE /tasks/{id}` sets `status='deleted'` (soft delete; preserves recovery). Action type `delete_task`. Hard delete is **out of scope** in Phase 3.
- `PATCH /events/{id}` accepts `title`, `starts_at`, `ends_at`. When the caller sends raw text instead of ISO, the endpoint may call the resolver internally; if the resolver returns `kind='none'`, the request is rejected with 400.
- `DELETE /events/{id}` sets `status='deleted'`. Action type `delete_event`. (Adds a `status` column to `events` — see Schema Changes.)

All endpoints return the post-mutation row plus the `action_id` so the client can offer a one-tap undo.

## Undo via Action Logs

`POST /undo/{action_id}`:

1. Load the action row. 404 if missing. 409 if already undone (see `undone_at` column below).
2. Verify the action is reversible. Reversible types in Phase 3:
   - `capture` → soft-delete the inbox item.
   - `promote_task` → set inbox back to `status='open'` and soft-delete the created task.
   - `complete_task` → restore prior status and clear `completed_at`.
   - `update_task` / `update_event` → restore the row from `before_json`.
   - `delete_task` / `delete_event` → restore `status` from `before_json`.
   - `classify_task` / `classify_event` / `classify_inbox_fallback` → revert the resulting row(s) using their `before_json` snapshots (Phase 2 must already write these; if any path is missing snapshots, Phase 3 adds them — see Migration Notes).
3. Apply the inverse inside a transaction.
4. Write a *new* action row of type `undo` whose `target_type`/`target_id` point to the original action, with `before_json` capturing the row's state at undo time. This makes undo itself reversible (`undo` of an `undo` is supported only in this Phase if it falls out for free; otherwise it is out of scope — pick the simpler path).
5. Mark the original action's `undone_at`.

`POST /undo/latest`:

- Finds the most recent action where `undone_at IS NULL` and `action_type != 'undo'`.
- Applies the same logic.

Non-reversible action types (e.g. read-only) return 409 with a non-shaming explanation.

## Database Changes

Migrations stay minimal but Phase 3 cannot avoid them entirely. Use additive `ALTER TABLE` statements gated by `init_db()` column-existence checks (no migration framework yet).

Additions to `tasks`:

```sql
ALTER TABLE tasks ADD COLUMN due_at TEXT;            -- nullable ISO8601
```

`tasks.status` already allows `open|done|cancelled` from Phase 1; Phase 3 adds `deleted` to the allowed set in application code (no DB-level check needed since SQLite stores TEXT freely).

Additions to `events`:

```sql
ALTER TABLE events ADD COLUMN status TEXT NOT NULL DEFAULT 'open';
```

Allowed values: `open`, `deleted`.

Additions to `actions`:

```sql
ALTER TABLE actions ADD COLUMN undone_at TEXT;       -- nullable ISO8601
```

`before_json` / `after_json` are already present from Phase 1. Phase 3 audits every mutating path and guarantees they are populated; any code path discovered to write `NULL` is fixed as part of Task 2.

No new tables in Phase 3.

## Settings

Extend `backend/app/config.py`:

- `LOCAL_TIMEZONE: str = "UTC"` — default tz for resolver when caller omits one. Document that users should set this in `.env` for natural-language phrases like `"tomorrow 3pm"` to be interpreted correctly.
- `SEARCH_MAX_CANDIDATES: int = 5`
- `SEARCH_AMBIGUITY_THRESHOLD: float = 0.15` — minimum score gap between #1 and #2 below which the server treats the match as ambiguous.
- `UNDO_ENABLED: bool = True` — kill switch.

`.env.example` gains placeholder lines for these. No real values committed.

## Repository / Service Layout

New:

- `backend/app/resolver/{__init__.py,tokens.py,relative.py,absolute.py,resolver.py}`
- `backend/app/search.py` — candidate scoring across tasks and events.
- `backend/app/undo.py` — inverse-action dispatcher keyed by `action_type`.

Modified:

- `backend/app/repositories.py` — add `get_task`, `update_task`, `delete_task`, `get_event`, `update_event`, `delete_event`, `list_actions`, `mark_action_undone`. Each mutating function writes a full snapshot pair.
- `backend/app/schemas.py` — add `ResolveRequest`, `ResolveResult`, `TaskUpdateRequest`, `EventUpdateRequest`, `SearchRequest`, `SearchResponse`, `UndoResponse`, `ActionResponse`.
- `backend/app/main.py` — register the new endpoints.

## Testing Strategy

All tests run offline. No network calls. Use `tmp_path`-backed settings. Freeze `now` explicitly in every resolver test — never rely on wall-clock time.

- **Resolver tests** (`test_resolver.py`):
  - absolute: ISO strings, `YYYY-MM-DD HH:MM`, naive strings rejected when no `tz` provided.
  - relative: `tomorrow`, `tomorrow 3pm`, `in 2 hours`, `next monday`, weekday names with this-vs-next disambiguation.
  - DST boundaries: an `in 24 hours` request across a US DST transition still produces a valid timestamp.
  - ambiguous inputs return primary + at least one alternate.
  - non-matching inputs return `kind='none'`.
- **Repository update/delete tests** (`test_task_update_delete.py`, `test_event_update_delete.py`):
  - update writes both `before_json` and `after_json`.
  - delete is soft (`status='deleted'`); row still readable by id.
  - update of missing/deleted id returns clear 404.
- **Search tests** (`test_search.py`):
  - substring match returns the right row first.
  - ties below `SEARCH_AMBIGUITY_THRESHOLD` return multiple candidates in order.
  - empty/whitespace query rejected.
- **Undo tests** (`test_undo.py`):
  - undo of `capture` removes the inbox item.
  - undo of `complete_task` restores prior status and clears `completed_at`.
  - undo of `update_event` restores all original columns byte-for-byte.
  - undo of `delete_task` restores status.
  - undo of an already-undone action returns 409.
  - `POST /undo/latest` picks the newest non-undone action.
  - undo writes its own action row of type `undo`.
- **API tests** (`test_resolve_endpoint.py`, `test_patch_task.py`, `test_patch_event.py`, `test_search_endpoint.py`, `test_undo_endpoint.py`):
  - end-to-end through `TestClient`.
  - confirm that `PATCH` followed by `POST /undo/latest` leaves the row identical to its pre-patch state (compare full row dicts).
- **Negative tests**: empty patches rejected; unknown fields rejected; resolver-failing event update rejected with non-shaming message; undo with `UNDO_ENABLED=False` returns 409.
- **Regression tests**: full Phase 1 + Phase 2 suite must still pass unchanged.

Tests must never read or write the real local data file.

## Commit Boundaries

1. `docs: add phase 3 resolver and undo plan`
2. `feat: add datetime resolver and /resolve endpoint`
3. `feat: add task and event read endpoints`
4. `feat: add task update and soft-delete with action snapshots`
5. `feat: add event update and soft-delete with action snapshots`
6. `feat: add candidate search endpoint`
7. `feat: add undo endpoint and inverse-action dispatcher`
8. `feat: expose actions list endpoint`
9. `docs: document phase 3 resolver and undo`

Refactor or test-only commits are allowed if they fall out naturally. Do not split commits artificially. Each commit must leave the full test suite green before the next is started.

## Review Gates

Before pushing implementation commits:

1. Run targeted tests for the changed behavior.
2. Run the full test suite offline; no test may hit the network or rely on the current wall-clock time.
3. Inspect `git diff` for scope creep, accidental auth/multi-user concepts, hard-coded local paths, or committed secrets.
4. Confirm every mutating path writes both `before_json` and `after_json`; grep for `update_*` and `delete_*` functions and verify each has an action snapshot test.
5. Confirm `UNDO_ENABLED=False` disables `/undo/*` cleanly without breaking other endpoints.
6. Confirm the resolver is deterministic: re-run the resolver test file twice with the same frozen `now` and diff outputs.
7. For any ambiguous mutation reference, verify the API path goes through `/search` first — no endpoint accepts free-form text as a target selector.

## Migration Notes

- Phase 2 action types (`classify_task`, `classify_event`, `classify_inbox_fallback`) must carry `before_json` / `after_json` to be undoable. As part of Task 2, audit Phase 2 writes and add any missing snapshots. If a snapshot truly cannot exist (e.g. pure `capture` of a brand-new row), set `before_json=NULL` and have the undo dispatcher treat `NULL before_json` for a `create`-shaped action as "delete the created row" rather than "restore from null".
- The `events.status` column is new; backfill existing rows to `'open'` via the `ALTER TABLE ... DEFAULT 'open'`.
- `actions.undone_at` is nullable and defaults to NULL; existing rows remain undoable.

## Out of Scope for Phase 3

- login, auth, accounts, sessions, roles
- multi-user, `user_id`, permissions
- external calendar sync
- public runtime deployment
- TUI implementation (later phase)
- web dashboard (later phase)
- SSE / realtime (later phase)
- LLM-based datetime resolution (resolver stays deterministic in Phase 3)
- recurring events / RRULE expansion
- timezone conversion UI; `LOCAL_TIMEZONE` is a single setting
- hard delete and purge
- bulk update / bulk undo
- conflict resolution across concurrent writes (single-user assumption holds)
- redo (forward replay) — only inverse undo is in scope
- reminders, notifications, priority scoring
