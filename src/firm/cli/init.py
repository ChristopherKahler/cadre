"""`firm init` — initialize a workspace with a .firm/ directory and SQLite DB."""

from __future__ import annotations

import sys
from pathlib import Path

from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations


def run_init(workspace: Path, force: bool = False) -> int:
    """Initialize ``.firm/firm.db`` at *workspace*.

    Behavior:
    - If *workspace* does not exist: error to stderr, return 1.
    - If the DB already exists and ``force`` is False: print
      "Already initialized" to stdout, do not modify the DB, return 0.
    - Otherwise: create ``.firm/``, open a connection, apply all pending
      migrations, print a summary, return 0.

    ``force`` skips the already-initialized short-circuit but does not
    delete anything — apply_migrations is itself idempotent, so force is
    effectively a no-op on a fully migrated DB and a migration pass on one
    that is not fully caught up.
    """
    workspace = workspace.expanduser().resolve()

    if not workspace.is_dir():
        print(
            f"Error: workspace does not exist or is not a directory: {workspace}",
            file=sys.stderr,
        )
        return 1

    db_path = get_db_path(workspace)

    if db_path.exists() and not force:
        print(f"Already initialized: {db_path}")
        return 0

    conn = connect(db_path)
    try:
        applied = apply_migrations(conn)
    finally:
        conn.close()

    print(f"Initialized .firm/firm.db at {workspace}")
    if applied:
        print(f"Applied migrations: {', '.join(applied)}")
    else:
        print("No pending migrations.")
    return 0
