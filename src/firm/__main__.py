"""firm CLI entry point.

Usage:
    firm init <workspace> [--force]
    firm --version
    firm --help
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from firm import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="firm",
        description="Framework for orchestrating a company of AI Members.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"firm {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    init_parser = subparsers.add_parser(
        "init",
        help="Initialize a .firm/ directory and SQLite database at the given workspace.",
    )
    init_parser.add_argument(
        "workspace",
        type=Path,
        help="Path to the workspace where .firm/ will be created.",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the already-initialized short-circuit.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        from firm.cli.init import run_init

        return run_init(args.workspace, force=args.force)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
