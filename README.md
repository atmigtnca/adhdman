# ADHDman

ADHDman is a local-first, single-user execution support system for capturing tasks, events, and ambiguous thoughts with low friction.

The project is intentionally not a public SaaS. It has no built-in login, account, role, or multi-user system. Access control is expected to be handled outside the app through local binding, SSH tunnels, VPNs, or another trusted access-control layer.

## Security model

This app has no built-in authentication by design.

Do not expose it directly to the public internet.

Recommended deployment posture:

- Bind services to `127.0.0.1` by default.
- Use SSH tunneling, VPN, or a trusted external access-control layer for remote access.
- Keep real secrets in `.env` only; commit `.env.example` with placeholder values.
- Keep local SQLite data out of git.

## Phase 0 scope

Current foundation scope:

- FastAPI backend
- `/health` endpoint
- environment-based config loader
- SQLite path helper
- Docker Compose backend service
- pytest health/config tests

Out of scope for Phase 0:

- LLM intent harness
- TUI
- Web dashboard
- SSE
- CRUD/domain schema
- remote deployment

## Phase 1 API examples

Run the backend locally, then exercise the Phase 1 capture core with these requests:

```bash
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/capture -H 'Content-Type: application/json' -d '{"text":"pay rent"}'
curl -s http://127.0.0.1:8000/inbox
curl -s -X POST http://127.0.0.1:8000/inbox/1/promote-task
curl -s http://127.0.0.1:8000/tasks
curl -s -X POST http://127.0.0.1:8000/tasks/1/done
curl -s http://127.0.0.1:8000/today
```

Reminder: ADHDman is local-first and has no built-in authentication. Keep it bound to localhost or place it behind SSH tunneling, VPN, or another trusted access-control layer; do not expose it directly to the public internet.

## Phase 2 API examples

Phase 2 adds an intent-classification layer on top of capture. Every `POST /capture` still stores the raw text as an inbox row first, then runs the classifier. If the classifier produces a high-confidence `task` or `event`, the inbox row is promoted and a `task`/`event` row is created. Otherwise the item stays in the inbox under the capture-first guarantee.

```bash
curl -s -X POST http://127.0.0.1:8000/capture \
  -H 'Content-Type: application/json' \
  -d '{"text":"buy milk"}'
curl -s -X POST http://127.0.0.1:8000/capture \
  -H 'Content-Type: application/json' \
  -d '{"text":"Dentist 2026-07-04T09:00"}'
curl -s -X POST http://127.0.0.1:8000/classify \
  -H 'Content-Type: application/json' \
  -d '{"text":"groceries"}'
curl -s http://127.0.0.1:8000/events
```

`POST /capture` response shape (Phase 2):

```json
{
  "id": 1,
  "inbox_item_id": 1,
  "text": "buy milk",
  "status": "promoted",
  "created_at": "...",
  "updated_at": "...",
  "classification": {
    "intent": "task",
    "confidence": 0.9,
    "source": "rules",
    "title": "buy milk",
    "starts_at": null,
    "ends_at": null,
    "reason": "...",
    "created": { "type": "task", "id": 1 }
  }
}
```

`POST /classify` is a read-only preview: it returns the same classification block without writing inbox, task, event, action, or classification rows.

`GET /events` lists events created by the classifier, ordered by `starts_at` ascending (events without a start time sort last).

Diagnostic table: every persisted classification also writes a row into `classifications` (`inbox_item_id`, `intent`, `confidence`, `source`, `raw_response`, `created_at`) so recovery and debugging can reconstruct what the classifier decided. `source` is one of `rules`, `llm`, `repair`, `fallback`.

### Local-only LLM configuration

The LLM stage is optional. When `OPENROUTER_API_KEY` is unset, the pipeline runs the deterministic rules pass and falls back to the inbox for anything ambiguous; no network call is made. When the key is set in your local `.env`, the pipeline may call OpenRouter for inputs that the rules pass cannot classify confidently.

Configuration (placeholders only — never commit real keys):

```bash
# .env (local only; not committed)
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=inclusionai/ring-2.6-1t
LLM_TIMEOUT_SECONDS=8.0
RULES_ACCEPT_THRESHOLD=0.85
CLASSIFY_ENABLED=true
```

Setting `CLASSIFY_ENABLED=false` reverts capture to Phase 1 semantics: every `POST /capture` only writes an inbox row and a `capture` action; no task/event/classification row is produced.

The LLM call is initiated by the local process only. ADHDman remains local-first: bind to `127.0.0.1`, do not expose it to the public internet, and treat the OpenRouter key as a local secret.

## Phase 3 API examples

Phase 3 makes captured rows safely editable, deletable, and reversible.

