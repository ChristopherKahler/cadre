"""``firm escalation raise`` — raise an escalation to the Board from the CLI.

Thin wrapper over :func:`firm.services.escalation.raise_escalation` — the same
service the (soon-retired) firm MCP tool ``firm_escalate`` called. First verb of
the MCP->CLI write-surface migration (docs/MCP-TO-CLI-MIGRATION.md): members get
a loud-on-failure, server-less write path that resolves ``firm_id`` from the
process env instead of defaulting to ``chrisai``.

Connects via :func:`firm.core.db.db_connection`, so it honours ``CADRE_DB_URL``
— a Turso-backed firm (table-online) is reached the same as a local one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from firm.core.db import db_connection
from firm.services.escalation import raise_escalation


def run_escalation_raise(
    workspace: Path,
    *,
    raised_by_member_id: str,
    title: str,
    body: str = "",
    severity: str = "normal",
    target_entity_type: str = "",
    target_entity_id: str = "",
    firm_id: str,
) -> int:
    """Raise an escalation in *workspace*'s firm DB. JSON to stdout, errors to
    stderr. Returns 0 on success, 1 on a structured failure."""
    data: dict[str, Any] = {
        "raised_by_member_id": raised_by_member_id,
        "title": title,
    }
    if body:
        data["body"] = body
    if severity:
        data["severity"] = severity
    if target_entity_type:
        data["target_entity_type"] = target_entity_type
    if target_entity_id:
        data["target_entity_id"] = target_entity_id

    try:
        with db_connection(workspace) as conn:
            result = raise_escalation(conn, firm_id, data)
    except Exception as exc:  # service raises ValueError/TypeError on bad input
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, **dict(result)}, default=str))
    return 0
