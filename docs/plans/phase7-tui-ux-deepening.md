# Phase 7 TUI UX Deepening Implementation Plan

> **For Hermes:** Do not implement this plan until Seojongho explicitly says implementation should start. Use `adhdman-orchestration-workflow`, `test-driven-development`, and clean-context review for each implementation slice.

**Goal:** Turn ADHDman's current slash-command TUI from a thin endpoint console into an intuitive command center that shows useful state, guides the next action, and supports conversational capture without making the user memorize commands.

**Architecture:** Keep the backend contract stable and local-first. Improve the TUI in layers: information architecture, command/help model, natural action routing, richer rendering, onboarding, and dogfood verification. Most changes stay in `tui/`; backend changes are allowed only if a TUI UX need cannot be met from existing endpoints. Web remains read-only.

**Tech Stack:** Python 3.11, Textual, httpx, pytest, pytest-asyncio. No login/auth/multi-user. No public internet exposure.

---

## Why This Phase Exists

Phase 4 successfully created a working TUI shell. Phase 6 wired execution-helper commands. But the current result still feels like a developer console:

- The first screen has little guidance beyond `/help`.
- The user must remember slash commands.
- The log is mostly endpoint summaries, not an execution conversation.
- The Now pane does not yet feel like a live “what should I do now?” surface.
- There is no progressive onboarding, command discovery, or contextual next-step suggestion.
- The TUI does not yet make ADHDman feel like a running companion program.

Phase 7 should slow down and deliberately shape the TUI experience.

## Product Principles for TUI UX

1. **Visible current state:** The screen should always answer “what is happening now?”
2. **Next action, not command memory:** The user should see likely next commands/actions without opening docs.
3. **Conversational capture:** Plain Korean/natural text remains the default path; slash commands are shortcuts.
4. **One-thing bias:** Default screen should emphasize one thing and reduce lists unless requested.
5. **Recovery-first:** Undo, inbox fallback, and block reset should be visually reachable after mutations.
6. **Non-shaming language:** Empty/missed/blocked states should sound like a restart aid, not a failure report.
7. **Local-first trust:** No auth, no account, no user id, no public remote assumptions.

## Current Baseline

Current important files:

- `tui/app.py` — Textual app, layout, dispatch, network workers, summaries.
- `tui/commands.py` — dataclass command parser and `HELP_TEXT`.
- `tui/state.py` — listing/selection/pending suggestion state.
- `tui/rendering.py` — pure render helpers.
- `tui/client.py` — HTTP wrapper and loopback guard.
- `tui/cli.py` — `adhdman --help` / `--version` wrapper.
- `tui/tests/` — parser, state, client, app smoke, Phase 6 app tests.

Current layout:

```text
[Now]
[Log]
[Input]
```

This layout is correct enough to keep, but each pane needs more meaning.

## Target UX Shape

The TUI should eventually feel like this:

```text
┌─ Now ───────────────────────────────────────────────────────┐
│ 지금 하나: 보고서 초안 열기                                 │
│ 상태: focus 없음 · inbox 3 · 오늘 task 4 · 다음 일정 15:00  │
│ 제안: 1) focus 시작  2) 쪼개기  3) 생존모드                 │
├─ Conversation ──────────────────────────────────────────────┤
│ system  오늘은 한 번에 하나만 보자.                         │
│ you     보고서 너무 커서 못 하겠어                           │
│ adh     task #12와 관련 있어 보여. `/breakdown 1` 가능해.   │
│ adh     막혔으면 `/stuck`으로 크기 줄이기부터 해도 돼.       │
├─ Guide ─────────────────────────────────────────────────────┤
│ Enter: capture · ?: help · Tab: suggestions · Esc: clear     │
│ 지금 가능한 것: /today /tasks /focus /breakdown /stuck       │
└─ Input ─────────────────────────────────────────────────────┘
> 
```

Important: this does not require an LLM first. Rule-based hints are enough for this phase.

