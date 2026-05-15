# Phase 5 Read-only Web Memory Implementation Plan

> **For Hermes:** Use subagent-driven-development style execution: implement one coherent slice at a time, review for spec compliance and public safety, then commit only verified changes.

**Goal:** Add a localhost-only, read-only web dashboard that helps the user remember what exists in ADHDman without adding another mutation surface.

**Architecture:** Serve a small static dashboard from the existing FastAPI backend. The browser fetches read-only JSON endpoints for today, inbox, tasks, events, recent actions, and a week view. Existing mutation endpoints remain API-only/TUI-only; the web page must not contain forms or buttons that mutate state. The dashboard is local-first and inherits the project security model: no login, no accounts, no `user_id`, no public exposure.

**Tech Stack:** FastAPI static file serving, vanilla HTML/CSS/JavaScript, SQLite-backed read-only repository queries, pytest + TestClient. Optional SSE is allowed only after the static polling/read-only dashboard is green and only if it stays read-only.

---

## Product Constraints

- Read-only means read-only: Phase 5 must not add web-side edit, done, delete, undo, promote, or capture controls.
- No auth/account/session concepts. Do not add login screens, cookies, CSRF machinery, roles, permissions, or `user_id`.
- Local-only. The app remains intended for `127.0.0.1`, SSH tunnel, VPN, or trusted external access-control layers only.
- One-thing principle: the top of the page shows `GET /today` as the primary focus; lists are secondary memory aids.
- Recovery-first: recent actions are visible so the user can remember what changed, but undo remains outside the web dashboard in Phase 5.
- Non-shaming tone: empty states should say what is available now, not judge inactivity.
- Public-safe docs/code: no personal names, local absolute paths, real secrets, tokens, or private project notes.

## Scope

In scope:

- Static dashboard route, e.g. `GET /web` or `GET /dashboard`, backed by files under `backend/app/static/` or a similarly small static directory.
- Read-only JSON endpoint(s) for data not already exposed:
  - recent actions / changes
  - week overview derived from tasks/events
  - optional combined dashboard payload if it reduces client complexity
- Dashboard sections:
  - Now: current `/today` one thing and counts
  - Inbox: open inbox items
  - Tasks: open tasks, plus recently completed tasks if already available through a read-only query
  - Events: upcoming events
  - Week: grouped date view for tasks/events
  - Recent Changes: latest actions with action id, action type, target, timestamp
- Browser refresh button and/or short polling for read-only refresh.
- Offline/static tests for HTML content and JS safety where practical.
- TestClient integration tests for every new route/endpoint.

Out of scope:

- Web capture input.
- Web completion/edit/delete/undo/promote buttons.
- Websocket bidirectional sync.
- Login/auth/session/user system.
- Multi-user support.
- Remote deployment.
- Framework-heavy frontend build tooling.
- Writing to localStorage/sessionStorage unless a future UX need is explicit.

## Proposed Routes

Prefer small, explicit routes:

- `GET /web` — returns the dashboard HTML.
- `GET /static/web.css` — static CSS if using mounted static files.
- `GET /static/web.js` — static JavaScript if using mounted static files.
- `GET /dashboard` — combined read-only JSON payload for the page.
- `GET /actions/recent?limit=20` — read-only recent action log, if not folded into `/dashboard`.
- `GET /week` — read-only week overview, if not folded into `/dashboard`.

Decision rule:

- If a combined `/dashboard` endpoint keeps browser code simple and avoids multiple request failure states, implement `/dashboard` first.
- Add separate `/actions/recent` and `/week` only when tests or future callers benefit from them.

## Data Contract

`GET /dashboard` response shape:

