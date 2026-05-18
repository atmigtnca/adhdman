from tui.commands import (
    Capture,
    Delete,
    Done,
    Events,
    Help,
    Inbox,
    Noop,
    Pick,
    Quit,
    Resolve,
    Search,
    Tasks,
    Today,
    Undo,
    Unknown,
    parse_command,
)


def test_blank_is_noop():
    assert isinstance(parse_command(""), Noop)
    assert isinstance(parse_command("   "), Noop)
    assert isinstance(parse_command(None), Noop)


def test_bare_text_is_capture():
    cmd = parse_command("pay rent tomorrow")
    assert isinstance(cmd, Capture)
    assert cmd.text == "pay rent tomorrow"


def test_capture_strips_whitespace():
    cmd = parse_command("  call dentist  ")
    assert isinstance(cmd, Capture)
    assert cmd.text == "call dentist"


def test_slash_commands_parse():
    assert isinstance(parse_command("/today"), Today)
    assert isinstance(parse_command("/inbox"), Inbox)
    assert isinstance(parse_command("/tasks"), Tasks)
    assert isinstance(parse_command("/events"), Events)
    assert isinstance(parse_command("/help"), Help)
    assert isinstance(parse_command("/quit"), Quit)
    assert isinstance(parse_command("/exit"), Quit)


def test_korean_slash_commands_parse_as_first_class_aliases():
    assert isinstance(parse_command("/오늘"), Today)
    assert isinstance(parse_command("/인박스"), Inbox)
    assert isinstance(parse_command("/할일"), Tasks)
    assert isinstance(parse_command("/일정"), Events)
    assert isinstance(parse_command("/도움말"), Help)
    assert isinstance(parse_command("/종료"), Quit)


def test_case_insensitive():
    assert isinstance(parse_command("/Today"), Today)
    assert isinstance(parse_command("/HELP"), Help)


def test_done_with_and_without_index():
    cmd = parse_command("/done 2")
    assert isinstance(cmd, Done) and cmd.index == 2
    cmd2 = parse_command("/done")
    assert isinstance(cmd2, Done) and cmd2.index is None
    cmd3 = parse_command("/완료 2")
    assert isinstance(cmd3, Done) and cmd3.index == 2


def test_done_non_integer_is_unknown():
    assert isinstance(parse_command("/done foo"), Unknown)


def test_delete_with_and_without_index():
    cmd = parse_command("/delete 2")
    assert isinstance(cmd, Delete) and cmd.index == 2
    cmd2 = parse_command("/delete")
    assert isinstance(cmd2, Delete) and cmd2.index is None
    cmd3 = parse_command("/삭제 2")
    assert isinstance(cmd3, Delete) and cmd3.index == 2
    assert isinstance(parse_command("/delete foo"), Unknown)


def test_undo_variants():
    assert isinstance(parse_command("/undo"), Undo)
    assert parse_command("/undo").action_id is None
    assert parse_command("/undo 42").action_id == 42
    assert isinstance(parse_command("/되돌리기"), Undo)
    assert parse_command("/되돌리기 42").action_id == 42
    assert isinstance(parse_command("/undo bar"), Unknown)


def test_search_requires_query():
    cmd = parse_command("/search milk")
    assert isinstance(cmd, Search) and cmd.query == "milk"
    cmd_ko = parse_command("/검색 우유")
    assert isinstance(cmd_ko, Search) and cmd_ko.query == "우유"
    assert isinstance(parse_command("/search"), Unknown)


def test_pick():
    cmd = parse_command("/pick 3")
    assert isinstance(cmd, Pick) and cmd.index == 3
    cmd_ko = parse_command("/선택 3")
    assert isinstance(cmd_ko, Pick) and cmd_ko.index == 3
    assert isinstance(parse_command("/pick foo"), Unknown)


def test_resolve():
    cmd = parse_command("/resolve next friday 3pm")
    assert isinstance(cmd, Resolve) and cmd.text == "next friday 3pm"
    cmd_ko = parse_command("/해석 다음주 금요일 3시")
    assert isinstance(cmd_ko, Resolve) and cmd_ko.text == "다음주 금요일 3시"
    assert isinstance(parse_command("/resolve"), Unknown)


def test_unknown_command():
    cmd = parse_command("/wat")
    assert isinstance(cmd, Unknown) and cmd.raw == "/wat"