---

## Phase 7 Scope

### In Scope

- Better startup/onboarding screen.
- Contextual command suggestions.
- Richer Now pane with focus/body-double/survival/inbox/task summary.
- Human-readable conversation log.
- Short aliases and natural-ish local routing for common intents.
- Interactive help grouped by user need, not by endpoint.
- Safer command discovery without remembering every slash command.
- Dogfood scripts/checklists to verify real use.
- README update after UX stabilizes.

### Out of Scope

- Login/auth/accounts/multi-user.
- Public internet exposure.
- Full natural-language agent that can mutate arbitrary state.
- LLM-driven autonomous decision making.
- Web mutations.
- Calendar sync.
- Desktop notifications.
- Full mouse-driven UI.
- Persistent cross-session chat history unless explicitly approved later.

---

## Milestone A — UX Inventory and Acceptance Criteria

**Objective:** Define what “better TUI” means before changing behavior.

**Files:**
- Create: `docs/plans/phase7-tui-ux-acceptance.md`
- Read-only inspect: `tui/app.py`, `tui/commands.py`, `tui/rendering.py`, `tui/tests/`

**Tasks:**

1. Write a UX inventory document with current pain points:
   - startup screen too empty
   - help too command-list oriented
   - Now pane too thin
   - no contextual hints
   - conversation does not summarize intent
   - slash commands dominate
2. Define acceptance criteria:
   - first launch tells the user what to do in under 5 lines
   - after `/tasks`, the screen suggests valid next actions
   - after capture, the log says where the input went and what can happen next
   - after blocked/stuck language, TUI suggests `/stuck` or `/breakdown`
   - `adhdman --help` remains useful without launching TUI
3. No code changes in this milestone.

**Verification:**

```bash
git diff -- docs/plans/phase7-tui-ux-acceptance.md
```

Expected: planning document only.

**Commit boundary:**

```bash
git add docs/plans/phase7-tui-ux-deepening.md docs/plans/phase7-tui-ux-acceptance.md
git commit -m "docs: plan phase 7 tui ux deepening"
```

---

## Milestone B — Command Metadata Layer

**Objective:** Stop treating help as a raw string. Create structured command metadata that can power help, CLI help, contextual suggestions, and tests.

**Files:**
- Create or modify: `tui/command_catalog.py`
- Modify: `tui/commands.py`
- Modify: `tui/cli.py`
- Test: `tui/tests/test_command_catalog.py`
- Test: `tui/tests/test_cli_entrypoint.py`

**Design:**

Create a small catalog object:

```python
@dataclass(frozen=True)
class CommandDoc:
    name: str
    usage: str
    group: str
    purpose: str
    examples: tuple[str, ...] = ()
    when: str = ""
```

Groups should be user-goal based:

- `Start` — `/today`, `/help`
- `Capture` — plain text, `/inbox`
- `Choose` — `/tasks`, `/events`, `/search`, `/pick`
- `Act` — `/done`, `/focus`, `/breakdown`, `/mvs`
- `Recover` — `/undo`, `/stuck`, `/survival`
- `Body` — `/body-double`
- `Exit` — `/quit`

**TDD tasks:**

1. Write failing test: catalog contains all parser-supported commands.
2. Write failing test: rendered help includes groups and examples.
3. Implement `COMMAND_CATALOG` and `render_help_text()`.
4. Replace static `HELP_TEXT` with rendered catalog text.
5. Keep `adhdman --help` output stable and richer.

**Verification:**

```bash
pytest tui/tests/test_command_catalog.py tui/tests/test_cli_entrypoint.py -q
pytest tui/tests -q
ruff check tui
```

Expected: all pass.

---

## Milestone C — Startup and Onboarding Screen

**Objective:** First launch should not feel empty. It should explain the loop: capture, choose, focus, recover.

**Files:**
- Modify: `tui/app.py`
- Modify: `tui/rendering.py`
- Test: `tui/tests/test_app_smoke.py`

