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
import json
import os
import sys
from pathlib import Path

class _JsonUsageParser(argparse.ArgumentParser):
    """An argparse parser whose usage errors are one JSON object on STDOUT.

    Used for `cadre heartbeat ...` only (#131). Every heartbeat verb prints one
    pretty-printed object through `cli/heartbeat.py::_emit` -- except when
    argparse rejected the arguments, which put usage on stderr and left stdout
    EMPTY. A caller that parses stdout got nothing and could not tell a usage
    error from a crash; `dashboard/founding.py::set_pulse` already depends on
    exactly one object coming back.

    IT HAS TO REACH EVERY LEVEL. The five argument-error shapes are raised by
    three different parsers -- the top level, the heartbeat parser, and a verb
    subparser -- and which one raises a given shape MOVES as flags are added
    (measured: `heartbeat status --nope` reached the top level only while
    `status` had no arguments of its own). An override on one level would pass
    some shapes and fail others, which is the false PASS this shape exists to
    prevent. `argparse.add_subparsers` defaults `parser_class` to `type(self)`,
    so making the TOP parser this class carries it to every subparser
    underneath, and the mutation that breaks that is worth keeping (Q10).

    `parser` names the level that raised it, so a reader -- and a leg -- can
    tell which one did, rather than matching substrings in a usage blob.
    `message` is argparse's OWN words: one source for the wording, and it names
    the offending flag.
    """

    def error(self, message: str) -> None:      # type: ignore[override]
        print(json.dumps({"ok": False, "reason": "usage",
                          "parser": self.prog, "message": message}, indent=2))
        raise SystemExit(2)


class _VersionOnRequest(argparse._VersionAction):
    """``--version``, resolved only when the flag is given.

    The version reads git in a checkout (#120). Handing it to argparse while the
    parser was built ran that for every command, the timer's pulse and every
    Member's CLI call among them, although only this flag prints it (#120 G2
    re-grade N3).
    """

    def __call__(self, parser, namespace, values, option_string=None):
        # One attribute read. `from firm import __version__` asks the module
        # twice (the import machinery checks hasattr first), which ran git twice.
        import firm
        self.version = f"{parser.prog} {firm.__version__}"
        super().__call__(parser, namespace, values, option_string)


