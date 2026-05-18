from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class Noop:
    pass


@dataclass(frozen=True)
class Capture:
    text: str


@dataclass(frozen=True)
class Today:
    pass


@dataclass(frozen=True)
class Inbox:
    pass


@dataclass(frozen=True)
class Tasks:
    pass


@dataclass(frozen=True)
class Events:
    pass


@dataclass(frozen=True)
class Done:
    index: int | None  # 1-based; None means "use last_selection"


@dataclass(frozen=True)
class Delete:
    index: int | None  # 1-based; None means use last_selection


@dataclass(frozen=True)
class Undo:
    action_id: int | None  # None means /undo/latest


@dataclass(frozen=True)
class Search:
    query: str


@dataclass(frozen=True)
class Pick:
    index: int


@dataclass(frozen=True)
class Resolve:
    text: str


@dataclass(frozen=True)
class Help:
    pass


@dataclass(frozen=True)
class Quit:
    pass


@dataclass(frozen=True)
class Unknown:
    raw: str


# ----- Phase 6 execution helper commands ---------------------------------


@dataclass(frozen=True)
class FocusStart:
    index: int  # 1-based index into last_listing


@dataclass(frozen=True)
class FocusStop:
    pass


@dataclass(frozen=True)
class FocusCurrent:
    pass


@dataclass(frozen=True)
class BreakdownSuggest:
    index: int  # 1-based; resolved against last_listing (tasks)


@dataclass(frozen=True)
class BreakdownCommit:
    pass


@dataclass(frozen=True)
class StuckOptions:
    pass


@dataclass(frozen=True)
class StuckApply:
    choice: str  # "shrink" | "swap" | "skip" | "park"


@dataclass(frozen=True)
class BodyDoubleStart:
    interval_seconds: int | None


@dataclass(frozen=True)
class BodyDoubleStop:
    pass


@dataclass(frozen=True)
class BodyDoubleCheckIn:
    pass


@dataclass(frozen=True)
class BodyDoubleCurrent:
    pass


@dataclass(frozen=True)
class MVSSuggest:
    index: int  # 1-based; resolved against last_listing (task or inbox)


@dataclass(frozen=True)
class MVSCommit:
    pass


@dataclass(frozen=True)
class SurvivalOn:
    pass


@dataclass(frozen=True)
class SurvivalOff:
    pass


@dataclass(frozen=True)
class SurvivalStatus:
    pass


Command = Union[
    Noop, Capture, Today, Inbox, Tasks, Events, Done, Delete, Undo, Search, Pick,
    Resolve, Help, Quit, Unknown,
    FocusStart, FocusStop, FocusCurrent,
    BreakdownSuggest, BreakdownCommit,
    StuckOptions, StuckApply,
    BodyDoubleStart, BodyDoubleStop, BodyDoubleCheckIn, BodyDoubleCurrent,
    MVSSuggest, MVSCommit,
    SurvivalOn, SurvivalOff, SurvivalStatus,
]


_STUCK_CHOICES = {"shrink", "swap", "skip", "park"}
_STUCK_CHOICE_ALIASES = {
    "줄이기": "shrink",
    "바꾸기": "swap",
    "넘기기": "skip",
    "미루기": "park",
    "보류": "park",
}


