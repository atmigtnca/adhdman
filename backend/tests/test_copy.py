"""Lint tests for the non-shaming copy library.

These strings are surfaced to the user across the API, TUI, and web dashboard.
The tone contract: calm, supportive, no blame, no streak/quantity language.
"""

from __future__ import annotations

from app import copy as copy_module


def test_copy_module_exposes_expected_constants() -> None:
    expected = {
        "EMPTY_FOCUS",
        "FOCUS_CONFLICT",
        "BLOCK_RESET_PROMPT",
        "STUCK_OPTIONS",
        "BODY_DOUBLE_START",
        "BODY_DOUBLE_CHECK_IN",
        "BODY_DOUBLE_STOP",
        "SURVIVAL_ENTER",
        "SURVIVAL_EXIT",
        "MVS_PROMPT",
        "BREAKDOWN_PROMPT",
    }
    for name in expected:
        assert hasattr(copy_module, name), name


def test_stuck_options_cover_all_four_choices() -> None:
    assert set(copy_module.STUCK_OPTIONS.keys()) == {
        "shrink",
        "swap",
        "skip",
        "park",
    }


def test_stuck_options_mapping_is_read_only() -> None:
    try:
        copy_module.STUCK_OPTIONS["new"] = "nope"  # type: ignore[index]
    except TypeError:
        return
    raise AssertionError("STUCK_OPTIONS must be immutable")


def test_all_strings_contains_every_user_visible_line() -> None:
    individual = {
        copy_module.EMPTY_FOCUS,
        copy_module.FOCUS_CONFLICT,
        copy_module.BLOCK_RESET_PROMPT,
        copy_module.BODY_DOUBLE_START,
        copy_module.BODY_DOUBLE_CHECK_IN,
        copy_module.BODY_DOUBLE_STOP,
        copy_module.SURVIVAL_ENTER,
        copy_module.SURVIVAL_EXIT,
        copy_module.MVS_PROMPT,
        copy_module.BREAKDOWN_PROMPT,
        *copy_module.STUCK_OPTIONS.values(),
    }
    assert individual.issubset(set(copy_module.ALL_STRINGS))


def test_no_forbidden_tokens_appear_in_copy() -> None:
    for line in copy_module.ALL_STRINGS:
        lowered = line.lower()
        for token in copy_module.FORBIDDEN_TOKENS:
            assert token not in lowered, (
                f"forbidden token {token!r} appeared in copy: {line!r}"
            )


def test_no_second_person_blame_phrases() -> None:
    blame_phrases = (
        "you didn't",
        "you did not",
        "you missed",
        "you should",
        "your fault",
        "you keep",
    )
    for line in copy_module.ALL_STRINGS:
        lowered = line.lower()
        for phrase in blame_phrases:
            assert phrase not in lowered, (
                f"blame phrase {phrase!r} appeared in copy: {line!r}"
            )


def test_no_urgency_punctuation_pile_ups() -> None:
    for line in copy_module.ALL_STRINGS:
        assert "!!" not in line, line
        assert "!?" not in line, line
        assert "??" not in line, line


def test_no_streak_or_quantity_shaming() -> None:
    shaming_phrases = (
        "only finished",
        "lost your streak",
        "broke your streak",
        "behind",
    )
    for line in copy_module.ALL_STRINGS:
        lowered = line.lower()
        for phrase in shaming_phrases:
            assert phrase not in lowered, (
                f"shaming phrase {phrase!r} appeared in copy: {line!r}"
            )


def test_all_strings_are_non_empty_and_trimmed() -> None:
    for line in copy_module.ALL_STRINGS:
        assert line == line.strip(), f"copy must not have leading/trailing whitespace: {line!r}"
        assert line, "copy strings must not be empty"
