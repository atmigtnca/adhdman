# Phase 2 Intent & Classification Implementation Plan

**Goal:** Convert raw captured text into structured candidates (`task`, `event`, or `inbox`) without ever losing the original input. Every classification must either succeed under a strict JSON schema or fall back to the inbox so the capture-first guarantee from Phase 1 is preserved.

**Architecture:** Add a thin classification layer between `POST /capture` and persistence. The layer is composed of: (1) a deterministic rules pass that handles obvious shapes, (2) an optional LLM provider call via OpenRouter when an API key is configured, (3) a strict JSON schema validator with one repair attempt, and (4) a guaranteed inbox fallback. Keep the runtime local-first and single-user. Do not add auth, accounts, `user_id`, external calendar sync, public exposure, or web UI in this phase.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, SQLite, `httpx` for the OpenRouter call, `jsonschema` (or Pydantic) for validation, pytest with a fake transport for LLM tests.

---

## Product Constraints

- Capture-first: ambiguous or failed classification must still result in an inbox row; nothing is dropped.
- Recovery-first: every mutating outcome of classification (task created, event created, inbox stored) writes an `actions` row.
- One-thing principle: classification only produces a single best candidate, not a ranked list.
- Non-shaming tone: any user-visible classification message stays calm and restart-oriented.
- Single-user only: no auth, no accounts, no `user_id`, no roles, no sessions.
- Local-first only: no public deployment; LLM calls go out from the local process only when an API key is present in the local environment.
- Secrets: only `.env.example` may be committed. Never log API keys or full prompt/response bodies that contain user text in production logs.

## Phase 2 API Target

Phase 2 augments Phase 1 rather than replacing it. New and changed shapes:

```text
POST /capture           # extended response: includes classification result
POST /classify          # idempotent: classify text without persisting
GET  /inbox             # unchanged
GET  /tasks             # unchanged
GET  /events            # new: list events created by classification
```

`POST /capture` always stores something. Its response indicates which path was taken:

```json
{
  "inbox_item_id": 12,
  "classification": {
    "intent": "task",
    "confidence": 0.92,
    "source": "rules",
    "created": { "type": "task", "id": 7 }
  }
}
```

`POST /classify` returns the same `classification` block without writing rows. It exists for tests, the future TUI preview, and debugging.

## Classification Pipeline

The pipeline is a fixed order. Each stage may short-circuit. The final stage is always the inbox fallback so the pipeline cannot fail closed.

1. **Normalize** — trim, collapse internal whitespace, reject empty.
2. **Rules pass (deterministic)** — fast, offline, no network:
   - Datetime-like phrases (e.g. `tomorrow 3pm`, `at 14:00`, ISO timestamps) → candidate `event`.
   - Imperative verbs at start (`buy`, `pay`, `email`, `call`, `fix`, `write`, `read`, `send`, `book`, `schedule`) without a datetime → candidate `task`.
   - Bare nouns, questions, ambiguous fragments → `inbox`.
   - Rules produce a confidence in `[0.0, 1.0]`. Confidence ≥ `RULES_ACCEPT_THRESHOLD` short-circuits and skips the LLM.
3. **LLM pass (optional)** — only when `OPENROUTER_API_KEY` is present in the environment and rules did not short-circuit:
   - Provider: OpenRouter.
   - Model: `inclusionai/ring-2.6-1t`.
   - Endpoint: OpenRouter chat completions, called over `httpx` with a strict timeout.
   - The provider receives the normalized text plus a small system prompt that defines the JSON schema and the allowed intents.
4. **Schema validation** — the LLM response is parsed as JSON and validated against the strict schema below. On invalid JSON or schema mismatch, one **repair attempt** is made by re-prompting with the previous output and the validator error message.
5. **Inbox fallback** — if rules are inconclusive and either (a) no API key is configured, (b) the LLM call errors/times out, or (c) repair also fails validation, the item is stored as `inbox` with `classification.source = "fallback"` and a non-shaming reason code. The raw text is preserved verbatim.

## JSON Schema (Classifier Output)

Returned by both the LLM stage and the rules stage so downstream code is uniform.

```json
{
  "intent": "task | event | inbox",
  "confidence": 0.0,
  "title": "short normalized title",
  "starts_at": "ISO8601 or null",
  "ends_at": "ISO8601 or null",
  "reason": "short rationale, no chain-of-thought"
}
```

Validation rules:

- `intent` must be one of `task`, `event`, `inbox`.
- `confidence` must be a float in `[0.0, 1.0]`.
- `title` is required for `task` and `event`, optional for `inbox`.
- `starts_at` / `ends_at` are required only for `event`. They must parse as ISO8601 or be `null`.
- `reason` is bounded in length; oversize values are truncated, not rejected.
- Any other field is ignored.

## OpenRouter Provider

Module: `backend/app/llm/openrouter.py` (new).

Responsibilities:

- Read `OPENROUTER_API_KEY` and optional `OPENROUTER_BASE_URL` from settings. If the key is missing, the provider reports `available=False` and the pipeline skips stage 3.
- Use a single small system prompt that names the schema and forbids extra text.
- Call OpenRouter with model `inclusionai/ring-2.6-1t`, a low temperature, and a hard request timeout (default 8s).
- Never log the API key. Log request metadata (model, latency, status) but not the full user text in production logs; tests may capture it.
- Return a typed result: `LLMResult(text: str)` on success or `LLMError(kind, message)` on failure. The pipeline maps errors to fallback.

