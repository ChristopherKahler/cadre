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
        help="Firm scope for the records row. Defaults to the firm this workspace's db holds.",
    )
    complete_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the planned changes without writing to the DB.",
    )

    # ---- escalation subparser (MCP->CLI write-surface migration) ----
    esc_parser = subparsers.add_parser(
        "escalation",
        help="Escalation operations (raise, ...). Replaces the firm MCP firm_escalate tool.",
    )
    esc_sub = esc_parser.add_subparsers(dest="escalation_command", metavar="<escalation-command>")

    esc_raise_parser = esc_sub.add_parser(
        "raise",
        help="Raise an escalation to the Board (dedup-aware, notifies immediately).",
    )
    esc_raise_parser.add_argument(
        "--member", dest="raised_by_member_id", required=True,
        help="Member ID raising the escalation (actor on the records row).",
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
             "propose from inside a run and the proposal arrives as a Gate.",
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

    # ---- notify subparser ----
    notify_parser = subparsers.add_parser(
        "notify",
        help="Send a Board notification (Slack DM / webhook / Telegram) via the firm's notify_config.",
    )
    notify_parser.add_argument("message", help="Message text to deliver to the Board.")
    notify_parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace containing .firm/firm.db (defaults to current directory).",
    )
    notify_parser.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.",
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
        help="Autonomous pulse cadence — manage the per-firm systemd user "
             "timer that fires `cadre pulse` on an interval.",
    )
    heartbeat_sub = heartbeat_parser.add_subparsers(dest="heartbeat_command")

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
    hb_disable.add_argument(
        "--firm-id", dest="firm_id", default=None,
        help="Firm scope. Defaults to the firm this workspace's db holds.",
    )

    heartbeat_sub.add_parser(
        "status", help="List installed heartbeat timers with liveness and last pulse.",
    )

    # ---- slack rail subparser ----
    slack_parser = subparsers.add_parser(
        "slack",
        help="The Slack rail — a board channel that opens headless Co-Board "
             "sessions; every thread is a conversation with the boardroom.",
    )
    slack_sub = slack_parser.add_subparsers(dest="slack_command", metavar="<slack-command>")

    slack_sub.add_parser(
        "manifest",
        help="Print the Slack app manifest (paste at api.slack.com/apps → From a manifest).",
    )
    slack_setup = slack_sub.add_parser(
        "setup",
        help="Wizard: tokens → vault (global tier), pick the board channel, "
             "pair the operator, choose the permission mode.",
    )
    slack_setup.add_argument(
        "--firms-root", dest="firms_root", type=Path,
        default=Path.home() / "firms",
        help="The Co-Board's boot directory — headless sessions run here "
             "(default ~/firms).",
    )
    slack_sub.add_parser(
        "serve",
        help="Run the rail daemon in the foreground (Socket Mode — no public URL).",
    )
    slack_sub.add_parser(
        "enable",
        help="Install and start the rail as a systemd user service "
             "(its own unit — hub restarts never drop a board turn).",
    )
    slack_sub.add_parser("disable", help="Stop and remove the rail service.")
    slack_mode = slack_sub.add_parser(
        "mode",
        help="Show or set the permission posture (approve = 👍/👎 gates every "
             "action; skip = full trust). Restarts the service when changed.",
    )
    slack_mode.add_argument(
        "value", nargs="?", choices=["approve", "skip"], default=None,
        help="Omit to show the current mode.",
    )
    slack_model = slack_sub.add_parser(
        "model",
        help="Show or set the model board turns run on (opus, opus[1m], "
             "sonnet, or a full id; 'default' clears the override).",
    )
    slack_model.add_argument(
        "value", nargs="?", default=None,
        help="Omit to show the current model.",
    )
    slack_updates = slack_sub.add_parser(
        "updates",
        help="Show or toggle in-turn proactive thread updates (on = narrate "
             "load-bearing moments via `slack say`; off = quiet, one answer per turn).",
    )
    slack_updates.add_argument(
        "value", nargs="?", choices=["on", "off"], default=None,
        help="Omit to show the current setting.",
    )
    slack_sub.add_parser(
        "status",
        help="Service state, config summary, thread-map size, last activity.",
    )
    slack_sub.add_parser(
        "test",
        help="Post a wiring-test message into the configured board channel.",
    )
    slack_say = slack_sub.add_parser(
        "say",
        help="Post a message into the board channel/thread — used by running "
             "boardroom sessions to answer mid-turn (thread comes from env).",
    )
    slack_say.add_argument("text", help="Message text to post.")
    slack_say.add_argument(
        "--thread", default=None,
        help="Thread ts (defaults to $CADRE_RAIL_THREAD_TS from the spawn env).",
    )

    # ---- chat rail subparser ----
    chat_parser = subparsers.add_parser(
        "chat",
        help="The chat rail — cadre's own boardroom chat in the browser; "
             "every conversation is a headless Co-Board session. No Slack, "
             "no tokens, localhost only.",
    )
    chat_sub = chat_parser.add_subparsers(dest="chat_command", metavar="<chat-command>")

    chat_setup = chat_sub.add_parser(
        "setup",
        help="Wizard: firms root + port + permission mode — no external app.",
    )
    chat_setup.add_argument(
        "--firms-root", dest="firms_root", type=Path,
        default=Path.home() / "firms",
        help="The Co-Board's boot directory — headless sessions run here "
             "(default ~/firms).",
    )
    chat_sub.add_parser(
        "serve",
        help="Run the rail daemon in the foreground (UI + API on localhost).",
    )
    chat_sub.add_parser(
        "open",
        help="Print the chat UI URL and try to open it in a browser.",
    )
    chat_sub.add_parser(
        "enable",
        help="Install and start the rail as a systemd user service "
             "(its own unit — hub restarts never drop a board turn).",
    )
    chat_sub.add_parser("disable", help="Stop and remove the rail service.")
    chat_mode = chat_sub.add_parser(
        "mode",
        help="Show or set the permission posture (approve = Allow/Deny card "
             "per action; skip = full trust). Restarts the service when changed.",
    )
    chat_mode.add_argument(
        "value", nargs="?", choices=["approve", "skip"], default=None,
        help="Omit to show the current mode.",
    )
    chat_model = chat_sub.add_parser(
        "model",
        help="Show or set the model board turns run on (opus, opus[1m], "
             "sonnet, or a full id; 'default' clears the override).",
    )
    chat_model.add_argument(
        "value", nargs="?", default=None,
        help="Omit to show the current model.",
    )
    chat_host = chat_sub.add_parser(
        "host",
        help="Show or set the bind address: local (127.0.0.1, default), "
             "tailscale (this machine's tailnet IP — phone access), or an IPv4.",
    )
    chat_host.add_argument(
        "value", nargs="?", default=None,
        help="Omit to show the current bind.",
    )
    chat_updates = chat_sub.add_parser(
        "updates",
        help="Show or toggle in-turn proactive updates (on = narrate "
             "load-bearing moments via `chat say`; off = quiet, one answer per turn).",
    )
    chat_updates.add_argument(
        "value", nargs="?", choices=["on", "off"], default=None,
        help="Omit to show the current setting.",
    )
    chat_sub.add_parser(
        "status",
        help="Service state, config summary, conversation count, last activity.",
    )
    chat_sub.add_parser(
        "test",
        help="Round-trip the daemon's state endpoint — wiring check.",
    )
    chat_say = chat_sub.add_parser(
        "say",
        help="Post a message into a conversation — used by running boardroom "
             "sessions to answer mid-turn (routing comes from env).",
    )
    chat_say.add_argument("text", help="Message text to post.")
    chat_say.add_argument(
        "--conversation", default=None,
        help="Conversation id (defaults to $CADRE_RAIL_THREAD_TS from the spawn env).",
    )

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
            firm_id = args.firm_id or None
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
        )

    if args.command == "backup":
        from firm.cli.backup import run_backup

        workspace = args.workspace if args.workspace is not None else Path.cwd()
        return run_backup(workspace, label=args.label)

    if args.command == "notify":
        from firm.cli.notify import run_notify

        workspace = args.workspace if args.workspace is not None else Path.cwd()
        firm_id = args.firm_id or None
        return run_notify(workspace, args.message, firm_id=firm_id)

    if args.command == "heartbeat":
        if args.heartbeat_command == "enable":
            from firm.cli.heartbeat import run_enable

            workspace = args.workspace if args.workspace is not None else Path.cwd()
            firm_id = args.firm_id or None
            return run_enable(workspace, firm_id, args.interval)
        if args.heartbeat_command == "disable":
            from firm.cli.heartbeat import run_disable

            firm_id = args.firm_id or None
            return run_disable(firm_id)
        if args.heartbeat_command == "status":
            from firm.cli.heartbeat import run_status

            return run_status()
        parser.parse_args(["heartbeat", "--help"])
        return 0

    if args.command == "slack":
        from firm.cli import rail_slack

        if args.slack_command == "manifest":
            return rail_slack.run_manifest()
        if args.slack_command == "setup":
            return rail_slack.run_setup(args.firms_root)
        if args.slack_command == "serve":
            return rail_slack.run_serve()
        if args.slack_command == "enable":
            return rail_slack.run_enable()
        if args.slack_command == "disable":
            return rail_slack.run_disable()
        if args.slack_command == "mode":
            return rail_slack.run_mode(args.value)
        if args.slack_command == "model":
            return rail_slack.run_model(args.value)
        if args.slack_command == "updates":
            return rail_slack.run_updates(args.value)
        if args.slack_command == "status":
            return rail_slack.run_status()
        if args.slack_command == "test":
            return rail_slack.run_test()
        if args.slack_command == "say":
            return rail_slack.run_say(args.text, args.thread)
        parser.parse_args(["slack", "--help"])
        return 0

    if args.command == "chat":
        from firm.cli import rail_chat

        if args.chat_command == "setup":
            return rail_chat.run_setup(args.firms_root)
        if args.chat_command == "serve":
            return rail_chat.run_serve()
        if args.chat_command == "open":
            return rail_chat.run_open()
        if args.chat_command == "enable":
            return rail_chat.run_enable()
        if args.chat_command == "disable":
            return rail_chat.run_disable()
        if args.chat_command == "mode":
            return rail_chat.run_mode(args.value)
        if args.chat_command == "model":
            return rail_chat.run_model(args.value)
        if args.chat_command == "host":
            return rail_chat.run_host(args.value)
        if args.chat_command == "updates":
            return rail_chat.run_updates(args.value)
        if args.chat_command == "status":
            return rail_chat.run_status()
        if args.chat_command == "test":
            return rail_chat.run_test()
        if args.chat_command == "say":
            return rail_chat.run_say(args.text, args.conversation)
        parser.parse_args(["chat", "--help"])
        return 0

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