**Target behavior:**

When TUI starts:

- Log line 1: “ADHDman은 한 번에 하나만 잡는 로컬 작업실이야.”
- Log line 2: “그냥 입력하면 capture, 막혔으면 `/stuck`, 오늘 볼 건 `/today`.”
- Now pane should show a friendly empty state plus counts if backend is reachable.
- If backend is down, show calm local guidance, not a stack trace.

**TDD tasks:**

1. Test startup renders onboarding lines.
2. Test backend-down startup shows a calm hint.
3. Implement `render_onboarding_lines()` as a pure helper.
4. Update `on_mount()` to log onboarding and refresh today.

**Verification:**

```bash
pytest tui/tests/test_app_smoke.py -q
pytest tui/tests -q
```

---

## Milestone D — Contextual Guide Pane / Footer

**Objective:** Add a visible guide area that changes based on state so the user does not need to remember commands.

**Files:**
- Modify: `tui/app.py`
- Modify: `tui/state.py`
- Create/modify: `tui/suggestions.py`
- Test: `tui/tests/test_suggestions.py`
- Test: `tui/tests/test_app_smoke.py`

**Design:**

Add a fourth small pane or reuse a footer-like `Static` widget:

```text
Guide: Enter=capture · /help · after /tasks: /done N · /focus N · /breakdown N
```

Suggestion logic should be pure:

```python
def suggest_next_actions(state: AppState, last_command: str | None) -> list[str]:
    ...
```

Example suggestions:

- No listing: `/today`, `/inbox`, `/tasks`, plain capture.
- After `/tasks`: `/done N`, `/focus N`, `/breakdown N`, `/mvs N`.
- After `/inbox`: “promote not yet supported in TUI” or `/search <query>` if needed.
- Pending breakdown: `/breakdown commit`, `/undo`.
- Active focus: `/focus stop`, `/body-double 300`, `/stuck`.
- Survival mode active: `/survival off`, `/today`, `/done N`.

**TDD tasks:**

1. Write pure suggestion tests for each state.
2. Add `guide` widget to layout.
3. Refresh guide after every dispatch/log mutation.
4. App smoke test verifies guide changes after `/tasks`.

**Verification:**

```bash
pytest tui/tests/test_suggestions.py tui/tests/test_app_smoke.py -q
pytest tui/tests -q
```

---

## Milestone E — Rich Now Pane

**Objective:** The Now pane should be the live dashboard, not just a short `/today` render.

**Files:**
- Modify: `tui/client.py`
- Modify: `tui/rendering.py`
- Modify: `tui/app.py`
- Test: `tui/tests/test_client.py`
- Test: `tui/tests/test_state.py`
- Test: `tui/tests/test_app_smoke.py`

**Possible data sources:**

Prefer existing endpoints:

- `GET /today`
- `GET /focus/current`
- `GET /survival`
- `GET /body-double/current`

Avoid backend changes unless too slow or too awkward. If multiple calls are used, keep them in a worker and tolerate partial failure.

**Target Now pane content:**

```text
지금 하나: <today one thing or empty restart message>
Focus: <active focus or none>
Mode: survival/body-double status
Counts: inbox/tasks/events if available from today/dashboard payload
Hint: <one contextual next action>
```

**TDD tasks:**

1. Test render function with no data.
2. Test render function with active focus.
3. Test render function with survival mode.
4. Add client wrappers if missing.
5. Update refresh routine to gather state and render partials.

**Verification:**

```bash
pytest tui/tests/test_client.py tui/tests/test_app_smoke.py -q
pytest tui/tests -q
```

---

## Milestone F — Conversation Log Language

**Objective:** Make the log feel like a conversation about execution, not raw API output.

**Files:**
- Modify: `tui/app.py`
- Modify: `tui/rendering.py`
- Test: `tui/tests/test_app_smoke.py`
- Test: `tui/tests/test_phase6_app.py`

