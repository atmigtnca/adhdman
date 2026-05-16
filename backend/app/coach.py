"""Read-only execution coach for ADHDman.

The coach never mutates storage. It explains the deterministic agenda result and
returns one tiny next step plus at most three suggested commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from app.agenda import AgendaItem, get_agenda_now
from app.config import Settings

CoachMode = Literal["agenda", "stuck", "mvs", "transition", "survival", "clarification"]
CoachSource = Literal["rules", "llm"]

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
        return command
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


def coach_next(
    *,
    now: str,
    settings: Settings,
    user_text: str | None = None,
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

    if mode == "mvs":
        message = _mvs_message(item)
        commands = [_command_for(item, "최소단계"), _command_for(item, "쪼개기"), "/바디더블 300"]
    elif mode == "stuck":
        message = _stuck_message(item)
        commands = [_command_for(item, "쪼개기"), _command_for(item, "최소단계"), "/바디더블 300"]
    elif mode == "survival":
        message = _survival_message(item)
        commands = ["/생존", _command_for(item, "최소단계"), "/바디더블 300"]
    else:
        message = _agenda_message(item)
        commands = [_command_for(item, "집중"), _command_for(item, "쪼개기"), _command_for(item, "최소단계")]

    return CoachResponse(
        mode=mode,
        message=message[:240],
        tiny_step=_tiny_step(item, mode)[:80],
        suggested_commands=commands[:3],
        needs_confirmation=False,
        clarification_options=[],
        source="rules",
    )
