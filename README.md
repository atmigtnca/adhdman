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

## Development

```bash
python -m pytest backend/tests -q
docker compose up --build
curl -s http://127.0.0.1:8000/health
```
