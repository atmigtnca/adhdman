# Phase 1 Capture Core Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task after Seojongho explicitly says "구현 시작" or equivalent.

**Goal:** Build the smallest reliable ADHDman core that never loses user input: capture every thought into a local inbox, then allow simple promotion into tasks and a minimal "one thing now" summary.

**Architecture:** Keep the app local-first and single-user. Use FastAPI endpoints over a small SQLite schema with repository functions, Pydantic request/response schemas, and action logging for recovery. Do not add login, accounts, `user_id`, external calendar sync, LLM parsing, web UI, or public runtime exposure.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, SQLite from the standard library, pytest, Docker Compose.

---

## Product Constraints

- Capture-first: valid non-empty input must be stored even when it is ambiguous.
- Recovery-first: mutating operations should create an `actions` row.
- One-thing principle: `/today` returns one suggested next item, not a giant dashboard.
- Non-shaming tone: empty/fallback messages should sound calm and restart-oriented.
- Single-user only: do not add auth, accounts, roles, permissions, sessions, or `user_id`.
- Local-first only: keep the runtime bound to localhost; do not deploy publicly in this phase.

## Phase 1 API Target

```text
GET  /health
POST /capture
GET  /inbox
POST /inbox/{id}/promote-task
GET  /tasks
POST /tasks/{id}/done
GET  /today
```

`events` are included in the schema because they are part of the core domain, but event promotion is intentionally deferred unless Phase 1 has spare capacity after the task flow is stable.

## Database Schema

Use plain SQLite and an `init_db()` function. Keep migrations out of Phase 1; this is still early schema foundation.

### `inbox_items`

```sql
CREATE TABLE IF NOT EXISTS inbox_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  promoted_to_type TEXT,
  promoted_to_id INTEGER
);
```

Allowed `status` values for Phase 1:

```text
open
promoted
archived
```

### `tasks`

```sql
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  source_inbox_item_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(source_inbox_item_id) REFERENCES inbox_items(id)
);
```

Allowed `status` values for Phase 1:

```text
open
done
cancelled
```

### `events`

```sql
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  starts_at TEXT,
  ends_at TEXT,
  source_inbox_item_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(source_inbox_item_id) REFERENCES inbox_items(id)
);
```

### `actions`

```sql
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action_type TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id INTEGER NOT NULL,
  before_json TEXT,
  after_json TEXT,
  created_at TEXT NOT NULL
);
```

## Testing Strategy

- Use temporary SQLite files via pytest `tmp_path`; never touch the real local data file in tests.
- Keep tests behavior-focused, not implementation-coupled.
- API tests should use FastAPI `TestClient`.
- Repository tests should verify persisted rows and action log side effects.
- Every mutating endpoint should have at least one success test and one edge/failure test.

## Commit Boundaries

Keep commits meaningful, not artificially inflated:

1. `docs: add phase 1 capture core plan`
2. `feat: add phase 1 database schema`
3. `feat: capture input to inbox`
4. `feat: list inbox items`
5. `feat: promote inbox items to tasks`
6. `feat: manage task completion`
7. `feat: add today summary`
8. `docs: document phase 1 API`

If implementation naturally needs a separate refactor or test-only commit, that is acceptable. Do not split commits merely to increase count.

---

## Task 1: Add Phase 1 Database Schema

**Objective:** Create SQLite schema initialization for `inbox_items`, `tasks`, `events`, and `actions`.

**Files:**

- Modify: `backend/app/db.py`
- Create: `backend/tests/test_db.py`

**Step 1: Write failing tests**

Add tests that use a temporary `Settings` instance with `DATABASE_PATH` pointing inside `tmp_path`.

Required behavior:

- `ensure_database_parent()` creates the parent directory.
- `init_db(settings)` creates all four tables.
- `init_db(settings)` is idempotent.

**Step 2: Run test to verify failure**

```bash
python -m pytest backend/tests/test_db.py -q
```

Expected: fails because `init_db()` does not exist yet.

**Step 3: Implement schema initialization**

In `backend/app/db.py`:

- import `sqlite3`
- add `get_connection(settings: Settings | None = None) -> sqlite3.Connection`
- add `init_db(settings: Settings | None = None) -> Path`
- enable foreign keys with `PRAGMA foreign_keys = ON`
- create the four tables using `CREATE TABLE IF NOT EXISTS`

