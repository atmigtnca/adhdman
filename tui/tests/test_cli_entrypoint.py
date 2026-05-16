from __future__ import annotations

import pytest
import tomllib
from pathlib import Path

from tui import cli


def test_pyproject_exposes_adhdman_console_script() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"]["adhdman"] == "tui.cli:main"


def test_help_prints_command_reference_without_starting_tui(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "ADHDman" in out
    assert "/today" in out
    assert "/focus N" in out
    assert "/survival on" in out


def test_version_prints_project_version_without_starting_tui(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "adhdman 0.1.0"
