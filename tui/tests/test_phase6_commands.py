from tui.commands import (
    BodyDoubleCheckIn,
    BodyDoubleCurrent,
    BodyDoubleStart,
    BodyDoubleStop,
    BreakdownCommit,
    BreakdownSuggest,
    FocusCurrent,
    FocusStart,
    FocusStop,
    MVSCommit,
    MVSSuggest,
    StuckApply,
    StuckOptions,
    SurvivalOff,
    SurvivalOn,
    SurvivalStatus,
    Unknown,
    parse_command,
)


def test_focus_variants():
    assert isinstance(parse_command("/focus"), FocusCurrent)
    assert isinstance(parse_command("/focus current"), FocusCurrent)
    assert isinstance(parse_command("/focus stop"), FocusStop)
    cmd = parse_command("/focus 3")
    assert isinstance(cmd, FocusStart) and cmd.index == 3
    assert isinstance(parse_command("/focus foo"), Unknown)


def test_breakdown_variants():
    cmd = parse_command("/breakdown 2")
    assert isinstance(cmd, BreakdownSuggest) and cmd.index == 2
    assert isinstance(parse_command("/breakdown commit"), BreakdownCommit)
    assert isinstance(parse_command("/breakdown COMMIT"), BreakdownCommit)
    assert isinstance(parse_command("/breakdown"), Unknown)
    assert isinstance(parse_command("/breakdown foo"), Unknown)


def test_stuck_variants():
    assert isinstance(parse_command("/stuck"), StuckOptions)
    assert isinstance(parse_command("/stuck options"), StuckOptions)
    for choice in ("shrink", "swap", "skip", "park"):
        cmd = parse_command(f"/stuck {choice}")
        assert isinstance(cmd, StuckApply) and cmd.choice == choice
    cmd2 = parse_command("/stuck apply park")
    assert isinstance(cmd2, StuckApply) and cmd2.choice == "park"
    assert isinstance(parse_command("/stuck wat"), Unknown)


def test_body_double_variants():
    assert isinstance(parse_command("/body-double"), BodyDoubleCurrent)
    assert isinstance(parse_command("/body-double current"), BodyDoubleCurrent)
    assert isinstance(parse_command("/body-double stop"), BodyDoubleStop)
    assert isinstance(parse_command("/body-double check-in"), BodyDoubleCheckIn)
    assert isinstance(parse_command("/body-double checkin"), BodyDoubleCheckIn)
    cmd = parse_command("/body-double start")
    assert isinstance(cmd, BodyDoubleStart) and cmd.interval_seconds is None
    cmd2 = parse_command("/body-double start 300")
    assert isinstance(cmd2, BodyDoubleStart) and cmd2.interval_seconds == 300
    cmd3 = parse_command("/body-double 600")
    assert isinstance(cmd3, BodyDoubleStart) and cmd3.interval_seconds == 600
    assert isinstance(parse_command("/body-double start foo"), Unknown)
    assert isinstance(parse_command("/body-double start -10"), Unknown)
    assert isinstance(parse_command("/body-double -10"), Unknown)
    assert isinstance(parse_command("/body-double 0"), Unknown)
    assert isinstance(parse_command("/body-double foo"), Unknown)


def test_mvs_variants():
    cmd = parse_command("/mvs 1")
    assert isinstance(cmd, MVSSuggest) and cmd.index == 1
    cmd2 = parse_command("/mvs suggest 4")
    assert isinstance(cmd2, MVSSuggest) and cmd2.index == 4
    assert isinstance(parse_command("/mvs commit"), MVSCommit)
    assert isinstance(parse_command("/mvs"), Unknown)
    assert isinstance(parse_command("/mvs foo"), Unknown)


def test_survival_variants():
    assert isinstance(parse_command("/survival"), SurvivalStatus)
    assert isinstance(parse_command("/survival status"), SurvivalStatus)
    assert isinstance(parse_command("/survival on"), SurvivalOn)
    assert isinstance(parse_command("/survival off"), SurvivalOff)
    assert isinstance(parse_command("/survival wat"), Unknown)


def test_korean_helper_commands_parse_as_first_class_aliases():
    assert isinstance(parse_command("/집중"), FocusCurrent)
    assert isinstance(parse_command("/집중 중지"), FocusStop)
    cmd = parse_command("/집중 3")
    assert isinstance(cmd, FocusStart) and cmd.index == 3

    cmd2 = parse_command("/쪼개기 2")
    assert isinstance(cmd2, BreakdownSuggest) and cmd2.index == 2
    assert isinstance(parse_command("/쪼개기 저장"), BreakdownCommit)

    assert isinstance(parse_command("/막힘"), StuckOptions)
    cmd3 = parse_command("/막힘 미루기")
    assert isinstance(cmd3, StuckApply) and cmd3.choice == "park"

    assert isinstance(parse_command("/바디더블"), BodyDoubleCurrent)
    assert isinstance(parse_command("/바디더블 중지"), BodyDoubleStop)
    assert isinstance(parse_command("/바디더블 체크인"), BodyDoubleCheckIn)
    cmd4 = parse_command("/바디더블 300")
    assert isinstance(cmd4, BodyDoubleStart) and cmd4.interval_seconds == 300

    cmd5 = parse_command("/최소단계 1")
    assert isinstance(cmd5, MVSSuggest) and cmd5.index == 1
    assert isinstance(parse_command("/최소단계 저장"), MVSCommit)

    assert isinstance(parse_command("/생존"), SurvivalStatus)
    assert isinstance(parse_command("/생존 켜기"), SurvivalOn)
    assert isinstance(parse_command("/생존 끄기"), SurvivalOff)


def test_case_insensitive_helpers():
    assert isinstance(parse_command("/FOCUS stop"), FocusStop)
    assert isinstance(parse_command("/Body-Double STOP"), BodyDoubleStop)
    assert isinstance(parse_command("/Survival ON"), SurvivalOn)


def test_number_pick_discipline_still_for_helpers():
    """Helpers that need a target must take an integer index, never free text."""
    assert isinstance(parse_command("/focus pay rent"), Unknown)
    assert isinstance(parse_command("/breakdown call dentist"), Unknown)
    assert isinstance(parse_command("/mvs pay rent"), Unknown)
