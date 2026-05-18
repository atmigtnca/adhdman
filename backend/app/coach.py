"""Read-only execution coach for ADHDman.

The coach never mutates storage. It explains the deterministic agenda result and
returns one tiny next step plus at most three suggested commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from typing import Any, Literal

from app.agenda import AgendaItem, get_agenda_now
from app.config import Settings
from app.llm.base import LLMError, LLMProvider, LLMResult

CoachMode = Literal["agenda", "stuck", "mvs", "transition", "survival", "clarification"]
CoachSource = Literal["rules", "llm"]
ALLOWED_MODES = {"agenda", "stuck", "mvs", "transition", "survival", "clarification"}

STUCK_MARKERS = (
    "못하",
    "안 돼",
    "안돼",
    "너무 커",
    "하기 싫",
    "시작",
    "막힘",
    "막혔",
)
SURVIVAL_MARKERS = ("죽겠", "무기력", "번아웃", "아무것도", "포기", "나는 안", "망했다")
MVS_MARKERS = ("시간이 없어", "늦", "마감", "망했다", "제출")


@dataclass
class CoachResponse:
    mode: CoachMode
    message: str
    tiny_step: str
    suggested_commands: list[str] = field(default_factory=list)
    needs_confirmation: bool = False
    clarification_options: list[str] = field(default_factory=list)
    source: CoachSource = "rules"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _text_has(text: str, markers: tuple[str, ...]) -> bool:
    compact = text.strip().lower()
    return any(marker.lower() in compact for marker in markers)


def _deadline_pressure(item: AgendaItem | None, now_dt: datetime) -> bool:
    if item is None or item.kind != "task":
        return False
    due_at = _parse_dt(item.due_at)
    if due_at is None:
        return False
    return timedelta(0) <= due_at - now_dt <= timedelta(hours=3)


def _command_for(item: AgendaItem | None, command: str) -> str:
    if item is None or item.kind != "task":
        return f"/{command}"
    return f"/{command} 1"


def _tiny_step(item: AgendaItem | None, mode: CoachMode) -> str:
    if item is None:
        return "떠오르는 일 하나만 적기"
    title = item.title[:48]
    if mode == "mvs":
        return f"{title} 제출 가능한 최소 형태 정하기"[:80]
    if item.kind == "event":
        return f"{title} 준비물 하나 확인하기"[:80]
    return f"{title} 파일이나 첫 화면만 열기"[:80]


def _agenda_message(item: AgendaItem | None) -> str:
    if item is None:
        return "지금은 비어 있어. 떠오르는 일을 하나만 적어두면 돼."
    return f"지금은 {item.title}부터 보자. {item.reason} 딱 2분만 시작해."[:240]


def _stuck_message(item: AgendaItem | None) -> str:
    if item is None:
        return "전체를 하려는 게 아니라 시작 마찰만 낮추자. 지금은 떠오르는 일 하나만 적으면 돼."
    return f"전체 말고 하나만 낮추자. 지금은 {item.title}의 첫 화면만 열면 돼. 2분만 가자."[:240]


def _mvs_message(item: AgendaItem | None) -> str:
    if item is None:
        return "완벽 말고 오늘 제출 가능한 60점짜리 하나로 줄이자."
    return f"시간이 가까워. {item.title}은 완벽 말고 제출 가능한 60점짜리로 가자."[:240]


def _survival_message(item: AgendaItem | None) -> str:
    if item is None:
        return "오늘은 범위를 낮추자. 물 한 잔, 그리고 떠오르는 일 하나만 적으면 충분해."
    return f"오늘은 범위를 낮추자. {item.title} 전체가 아니라 첫 행동 하나만 보면 돼."[:240]



def _allowed_commands(item: AgendaItem | None, mode: CoachMode) -> list[str]:
    if mode == "mvs":
        return [_command_for(item, "최소단계"), _command_for(item, "쪼개기"), "/바디더블 300"]
    if mode == "stuck":
        return [_command_for(item, "쪼개기"), _command_for(item, "최소단계"), "/바디더블 300"]
    if mode == "survival":
        return ["/생존", _command_for(item, "최소단계"), "/바디더블 300"]
    return [_command_for(item, "집중"), _command_for(item, "쪼개기"), _command_for(item, "최소단계")]


def _rules_response(item: AgendaItem | None, mode: CoachMode) -> CoachResponse:
    if mode == "mvs":
        message = _mvs_message(item)
    elif mode == "stuck":
        message = _stuck_message(item)
    elif mode == "survival":
        message = _survival_message(item)
    else:
        message = _agenda_message(item)
    return CoachResponse(
        mode=mode,
        message=message[:240],
        tiny_step=_tiny_step(item, mode)[:80],
        suggested_commands=_allowed_commands(item, mode)[:3],
        needs_confirmation=False,
        clarification_options=[],
        source="rules",
    )


def _coach_system_prompt() -> str:
    return (
        "You are ADHDman's execution coach. Return JSON only. "
        "Use Korean, short non-shaming wording. Keep one thing visible. "
        "Never invent state, never mutate data, never give medical advice. "
        "Schema: mode, message, tiny_step, suggested_commands, needs_confirmation, "
        "clarification_options. message <= 240 chars, tiny_step <= 80 chars, "
        "suggested_commands max 3 and only from allowed_commands."
    )


def _coach_user_prompt(*, now: str, item: AgendaItem | None, mode: CoachMode, user_text: str, allowed_commands: list[str]) -> str:
    if item is None:
        agenda = {"now": None}
    else:
        agenda = {
            "kind": item.kind,
            "title": item.title,
            "reason": item.reason,
            "due_at": item.due_at,
            "starts_at": item.starts_at,
            "urgency": item.urgency,
        }
    return json.dumps(
        {
            "now": now,
            "recommended_mode": mode,
            "current_agenda": agenda,
            "recent_user_text": user_text[:500],
            "allowed_commands": allowed_commands,
        },
        ensure_ascii=False,
    )


def _coerce_bool(value: Any) -> bool:
    return value if isinstance(value, bool) else False


def _validate_llm_response(raw: str, *, fallback: CoachResponse, allowed_commands: list[str]) -> CoachResponse | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    mode = data.get("mode", fallback.mode)
    if mode not in ALLOWED_MODES:
        mode = fallback.mode
    message = data.get("message")
    tiny_step = data.get("tiny_step")
    if not isinstance(message, str) or not message.strip() or len(message) > 240:
        return None
    if tiny_step is None:
        tiny_step = fallback.tiny_step
    if not isinstance(tiny_step, str) or len(tiny_step) > 80:
        return None
    raw_commands = data.get("suggested_commands") or []
    if not isinstance(raw_commands, list):
        return None
    commands: list[str] = []
    for command in raw_commands:
        if isinstance(command, str) and command in allowed_commands and command not in commands:
            commands.append(command)
        if len(commands) == 3:
            break
    raw_options = data.get("clarification_options") or []
    options = [str(opt)[:80] for opt in raw_options[:3]] if isinstance(raw_options, list) else []
    return CoachResponse(
        mode=mode,  # type: ignore[arg-type]
        message=message,
        tiny_step=tiny_step,
        suggested_commands=commands or fallback.suggested_commands,
        needs_confirmation=_coerce_bool(data.get("needs_confirmation")),
        clarification_options=options,
        source="llm",
    )

def coach_next(
    *,
    now: str,
    settings: Settings,
    user_text: str | None = None,
    provider: LLMProvider | None = None,
) -> CoachResponse:
    now_dt = datetime.fromisoformat(now)
    agenda = get_agenda_now(now=now, settings=settings)
    item = agenda.now
    text = user_text or ""

    if _deadline_pressure(item, now_dt) and _text_has(text, MVS_MARKERS):
        mode: CoachMode = "mvs"
    elif _text_has(text, SURVIVAL_MARKERS):
        mode = "survival" if not _deadline_pressure(item, now_dt) else "mvs"
    elif _text_has(text, STUCK_MARKERS):
        mode = "stuck"
    elif _deadline_pressure(item, now_dt):
        mode = "mvs"
    else:
        mode = "agenda"

    fallback = _rules_response(item, mode)
    if provider is None or not provider.available:
        return fallback

    allowed_commands = _allowed_commands(item, mode)[:3]
    result = provider.complete(
        _coach_system_prompt(),
        _coach_user_prompt(
            now=now,
            item=item,
            mode=mode,
            user_text=text,
            allowed_commands=allowed_commands,
        ),
    )
    if isinstance(result, LLMError):
        return fallback
    if isinstance(result, LLMResult):
        llm_response = _validate_llm_response(
            result.text, fallback=fallback, allowed_commands=allowed_commands
        )
        if llm_response is not None:
            return llm_response
    return fallback
