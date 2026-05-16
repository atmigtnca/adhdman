from __future__ import annotations

import tomllib
from pathlib import Path


def test_pyproject_exposes_adhdman_console_script() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"]["adhdman"] == "tui.app:main"