A provider abstraction (`LLMProvider` protocol) is introduced so tests can inject a fake transport without monkeypatching `httpx` directly. Only OpenRouter is implemented in Phase 2.

## Settings

Extend `backend/app/config.py` with:

- `OPENROUTER_API_KEY: str | None = None`
- `OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"`
- `OPENROUTER_MODEL: str = "inclusionai/ring-2.6-1t"`
- `LLM_TIMEOUT_SECONDS: float = 8.0`
- `RULES_ACCEPT_THRESHOLD: float = 0.85`
- `CLASSIFY_ENABLED: bool = True` — kill switch that forces every capture to land in the inbox.

`.env.example` gains placeholder lines for these keys with empty values. No real keys are committed.

## Database Changes

No schema migration is strictly required. The existing `inbox_items`, `tasks`, `events`, and `actions` tables from Phase 1 already cover the outcomes. Phase 2 adds:

- A small `classifications` table for diagnostics. Optional but recommended for recovery:

```sql
CREATE TABLE IF NOT EXISTS classifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  inbox_item_id INTEGER NOT NULL,
  intent TEXT NOT NULL,
  confidence REAL NOT NULL,
  source TEXT NOT NULL,
  raw_response TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(inbox_item_id) REFERENCES inbox_items(id)
);
```

`source` values: `rules`, `llm`, `repair`, `fallback`.

Action log additions (no schema change, just new `action_type` values):

- `classify_task`
- `classify_event`
- `classify_inbox_fallback`

## Repository / Service Layout

New module: `backend/app/classification/` with:

- `rules.py` — deterministic rule pass.
- `schema.py` — Pydantic models or JSON Schema for classifier output.
- `pipeline.py` — orchestrates normalize → rules → LLM → validate → repair → fallback.
- `repair.py` — single retry that re-prompts with the validator error.

New module: `backend/app/llm/openrouter.py` — provider implementation behind the `LLMProvider` protocol in `backend/app/llm/base.py`.

`POST /capture` is updated to call the pipeline. `POST /classify` is a new read-only endpoint that runs the pipeline without persistence.

## Testing Strategy

All tests run offline. No real network calls. Use a fake `LLMProvider` injected via FastAPI dependency override.

- **Rules tests** (`test_rules.py`): assert deterministic intent and confidence on a fixed corpus, including datetime-like phrases, imperatives, questions, and bare nouns.
- **Schema tests** (`test_classifier_schema.py`): valid payloads accepted, invalid payloads rejected with a clear error, oversize `reason` truncated.
- **Pipeline tests** (`test_pipeline.py`):
  - rules short-circuit at or above threshold; LLM not called.
  - LLM path invoked when rules are inconclusive and key is configured.
  - missing API key → never calls the provider; falls back to inbox.
  - LLM timeout / HTTP error → inbox fallback with `source = "fallback"`.
  - invalid JSON → one repair attempt; success path and final-fallback path both covered.
- **Provider tests** (`test_openrouter_provider.py`): use a fake transport to assert the request shape (model name `inclusionai/ring-2.6-1t`, auth header presence, timeout applied) without making real calls.
- **API tests** (`test_capture_classify.py`):
  - `POST /capture` with imperative text creates a task and an action row.
  - `POST /capture` with datetime-like text creates an event and an action row.
  - `POST /capture` with ambiguous text creates only an inbox row.
  - `POST /capture` with `CLASSIFY_ENABLED=False` always stores inbox only.
  - `POST /classify` returns a classification without writing rows.
- **Negative tests**: empty/whitespace text rejected at the API boundary, same as Phase 1.

Tests must never read or write the real local data file; use `tmp_path`-backed settings as in Phase 1.

## Commit Boundaries

1. `docs: add phase 2 intent classification plan`
2. `feat: add classifier schema and rules pass`
3. `feat: add openrouter llm provider`
4. `feat: wire classification pipeline into capture`
5. `feat: add /classify endpoint`
6. `feat: log classifications table and action types`
7. `docs: document phase 2 classification`

Refactor or test-only commits are allowed if they fall out naturally. Do not split commits artificially.

## Out of Scope for Phase 2

- login, auth, accounts, sessions, roles
- multi-user, `user_id`, permissions
- external calendar sync
- public runtime deployment
- TUI implementation (later phase)
- web dashboard (later phase)
- SSE / realtime (later phase)
- multiple LLM providers beyond OpenRouter
- streaming responses, tool use, function calling
- prompt-cache tuning or model selection UI
- undo endpoint (still deferred)
- priority scoring, recurring tasks, reminders

## Review Gates

Before pushing implementation commits:

1. Run targeted tests for the changed behavior.
2. Run the full test suite offline; no test may hit the network.
3. Inspect `git diff` for scope creep, accidental auth/multi-user concepts, hard-coded local paths, or committed secrets.
4. Confirm that with `OPENROUTER_API_KEY` unset, the entire pipeline still passes tests and `POST /capture` still never drops input.
5. Confirm that with `CLASSIFY_ENABLED=False`, behavior is byte-for-byte equivalent to Phase 1 capture.
