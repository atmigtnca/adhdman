# Phase 9 LLM Execution Coach Implementation Plan

> **For Hermes:** Do not implement this plan until Seojongho explicitly approves implementation. Phase 8 contextual priority comes first or must be designed in parallel. Use strict TDD, clean-context reviews, and non-shaming UX review.

**Goal:** Add an LLM-powered execution coach that helps the user keep moving after ADHDman selects the current next action. The coach should reduce friction, break stuck states, preserve momentum, and guide the user to the next tiny step without taking unsafe autonomous action.

**Architecture:** Split responsibilities clearly. Deterministic agenda/priority logic chooses *what* should be shown now. The LLM coach helps with *how to continue*: next-step wording, encouragement, clarification questions, MVS suggestions, stuck recovery, and post-completion transition. LLM outputs must be schema-validated, bounded, auditable, and never directly mutate state without explicit user command/confirmation.

**Tech Stack:** Python 3.11, FastAPI, SQLite, OpenRouter LLM provider, Textual TUI, static read-only Web UI, pytest. Local-first, single-user, no auth/multi-user.

---

## Product Role

The LLM is not just a classifier. It is a **coach**.

Its job:

```text
사용자가 계속 일을 진행할 수 있도록 돕는다.
```

It should behave like an external executive-function support layer:

- remember the current context
- keep attention on one thing
- make the next step small enough to start
- explain why this thing matters now
- notice stuck/avoidance language
- suggest recovery options
- transition after completion
- avoid shame
- avoid overwhelming the user with lists

It should not behave like:

- a generic chatbot
- a therapist
- a medical advisor
- a project manager dumping all tasks
- an autonomous agent mutating the database silently

## Relationship to Phase 8

Phase 8: **Contextual Priority Engine**

- deterministic
- reads tasks/events/current time
- selects current item
- produces reason and next/later lists
- owns ordering and priority

Phase 9: **LLM Execution Coach**

- conversational
- uses agenda result as context
- writes short Korean coaching messages
- suggests next tiny step
- recognizes stuck/emotional states
- proposes safe commands
- never overrides priority silently

Principle:

```text
Engine decides the agenda.
Coach helps the human execute the agenda.
```

## Core User Flow

### Flow A — Start work

Input/state:

```text
Agenda now: DB 과제
Deadline: 6월 1일 01:00
Current time: 5월 31일 20:40
User energy: unknown
```

Coach output:

```text
지금은 DB 과제가 먼저야. 마감이 가장 가까워.
딱 2분만 시작하자: 과제 파일을 열고 문제 번호만 확인해.
가능한 다음 행동: /집중 1, /쪼개기 1, /최소단계 1
```

### Flow B — User says stuck

User:

```text
못하겠어
너무 커
하기 싫어
망했다
```

Coach output:

```text
전체를 하려는 게 아니라, 시작 마찰만 낮추자.
지금 할 수 있는 건 “파일 열기” 하나야.
크면 /쪼개기 1, 마감이 가까우면 /최소단계 1로 가자.
```

### Flow C — User completes current task

User:

```text
/완료 1
```

Coach output after deterministic refresh:

```text
좋아, 하나 끝났어.
이제 13:00–18:00 오스카 모임이 다음이야.
준비할 게 있으면 2분짜리로 하나만 적어둬.
```

### Flow D — Event passed

After Oscar meeting window passes:

```text
이제 다음은 DB 과제야. CPP보다 마감이 먼저라서 이걸 먼저 보여줄게.
딱 2분만: DB 과제 파일 열기.
```

## Coach Modes

The coach should have explicit modes, not free-form unbounded chat.

### 1. Agenda Coach

Default mode. Explains current recommendation and next tiny action.

Inputs:

- agenda now item
- next/later summary
- current time
- active focus/body-double/survival state

Outputs:

- short reason
- one tiny step
- safe command suggestions

### 2. Stuck Coach

Triggered by user language or `/막힘`.

Signals:

```text
못하겠어, 하기 싫어, 너무 커, 막힘, 모르겠어, 시작이 안 돼, 망했다
```

Outputs:

- normalize without shame
- shrink task
- offer `/쪼개기`, `/최소단계`, `/생존`, `/바디더블`

### 3. MVS Coach

Triggered by urgent deadline or `/최소단계`.

Outputs:

- 60-point submission plan
- time blocks
- minimum deliverable
- “제출 가능한 형태” focus

### 4. Transition Coach

Triggered after completion, event start/end, focus stop.