**Rules:**

- Prefixes can be `you`, `adh`, `system`, command names.
- Mutations should show recovery hint: “되돌리기: /undo”.
- Capture should distinguish:
  - stored as task
  - stored as event
  - stored as inbox fallback
- Empty states should offer one next action.
- Errors should be calm and specific.

**Example outputs:**

```text
you  보고서 너무 커서 못 하겠어
adh  inbox #18에 넣어뒀어. 나중에 /inbox에서 꺼낼 수 있어.
adh  막힌 일이면 /stuck, 큰 일이면 /breakdown N.
```

**TDD tasks:**

1. Add tests for capture summary by payload type.
2. Add tests for action log recovery hint.
3. Add tests for 409/client error wording.
4. Refactor summary helpers into pure functions if needed.

**Verification:**

```bash
pytest tui/tests/test_app_smoke.py tui/tests/test_phase6_app.py -q
pytest tui/tests -q
```

---

## Milestone G — Natural Shortcuts and Intent Hints

**Objective:** Let common non-slash inputs trigger helpful local suggestions without making unsafe automatic mutations.

**Files:**
- Create: `tui/intent_hints.py`
- Modify: `tui/app.py`
- Test: `tui/tests/test_intent_hints.py`
- Test: `tui/tests/test_app_smoke.py`

**Important safety rule:**

Plain text still captures first. Intent hints may suggest next actions, but must not silently call mutating helper endpoints unless the input is an explicit shortcut.

**Allowed explicit shortcuts:**

- `?` → `/help`
- `q` only maybe not; avoid accidental quit unless exact `/quit`
- `오늘` / `today` → local alias for `/today`
- `할일` / `tasks` → local alias for `/tasks`
- `인박스` / `inbox` → local alias for `/inbox`
- `막힘` / `stuck` → local alias for `/stuck`

**Hint-only phrases:**

- “못하겠어”, “막혔어”, “너무 커” → capture, then suggest `/stuck` or `/breakdown N`.
- “뭐하지”, “지금 뭐” → suggest `/today` or `/focus`.
- “최소”, “대충”, “제출” → suggest `/mvs N`.

**TDD tasks:**

1. Test exact aliases map to commands.
2. Test emotional/blocking phrases do not bypass capture.
3. Test hints appear after capture.
4. App smoke test for `오늘` and `막혔어` flows.

**Verification:**

```bash
pytest tui/tests/test_intent_hints.py tui/tests/test_app_smoke.py -q
pytest tui/tests -q
```

---

## Milestone H — Command Palette / Discoverability

**Objective:** Provide a lightweight way to discover actions in the TUI without reading a long help wall.

**Files:**
- Modify: `tui/commands.py`
- Modify: `tui/app.py`
- Modify: `tui/command_catalog.py`
- Test: `tui/tests/test_commands.py`
- Test: `tui/tests/test_app_smoke.py`

**Candidate design:**

Use `/actions` or `?` to show a short action palette based on current state:

```text
가능한 행동
1. 오늘 하나 보기        /today
2. 열린 할 일 보기       /tasks
3. 지금 하나 시작        /focus N
4. 너무 크면 쪼개기      /breakdown N
5. 막혔으면 리셋         /stuck
```

Do not implement fuzzy interactive menus yet. Keep it text-based and testable.

**TDD tasks:**

1. Parse `/actions`.
2. Render state-aware action palette from command catalog and suggestions.
3. Test action palette after `/tasks` includes `/focus N` and `/breakdown N`.

**Verification:**

```bash
pytest tui/tests/test_commands.py tui/tests/test_app_smoke.py -q
pytest tui/tests -q
```

---

## Milestone I — Real Dogfood Smoke Scenario

**Objective:** Verify that the TUI supports a real “I’m stuck, help me do one thing” session.

**Files:**
- Create: `docs/dogfood/phase7-tui-smoke.md`
- Optional create: `scripts/dogfood_tui_seed.py` if a deterministic local seed is useful.

