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

## Development

```bash
python -m pytest backend/tests -q
docker compose up --build
curl -s http://127.0.0.1:8000/health
```
