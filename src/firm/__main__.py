"""Cadre (a.k.a. firm) CLI entry point.

Usage:
    cadre init <workspace> [--force] [--demo] [--install-hooks]
    cadre unit complete <unit_id> --member <member_id> [...flags]
    cadre run end <run_id> --status <status> [...flags]
    cadre --version
    cadre --help

Both `cadre` and `firm` console scripts route here — the import package
stays `firm`; `cadre` is the public-facing distribution/command name.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from firm import __version__


def _build_parser() -> argparse.ArgumentParser:
    prog_name = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else "cadre"
    if prog_name.endswith(".py"):
        prog_name = "cadre"
    parser = argparse.ArgumentParser(
        prog=prog_name,
        description="Cadre — Coordinated Agent Deployment Runtime Engine. Orchestrates a Firm of AI Members.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{prog_name} {__version__}",
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
    init_parser.add_argument(
        "--demo",
        action="store_true",
        help="Seed the generic `demo` firm after migrations (non-chrisai example).",
    )
    init_parser.add_argument(
        "--install-hooks",
        dest="install_hooks_flag",
        action="store_true",
        help="Install session-pulse hook into <workspace>/.claude/hooks/ and register in settings.json.",
    )

    unit_parser = subparsers.add_parser(
        "unit",
        help="Unit lifecycle operations (complete, ...).",
    )
    unit_sub = unit_parser.add_subparsers(dest="unit_command", metavar="<unit-command>")

    complete_parser = unit_sub.add_parser(
        "complete",
        help="Mark a Unit as done; writes a records row and flips resolved AC.",
    )
    complete_parser.add_argument("unit_id", help="ID of the Unit to complete (e.g., UNIT-100).")
    complete_parser.add_argument(
        "--member", dest="member_id", required=True,
        help="Member ID completing the Unit (actor on the records row).",
    )
    complete_parser.add_argument(
        "--run-id", dest="run_id", default=None,
        help="Optional member_run id linking the transition to a Run.",
    )
    complete_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )
    complete_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope for the records row. Defaults to $FIRM_ID or 'chrisai'.",
    )
    complete_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the planned changes without writing to the DB.",
    )

    # ---- run subparser ----
    run_parser = subparsers.add_parser(
        "run",
        help="Member Run lifecycle operations (end, ...).",
    )
    run_sub = run_parser.add_subparsers(dest="run_command", metavar="<run-command>")

    end_parser = run_sub.add_parser(
        "end",
        help="Finalize a Member Run; writes usage_event + records row.",
    )
    end_parser.add_argument("run_id", help="ID of the Member Run to finalize (e.g., RUN-001).")
    end_parser.add_argument(
        "--status", "-s", dest="final_status", required=True,
        choices=["completed", "failed", "cancelled"],
        help="Final status of the Run.",
    )
    end_parser.add_argument(
        "--outputs", dest="outputs_json", default=None,
        help='JSON array of output artifacts (e.g., \'[{"path":"post.md"}]\').',
    )
    end_parser.add_argument(
        "--usage", dest="usage_json", default=None,
        help='JSON dict of token usage (e.g., \'{"plan":"api","tokens_in":1000}\').',
    )
    end_parser.add_argument(
        "--error", dest="error_json", default=None,
        help='JSON dict of error details (e.g., \'{"message":"timeout"}\').',
    )
    end_parser.add_argument(
        "--notes", default=None,
        help="Freeform notes (credential patterns auto-redacted before write).",
    )
    end_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )
    end_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope for records/usage rows. Defaults to $FIRM_ID or 'chrisai'.",
    )
    end_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the planned changes without writing to the DB.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        from firm.cli.init import run_init

        return run_init(
            args.workspace,
            force=args.force,
            demo=args.demo,
            install_hooks_flag=args.install_hooks_flag,
        )

    if args.command == "unit":
        if args.unit_command == "complete":
            from firm.cli.unit import run_unit_complete

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            firm_id = args.firm_id or os.environ.get("FIRM_ID", "chrisai")
            return run_unit_complete(
                workspace=workspace,
                unit_id=args.unit_id,
                member_id=args.member_id,
                run_id=args.run_id,
                dry_run=args.dry_run,
                firm_id=firm_id,
            )
        parser.parse_args(["unit", "--help"])
        return 0

    if args.command == "run":
        if args.run_command == "end":
            from firm.cli.run import run_run_end

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            firm_id = args.firm_id or os.environ.get("FIRM_ID", "chrisai")
            return run_run_end(
                workspace=workspace,
                run_id=args.run_id,
                final_status=args.final_status,
                outputs_json=args.outputs_json,
                usage_json=args.usage_json,
                error_json=args.error_json,
                notes=args.notes,
                dry_run=args.dry_run,
                firm_id=firm_id,
            )
        parser.parse_args(["run", "--help"])
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