```json
{
  "today": {
    "message": "Nothing urgent right now.",
    "one_thing": null,
    "counts": { "open_tasks": 0, "open_inbox": 0, "upcoming_events": 0 }
  },
  "inbox": [
    { "id": 1, "text": "ambiguous note", "status": "open", "created_at": "..." }
  ],
  "tasks": [
    { "id": 2, "title": "pay rent", "status": "open", "due_at": null, "created_at": "...", "updated_at": "..." }
  ],
  "events": [
    { "id": 3, "title": "dentist", "starts_at": "2026-05-20T10:00:00", "ends_at": null, "status": "open" }
  ],
  "week": [
    { "date": "2026-05-20", "items": [ { "type": "event", "id": 3, "title": "dentist", "time": "10:00" } ] }
  ],
  "recent_actions": [
    { "id": 9, "action_type": "capture", "target_type": "inbox_item", "target_id": 1, "created_at": "..." }
  ]
}
```

Rules:

- Do not include raw LLM provider responses in the dashboard payload.
- Do not include secrets or environment values.
- Do not expose file paths.
- Keep `before_json` / `after_json` snapshots out of the default dashboard payload unless a future explicit debug view is added.

## UI Layout

```text
┌──────────────────────────────────────────────┐
│ ADHDman Web Memory                           │
│ local read-only dashboard                    │
├──────────────────────────────────────────────┤
│ Now                                          │
│   One thing now / calm empty message         │
├──────────────────────┬───────────────────────┤
│ Inbox                │ Tasks                 │
│ open items           │ open + due soon       │
├──────────────────────┴───────────────────────┤
│ Week                                         │
│ grouped events/tasks                         │
├──────────────────────────────────────────────┤
│ Recent Changes                               │
│ action log summary                           │
└──────────────────────────────────────────────┘
```

UX requirements:

- The page must visibly say `Read-only` near the header.
- If the backend is unavailable, JS shows a calm error line and leaves the static shell visible.
- Refresh is safe: the refresh control only re-fetches read-only endpoints.
- No `<form>` elements for mutations.
- No buttons labeled done/delete/undo/edit/capture/promote.

## Implementation Tasks

### Task 1: Add dashboard schemas

**Objective:** Define typed response models for the read-only dashboard payload.

**Files:**

- Modify: `backend/app/schemas.py`
- Test: `backend/tests/test_dashboard.py`

**Steps:**

1. Add Pydantic models for dashboard summary, week items, recent action summaries, and dashboard response.
2. Keep fields minimal and public-safe.
3. Write a schema serialization test using representative sample data.
4. Run:

```bash
python -m pytest backend/tests/test_dashboard.py -q
```

Expected: targeted dashboard schema tests pass.

### Task 2: Add read-only repository queries

**Objective:** Fetch recent actions and week overview without changing existing data.

**Files:**

- Modify: `backend/app/repositories.py`
- Test: `backend/tests/test_dashboard.py`

**Steps:**

1. Add `list_recent_actions(conn, limit=20)` returning action metadata only.
2. Add helper query functions for week candidates from open tasks/events.
3. Exclude soft-deleted rows.
4. Clamp limits to safe small values.
5. Add tests that seed inbox/tasks/events/actions and verify ordering and exclusion of soft-deleted rows.
6. Run targeted tests.

### Task 3: Add `GET /dashboard`

**Objective:** Return one combined read-only payload for the web dashboard.

**Files:**

- Modify: `backend/app/main.py`
- Test: `backend/tests/test_dashboard.py`

**Steps:**

1. Compose existing `today`, `inbox`, `tasks`, and `events` read-only logic with the new recent/week helpers.
2. Do not call mutation functions.
3. Do not expose raw action snapshots by default.
4. Add TestClient tests verifying:
   - empty DB response is calm and structured
   - seeded data appears in the right sections
   - no mutation occurs when `/dashboard` is called repeatedly
5. Run:

```bash
python -m pytest backend/tests/test_dashboard.py backend/tests/test_today.py -q
```

### Task 4: Serve static web shell

**Objective:** Add the browser page without adding frontend build tooling.

**Files:**

