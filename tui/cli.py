from __future__ import annotations

import argparse
from collections.abc import Sequence

from tui.app import TuiApp
from tui.commands import HELP_TEXT

VERSION = "0.1.0"


class _HelpFormatter(argparse.RawDescriptionHelpFormatter):
    pass


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="adhdman",
        description="ADHDman local-first TUI command center.",
        epilog=HELP_TEXT,
        formatter_class=_HelpFormatter,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    parser.add_argument(
        "--version",
        action="version",
        version=f"adhdman {VERSION}",
    )
    parser.parse_args(argv)

    app = TuiApp()
    try:
        app.run()
    finally:
        app.client.close()