**Step 4: Run verification**

```bash
python -m pytest backend/tests/test_db.py -q
python -m pytest backend/tests -q
```

Expected: all tests pass.

**Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_db.py
git commit -m "feat: add phase 1 database schema"
```

---

## Task 2: Capture Input to Inbox

**Objective:** Add `POST /capture` so non-empty text is always stored as an open inbox item.

**Files:**

- Create: `backend/app/schemas.py`
- Create: `backend/app/repositories.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_capture.py`

**Step 1: Write failing API tests**

Required behavior:

- `POST /capture` with `{ "text": "pay rent" }` returns `200` or `201` with a new inbox item id.
- Whitespace-only text is rejected.
- Captured text appears in the database as `status='open'`.
- Capture creates an `actions` row with `action_type='capture'`.

**Step 2: Run test to verify failure**

```bash
python -m pytest backend/tests/test_capture.py -q
```

Expected: fails because `/capture` does not exist.

**Step 3: Implement schemas and repository function**

Suggested schemas:

```python
class CaptureRequest(BaseModel):
    text: str = Field(min_length=1)

class InboxItemResponse(BaseModel):
    id: int
    text: str
    status: str
    created_at: str
    updated_at: str
```

Repository behavior:

- normalize by trimming text
- reject empty normalized text
- insert inbox row
- insert action row
- return the inserted item

**Step 4: Add endpoint**

`POST /capture` should call the repository and return a simple response.

**Step 5: Run verification**

```bash
python -m pytest backend/tests/test_capture.py -q
python -m pytest backend/tests -q
```

**Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/repositories.py backend/app/main.py backend/tests/test_capture.py
git commit -m "feat: capture input to inbox"
```

---

## Task 3: List Inbox Items

**Objective:** Add `GET /inbox` for reviewing open captured items.

**Files:**

- Modify: `backend/app/repositories.py`
- Modify: `backend/app/main.py`
- Create or modify: `backend/tests/test_inbox.py`

**Step 1: Write failing tests**

Required behavior:

- `GET /inbox` returns captured open inbox items.
- Default ordering is oldest first.
- Promoted items are not returned by default.

**Step 2: Run test to verify failure**

```bash
python -m pytest backend/tests/test_inbox.py -q
```

**Step 3: Implement repository and endpoint**

Add a repository function like `list_inbox_items(status: str = "open")` and an endpoint returning a list of inbox item responses.

**Step 4: Run verification**

```bash
python -m pytest backend/tests/test_inbox.py -q
python -m pytest backend/tests -q
```

**Step 5: Commit**

```bash
git add backend/app/repositories.py backend/app/main.py backend/tests/test_inbox.py
git commit -m "feat: list inbox items"
```

---

## Task 4: Promote Inbox Items to Tasks

**Objective:** Convert an open inbox item into an open task without losing the original input.

**Files:**

- Modify: `backend/app/schemas.py`
- Modify: `backend/app/repositories.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_promote_task.py`

**Step 1: Write failing tests**

Required behavior:

- `POST /inbox/{id}/promote-task` creates a task with `title` copied from inbox text by default.
- The source inbox item becomes `status='promoted'`.
- The source inbox item stores `promoted_to_type='task'` and `promoted_to_id=<task id>`.
- An action row with `action_type='promote_task'` is created.
- Promoting a missing or non-open inbox item returns a clear 404 or 409 response.

**Step 2: Run test to verify failure**

```bash
python -m pytest backend/tests/test_promote_task.py -q
```

**Step 3: Implement promotion in a transaction**

Use one SQLite transaction so task creation and inbox status update cannot diverge.

**Step 4: Run verification**

```bash
python -m pytest backend/tests/test_promote_task.py -q
python -m pytest backend/tests -q
```

**Step 5: Commit**

```bash
git add backend/app/schemas.py backend/app/repositories.py backend/app/main.py backend/tests/test_promote_task.py
git commit -m "feat: promote inbox items to tasks"
```

---

## Task 5: Manage Task Completion

**Objective:** Add task listing and completion endpoints.

**Files:**

- Modify: `backend/app/schemas.py`
- Modify: `backend/app/repositories.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_tasks.py`

**Step 1: Write failing tests**

Required behavior:

- `GET /tasks` returns open tasks oldest first.
- `POST /tasks/{id}/done` marks an open task as done.
- Completion sets `completed_at`.
- Completion creates an action row with `action_type='complete_task'`.
- Completing a missing or non-open task returns a clear 404 or 409 response.

**Step 2: Run test to verify failure**

```bash
python -m pytest backend/tests/test_tasks.py -q
```

**Step 3: Implement task list and completion**

Keep task completion simple. Do not add due dates, priority, next_action, or recurring tasks in Phase 1.

**Step 4: Run verification**

```bash
python -m pytest backend/tests/test_tasks.py -q
python -m pytest backend/tests -q
```

**Step 5: Commit**

```bash
git add backend/app/schemas.py backend/app/repositories.py backend/app/main.py backend/tests/test_tasks.py
git commit -m "feat: manage task completion"
```

---

## Task 6: Add Today Summary

**Objective:** Add `GET /today` with a minimal one-thing summary.

**Files:**

- Modify: `backend/app/schemas.py`
- Modify: `backend/app/repositories.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_today.py`

**Step 1: Write failing tests**

Required behavior:

- Response includes `open_tasks_count`.
- Response includes `inbox_count`.
- If open tasks exist, `one_thing` is the oldest open task.
- If no open tasks exist but open inbox items exist, `one_thing` is the oldest open inbox item.
- If nothing exists, `one_thing` is null and message is non-shaming.

**Step 2: Run test to verify failure**

```bash
python -m pytest backend/tests/test_today.py -q
```

**Step 3: Implement summary function and endpoint**

Suggested response shape:

```json
{
  "open_tasks_count": 1,
  "inbox_count": 2,
  "one_thing": {
    "type": "task",
    "id": 1,
    "text": "pay rent"
  },
  "message": "One thing is ready."
}
```

Empty response message example:

```text
Nothing is waiting right now. You can capture the next thought when it appears.
```

**Step 4: Run verification**

```bash
python -m pytest backend/tests/test_today.py -q
python -m pytest backend/tests -q
```

**Step 5: Commit**

```bash
git add backend/app/schemas.py backend/app/repositories.py backend/app/main.py backend/tests/test_today.py
git commit -m "feat: add today summary"
```

---

## Task 7: Document Phase 1 API

**Objective:** Update public documentation with local-only Phase 1 usage examples.

**Files:**

- Modify: `README.md`

**Step 1: Add API examples**

Include curl examples for:

```bash
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/capture \
  -H 'Content-Type: application/json' \
  -d '{"text":"pay rent"}'
curl -s http://127.0.0.1:8000/inbox
curl -s -X POST http://127.0.0.1:8000/inbox/1/promote-task
curl -s http://127.0.0.1:8000/tasks
curl -s -X POST http://127.0.0.1:8000/tasks/1/done
curl -s http://127.0.0.1:8000/today
```

**Step 2: Re-run verification**

```bash
python -m pytest backend/tests -q
```

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document phase 1 API"
```

---

## Final Phase 1 Verification

Run all local tests:

```bash
python -m pytest backend/tests -q
```

Run Docker build/start:

```bash
docker compose up --build
```

In another shell:

```bash
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/capture \
  -H 'Content-Type: application/json' \
  -d '{"text":"pay rent"}'
curl -s http://127.0.0.1:8000/inbox
curl -s -X POST http://127.0.0.1:8000/inbox/1/promote-task
curl -s http://127.0.0.1:8000/tasks
curl -s http://127.0.0.1:8000/today
```

Expected result:

- health returns `{"status":"ok"}`
- capture returns an inbox item id
- inbox shows the captured item before promotion
- promote-task creates a task
- tasks shows the task
- today returns counts and one suggested item

## Review Gates

Before pushing implementation commits:

1. Run targeted tests for the changed behavior.
2. Run the full test suite.
3. Inspect `git diff` for scope creep, accidental auth/multi-user concepts, local paths, or secrets.
4. Use a clean-context review for significant diffs.
5. Push only after tests and review pass.

## Out of Scope for Phase 1

- login/auth/accounts
- multi-user support or `user_id`
- external calendar sync
- LLM parsing/classification
- TUI
- web dashboard
- notifications
- public runtime deployment
- priority scoring
- recurring tasks
- advanced event parsing
- undo endpoint implementation