Outputs:

- completion acknowledgement
- next agenda item
- no long praise, no dopamine spam
- one next action

### 5. Survival Coach

Triggered by survival mode or low-energy language.

Outputs:

- reduce scope to survival basics
- show one life-maintenance action
- defer non-critical tasks without guilt

### 6. Clarification Coach

Triggered when input is ambiguous.

Examples:

```text
오스카모임 전까지 과제 끝내기
```

If event match confidence is high, propose link. If not:

```text
“오스카모임”이 어떤 일정인지 확인이 필요해.
1. 오늘 13:00 오스카 모임
2. 새 일정으로 저장
```

## Non-Negotiable Safety Rules

1. LLM never writes directly to DB.
2. LLM never chooses priority in conflict with deterministic engine unless it returns a flagged suggestion for review.
3. LLM output must fit a schema.
4. LLM messages are short by default.
5. LLM must not provide medical diagnosis/treatment advice.
6. LLM must not shame, scold, or moralize.
7. LLM must not dump all tasks unless explicitly asked.
8. LLM must preserve capture-first: if confused, store/recover, do not discard.
9. LLM must suggest explicit commands for mutation.
10. User can disable coach or fall back to rules-only.

## Proposed API

### `POST /coach/next`

Read-only. Produces a coaching message from current state.

Request:

```json
{
  "user_text": "못하겠어",
  "mode": "auto",
  "agenda": {"...": "optional client-provided snapshot or server loads it"},
  "max_suggestions": 3
}
```

Response:

```json
{
  "mode": "stuck",
  "message": "전체를 하려는 게 아니라 시작만 낮추자. 지금은 DB 과제 파일 열기 하나만 해.",
  "tiny_step": "DB 과제 파일 열기",
  "suggested_commands": ["/쪼개기 1", "/최소단계 1", "/바디더블 300"],
  "risk": "none",
  "source": "llm"
}
```

No mutation.

### `POST /coach/preview-capture`

Optional later. Given raw input, returns how ADHDman would understand it, without writing.

Use for ambiguous natural-language planning.

## Prompt Contract

System prompt should include:

- ADHDman is a local-first execution support system.
- User has ADHD-like execution constraints.
- One thing only.
- Korean-first, informal but respectful/steady tone.
- No shame.
- Use manual principles: 2-minute start, salami slicing, MVS, 3 blocks, survival mode, body-double, strategic defer.
- Never invent database state.
- Use provided agenda only.
- Return JSON only.

User/context prompt should include compact state:

```text
now: 2026-05-31T20:40:00+09:00
current_agenda: DB 과제, due 2026-06-01T01:00, reason earliest_deadline
active_focus: none
survival_mode: false
recent_user_text: 못하겠어
available_commands: /집중 1, /쪼개기 1, /최소단계 1, /막힘, /바디더블 300
```

## Output Schema

Create Pydantic schema:

```python
class CoachMode(str, Enum):
    agenda = "agenda"
    stuck = "stuck"
    mvs = "mvs"
    transition = "transition"
    survival = "survival"
    clarify = "clarify"
    fallback = "fallback"

class CoachResponse(BaseModel):
    mode: CoachMode
    message: str = Field(max_length=240)
    tiny_step: str | None = Field(default=None, max_length=80)
    suggested_commands: list[str] = Field(default_factory=list, max_length=3)
    needs_confirmation: bool = False
    clarification_options: list[str] = Field(default_factory=list, max_length=3)
    source: Literal["llm", "rules", "fallback"]
```

If LLM output fails validation, return rules fallback.

## Rules Fallback

Coach must work even when LLM is unavailable.

Fallback examples:

- agenda mode: “지금은 {title}부터 보자. 이유: {reason}. 딱 2분만 시작해.”
- stuck mode: “크면 쪼개자. /쪼개기 1 또는 /막힘 을 써봐.”
- mvs mode: “완벽 말고 제출 가능한 60점짜리로 가자. /최소단계 1.”
- survival mode: “오늘은 생존 모드로 낮추자. 물 한 잔부터.”

## TUI Integration

TUI should show coach messages in the conversation log and guide/footer.

Examples:

```text
adh  지금은 DB 과제가 먼저야. 마감이 가장 가까워.
adh  딱 2분만: 과제 파일 열기.
     가능한 행동: /집중 1 · /쪼개기 1 · /최소단계 1
```

User can type:

```text
뭐하지
못하겠어
너무 커
다 했어
```

