# Phase 8 Contextual Priority Engine Implementation Plan

> **For Hermes:** Do not implement this plan until Seojongho explicitly approves implementation. Use `adhdman-orchestration-workflow`, `test-driven-development`, and clean-context reviews for each implementation slice.

**Goal:** Make ADHDman choose and display the single most important next thing from many tasks/events, using deadlines, time windows, dependencies, current time, and ADHD execution principles.

**Architecture:** Keep ADHDman local-first and single-user. Add a priority/agenda layer that reads existing tasks/events/inbox state and produces a compact “now / next / later” recommendation. TUI remains the input/control surface; Web UI becomes the read-only external memory that always shows what the user should do now. LLM can help parse natural-language inputs, but final ordering must be deterministic and inspectable.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Textual TUI, static read-only Web UI, pytest. No login/auth/multi-user.

---

## Product Blueprint From User

ADHD users often cannot hold several incoming tasks in working memory. When many tasks arrive, they are easily forgotten, delayed, or all missed. ADHDman should not merely store tasks; it should continuously decide what deserves attention now.

Example input stream:

```text
6월 2일 13시까지 cpp과제
6월 1일 01시까지 db 과제
6월 23일 kcc 학회
오늘 13시~18시 오스카 모임
오스카모임 전까지 과제 끝내기
```

Expected behavior:

1. Before the Oscar meeting, the Web UI should show that the user needs to finish the assignment before the Oscar meeting.
2. When the user marks that assignment done, ADHDman should surface the 13:00–18:00 Oscar meeting.
3. After that time passes, ADHDman should surface the next urgent academic tasks.
4. Because the DB assignment deadline is earlier than the CPP assignment deadline, ADHDman should recommend DB first.
5. The user should not need to manually calculate priority or remember all tasks.

This is the core product behavior, not a polish feature.

## Manual Principles to Encode

Source file currently available locally:

```text
/home/ubuntu/.hermes/profiles/seojongho/cache/documents/doc_c02677342053_ADHD_.md
```

Relevant principles from the ADHD execution manual, summarized without embedding long copyrighted text:

- Behavior is not willpower; it needs environment/system design.
- Choose exactly one thing when overwhelmed.
- Large work should be sliced into small pieces that feel doable.
- A task chunk should ideally be small enough to start within minutes.
- Checklists and completion feedback produce momentum.
- In crisis/deadline mode, Minimum Viable Submission is better than missing entirely.
- Split time into blocks to reduce fear and restart after failure.
- A day can restart at morning/afternoon/evening boundaries.
- On low-energy days, survival mode and strategic dropping are valid.
- Tone must avoid shame and support restarting.

## Key Product Concept

ADHDman needs a **Contextual Priority Engine**.

It should answer:

```text
지금 뭐 해야 해?
왜 이걸 해야 해?
다음엔 뭐가 와?
이걸 끝내면 화면이 어떻게 바뀌어?
```

Not:

```text
전체 task 목록은 여기 있어. 네가 알아서 골라.
```

## Data Concepts Needed

Current entities already exist:

- `tasks`
- `events`
- `inbox_items`
- `actions`
- `focus_sessions`

Likely additions or derived fields:

```text
Task.due_at             absolute deadline
Task.do_before_event_id optional event dependency
Task.do_before_at       derived/latest finish time
Task.estimated_minutes  optional rough size
Task.priority_score     derived, not stored initially
Task.block_state        already exists for stuck/survival helpers
Event.starts_at
Event.ends_at
```

Important: prefer **derived priority** first instead of storing fragile priority numbers.

## Priority Rules v1

The first version should be deterministic and easy to explain.

### Candidate categories

1. Active focus task
2. Task that must be completed before an upcoming event
3. Ongoing or soon-starting event
4. Deadline task ordered by due time
5. Overdue task ordered by urgency/age
6. Inbox fallback if there is nothing scheduled
7. Survival mode override if active

### Suggested ordering

At a given `now`:

1. If survival mode is active:
   - show only survival-capped now state.
2. If there is an active focus target and it is still valid:
   - keep showing it unless blocked/completed/deleted.
3. If there is a task with `do_before_at <= upcoming_event.starts_at` and the event is upcoming soon:
   - show that task before the event.
   - Example: “오스카모임 전까지 과제 끝내기” should beat merely showing “오스카 모임” before 13:00.
4. If an event is currently happening or starts very soon:
   - show the event.
5. If no blocking pre-event task exists:
   - show the earliest deadline task.
6. If multiple tasks are due:
   - earlier deadline wins.
   - DB due June 1 01:00 beats CPP due June 2 13:00.
7. If a task is too large or user says it is blocked:
   - suggest breakdown/MVS/stuck flow instead of showing the full task as the only action.

## Example Scenario Acceptance Test

Use a deterministic clock.

Seed:

```text
now = 2026-05-31 20:00 Asia/Seoul
Task A: CPP assignment due 2026-06-02 13:00
Task B: DB assignment due 2026-06-01 01:00
Event C: KCC conference on 2026-06-23
Event D: Oscar meeting today 13:00-18:00
Task E: Finish assignment before Oscar meeting, do_before_event=Oscar meeting
```

Expected:

- Before Oscar meeting: `now` recommendation is Task E.
- After Task E is completed and before/during meeting window: recommendation is Event D.
- After Event D passes: recommendation is Task B, because DB deadline is earlier than CPP.
- After Task B done: recommendation is Task A.
- KCC conference is visible as later/upcoming, but should not displace urgent assignments.