**Manual scenario:**

1. Start backend locally or remote tunnel.
2. Run `adhdman`.
3. Confirm startup guidance is visible.
4. Type: `보고서 너무 커서 못 하겠어`.
5. Confirm input is captured and TUI suggests `/stuck` or `/breakdown`.
6. Run `/tasks`.
7. Pick/focus one task.
8. Run `/breakdown N` and `/breakdown commit`.
9. Run `/focus N` on the smallest step.
10. Start `/body-double 300`.
11. Mark `/done N`.
12. Confirm `/undo` remains visible as recovery.

**Verification:**

Document:

- what felt obvious
- what still required memory
- confusing command names
- missing backend data
- whether the screen felt alive enough

No code commit is required unless fixes are made.

---

## Milestone J — Documentation and Public-Safety Pass

**Objective:** Update public docs after behavior exists, not before.

**Files:**
- Modify: `README.md`
- Optional modify: `LOCALREADME.md`
- Optional create: `docs/tui.md`

**Docs should include:**

- `adhdman --help`
- `adhdman --version`
- how to run backend and TUI
- daily loop:
  - capture
  - `/today`
  - `/tasks`
  - `/focus N`
  - `/breakdown N`
  - `/stuck`
  - `/undo`
- remote tunnel note without exposing private deployment details
- no-auth/local-first warning

**Verification:**

```bash
pytest -q
ruff check backend tui
python -m pip install -e '.[tui]'
adhdman --help
adhdman --version
git diff --check
git status --short
```

Also run the repository's current public-safety scan/checklist. Expected: no secrets, private keys, local-only credentials, or private deployment details in tracked public docs.

---

## Review Gates for Every Implementation Slice

Before committing any Phase 7 implementation:

1. Targeted tests pass.
2. Full tests pass.
3. Ruff passes.
4. `git diff --check` passes.
5. No auth/multi-user/user_id/session-user concept introduced.
6. No mutation from vague plain text without explicit command or clear confirmation.
7. UI language remains non-shaming.
8. Now pane remains compact.
9. Help and guide stay consistent with parser behavior.
10. Working tree is either intentionally dirty during work or clean after commit.

## Commit Strategy

Do not create artificial micro-commits, but keep changes reviewable:

1. `docs: plan phase 7 tui ux deepening`
2. `feat: add structured tui command catalog`
3. `feat: improve tui onboarding and guide hints`
4. `feat: render richer tui now state`
5. `feat: improve tui conversational summaries`
6. `feat: add tui aliases and intent hints`
7. `feat: add tui action palette`
8. `docs: add phase 7 tui dogfood notes`
9. `docs: update tui usage`

Each commit must leave the repo green.

## Open Product Questions

These should be decided before implementation, or during Milestone A:

1. Should Korean aliases be first-class now? Suggested answer: yes for common commands.
2. Should plain text always capture before hinting? Suggested answer: yes.
3. Should `/actions` exist, or should `?` show contextual actions? Suggested answer: both can map to the same command, but `?` should not conflict with text capture.
4. Should the TUI fetch dashboard data directly? Suggested answer: maybe, but prefer smaller existing endpoints first.
5. Should body-double default interval be minutes or seconds in UX wording? Current command uses seconds; guide should make this obvious.
6. Should inbox promotion be added to TUI in this phase? Suggested answer: only if dogfood shows it blocks daily use; otherwise defer.

## Definition of Done

Phase 7 is done when:

- New user can launch `adhdman` and understand the first action without reading README.
- `adhdman --help` and in-app `/help` share the same structured command metadata.
- TUI shows contextual next actions after list/capture/focus/helper flows.
- Now pane shows enough live state to feel useful.
- Common Korean aliases work for navigation/help, while free-form thoughts are still safely captured.
- Dogfood smoke scenario is documented.
- Full tests pass.
- Public docs are updated.
- Repo is clean and pushed only after review.