def parse_command(line: str) -> Command:
    """Parse a single user input line into a Command.

    Anything not starting with '/' (after stripping) is a Capture.
    Blank input is Noop. Unknown slash commands return Unknown.
    """
    if line is None:
        return Noop()
    stripped = line.strip()
    if not stripped:
        return Noop()
    if not stripped.startswith("/"):
        return Capture(text=stripped)

    parts = stripped.split(maxsplit=1)
    verb = parts[0][1:].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if verb in ("today", "오늘"):
        return Today()
    if verb in ("inbox", "인박스", "받은것", "받은거"):
        return Inbox()
    if verb in ("tasks", "할일", "일"):
        return Tasks()
    if verb in ("events", "일정"):
        return Events()
    if verb in ("done", "완료"):
        if not rest:
            return Done(index=None)
        try:
            return Done(index=int(rest))
        except ValueError:
            return Unknown(raw=stripped)
    if verb in ("delete", "삭제", "지우기", "제거"):
        if not rest:
            return Delete(index=None)
        try:
            return Delete(index=int(rest))
        except ValueError:
            return Unknown(raw=stripped)
    if verb in ("undo", "되돌리기", "취소"):
        if not rest:
            return Undo(action_id=None)
        try:
            return Undo(action_id=int(rest))
        except ValueError:
            return Unknown(raw=stripped)
    if verb in ("search", "검색"):
        if not rest:
            return Unknown(raw=stripped)
        return Search(query=rest)
    if verb in ("pick", "선택"):
        try:
            return Pick(index=int(rest))
        except ValueError:
            return Unknown(raw=stripped)
    if verb in ("resolve", "해석"):
        if not rest:
            return Unknown(raw=stripped)
        return Resolve(text=rest)
    if verb in ("help", "도움말", "도움", "?"):
        return Help()
    if verb in ("quit", "exit", "종료", "나가기"):
        return Quit()
    if verb in ("focus", "집중"):
        if not rest:
            return FocusCurrent()
        low = rest.lower()
        if low in ("stop", "중지", "멈춤", "끝"):
            return FocusStop()
        if low in ("current", "현재"):
            return FocusCurrent()
        try:
            return FocusStart(index=int(rest))
        except ValueError:
            return Unknown(raw=stripped)
    if verb in ("breakdown", "쪼개기", "분해"):
        if not rest:
            return Unknown(raw=stripped)
        low = rest.lower()
        if low in ("commit", "저장", "확정"):
            return BreakdownCommit()
        try:
            return BreakdownSuggest(index=int(rest))
        except ValueError:
            return Unknown(raw=stripped)
    if verb in ("stuck", "막힘"):
        if not rest or rest.lower() in ("options", "선택지"):
            return StuckOptions()
        choice = rest.lower()
        if choice in _STUCK_CHOICE_ALIASES:
            choice = _STUCK_CHOICE_ALIASES[choice]
        if choice in _STUCK_CHOICES:
            return StuckApply(choice=choice)
        # accept "apply shrink" style for symmetry
        parts2 = rest.split(maxsplit=1)
        if len(parts2) == 2 and parts2[0].lower() == "apply":
            sub = parts2[1].strip().lower()
            sub = _STUCK_CHOICE_ALIASES.get(sub, sub)
            if sub in _STUCK_CHOICES:
                return StuckApply(choice=sub)
        return Unknown(raw=stripped)
    if verb in ("body-double", "바디더블"):
        if not rest:
            return BodyDoubleCurrent()
        low = rest.lower()
        if low in ("stop", "중지", "멈춤", "끝"):
            return BodyDoubleStop()
        if low in ("check-in", "checkin", "체크인"):
            return BodyDoubleCheckIn()
        if low in ("current", "현재"):
            return BodyDoubleCurrent()
        if low in ("start", "시작"):
            return BodyDoubleStart(interval_seconds=None)
        parts2 = rest.split(maxsplit=1)
        head = parts2[0].lower()
        tail = parts2[1].strip() if len(parts2) > 1 else ""
        if head in ("start", "시작"):
            if not tail:
                return BodyDoubleStart(interval_seconds=None)
            try:
                interval = int(tail)
            except ValueError:
                return Unknown(raw=stripped)
            if interval <= 0:
                return Unknown(raw=stripped)
            return BodyDoubleStart(interval_seconds=interval)
        # bare positive integer => start with that interval
        try:
            interval = int(rest)
        except ValueError:
            return Unknown(raw=stripped)
        if interval <= 0:
            return Unknown(raw=stripped)
        return BodyDoubleStart(interval_seconds=interval)
    if verb in ("mvs", "최소단계", "최소"):
        if not rest:
            return Unknown(raw=stripped)
        low = rest.lower()
        if low in ("commit", "저장", "확정"):
            return MVSCommit()
        parts2 = rest.split(maxsplit=1)
        head = parts2[0].lower()
        tail = parts2[1].strip() if len(parts2) > 1 else ""
        if head in ("suggest", "제안"):
            if not tail:
                return Unknown(raw=stripped)
            try:
                return MVSSuggest(index=int(tail))
            except ValueError:
                return Unknown(raw=stripped)
        try:
            return MVSSuggest(index=int(rest))
        except ValueError:
            return Unknown(raw=stripped)
    if verb in ("survival", "생존"):
        low = rest.lower()
        if not rest or low in ("status", "상태"):
            return SurvivalStatus()
        if low in ("on", "켜기", "시작"):
            return SurvivalOn()
        if low in ("off", "끄기", "종료"):
            return SurvivalOff()
        return Unknown(raw=stripped)
    return Unknown(raw=stripped)


