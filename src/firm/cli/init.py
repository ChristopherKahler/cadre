"""`cadre init` / `firm init` — initialize a workspace with a .firm/ directory, DB, and optional demo seed + hooks."""

from __future__ import annotations

import sys
from pathlib import Path

from firm import __framework_name__
from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations


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
    finally:
        conn.close()

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