The exact seed date should be adjusted so “today 13:00–18:00” is coherent in tests.

## Backend API Shape

Prefer adding a read-only endpoint:

```text
GET /agenda/now
```

Response idea:

```json
{
  "now": {
    "kind": "task",
    "id": 123,
    "title": "오스카모임 전까지 과제 끝내기",
    "reason": "13:00 오스카 모임 전에 끝내야 해서 지금 먼저 보여줘요.",
    "urgency": "before_event",
    "suggested_commands": ["/집중 1", "/쪼개기 1", "/막힘"]
  },
  "next": [
    {"kind": "event", "title": "오스카 모임", "starts_at": "..."},
    {"kind": "task", "title": "db 과제", "due_at": "..."}
  ],
  "later": [...],
  "counts": {"tasks": 3, "events": 2, "inbox": 0}
}
```

Do not mutate state from this endpoint.

## TUI Behavior

TUI should support Korean-first input and display:

```text
6월 2일 13시까지 cpp과제
6월 1일 01시까지 db 과제
6월 23일 kcc 학회
오늘 13시~18시 오스카 모임
오스카모임 전까지 과제 끝내기
```

After capture/classification, TUI should say in user language:

```text
보관했어. 마감/일정 기준으로 지금 볼 순서를 다시 계산할게.
```

Commands:

```text
/오늘       current now recommendation
/다음       next item(s)
/전체       compact later list, not overwhelming
/집중 N
/완료 N
/쪼개기 N
/최소단계 N
/막힘
```

Avoid requiring the user to know endpoint terms.

## Web UI Behavior

Web UI should become the read-only external memory.

Primary area:

```text
지금 해야 할 것
오스카모임 전까지 과제 끝내기
이유: 13:00 오스카 모임 전에 끝내야 해서 지금 먼저 보여줘요.
```

Secondary area:

```text
다음
13:00–18:00 오스카 모임
6월 1일 01:00 db 과제
6월 2일 13:00 cpp 과제
6월 23일 kcc 학회
```

The Web UI should not show a flat list as the main experience. The main experience is one thing now.

## LLM Role

Allowed:

- Parse natural input into task/event/inbox.
- Infer due times from phrases like “까지”.
- Infer “before event” dependency from phrases like “오스카모임 전까지”.
- Suggest short reason text.

Not allowed:

- Silently mutate priority without persisted evidence.
- Invent dates when ambiguous.
- Override deterministic ordering without explanation.
- Diagnose or medically advise.

If “오스카모임 전까지 과제 끝내기” references a known event, the system should resolve it to that event if confidence is high. If not high, it should keep the item in inbox or ask for clarification in TUI later.

## Implementation Milestones

### Milestone A — Scenario Spec and Fixtures

**Objective:** Freeze the user scenario as tests before changing logic.

Files:

- Create: `backend/tests/test_agenda_now.py`
- Create/modify: test fixture helpers if needed

Tests:

- before-event task beats upcoming event
- completing before-event task reveals event
- after event passes, earliest deadline task wins
- DB due before CPP wins
- KCC stays later

No production code before failing tests.

### Milestone B — Due Date and Before-Event Data Support

**Objective:** Ensure tasks can represent deadlines and event dependencies.

Files:

- Modify: `backend/app/db.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/repositories.py`
- Tests: repository/schema tests

Prefer additive migrations compatible with existing SQLite.

### Milestone C — Agenda Ranking Engine

**Objective:** Pure function that ranks candidates by context.

Files:

- Create: `backend/app/agenda.py`
- Tests: `backend/tests/test_agenda_engine.py`

The engine should accept already-loaded tasks/events plus `now`, and return a ranked recommendation object.

### Milestone D — `GET /agenda/now`

**Objective:** Expose read-only agenda recommendation.

Files:

- Modify: `backend/app/main.py`
- Modify: `backend/app/schemas.py`
- Tests: endpoint tests

No mutation.

### Milestone E — Dashboard/Web Integration

**Objective:** Web UI shows “지금 해야 할 것” as the main card.

Files:

- Modify: `GET /dashboard` payload or have Web fetch `/agenda/now`
- Modify: `backend/app/static/web/index.html`
- Modify: `backend/app/static/web/web.js`
- Modify: `backend/app/static/web/web.css`
- Tests: static/read-only tests

Ensure Web still does no POST/PATCH/DELETE.

### Milestone F — TUI Integration

**Objective:** `/오늘` uses agenda recommendation and shows next action reason.

Files:

- Modify: `tui/client.py`
- Modify: `tui/rendering.py`
- Modify: `tui/app.py`
- Tests: TUI smoke tests

### Milestone G — Natural Reference Resolution

**Objective:** Make “오스카모임 전까지 과제 끝내기” attach to the Oscar event when possible.

Files:

- Classification pipeline and/or post-classification resolver
- Search/resolution helpers
- Tests with ambiguous event names

This should be conservative: if uncertain, keep recoverable inbox state.

### Milestone H — Dogfood Scenario

**Objective:** Run the exact example as a local/remote smoke test.

Document results in:

```text
docs/dogfood/phase8-contextual-priority-smoke.md
```

## Definition of Done

- User can enter several tasks/events in Korean natural language.
- Web UI prominently shows exactly one current recommendation.
- When the current task is completed, Web/TUI shift to the next contextually correct item.
- Earlier deadlines beat later deadlines.
- Event windows matter.
- “Before event” dependencies matter.
- TUI/Web explain why the item is being shown.
- The system remains non-shaming.
- Full tests pass.
- Remote deployment is verified only after local green state.
