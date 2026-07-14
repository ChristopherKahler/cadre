"""``firm notify`` — send a Board notification from the command line.

Thin CLI over ``firm.notify.send_board_dm``. Exists so operator sessions
(e.g. the Board Proxy) can DM the Board WITHOUT a Slack MCP server being
registered in the firm workspace — keeping messaging tools (and their
tokens) out of Member session surfaces entirely. Token comes from the env
var named in firm.notify_config (default CADRE_SLACK_TOKEN).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from firm.core.db import connect, get_db_path, resolve_firm_id
from firm.notify import send_board_dm


def run_notify(
    workspace: Path,
    message: str,
    *,
    firm_id: str | None = None,
) -> int:
    """Send *message* to the Board. Returns 0 if delivered, 1 otherwise."""
    workspace = workspace.expanduser().resolve()
    db_path = get_db_path(workspace)
    if not db_path.exists():
        print(json.dumps({
            "ok": False, "reason": "db-not-found", "workspace": str(workspace),
        }), file=sys.stderr)
        return 1

    conn = connect(db_path)
    try:
        firm_id = resolve_firm_id(conn, firm_id)
        result = send_board_dm(conn, firm_id, message)
    except ValueError as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}), file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(json.dumps({"ok": result["sent"], "detail": result["reason"]}))
    return 0 if result["sent"] else 1
