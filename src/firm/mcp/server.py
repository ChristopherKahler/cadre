"""Firm MCP server entry point — FastMCP with stdio transport.

Usage:
    python -m firm.mcp.server

Registers all entity tools from firm.mcp.tools.
DB path resolved from FIRM_CWD env var or cwd.

Migrations are applied once at startup so a firm initialized on an older
schema picks up new tables/columns the first time a session connects —
tool calls must never hit a missing table on a live firm.
"""

import os
from pathlib import Path

from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations
from firm.mcp.tools import mcp


def _migrate_startup() -> None:
    cwd = os.environ.get("FIRM_CWD", os.getcwd())
    db_path = get_db_path(Path(cwd))
    if not db_path.exists():
        return  # firm not initialized here; tools will surface honest errors
    conn = connect(db_path)
    try:
        apply_migrations(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    _migrate_startup()
    mcp.run()