TUI routes:

- “뭐하지” → coach agenda
- “못하겠어/너무 커” → capture if meaningful + coach stuck
- “다 했어” → do not auto-complete unless active focus target is clear; ask or suggest `/완료 N`

## Web Integration

Web remains read-only and is a central product differentiator: the user should be able to look at the Web UI and immediately understand what to do right now. It must not feel like a generic task database.

Primary order:

1. “지금 해야 할 것” agenda card
2. reason / deadline / event dependency
3. coach suggestion
4. next/later compact list

Add a small coach card under the current agenda card:

```text
코치 제안
딱 2분만: DB 과제 파일 열기
추천: /집중 1 · /쪼개기 1 · /최소단계 1
```

Web must not send mutation requests.

## Data / Logging

Possible table later:

```text
coach_messages
- id
- created_at
- mode
- agenda_kind
- agenda_id
- user_text_hash or nullable raw? prefer no raw first
- message
- source
```

For MVP, do not persist raw coach messages unless needed. Avoid storing sensitive emotional text by default.

## Milestones

### Milestone A — Coach Contract and Tests

**Objective:** Define schema, modes, and fallback behavior.

Files:

- Create: `backend/app/coach/schema.py`
- Create: `backend/app/coach/fallback.py`
- Test: `backend/tests/test_coach_schema.py`
- Test: `backend/tests/test_coach_fallback.py`

Tests:

- message max length enforced
- suggested commands max 3
- stuck fallback suggests shrink commands
- mvs fallback uses 60-point framing
- survival fallback avoids task overload

### Milestone B — Coach Prompt Builder

**Objective:** Build compact, bounded prompts from agenda state.

Files:

- Create: `backend/app/coach/prompts.py`
- Test: `backend/tests/test_coach_prompts.py`

Tests:

- prompt includes current agenda title/reason
- prompt includes allowed commands only
- prompt excludes raw task dump beyond limit
- prompt includes non-shaming rules
- prompt requests JSON only

### Milestone C — LLM Coach Pipeline

**Objective:** Use existing OpenRouter provider to produce validated coach responses.

Files:

- Create: `backend/app/coach/pipeline.py`
- Test: `backend/tests/test_coach_pipeline.py`

Tests:

- valid LLM JSON returns `source=llm`
- invalid JSON falls back
- provider unavailable falls back
- overlong message rejected/fallback
- command suggestions outside allowed list are filtered or rejected

### Milestone D — Read-Only API

**Objective:** Add `POST /coach/next` without mutation.

Files:

- Modify: `backend/app/main.py`
- Modify: `backend/app/schemas.py` or add coach schemas
- Test: `backend/tests/test_coach_endpoint.py`

Tests:

- endpoint returns coach response
- does not create tasks/events/inbox/actions
- handles provider unavailable
- handles stuck text

### Milestone E — Agenda Integration

**Objective:** Feed Phase 8 agenda recommendation into coach.

Files:

- `backend/app/agenda.py`
- `backend/app/coach/context.py`
- Tests combining agenda + coach

Depends on Phase 8.

### Milestone F — TUI Coach Messages

**Objective:** Show coach output after `/오늘`, stuck phrases, and completion.

Files:

- Modify: `tui/client.py`
- Modify: `tui/app.py`
- Modify: `tui/rendering.py`
- Tests: `tui/tests/test_coach_app.py`

### Milestone G — Web Coach Card

**Objective:** Show read-only coach suggestion card.

Files:

- Modify dashboard endpoint or Web fetch path
- Modify static web files
- Tests: Web static/read-only tests

### Milestone H — Dogfood Script

**Objective:** Run realistic scenario:

```text
6월 1일 01시까지 db 과제
못하겠어
너무 커
다 했어
```

Expected:

- coach keeps one thing visible
- suggests 2-minute start
- suggests MVS near deadline
- does not shame
- does not auto-complete vague “다 했어” without clear target

Document in:

```text
docs/dogfood/phase9-llm-execution-coach.md
```

## Definition of Done

- LLM coach has explicit schema and modes.
- LLM coach works with fallback when provider unavailable.
- Coach never mutates state directly.
- Coach does not override deterministic priority.
- TUI shows short Korean coaching messages.
- Web shows read-only coach suggestion.
- Stuck/avoidance language gets recovery support.
- Completion transition shows next agenda item.
- Tests cover invalid LLM output, unavailable LLM, and no-mutation guarantees.
- Full test suite passes.