SLASH_COMMAND_MENU = """\
명령어를 고를 수 있어.

기본:
  /오늘        지금 해야 할 것 보기
  /인박스      보관함 보기
  /할일        열린 할 일 보기
  /일정        예정된 일정 보기
  /검색        task/event/inbox 검색

실행:
  /집중 N      N번에 집중 시작
  /완료 N      N번 할 일 완료
  /삭제 N      N번 할 일/일정 삭제
  /쪼개기 N    작은 단계로 쪼개기
  /막힘        막혔을 때 선택지 보기
  /최소단계 N  제출 가능한 최소 단계
  /생존        생존 모드 상태

기타:
  /도움말      전체 명령어 보기
  /종료        종료
"""


HELP_TEXT = """\
ADHDman에서 쓸 수 있는 명령어야.
명령어 없이 그냥 적으면 일단 보관함에 넣어둬.

기본 흐름:

  /오늘              지금 하나와 오늘 상태 보기        (영어 명령: /today)
  /인박스            아직 정리 안 된 입력 보기          (영어 명령: /inbox)
  /할일              열린 할 일 목록 보기               (영어 명령: /tasks)
  /일정              예정된 일정 보기                   (영어 명령: /events)
  /완료 N            마지막 /할일 목록의 N번 완료       (영어 명령: /done N)
  /삭제 N            마지막 목록의 N번 삭제              (영어 명령: /delete N)
  /되돌리기          가장 최근 변경 되돌리기            (영어 명령: /undo)
  /되돌리기 ID       특정 action id 되돌리기             (영어 명령: /undo ID)
  /검색 <내용>       task/event/inbox 검색               (영어 명령: /search <query>)
  /선택 N            마지막 검색/목록의 N번 선택         (영어 명령: /pick N)
  /해석 <날짜말>     자연어 날짜/시간 해석               (영어 명령: /resolve <text>)

실행 보조:

  /집중              현재 집중 상태 보기                (영어 명령: /focus)
  /집중 N            마지막 목록의 N번에 집중 시작      (영어 명령: /focus N)
  /집중 중지         현재 집중 끝내기                   (영어 명령: /focus stop)
  /쪼개기 N          할 일 N번을 2-5개 작은 단계로 쪼개기 (영어 명령: /breakdown N)
  /쪼개기 저장       방금 제안된 작은 단계 저장         (영어 명령: /breakdown commit)
  /막힘              막혔을 때 선택지 보기              (영어 명령: /stuck)
  /막힘 줄이기       더 작은 행동으로 줄이기            (영어 명령: /stuck shrink)
  /막힘 바꾸기       다른 일로 바꾸기                   (영어 명령: /stuck swap)
  /막힘 넘기기       지금 블록에서 넘기기               (영어 명령: /stuck skip)
  /막힘 미루기       잠깐 보류하기                      (영어 명령: /stuck park)
  /바디더블          현재 바디더블 상태 보기            (영어 명령: /body-double)
  /바디더블 N        N초 간격 바디더블 시작             (영어 명령: /body-double N)
  /바디더블 체크인   하트비트 기록                      (영어 명령: /body-double check-in)
  /바디더블 중지     바디더블 끝내기                    (영어 명령: /body-double stop)
  /최소단계 N        N번 항목의 최소 실행 단계 제안     (영어 명령: /mvs N)
  /최소단계 저장     제안된 최소 단계를 저장하고 집중   (영어 명령: /mvs commit)
  /생존 켜기         생존 모드 켜기                     (영어 명령: /survival on)
  /생존 끄기         생존 모드 끄기                     (영어 명령: /survival off)
  /생존              생존 모드 상태 보기                (영어 명령: /survival)

  /도움말            이 도움말 보기                     (영어 명령: /help)
  /종료              종료                               (영어 명령: /quit, /exit)
"""