```bash
# Resolve a free-form datetime phrase (read-only)
curl -s -X POST http://127.0.0.1:8000/resolve \
  -H 'Content-Type: application/json' \
  -d '{"text":"tomorrow 3pm","tz":"America/Los_Angeles"}'

# Edit a task / event by id (logs a full before/after snapshot)
curl -s -X PATCH http://127.0.0.1:8000/tasks/1 \
  -H 'Content-Type: application/json' \
  -d '{"title":"call dentist","due_at":"2026-05-20T10:00"}'
curl -s -X PATCH http://127.0.0.1:8000/events/1 \
  -H 'Content-Type: application/json' \
  -d '{"starts_at":"2026-06-02T10:00"}'

# Soft-delete (recoverable via undo)
curl -s -X DELETE http://127.0.0.1:8000/tasks/1
curl -s -X DELETE http://127.0.0.1:8000/events/1

# Find candidates by free-form text (READ-ONLY; never mutates)
curl -s -X POST http://127.0.0.1:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"dentist"}'

# Reverse a specific action or the most recent one
curl -s -X POST http://127.0.0.1:8000/undo/12
curl -s -X POST http://127.0.0.1:8000/undo/latest
```

`POST /search` is the safe candidate-selection layer. It returns up to
`SEARCH_MAX_CANDIDATES` (default 5) scored candidates across tasks, events, and
open inbox items. Soft-deleted rows are excluded. The response also reports an
`ambiguous` flag when the top two candidates' scores are within
`SEARCH_AMBIGUITY_THRESHOLD` of each other, so callers can disambiguate before
calling a mutating endpoint. Mutations never accept free-form text as a target
selector — the caller must pass an explicit id to `PATCH`/`DELETE`.

```json
{
  "query": "dentist",
  "candidates": [
    { "type": "event", "id": 12, "title": "dentist checkup", "starts_at": "2026-05-20T10:00:00-07:00", "score": 0.88 },
    { "type": "task",  "id":  7, "title": "call dentist",     "starts_at": null,                       "score": 0.61 }
  ],
  "ambiguous": false,
  "max_candidates": 5,
  "ambiguity_threshold": 0.15
}
```

Phase 3 configuration (placeholders only; never commit real values):

```bash
# .env (local only)
LOCAL_TIMEZONE=UTC
SEARCH_MAX_CANDIDATES=5
SEARCH_AMBIGUITY_THRESHOLD=0.15
UNDO_ENABLED=true
```

## Phase 5 Web Memory

Phase 5 adds a localhost-only, read-only web dashboard that helps you remember what already exists in ADHDman. The page is a small static shell served by the FastAPI backend; the browser fetches only `GET /dashboard` and renders Now, Inbox, Tasks, Events, Week, and Recent Changes sections. There are no forms, no mutation buttons, and no auth surface — Phase 1–3 mutation endpoints remain API/TUI-only.

```bash
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000/web in a local browser
```

The dashboard is read-only by design: the refresh control only re-fetches `GET /dashboard`. Do not expose this app directly to the public internet; keep it bound to `127.0.0.1` or behind SSH tunneling, VPN, or another trusted access-control layer.

## Phase 6 Execution Helpers

Phase 6 adds a small set of opinionated execution helpers on top of the Phase 1–5 capture/resolve/dashboard core. These helpers do not capture new data — they help with starting, continuing, or surviving when the existing surfaces feel too heavy. Helpers are local-only, single-user, state-light, recoverable via `/undo`, and use non-shaming tone strings from `backend/app/copy.py`.

The helpers never call a remote presence service. Body-double is a local timer + render contract. Mutations write `actions` rows so `/undo` reverses them like any other Phase 3 change. The web dashboard surfaces focus/body-double/survival state read-only — no start/stop controls on the web side.

### Focus (one thing)

```bash
curl -s http://127.0.0.1:8000/focus/current
curl -s -X POST http://127.0.0.1:8000/focus/start \
  -H 'Content-Type: application/json' \
  -d '{"target_type":"task","target_id":1}'
curl -s -X POST http://127.0.0.1:8000/focus/stop
```

Only one focus session is active at a time. Starting a second session without `"replace": true` returns a 409 with a structured `existing` payload rather than silently swapping.

### Breakdown

```bash
curl -s -X POST http://127.0.0.1:8000/tasks/1/breakdown/suggest \
  -H 'Content-Type: application/json' \
  -d '{}'
curl -s -X POST http://127.0.0.1:8000/tasks/1/breakdown \
  -H 'Content-Type: application/json' \
  -d '{"steps":["open the document","write one paragraph"],"source":"manual"}'
curl -s http://127.0.0.1:8000/tasks/1/children
```

Children are normal task rows linked by `parent_task_id`; a single `breakdown` action covers all children, so one `/undo` reverses the whole split. Suggestions are deterministic and rules-only in this phase.

### Block reset (stuck)