def _force_utf8_streams() -> None:
    """Take the CLI's own output off the platform locale encoding.

    Cadre prints check marks, crosses, arrows and box-drawing characters.
    Python encodes stdout with the platform locale, which on Windows is
    cp1252 for a pipe or a file and the console code page for a terminal.
    None of those can represent U+2713 or U+2192, so the command does not
    print a degraded line - it raises UnicodeEncodeError and exits 1.

    Measured on Windows 10 / Python 3.12.6 against the installed wheel, with
    stdout piped:

        cadre doctor          rc 1, UnicodeEncodeError on U+2713
        cadre templates list  rc 1, UnicodeEncodeError on U+2192

    Both work interactively on a terminal that is already UTF-8 and die the
    moment output is redirected to a file, piped into another command, or
    captured by a script - which is exactly when an operator is trying to
    keep a record of what the tool said.

    UTF-8 with errors="replace": a redirect or a pipe now gets the real
    characters, a UTF-8 terminal renders them, and a legacy code-page console
    shows a substitute glyph instead of a traceback. Nothing exits 1 over a
    character any more.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # already wrapped, or not a real stream


def _build_parser(json_usage: bool = False) -> argparse.ArgumentParser:
    # Imported here rather than at module scope, the way this file reaches for
    # everything else it needs: the source labels come from the ledger so the
    # parser and the column cannot hold two different lists.
    from firm.services import pulse_ledger

    prog_name = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else "cadre"
    if prog_name.endswith(".py"):
        prog_name = "cadre"
    # One class for the whole tree, because `add_subparsers` defaults
    # `parser_class` to `type(self)`: set it here and every level below
    # inherits it, which is what condition 1 asks for.
    parser_class = _JsonUsageParser if json_usage else argparse.ArgumentParser
    parser = parser_class(
        prog=prog_name,
        description="Cadre — Coordinated Agent Deployment Runtime Engine. Orchestrates a Firm of AI Members.",
    )
    parser.add_argument("--version", action=_VersionOnRequest)

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    init_parser = subparsers.add_parser(
        "init",
        help="Initialize a .firm/ directory and SQLite database at the given workspace.",
    )
    init_parser.add_argument(
        "workspace",
        type=Path,
        # Optional, because `--proposal-template` prints a schema and has no
        # directory to print it about. argparse refuses the command before
        # any code of ours runs otherwise.
        nargs="?",
        default=None,
        help="Path to the workspace where .firm/ will be created, or the "
             "firms ROOT when founding with --proposal.",
    )
    init_parser.add_argument(
        "--proposal",
        type=Path,
        metavar="FILE",
        help="Found a complete firm from a proposal file, at "
             "<root>/<firm_id>. See --proposal-template for the shape.",
    )
    init_parser.add_argument(
        "--brief",
        type=Path,
        metavar="FILE",
        help="Found a complete firm from a written spec, by running the "
             "founding agent on it.",
    )
    init_parser.add_argument(
        "--proposal-template",
        dest="proposal_template",
        action="store_true",
        help="Print the proposal schema, with every key and what happens to "
             "it, and exit.",
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
        help="Unit lifecycle operations (create, complete).",
    )
    unit_sub = unit_parser.add_subparsers(dest="unit_command", metavar="<unit-command>")

    create_parser = unit_sub.add_parser(
        "create",
        help="Queue a new Unit of work; prints the new unit id.",
    )
    create_parser.add_argument(
        "--name", required=True,
        help="What the Unit is (required).",
    )
    create_parser.add_argument(
        "--project", dest="project_id", required=True,
        help="Project the Unit belongs to (e.g., PRJ-010).",
    )
    create_parser.add_argument(
        "--description", default="",
        help="The why and the context the assignee needs.",
    )
    create_parser.add_argument(
        "--assignee", default=None,
        help="Member to assign (e.g., MEM-004). Defaults to the calling member "
             "($CADRE_MEMBER_ID); omitted entirely for a Board call.",
    )
    create_parser.add_argument(
        "--priority", default="medium",
        choices=["urgent", "high", "medium", "low"],
        help="Queue priority (default: medium).",
    )
    create_parser.add_argument(
        "--depends-on", dest="depends_on", action="append", default=None,
        metavar="UNIT_ID",
        help="Unit this one is blocked by. Repeatable.",
    )
    create_parser.add_argument(
        "--ac", dest="acceptance_criteria", action="append", default=None,
        metavar="TEXT",
        help="Acceptance criterion for the Unit. Repeatable.",
    )
    create_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )
    create_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.",
    )
    create_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the planned changes without writing to the DB.",
    )

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
        "--outputs", action="append", default=None, metavar="PATH",
        help="Produced file to register as a Document against the Unit and "
             "record on unit.outputs. Repeatable. A path that isn't on disk "
             "aborts without completing.",
    )
    complete_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )
    complete_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope for the records row. Defaults to the firm this workspace's db holds.",
    )
    complete_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the planned changes without writing to the DB.",
    )

    # ---- doc subparser: the Member's deliverable-registration surface ----
    doc_parser = subparsers.add_parser(
        "doc",
        help="Deliverable operations (register).",
    )
    doc_sub = doc_parser.add_subparsers(dest="doc_command", metavar="<doc-command>")

    doc_register_parser = doc_sub.add_parser(
        "register",
        help="Register a produced file as a Unit's deliverable (Document + outputs).",
    )
    doc_register_parser.add_argument(
        "--unit", dest="unit_id", required=True,
        help="Unit the deliverable belongs to (e.g., UNIT-012).",
    )
    doc_register_parser.add_argument(
        "--path", required=True,
        help="Path to the produced file. Must exist on disk.",
    )
    doc_register_parser.add_argument(
        "--name", default=None,
        help="Document name (defaults to the filename).",
    )
    doc_register_parser.add_argument(
        "--type", dest="doc_type", default="draft",
        help="Document type (default: draft).",
    )
    doc_register_parser.add_argument(
        "--member", dest="member_id", default=None,
        help="Member registering it (actor on the records row). Defaults to "
             "$CADRE_MEMBER_ID.",
    )
    doc_register_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )
    doc_register_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.",
    )

    # ---- escalation subparser (MCP->CLI write-surface migration) ----
    esc_parser = subparsers.add_parser(
        "escalation",
        help="Escalation operations (raise, ...). The Board-escalation path that "
             "works in every firm, with or without the firm MCP server.",
    )
    esc_sub = esc_parser.add_subparsers(dest="escalation_command", metavar="<escalation-command>")

    esc_raise_parser = esc_sub.add_parser(
        "raise",
        help="Raise an escalation to the Board (dedup-aware, notifies immediately).",
    )
    esc_raise_parser.add_argument(
        "--member", dest="raised_by_member_id", default=None,
        help="Member ID raising the escalation (actor on the records row). "
             "Defaults to $CADRE_MEMBER_ID, which every Member run has "
             "exported, so a Member need not know its own id. Matches "
             "`firm gate request`.",
    )
    esc_raise_parser.add_argument("--title", required=True, help="Short escalation title.")
    esc_raise_parser.add_argument("--body", default="", help="Escalation detail body.")
    esc_raise_parser.add_argument(
        "--severity", default="normal", choices=["low", "normal", "high", "critical"],
        help="Severity (default: normal).",
    )
    esc_raise_parser.add_argument(
        "--target-type", dest="target_entity_type", default="",
        help="Optional target entity type (e.g., unit).",
    )
    esc_raise_parser.add_argument(
        "--target-id", dest="target_entity_id", default="",
        help="Optional target entity id (e.g., UNIT-018).",
    )
    esc_raise_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )
    esc_raise_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.",
    )

    # ---- gate subparser (MCP->CLI write-surface migration) ----
    gate_parser = subparsers.add_parser(
        "gate",
        help="Gate operations (request, ...). The Board-approval path that used "
             "to exist only as the firm MCP tool firm_request_gate.",
    )
    gate_sub = gate_parser.add_subparsers(dest="gate_command", metavar="<gate-command>")

    gate_request_parser = gate_sub.add_parser(
        "request",
        help="Ask the Board to approve an action (creates a pending Gate, notifies them).",
    )
    gate_request_parser.add_argument(
        "--action", required=True,
        help="What you are asking permission to do, in plain words.",
    )
    gate_request_parser.add_argument(
        "--target-type", dest="target_entity_type", required=True,
        help="Type of the thing the action is about (e.g., unit).",
    )
    gate_request_parser.add_argument(
        "--target-id", dest="target_entity_id", required=True,
        help="Id of the thing the action is about (e.g., UNIT-018).",
    )
    gate_request_parser.add_argument(
        "--member", dest="requesting_member_id", default=None,
        help="Member ID making the request. Defaults to $CADRE_MEMBER_ID.",
    )
    gate_request_parser.add_argument(
        "--context", default="",
        help="Why you are asking — the Board reads this before deciding.",
    )
    gate_request_parser.add_argument(
        "--expires-at", dest="expires_at", default="",
        help="Optional ISO timestamp after which the request lapses.",
    )
    gate_request_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )
    gate_request_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.",
    )

    # ---- goal subparser ----
    goal_parser = subparsers.add_parser(
        "goal",
        help="Goal lifecycle operations (update, ...).",
    )
    goal_sub = goal_parser.add_subparsers(dest="goal_command", metavar="<goal-command>")

    goal_update_parser = goal_sub.add_parser(
        "update",
        help="Refresh a Goal's metric — merges given fields into the metric JSON the goal-health banner parses.",
    )
    goal_update_parser.add_argument("goal_id", help="ID of the Goal to update (e.g., GL-002).")
    goal_update_parser.add_argument(
        "--current", default=None,
        help="Current observed value of the metric (e.g., 6).",
    )
    goal_update_parser.add_argument(
        "--value", default=None,
        help="Target value of the metric (e.g., 5).",
    )
    goal_update_parser.add_argument(
        "--unit", default=None,
        help="Unit label for the metric (e.g., assets, followers).",
    )
    goal_update_parser.add_argument(
        "--type", dest="metric_type", default=None,
        help="Metric type slug (e.g., publish_ready_queue_depth).",
    )
    goal_update_parser.add_argument(
        "--deadline", default=None,
        help="ISO deadline for the metric (e.g., 2026-08-01).",
    )
    goal_update_parser.add_argument(
        "--trend", default=None,
        help="Freeform trend note (e.g., 'up 3 this week').",
    )
    goal_update_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )

    goal_create_parser = goal_sub.add_parser(
        "create",
        help="Author a Goal as the Board. Members don't get this verb — they "
             "propose from inside a run with `firm goal propose` and the "
             "proposal arrives as a Gate.",
    )
    goal_create_parser.add_argument(
        "target", help="The goal, stated as a measurable outcome.")
    goal_create_parser.add_argument(
        "--parent-type", required=True, dest="parent_entity_type",
        choices=["firm", "member", "operation", "project"],
        help="What this goal attaches to.")
    goal_create_parser.add_argument(
        "--parent-id", required=True, dest="parent_entity_id",
        help="ID of the parent entity (e.g., MEM-001, OP-002, or the firm id).")
    goal_create_parser.add_argument(
        "--metric", default=None,
        help='Metric JSON (e.g. \'{"value": 10, "unit": "pages/week"}\').')
    goal_create_parser.add_argument(
        "--level", default=None, help="Goal level (e.g., firm, member).")
    goal_create_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.")
    goal_create_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )

    goal_propose_parser = goal_sub.add_parser(
        "propose",
        help="Propose a Goal for the Board to approve. It raises a Gate, and "
             "the goal exists only once the Board approves it. Works with or "
             "without the firm MCP server.",
    )
    goal_propose_parser.add_argument(
        "target", help="The goal, stated as a measurable outcome.")
    goal_propose_parser.add_argument(
        "--parent-type", required=True, dest="parent_entity_type",
        choices=["firm", "member", "operation", "project"],
        help="What this goal attaches to. Your own goal is 'member'.")
    goal_propose_parser.add_argument(
        "--parent-id", required=True, dest="parent_entity_id",
        help="ID of the parent entity (e.g., your own $CADRE_MEMBER_ID, or OP-002).")
    goal_propose_parser.add_argument(
        "--reasoning", required=True,
        help="Why this metric proves the outcome. The Board reads it before deciding.")
    goal_propose_parser.add_argument(
        "--metric", default=None,
        help='Metric JSON (e.g. \'{"value": 10, "unit": "pages/week"}\').')
    goal_propose_parser.add_argument(
        "--member", dest="member_id", default=None,
        help="Member ID making the proposal. Defaults to $CADRE_MEMBER_ID.")
    goal_propose_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.")
    goal_propose_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )

    # ---- brief subparser ----
    # `base cadre brief` reaches this through the extension's command handler.
    brief_parser = subparsers.add_parser(
        "brief",
        help="Print this Member's briefing: who it is and which Units are open "
             "for it. Rules and decisions arrive separately, through base's "
             "domain layer, and are deliberately not repeated here.",
    )
    brief_parser.add_argument(
        "--member", dest="member_id", default=None,
        help="Member to brief. Defaults to $CADRE_MEMBER_ID, which "
             "spawn_member_run exports into every Member run.")
    brief_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.")
    brief_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )

    # ---- learn subparser ----
    # `base cadre learn` reaches this through the extension's command handler.
    # It is the other half of the Stop gate: the gate blocks on a Unit closed
    # with nothing recorded, and this is the only thing that clears it.
    learn_parser = subparsers.add_parser(
        "learn",
        help="Record what this Member learned into the firm's own graph. With "
             "--unit it also clears that Unit's write-back debt, which is what "
             "lets the session end.",
    )
    learn_parser.add_argument(
        "--text", required=True,
        help="The lesson, in the Member's own words.")
    learn_parser.add_argument(
        "--unit", dest="unit_id", default=None,
        help="The Unit this lesson came from. Clears its write-back debt. "
             "Omit for a lesson that belongs to no single Unit — the debt on "
             "any closed Unit then stays open, because it is per Unit.")
    learn_parser.add_argument(
        "--type", dest="note_type", default="insight",
        help="base note type: insight, correction, decision, commitment, "
             "shift. Use correction for a mistake worth not repeating.")
    learn_parser.add_argument(
        "--member", dest="member_id", default=None,
        help="Member recording it. Defaults to $CADRE_MEMBER_ID.")
    learn_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.")
    learn_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )

    # ---- complete subparser ----
    # `firm unit complete` unchanged, plus the write-back debt. Kept separate
    # from `firm unit complete` on purpose: that surface is the Board's and the
    # tooling's as well as a Member's, and opening a Member's write-back debt
    # from a Board close would block the wrong session.
    complete_parser = subparsers.add_parser(
        "complete",
        help="Close one of this Member's Units and open its write-back debt. "
             "The close itself is `firm unit complete`, unchanged.",
    )
    complete_parser.add_argument(
        "unit_id", help="The Unit to close.")
    complete_parser.add_argument(
        "--outputs", nargs="*", default=None,
        help="Deliverable file(s) to register against the Unit before it closes.")
    complete_parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Show what would happen. Opens no debt, because nothing closes.")
    complete_parser.add_argument(
        "--member", dest="member_id", default=None,
        help="Member closing it. Defaults to $CADRE_MEMBER_ID.")
    complete_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.")
    complete_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )

    # ---- extension subparser ----
    # The one step of "make base the engine for this firm" that had no command
    # behind it. It was a `python -c` in the runbook, which is the shape of
    # step that gets skipped.
    ext_parser = subparsers.add_parser(
        "extension",
        help="The firm's base extension — install the Cadre manifest into "
             "base so `base cadre <verb>` resolves.",
    )
    ext_sub = ext_parser.add_subparsers(dest="extension_command", required=True,
                                        metavar="<extension-command>")
    ext_install = ext_sub.add_parser(
        "install",
        help="Render, validate, install and read the manifest back. Exits 0 "
             "when base is not installed at all — a firm without base is "
             "degraded, never broken.",
    )
    ext_install.add_argument(
        "--framework-dir", dest="framework_dir", default=None,
        help="Where Cadre is installed. Defaults to the package's own root, "
             "which is what a normal install wants.")
    # Not the house sentence "(defaults to current directory)" the other
    # --workspace flags carry, on purpose: this default is different. It walks
    # up to the firm you are standing in, and outside any firm it names no
    # workspace at all. Copying that sentence would tell the operator about a
    # default this flag does not have.
    ext_install.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to the firm you are "
             "standing in). The manifest goes into that firm's own base tier. "
             "Outside a firm, with no flag, it goes where it always went.")

    # ---- export subparser ----
    # The manifest declares `[[hooks.session_start.ingest]]` blocks that base
    # opens at every Member's session start. Until this verb existed nothing in
    # the product ever wrote the files they name, so base ingested nothing and
    # a firm's roster never reached the graph. That is issue #4.
    export_parser = subparsers.add_parser(
        "export",
        help="Write the firm's state out for another tool to read.",
    )
    export_sub = export_parser.add_subparsers(dest="export_command", required=True,
                                              metavar="<export-command>")
    export_base = export_sub.add_parser(
        "base",
        help="Write the JSON files base's extension manifest declares as "
             "ingest sources, so members, units and gates become queryable in "
             "the firm's graph. Exits non-zero if the export did not happen.",
    )
    export_base.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.")
    export_base.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )

    # ---- relay subparser ----
    # A firm keeps its own message store, because #117 gives it its own
    # BASE_HOME and base keeps the relay inbox in that tier. `base relay ping`
    # typed by hand reaches whatever store the environment happens to name --
    # the operator's -- where a Member running inside a firm is not registered
    # and never hears it. This verb resolves the firm and talks to ITS store,
    # so steering a firm works from anywhere with nothing exported by hand.
    relay_parser = subparsers.add_parser(
        "relay",
        help="Talk to the sessions inside a firm's own message store.",
    )
    relay_sub = relay_parser.add_subparsers(dest="relay_command", required=True,
                                            metavar="<relay-command>")
    for _verb, _blurb in (
        ("sessions", "List the sessions registered in this firm's store."),
        ("ping", "Send a message to a session in this firm's store."),
        ("task", "Steer a LIVE session in this firm's store, mid-turn."),
    ):
        _p = relay_sub.add_parser(_verb, help=_blurb)
        _p.add_argument(
            "--firm", dest="firm_dir", type=Path, default=None,
            help="The firm's directory. Defaults to the firm you are standing "
                 "in (walks up for .firm/firm.db).")
        if _verb in ("ping", "task"):
            _p.add_argument("--to", required=True,
                            help="The title to reach in this firm's store.")
            _p.add_argument("--from", dest="from_name", default="cadre",
                            help="Who it is from. Defaults to cadre.")
        if _verb == "ping":
            _p.add_argument("--msg", required=True, help="What to say.")
        if _verb == "task":
            _p.add_argument("--summary", required=True,
                            help="What that session must act on now.")
            _p.add_argument("--slug", default=None,
                            help="The id the receiver clears. Defaults to a "
                                 "timestamped one.")

    # ---- install and identity subparsers ----
    # Both are about the INSTALL rather than a firm, so neither takes
    # --workspace. Issue #120.
    install_parser = subparsers.add_parser(
        "install",
        help="Install a Cadre wheel into this environment and PROVE it took.",
    )
    install_parser.add_argument(
        "wheel", type=Path,
        help="Path to a .whl file. A path only: this is not a package manager.",
    )
    install_parser.add_argument(
        "--python", dest="python_bin", default=None,
        help="Interpreter to install into (defaults to the running one).",
    )
    install_parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Machine-readable result.",
    )

    # `identity` deliberately takes no --workspace and no --firm-id. "What
    # commit are you?" is a question about the INSTALL, and it has to be
    # answerable on a machine that has Cadre installed and no firm yet. That is
    # why it cannot live under `doctor`, which returns db-not-found and exits
    # before any check runs when there is no .firm/firm.db.
    identity_parser = subparsers.add_parser(
        "identity",
        help="What this install is: version, commit, wheel and hash. "
             "Needs no firm and no workspace.",
    )
    identity_parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Machine-readable form.",
    )

    # ---- doctor subparser ----
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Framework-drift report card for a firm; --fix repairs the "
             "mechanical findings (migrations, policy gate, ghosts). "
             "Judgment stays with Train; authority stays with the Board.",
    )
    doctor_parser.add_argument(
        "--install", dest="install_only", action="store_true",
        help="Report on the INSTALL rather than a firm: version, commit, "
             "artifact and whether the three agree. Needs no workspace and no "
             "firm database.")
    doctor_parser.add_argument(
        "--fix", action="store_true",
        help="Apply mechanical fixes (never touches loadouts, models, or goals).")
    doctor_parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit the report card as JSON.")
    doctor_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.")
    doctor_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )

    # ---- pulse subparser ----
    pulse_parser = subparsers.add_parser(
        "pulse",
        help="Run one PULSE activation cycle — spawn due Members per frequency/budget/validation gating.",
    )
    pulse_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )
    pulse_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show which Members would activate without spawning.",
    )
    pulse_parser.add_argument(
        "--abort", action="store_true",
        help="SIGTERM all tracked in-flight Member runs and exit.",
    )
    pulse_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.",
    )
    pulse_parser.add_argument(
        "--only", default=None, metavar="MEMBER_ID",
        help="Board-targeted pulse: activate ONLY this Member "
             "(frequency throttle waived for the target).",
    )
    pulse_parser.add_argument(
        "--drain-queue", action="store_true", dest="drain_queue",
        help="Claim pending pulse_request rows and pulse once per request, "
             "waiting for the pulse lock instead of failing on it.",
    )
    # A FLAG, NOT AN ENVIRONMENT VARIABLE (#128 D3, osprey's verdict item 4).
    # A variable is inherited by everything below the process that sets it --
    # spawn_member_run hands every Member run a copy of the pulse's whole
    # environment, and each scheduler backend starts a Board pulse with the
    # hub server's -- so a label there describes whoever set it, not the pulse
    # that reads it. An argument belongs to exactly one process, every launch
    # site already builds an argument list, and doctor can read a timer's
    # label out of the installed definition without running a pulse.
    #
    # `unset` is deliberately NOT a choice: it is what a pulse with no flag
    # records, and every timer installed before this flag existed passes none.
    # Accepting it would let a caller fake the absence, and choices rejects a
    # misspelling with a usage error where an environment value stores one
    # silently.
    pulse_parser.add_argument(
        "--source", default=None, choices=list(pulse_ledger.SOURCES),
        help="Where this pulse came from, recorded in the pulse ledger. "
             "Omitted records 'unset'. --drain-queue records 'queue'.",
    )

    # ---- notify subparser ----
    notify_parser = subparsers.add_parser(
        "notify",
        help="Send a Board notification (Slack DM / webhook / Telegram) via the firm's notify_config.",
    )
    notify_parser.add_argument(
        "message", nargs="?", default=None,
        help="Message text to deliver to the Board. Omit with --check.",
    )
    notify_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )
    notify_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.",
    )
    notify_parser.add_argument(
        "--check", action="store_true",
        help="Probe the rail's resolvability (config + token + one read-only "
             "identity call) WITHOUT delivering a message.",
    )

    # ---- backup subparser ----
    backup_parser = subparsers.add_parser(
        "backup",
        help="Snapshot every entity table to versionable JSON under "
             ".firm/snapshots/ — commit it, and a bad write becomes a diff.",
    )
    backup_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )
    backup_parser.add_argument(
        "--label", default="manual",
        help="Short label folded into the snapshot filename (e.g. pre-seed).",
    )

    # ---- heartbeat subparser ----
    heartbeat_parser = subparsers.add_parser(
        "heartbeat",
        help="Autonomous pulse cadence — manage the per-firm timer that "
             "fires `cadre pulse` on an interval, through the host "
             "scheduler: systemd user timers on Linux/WSL2, launchd on "
             "macOS, Task Scheduler on Windows.",
    )
    # required=True so ARGPARSE raises for a bare `heartbeat` and the words are
    # its own, rather than this file inventing a message for the one shape
    # argparse would otherwise let through. Bare `heartbeat` used to print help
    # and exit 0, which tells a calling script it worked -- for example one
    # whose verb variable came out empty (osprey's verdict item 6).
    heartbeat_sub = heartbeat_parser.add_subparsers(dest="heartbeat_command",
                                                    required=True)

    hb_enable = heartbeat_sub.add_parser(
        "enable", help="Install and start the heartbeat timer for a firm.",
    )
    hb_enable.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )
    hb_enable.add_argument(
        "--interval", default="30m",
        help="Tick interval as <number><unit>, unit s/m/min/h/d (default 30m). "
             "Ticks are near-free no-ops unless a Member is actually due.",
    )
    hb_enable.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.",
    )

    hb_disable = heartbeat_sub.add_parser(
        "disable", help="Stop and remove the heartbeat timer for a firm.",
    )
    # --workspace with enable's meaning (#131). The three verbs manage one
    # timer and disagreed about how you name the firm it belongs to, and the
    # failure was silent: `disable --workspace /some/firm` was an
    # `unrecognized arguments` error from the TOP-LEVEL parser, exit 2,
    # nothing on stdout, and the timer kept firing.
    hb_disable.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )
    hb_disable.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.",
    )

    hb_status = heartbeat_sub.add_parser(
        "status", help="List installed heartbeat timers with liveness and last pulse.",
    )
    # With NEITHER flag `status` keeps listing every installed heartbeat --
    # that is what the verb is for. Either one narrows it to a single firm.
    hb_status.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db. With no flags, every "
             "installed heartbeat is listed.",
    )
    hb_status.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. With no flags, every installed heartbeat is listed.",
    )

    # ---- rail subparsers (Cadre OS addons — lazy delegates) ----
    # The rails (Slack, Co-Board Chat) ship with Cadre OS, not the
    # open-source framework. `cadre slack …` / `cadre chat …` forward
    # verbatim to the addon CLIs when installed; without them the stubs
    # say exactly what's missing. add_help=False so --help forwards too.
    slack_parser = subparsers.add_parser(
        "slack",
        help="Slack Rail — the boardroom in a Slack channel (Cadre OS addon).",
        add_help=False,
    )
    slack_parser.add_argument("slack_args", nargs=argparse.REMAINDER)
    chat_parser = subparsers.add_parser(
        "chat",
        help="Co-Board Chat — the boardroom as a conversation (Cadre OS addon).",
        add_help=False,
    )
    chat_parser.add_argument("chat_args", nargs=argparse.REMAINDER)

    # ---- dashboard subparser ----
    dash_parser = subparsers.add_parser(
        "dashboard",
        help="Serve the Boardroom dashboard — local web command center over the firm DB.",
    )
    dash_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )
    dash_parser.add_argument(
        "--host", default="127.0.0.1",
        help="Bind address (default 127.0.0.1 — local only).",
    )
    dash_parser.add_argument(
        "--port", type=int, default=8484,
        help="Port to serve on (default 8484).",
    )
    dash_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.",
    )

    # ---- hub subparser ----
    hub_parser = subparsers.add_parser(
        "hub",
        help="Serve EVERY firm from one process — portfolio landing at /, "
             "each boardroom at /f/<firm-id>/.",
    )
    hub_parser.add_argument(
        "--firms-root", dest="firms_root", type=Path,
        default=Path.home() / "firms",
        help="Directory scanned for firm workspaces (default ~/firms). "
             "Any child holding .firm/firm.db is served; new firms appear live.",
    )
    hub_parser.add_argument(
        "--host", default="127.0.0.1",
        help="Bind address (default 127.0.0.1 — local only).",
    )
    hub_parser.add_argument(
        "--port", type=int, default=8484,
        help="Port to serve on (default 8484).",
    )

    # ---- member subparser ----
    member_parser = subparsers.add_parser(
        "member",
        help="Member management (grant/revoke authority, ...).",
    )
    member_sub = member_parser.add_subparsers(
        dest="member_command", metavar="<member-command>",
    )

    # The canonical grant surface. Deliberately CLI-only and never an MCP
    # tool: an authority holder able to mint authority is the same hole one
    # level up. Coboard and the dashboard toggle call the same service.
    for verb, blurb in (
        ("grant", "Grant a capability to a Member (Board action)."),
        ("revoke", "Revoke a capability from a Member (Board action)."),
    ):
        vp = member_sub.add_parser(verb, help=blurb)
        vp.add_argument(
            "capability", choices=["authority"],
            help="Capability to %s. 'authority' unlocks the self-govern "
                 "management tools (create/update member, complete unit, "
                 "resolve escalation, update goal)." % verb,
        )
        vp.add_argument("member_id", help="ID of the Member (e.g., MEM-003).")
        vp.add_argument(
            "--comment", default=None,
            help="Why — recorded on the audit row (Records) and shown in the "
                 "member's Activity feed.",
        )
        vp.add_argument(
            "--workspace", type=Path, default=None,
            help="Workspace containing .firm/firm.db (defaults to current directory).",
        )

    # ---- board subparser ----
    board_parser = subparsers.add_parser(
        "board",
        help="Board credentials (the boardroom's human password).",
    )
    board_sub = board_parser.add_subparsers(
        dest="board_command", metavar="<board-command>",
    )
    board_pw = board_sub.add_parser(
        "password",
        help="Set the board password the boardroom modal asks for "
             "(stored as a salted hash, typed interactively).",
    )
    board_pw.add_argument(
        "--clear", action="store_true",
        help="Remove the password; the machine token stays the only credential.",
    )

    # ---- roll subparser ----
    roll_parser = subparsers.add_parser(
        "roll",
        help="Roll dice (game firms) — OS randomness, result written to Records.",
    )
    roll_parser.add_argument("expr", help="Dice expression, e.g. 1d20+5 or 3d6.")
    roll_parser.add_argument(
        "--reason", required=True,
        help="What the roll is for (e.g. 'Fen: Sleight of Hand vs gate ledger').",
    )
    roll_parser.add_argument(
        "--member", dest="member_id", default=None,
        help="Acting member ID (omit for a Board roll).",
    )
    roll_parser.add_argument(
        "--target-type", dest="target_type", default=None,
        help="Records target entity type (default: firm).",
    )
    roll_parser.add_argument(
        "--target-id", dest="target_id", default=None,
        help="Records target entity ID (default: the firm).",
    )
    roll_parser.add_argument(
        "--adv", action="store_true",
        help="Advantage — roll the expression twice, keep the higher total.",
    )
    roll_parser.add_argument(
        "--dis", action="store_true",
        help="Disadvantage — roll the expression twice, keep the lower total.",
    )
    roll_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
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
        help="Firm scope for records/usage rows. Defaults to the firm this workspace's db holds.",
    )
    end_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the planned changes without writing to the DB.",
    )

    # ---- env subparser (secrets vault) ----
    env_parser = subparsers.add_parser(
        "env",
        help="Firm secrets vault — encrypted variables, two tiers "
             "(global + firm), injected into member runs and `env exec`.",
    )
    env_sub = env_parser.add_subparsers(dest="env_command", metavar="<env-command>")

    env_set = env_sub.add_parser(
        "set", help="Store a variable (value prompted hidden when omitted).",
    )
    env_set.add_argument("key", help="Variable name, e.g. SLACK_TOKEN.")
    env_set.add_argument(
        "value", nargs="?", default=None,
        help="Value. Omit to enter it at a hidden prompt (keeps it out of "
             "shell history).",
    )
    env_set.add_argument(
        "--global", dest="global_tier", action="store_true",
        help="Store at the global tier (every firm inherits it). "
             "Default: firm tier — overrides global on collision.",
    )
    env_set.add_argument(
        "--workspace", type=Path, default=None,
        help="Firm workspace (defaults to current directory).",
    )

    env_unset = env_sub.add_parser("unset", help="Remove a variable from a tier.")
    env_unset.add_argument("key")
    env_unset.add_argument("--global", dest="global_tier", action="store_true")
    env_unset.add_argument("--workspace", type=Path, default=None)

    env_list = env_sub.add_parser(
        "list", help="List variables across both tiers (masked by default).",
    )
    env_list.add_argument("--show", action="store_true", help="Print plaintext values.")
    env_list.add_argument("--workspace", type=Path, default=None)

    env_exec = env_sub.add_parser(
        "exec",
        help="Run a command with the merged vault injected: "
             "cadre env exec -- <cmd> [args…]. The wrapper for .mcp.json "
             "servers and any firm tool.",
    )
    env_exec.add_argument("--workspace", type=Path, default=None)
    env_exec.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to run.")

    env_import = env_sub.add_parser(
        "import",
        help="Import the firm's plaintext .env into the firm-tier vault "
             "(verified read-back before any scrub).",
    )
    env_import.add_argument(
        "--scrub", action="store_true",
        help="Delete .env after verified import.",
    )
    env_import.add_argument("--workspace", type=Path, default=None)

    # ---- templates subparser ----
    tmpl_parser = subparsers.add_parser(
        "templates",
        help="List or install ship-with-the-package template families (protocols + loadout packs).",
    )
    tmpl_sub = tmpl_parser.add_subparsers(dest="templates_command", metavar="<templates-command>")
    tmpl_sub.add_parser(
        "list",
        help="List available template families and their files.",
    )
    tmpl_install_parser = tmpl_sub.add_parser(
        "install",
        help="Install a family into a firm workspace (protocols → .firm/protocols/, packs → .firm/templates/<family>/).",
    )
    tmpl_install_parser.add_argument(
        "family",
        help="Template family name (see `cadre templates list`).",
    )
    tmpl_install_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Firm workspace root containing .firm/ (defaults to current directory).",
    )
    tmpl_install_parser.add_argument(
        "--force", action="store_true",
        help="Overwrite files that already exist in the workspace.",
    )
    tmpl_apply_parser = tmpl_sub.add_parser(
        "apply",
        help="Merge a family's loadout packs into contracts (append-if-absent; safe to re-run).",
    )
    tmpl_apply_parser.add_argument(
        "family",
        help="Template family name (see `cadre templates list`).",
    )
    tmpl_apply_parser.add_argument(
        "--map", dest="mappings", action="append", required=True,
        metavar="PACK=CONTRACT[,CONTRACT]",
        help="Pack-to-contract mapping, e.g. --map dev=CON-ENG --map lead=CON-LEAD. Pack matches by filename prefix.",
    )
    tmpl_apply_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Firm workspace root containing .firm/ (defaults to current directory).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_streams()
    # The JSON usage contract is heartbeat's alone: every other command keeps
    # argparse's usage on stderr with stdout empty, which is what every other
    # caller of this CLI already expects. Read before the parse, because the
    # parse is the thing being changed.
    raw = sys.argv[1:] if argv is None else argv
    parser = _build_parser(json_usage=bool(raw) and raw[0] == "heartbeat")
    args = parser.parse_args(argv)

    if args.command == "init":
        from firm.cli.init import run_brief, run_found, run_init

        if args.proposal_template:
            # The JSON goes to stdout and the legend to stderr, so
            # `cadre init --proposal-template > firm.json` leaves a file that
            # parses while a session reading the terminal still sees what
            # every key does.
            from firm.services.founding import proposal_template

            body, legend = proposal_template()
            sys.stdout.write(body)
            sys.stderr.write(legend)
            return 0
        if args.proposal and args.brief:
            print(json.dumps({"ok": False, "error":
                              "--proposal and --brief are two doors onto the "
                              "same path; pass one"}))
            return 1
        if args.workspace is None:
            print(json.dumps({"ok": False, "error":
                              "cadre init needs a directory: the workspace to "
                              "initialize, or the firms root to found into"}))
            return 1
        if args.proposal:
            return run_found(args.workspace, args.proposal)
        if args.brief:
            return run_brief(args.workspace, args.brief)

        rc = run_init(
            args.workspace,
            force=args.force,
            demo=args.demo,
            install_hooks_flag=args.install_hooks_flag,
        )
        # THE SENTENCE #135 EXISTS BECAUSE OF. A bare `cadre init` makes a
        # workspace and nothing else: no roster, no goal, no Cadre extension
        # in the firm's tier. It used to say nothing about that, so a session
        # that ran it believed it had founded a firm. Printed here rather
        # than inside `run_init`, because `commit` calls `run_init` too and
        # that firm IS being founded.
        if rc == 0:
            print("\nThis is a workspace. A firm is not founded here: no "
                  "roster, no goal, no Cadre extension in its own tier.")
            print("  cadre init --proposal-template      the proposal schema")
            print("  cadre init <root> --proposal <file> found a whole firm")
            print("  cadre init <root> --brief <spec.md> found one from a "
                  "written spec")
        return rc

    if args.command == "brief":
        from firm.services.brief import run_brief

        return run_brief(args.workspace, firm_id=args.firm_id,
                         member_id=args.member_id)

    if args.command == "export":
        if args.export_command == "base":
            from firm.services.base_export import run_export

            return run_export(args.workspace, firm_id=args.firm_id)

    if args.command == "extension":
        if args.extension_command == "install":
            from firm.services.base_extension import run_install

            # THE FIRM YOU ARE STANDING IN (#117), resolved here rather than
            # inside run_install, which is also a Python API. An explicit
            # --workspace wins. Otherwise walk up for .firm/firm.db, and outside
            # any firm pass no workspace, which keeps today's tier. A Member
            # runs `base cadre` in its firm's own tier, so a flag people forget
            # would install where no Member looks.
            if args.workspace is not None:
                from firm.services.firm_relay import explicit_firm

                named, why = explicit_firm(args.workspace)
                if named is None:
                    # Refused BEFORE run_install, so before anything exists on
                    # disk: naming a workspace creates its tier. Exit 2, the
                    # answer `cadre relay --firm` gives for the same mistake.
                    print(f"Error: {why}", file=sys.stderr)
                    return 2
                # Absolute, because it becomes BASE_HOME, and a relative
                # BASE_HOME means whatever directory base resolves it from.
                workspace = named.absolute()
            else:
                from firm.services.firm_relay import resolve_firm

                workspace = resolve_firm()
            return run_install(args.framework_dir, workspace=workspace)

    if args.command == "learn":
        from firm.services.writeback import run_learn

        return run_learn(args.workspace, text=args.text, unit_id=args.unit_id,
                         note_type=args.note_type, firm_id=args.firm_id,
                         member_id=args.member_id)

    if args.command == "complete":
        from firm.services.writeback import run_complete

        return run_complete(args.workspace, unit_id=args.unit_id,
                            outputs=args.outputs, dry_run=args.dry_run,
                            firm_id=args.firm_id, member_id=args.member_id)

    if args.command == "unit":
        if args.unit_command == "create":
            from firm.cli.unit import run_unit_create

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            firm_id = args.firm_id or None
            return run_unit_create(
                workspace=workspace,
                name=args.name,
                project_id=args.project_id,
                description=args.description,
                assignee=args.assignee,
                priority=args.priority,
                depends_on=args.depends_on,
                acceptance_criteria=args.acceptance_criteria,
                dry_run=args.dry_run,
                firm_id=firm_id,
            )
        if args.unit_command == "complete":
            from firm.cli.unit import run_unit_complete

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            firm_id = args.firm_id or None
            return run_unit_complete(
                workspace=workspace,
                unit_id=args.unit_id,
                member_id=args.member_id,
                run_id=args.run_id,
                outputs=args.outputs,
                dry_run=args.dry_run,
                firm_id=firm_id,
            )
        parser.parse_args(["unit", "--help"])
        return 0

    if args.command == "doc":
        if args.doc_command == "register":
            from firm.cli.unit import run_doc_register

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            firm_id = args.firm_id or None
            return run_doc_register(
                workspace=workspace,
                unit_id=args.unit_id,
                path=args.path,
                member_id=args.member_id,
                name=args.name,
                doc_type=args.doc_type,
                firm_id=firm_id,
            )
        parser.parse_args(["doc", "--help"])
        return 0

    if args.command == "escalation":
        if args.escalation_command == "raise":
            from firm.cli.escalation import run_escalation_raise

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            firm_id = args.firm_id or None
            return run_escalation_raise(
                workspace=workspace,
                raised_by_member_id=args.raised_by_member_id,
                title=args.title,
                body=args.body,
                severity=args.severity,
                target_entity_type=args.target_entity_type,
                target_entity_id=args.target_entity_id,
                firm_id=firm_id,
            )
        parser.parse_args(["escalation", "--help"])
        return 0

    if args.command == "gate":
        if args.gate_command == "request":
            from firm.cli.gate import run_gate_request

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            firm_id = args.firm_id or None
            return run_gate_request(
                workspace=workspace,
                action=args.action,
                target_entity_type=args.target_entity_type,
                target_entity_id=args.target_entity_id,
                member_id=args.requesting_member_id,
                context=args.context,
                expires_at=args.expires_at,
                firm_id=firm_id,
            )
        parser.parse_args(["gate", "--help"])
        return 0

    if args.command == "goal":
        if args.goal_command == "create":
            from firm.cli.goal import run_goal_create

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            return run_goal_create(
                workspace=workspace,
                target=args.target,
                parent_entity_type=args.parent_entity_type,
                parent_entity_id=args.parent_entity_id,
                metric=args.metric,
                level=args.level,
                firm_id=args.firm_id,
            )
        if args.goal_command == "propose":
            from firm.cli.goal import run_goal_propose

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            return run_goal_propose(
                workspace=workspace,
                target=args.target,
                parent_entity_type=args.parent_entity_type,
                parent_entity_id=args.parent_entity_id,
                reasoning=args.reasoning,
                metric=args.metric,
                member_id=args.member_id,
                firm_id=args.firm_id or None,
            )
        if args.goal_command == "update":
            from firm.cli.goal import run_goal_update

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            return run_goal_update(
                workspace=workspace,
                goal_id=args.goal_id,
                current=args.current,
                value=args.value,
                unit=args.unit,
                metric_type=args.metric_type,
                deadline=args.deadline,
                trend=args.trend,
            )
        parser.parse_args(["goal", "--help"])
        return 0

    if args.command == "member":
        if args.member_command in ("grant", "revoke"):
            from firm.cli.member import run_member_authority

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            return run_member_authority(
                workspace=workspace,
                member_id=args.member_id,
                grant=args.member_command == "grant",
                comment=args.comment,
            )
        parser.parse_args(["member", "--help"])
        return 0

    if args.command == "board":
        if args.board_command == "password":
            from firm.cli.board import run_board_password

            return run_board_password(clear=args.clear)
        parser.parse_args(["board", "--help"])
        return 0

    if args.command == "relay":
        from firm.cli.relay import run_relay

        return run_relay(args)
    if args.command == "install":
        from firm.cli.install import run_install

        return run_install(args.wheel, python_bin=args.python_bin,
                           as_json=args.as_json)

    if args.command == "identity":
        from firm.identity import run_identity

        return run_identity(as_json=args.as_json)

    if args.command == "doctor":
        if args.install_only:
            # Ahead of run_doctor deliberately: that function returns
            # db-not-found and exits before a single check runs when there is
            # no .firm/firm.db, and the install question has to be answerable
            # on a machine with no firm on it at all.
            from firm.cli.install_doctor import run_install_doctor

            return run_install_doctor(as_json=args.as_json)

        from firm.cli.doctor import run_doctor

        workspace = args.workspace if args.workspace is not None else Path.cwd()
        return run_doctor(
            workspace,
            firm_id=args.firm_id,
            apply_fixes=args.fix,
            as_json=args.as_json,
        )

    if args.command == "pulse":
        from firm.cli.pulse import run_pulse

        workspace = args.workspace if args.workspace is not None else Path.cwd()
        firm_id = args.firm_id or None
        return run_pulse(
            workspace,
            dry_run=args.dry_run,
            abort=args.abort,
            firm_id=firm_id,
            only=args.only,
            drain_queue=args.drain_queue,
            source=args.source,
        )

    if args.command == "backup":
        from firm.cli.backup import run_backup

        workspace = args.workspace if args.workspace is not None else Path.cwd()
        return run_backup(workspace, label=args.label)

    if args.command == "notify":
        from firm.cli.notify import run_notify

        if not args.check and not args.message:
            print("error: MESSAGE is required unless --check is given", file=sys.stderr)
            return 1
        workspace = args.workspace if args.workspace is not None else Path.cwd()
        firm_id = args.firm_id or None
        return run_notify(workspace, args.message, firm_id=firm_id, check=args.check)

    if args.command == "heartbeat":
        if args.heartbeat_command == "enable":
            from firm.cli.heartbeat import run_enable

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            firm_id = args.firm_id or None
            return run_enable(workspace, firm_id, args.interval)
        if args.heartbeat_command == "disable":
            from firm.cli.heartbeat import run_disable

            firm_id = args.firm_id or None
            return run_disable(firm_id, workspace=args.workspace)
        if args.heartbeat_command == "status":
            from firm.cli.heartbeat import run_status

            return run_status(workspace=args.workspace,
                              firm_id=args.firm_id or None)
        # No fallback here any more: `heartbeat_sub` is required, so argparse
        # has already refused a bare `heartbeat` before this line can run. The
        # fallback that used to sit here printed help and exited 0.
        raise AssertionError("unreachable: the heartbeat verb is required")

    if args.command == "slack":
        try:
            from cadre_slack.__main__ import main as slack_main
        except ImportError:
            print("The Slack Rail is a Cadre OS addon — this install doesn't "
                  "have it.\nIt ships with Cadre OS; once installed: "
                  "cadre-slack setup", file=sys.stderr)
            return 1
        return slack_main(args.slack_args)

    if args.command == "chat":
        try:
            from cadre_chat.__main__ import main as chat_main
        except ImportError:
            print("Co-Board Chat is a Cadre OS addon — this install doesn't "
                  "have it.\nIt ships with Cadre OS; once installed: "
                  "cadre-chat setup", file=sys.stderr)
            return 1
        return chat_main(args.chat_args)

    if args.command == "dashboard":
        from firm.dashboard.server import run_dashboard

        workspace = args.workspace if args.workspace is not None else Path.cwd()
        firm_id = args.firm_id or None
        return run_dashboard(
            workspace,
            host=args.host,
            port=args.port,
            firm_id=firm_id,
        )

    if args.command == "hub":
        from firm.dashboard.server import run_hub

        return run_hub(
            args.firms_root,
            host=args.host,
            port=args.port,
        )

    if args.command == "roll":
        from firm.cli.roll import run_roll

        workspace = args.workspace if args.workspace is not None else Path.cwd()
        return run_roll(
            workspace,
            args.expr,
            reason=args.reason,
            member_id=args.member_id,
            target_type=args.target_type,
            target_id=args.target_id,
            advantage=args.adv,
            disadvantage=args.dis,
        )

    if args.command == "run":
        if args.run_command == "end":
            from firm.cli.run import run_run_end

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            firm_id = args.firm_id or None
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

    if args.command == "env":
        workspace = args.workspace if getattr(args, "workspace", None) else Path.cwd()
        if args.env_command == "set":
            from firm.cli.env import run_env_set
            return run_env_set(workspace, args.key, args.value, args.global_tier)
        if args.env_command == "unset":
            from firm.cli.env import run_env_unset
            return run_env_unset(workspace, args.key, args.global_tier)
        if args.env_command == "list":
            from firm.cli.env import run_env_list
            return run_env_list(workspace, args.show)
        if args.env_command == "exec":
            from firm.cli.env import run_env_exec
            return run_env_exec(workspace, args.cmd)
        if args.env_command == "import":
            from firm.cli.env import run_env_import
            return run_env_import(workspace, args.scrub)
        parser.parse_args(["env", "--help"])
        return 0

    if args.command == "templates":
        if args.templates_command == "list":
            from firm.cli.templates import run_templates_list

            return run_templates_list()
        if args.templates_command == "install":
            from firm.cli.templates import run_templates_install

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            return run_templates_install(args.family, workspace, force=args.force)
        if args.templates_command == "apply":
            from firm.cli.templates import run_templates_apply

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            return run_templates_apply(args.family, workspace, args.mappings)
        parser.parse_args(["templates", "--help"])
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