- Create: `backend/app/static/web/index.html`
- Create: `backend/app/static/web/web.css`
- Create: `backend/app/static/web/web.js`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_web_static.py`

**Steps:**

1. Serve `GET /web` as the static HTML shell.
2. Serve CSS/JS through FastAPI static mounting or explicit file responses.
3. HTML includes a clear read-only badge.
4. JS fetches only `GET /dashboard`.
5. Tests verify `/web` returns HTML and static assets return expected content types.
6. Tests inspect static files for forbidden mutation endpoint strings:
   - `POST /capture`
   - `/done`
   - `DELETE`
   - `PATCH`
   - `/undo`
   - `/promote`

### Task 5: Implement client-side rendering

**Objective:** Render dashboard payload into Now, Inbox, Tasks, Events, Week, and Recent Changes sections.

**Files:**

- Modify: `backend/app/static/web/web.js`
- Modify: `backend/app/static/web/web.css`
- Test: `backend/tests/test_web_static.py`

**Steps:**

1. Add pure JS render helpers for each section.
2. Escape user-provided text with `textContent`, not `innerHTML`.
3. Show calm empty states.
4. Add a refresh button that calls only `GET /dashboard`.
5. Add static tests checking no `innerHTML` assignment is used for user content unless reviewed and safe.

### Task 6: Document Phase 5 usage

**Objective:** Teach local users how to open the dashboard safely.

**Files:**

- Modify: `README.md`
- Optional Modify: `.env.example` only if a new documented placeholder is truly needed.

**Steps:**

1. Add a short `Phase 5 Web Memory` section.
2. Document:
   - `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`
   - open `http://127.0.0.1:8000/web`
   - dashboard is read-only
   - do not expose directly to public internet
3. Do not add deployment instructions.

### Task 7: Optional SSE spike, only if Phase 5 core is green

**Objective:** Decide whether SSE is worth adding now.

**Decision gate:** Skip SSE unless polling creates obvious UX/test complexity. A manual refresh or short polling interval is enough for Phase 5.

If implemented later:

- SSE endpoint must be read-only.
- No browser-to-server mutation channel.
- Tests must verify the event stream does not alter DB state.

Default decision: defer SSE.

## Test Plan

Run after each coherent slice:

```bash
python -m pytest backend/tests/test_dashboard.py -q
python -m pytest backend/tests/test_web_static.py -q
```

Full verification before commit/push:

```bash
python -m pytest backend/tests tui/tests -q
python -m ruff check backend/app backend/tests tui
```

Smoke check, only after tests are green:

```bash
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
curl -s http://127.0.0.1:8000/dashboard
curl -s http://127.0.0.1:8000/web
```

If using a background server for smoke checks, stop it before finalizing.

## Review Gates

Before committing Phase 5 implementation:

1. Confirm all new web routes are read-only.
2. Confirm static JS never calls `fetch` with `POST`, `PATCH`, or `DELETE`.
3. Confirm no auth/account/session/user concepts were added.
4. Confirm no `user_id`, roles, permissions, or cookies were added.
5. Confirm no secrets, personal names, local absolute paths, or private notes appear in docs/static files.
6. Confirm repeated dashboard loads do not mutate database state.
7. Confirm tests cover empty states and seeded data.
8. Confirm public safety grep passes.

## Commit Boundaries

Use meaningful, non-noisy commits:

1. `docs: add phase 5 web memory plan`
2. `feat: add read-only dashboard endpoint`
3. `feat: add read-only web memory dashboard`
4. `docs: document web memory dashboard`

Merge adjacent commits if a slice is too small to review independently. Do not inflate commit count artificially.

## Public-safety Grep

Before pushing:

```bash
git grep -n -E 'seojongho|서종호|/home/ubuntu|OPENROUTER_API_KEY=sk-|BEGIN (RSA|OPENSSH|PRIVATE) KEY|password\s*=|token\s*=|secret\s*=' -- . ':!*.lock'
```

Expected: no sensitive matches. Placeholder strings in `.env.example` are allowed only when they do not contain real values.
