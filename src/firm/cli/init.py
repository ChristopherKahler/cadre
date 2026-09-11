"""`cadre init` / `firm init` — initialize a workspace with a .firm/ directory, DB, and optional demo seed + hooks."""

from __future__ import annotations

import sys
from pathlib import Path

from firm import __framework_name__
from firm.core import repo
from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations


def _wire_base(workspace: Path, conn) -> None:
    """Scaffold the workspace's BASE tier and wire the firm's domain into it.

    Prints what happened rather than returning it, and never fails the init:
    base absent is a host fact, not a defect in this firm, and a firm without
    base is degraded rather than broken -- the contract `base_domain.sync` and
    `base_extension.install` both keep.

    The domain step is skipped when the workspace holds no firm yet, which is
    the ordinary state after a bare `cadre init`. Syncing with an empty firm id
    would write a domain block named "" -- a wire to nothing that every later
    check would have to special-case.
    """
    from firm.services import base_domain

    print("  BASE workspace:")
    tier = base_domain.scaffold_tier(workspace)
    if not tier["scaffolded"]:
        print(f"    skipped: {tier['detail']}")
        return
    print("    .base/ scaffolded")

    firms = repo.find(conn, "firm")
    if len(firms) != 1:
        print(f"    {len(firms)} firms in this workspace, so no domain wired yet"
              if firms else
              "    no firm here yet, so there is no domain to wire")
        return

    firm_id = str(firms[0]["id"])
    synced = base_domain.sync(workspace, firm_id, conn=conn)
    if not synced.get("ok"):
        print(f"    domain not written: {synced.get('reason') or 'unknown'}")
    elif not synced.get("rule_seeded"):
        print(f"    domain {firm_id!r} written but carries no rules, so base "
              "drops it whole — run cadre doctor --fix")
    else:
        print(f"    domain {firm_id!r} wired and its first rule seeded")


def run_init(
    workspace: Path,
    force: bool = False,
    demo: bool = False,
    install_hooks_flag: bool = False,
) -> int:
    """Initialize ``.firm/firm.db`` at *workspace*.

    Flags:
      force              — bypass the already-initialized short-circuit
      demo               — seed the generic `demo` firm after migrations
      install_hooks_flag — install session-pulse hook into .claude/hooks/
    """
    workspace = workspace.expanduser().resolve()

    if not workspace.is_dir():
        print(
            f"Error: workspace does not exist or is not a directory: {workspace}",
            file=sys.stderr,
        )
        return 1

    db_path = get_db_path(workspace)
    already_had_db = db_path.exists()

    if already_had_db and not force and not demo and not install_hooks_flag:
        print(f"Already initialized: {db_path}")
        return 0

    conn = connect(db_path)
    try:
        applied = apply_migrations(conn)

        if not already_had_db:
            print(f"Initialized {__framework_name__} at {workspace}")
            print(f"  Database: {db_path}")
        if applied:
            print(f"  Applied migrations: {', '.join(applied)}")

        if demo:
            from firm.seed_demo import seed_demo, summary_line
            seed_demo(conn)
            print(f"  {summary_line(conn)}")

        # The firm's own BASE tier. init used to skip this entirely and only
        # the dashboard founding flow ever made a `.base/`, so a firm created
        # from the command line had none -- `cadre learn` refused to write
        # outside a workspace (F4) and `base_domain.sync` returned early, so
        # the rule that makes the domain live was never seeded (F5). One
        # missing directory, both symptoms.
        #
        # Wired here, with `conn` still open, so `sync` derives the domain's
        # triggers from the real roster. Doing it after the connection closes
        # would fall back to the firm id alone and leave a freshly created
        # firm reporting stale against its own roster on its first doctor run.
        _wire_base(workspace, conn)
    finally:
        conn.close()

    # Every firm is born with the universal execution discipline — quality law
    # that only exists if someone remembers a command is a suggestion, not a
    # floor. Idempotent: existing (possibly customized) files are skipped.
    from firm.cli.templates import run_templates_install
    print("  Discipline templates:")
    run_templates_install("discipline", workspace)

    if install_hooks_flag:
        from firm.cli.install_hooks import install_hooks
        rc, messages = install_hooks(workspace)
        for msg in messages:
            print(f"  {msg}")
        if rc != 0:
            return rc

    hints: list[str] = []
    if not demo:
        hints.append("--demo           seed the generic demo firm")
    if not install_hooks_flag:
        hints.append("--install-hooks  register the session-pulse hook")
    if hints:
        print("\nNext steps:")
        for hint in hints:
            print(f"  cadre init . {hint}")

    return 0
