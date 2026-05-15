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

## Development

```bash
python -m pytest backend/tests -q
docker compose up --build
curl -s http://127.0.0.1:8000/health
```
