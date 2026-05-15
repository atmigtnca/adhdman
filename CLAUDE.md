# ADHDman Project Context

## Product

ADHDman is a local-first, strictly single-user execution support system.

The app captures natural-language events, tasks, and ambiguous thoughts; stores them safely; shows only what matters now; and supports recovery through inbox fallback, undo, and action logs.

## Architecture

- Backend: Python + FastAPI
- DB: local SQLite
- TUI: Python + Textual, primary input surface, later phase
- Web: read-only dashboard, later phase
- Realtime: SSE, later phase
- LLM: provider abstraction, later phase

## Non-negotiable Constraints

- Do not add login/auth/account systems unless the product direction explicitly changes.
- Do not add multi-user support.
- Do not add `user_id`, roles, permissions, or session-user separation.
- Do not add external calendar sync unless explicitly requested.
- Do not commit secrets. Use `.env.example` only.
- Do not expose the app directly to the public internet; keep it bound to localhost or behind SSH tunnel, VPN, or an external access-control layer.

## Product Principles

- Capture-first: never discard ambiguous input; later phases should fall back to inbox.
- Recovery-first: undo/action logs are core trust primitives.
- One-thing principle: show “one thing now” rather than overwhelming lists.
- Non-shaming tone: help restart; do not guilt or judge the user.

## Phase 0 Commands

```bash
python -m pytest backend/tests -q
docker compose up --build
curl -s http://127.0.0.1:8000/health
```