```bash
curl -s "http://127.0.0.1:8000/stuck/options?target_type=task&target_id=1"
curl -s -X POST http://127.0.0.1:8000/stuck \
  -H 'Content-Type: application/json' \
  -d '{"target_type":"task","target_id":1,"choice":"shrink"}'
```

`choice` is one of `shrink`, `swap`, `skip`, `park`. The prompt and labels come from the shared non-shaming copy library so TUI and web render the same text.

### Body double

```bash
curl -s -X POST http://127.0.0.1:8000/body-double/start \
  -H 'Content-Type: application/json' \
  -d '{"interval_seconds":300}'
curl -s -X POST http://127.0.0.1:8000/body-double/check-in
curl -s http://127.0.0.1:8000/body-double/current
curl -s -X POST http://127.0.0.1:8000/body-double/stop
```

`interval_seconds` must fall between `BODY_DOUBLE_MIN_INTERVAL` and `BODY_DOUBLE_MAX_INTERVAL`. No external call is made — heartbeats are recorded locally on the active `focus_sessions` row.

### Minimum viable step (mvs)

```bash
curl -s -X POST http://127.0.0.1:8000/mvs/suggest \
  -H 'Content-Type: application/json' \
  -d '{"target_type":"task","target_id":1}'
curl -s -X POST http://127.0.0.1:8000/mvs/commit \
  -H 'Content-Type: application/json' \
  -d '{"target_type":"task","target_id":1,"step":"open the document"}'
```

`/mvs/commit` creates a child task carrying the step and starts a focus session on it; both actions are individually reversible via `/undo`.

### Survival mode

```bash
curl -s -X POST http://127.0.0.1:8000/survival/enter \
  -H 'Content-Type: application/json' -d '{}'
curl -s http://127.0.0.1:8000/survival
curl -s http://127.0.0.1:8000/dashboard
curl -s -X POST http://127.0.0.1:8000/survival/exit \
  -H 'Content-Type: application/json' -d '{}'
```

Survival mode is a filter, not a delete: while active, `/dashboard` clamps tasks and events to `SURVIVAL_MAX_TASKS` and `SURVIVAL_MAX_EVENTS`. Nothing is removed and capture still works.

### TUI commands

Phase 6 maps each helper to a slash command. Target arguments index into the most recent listing in the same TUI session (free-form text is never used as a mutation target). `/body-double N` is the exception: `N` is a local check-in interval in seconds.

```
/focus            show current focus session
/focus N          focus on item N from the last listing
/focus stop       end the focus session
/breakdown N      suggest 2–5 micro-steps for task N
/breakdown commit persist the last suggestion as child tasks
/stuck            show block-reset options
/stuck CHOICE     apply shrink|swap|skip|park to the last-selected task
/body-double N    start a body-double timer with N-second cadence
/body-double check-in   record a heartbeat
/body-double stop end the body-double session
/mvs N            suggest one minimum-viable step for item N
/mvs commit       commit the suggested step and focus on it
/survival on      enter survival mode
/survival off     exit survival mode
/survival         show survival-mode state
```

### Read-only Web dashboard

`GET /dashboard` returns a `focus` block alongside the existing Now/Inbox/Tasks/Events/Week/Recent sections:

```json
{
  "focus": {
    "session": {
      "id": 4,
      "kind": "focus",
      "target_type": "task",
      "target_id": 7,
      "status": "active",
      "started_at": "...",
      "ended_at": null,
      "interval_seconds": null,
      "note": null,
      "last_check_in_at": null
    },
    "target": { "type": "task", "id": 7, "title": "open the document" },
    "body_double": null,
    "survival": false
  }
}
```

The web page at `http://127.0.0.1:8000/web` renders this as a Focus panel under Now, tags the header `Survival mode` when active, and never adds start/stop controls. Mutations stay API/TUI only.

### Phase 6 configuration

Placeholders only; never commit real values:

```bash
# .env (local only)
BODY_DOUBLE_DEFAULT_INTERVAL=300
BODY_DOUBLE_MIN_INTERVAL=60
BODY_DOUBLE_MAX_INTERVAL=1800
SURVIVAL_MAX_TASKS=1
SURVIVAL_MAX_EVENTS=1
```

Phase 6 keeps helper suggestions deterministic and rules-only. Existing Phase 2 settings (`OPENROUTER_API_KEY`, `LLM_TIMEOUT_SECONDS`, `CLASSIFY_ENABLED`) still apply to the capture/classification pipeline.

Reminder: ADHDman has no built-in authentication. Keep it bound to `127.0.0.1` and place it behind SSH tunneling, VPN, or another trusted access-control layer; do not expose it directly to the public internet.

## Development

```bash
python -m pytest backend/tests -q
docker compose up --build
curl -s http://127.0.0.1:8000/health
```
