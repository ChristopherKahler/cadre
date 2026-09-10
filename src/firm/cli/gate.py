"""``firm gate request`` — ask the Board to approve an action, from the CLI.

Thin wrapper over :func:`firm.services.gate.request_gate` — the same service the
firm MCP tool ``firm_request_gate`` calls. Before this verb existed, a Gate could
only be requested through the MCP server, and six of twelve firms load no firm
MCP server at all: in half the estate a Member had no way to ask for approval,
while its execution directive told it that approval requests "go through
firm_request_gate".

Conventions mirror :mod:`firm.cli.escalation` exactly — firm id from
``--firm-id`` or ``$FIRM_ID``, member from ``--member`` or ``$CADRE_MEMBER_ID``,
JSON to stdout, human errors to stderr, honest exit codes.

Connects via :func:`firm.core.db.db_connection`, so it honours ``CADRE_DB_URL``
— a Turso-backed firm is reached the same as a local one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from firm.core.db import db_connection, get_db_path, resolve_firm_id
from firm.services.authority import caller_member_id
from firm.services.gate import request_gate


def run_gate_request(
    workspace: Path,
    *,
    action: str,
    target_entity_type: str,
    target_entity_id: str,
    member_id: str | None = None,
    context: str = "",
    expires_at: str = "",
    firm_id: str | None = None,
) -> int:
    """Request a Gate in *workspace*'s firm DB. JSON to stdout, errors to
    stderr. Returns 0 on success, 1 on a structured failure.

    Unlike ``firm unit create``, a missing member identity is a hard error here
    rather than a ``None`` passed onward. ``request_gate`` requires
    ``requesting_member_id``; handing it an empty string would fail deeper down
    in ``require_exists`` with a message about a member id that was never given,
    which reads like a corrupt database rather than a missing flag.
    """
    requester = member_id or caller_member_id()
    if not requester:
        print(json.dumps({"ok": False, "error": (
            "no member identity — pass --member MEM-xxx, or run inside a Member "
            "session where $CADRE_MEMBER_ID is set. A Gate records WHO is asking; "
            "it cannot be requested anonymously"
        )}), file=sys.stderr)
        return 1

    # Refuse BEFORE opening the connection. ``db_connection`` goes through
    # ``sqlite3.connect``, which CREATES the file, so a request refused for any
    # later reason still leaves a 0-byte ``.firm/firm.db`` behind — and
    # ``founding.commit`` treats an existing db as "a firm already lives here"
    # and refuses to found one. A mistyped ``--workspace`` would quietly poison
    # that directory for founding. Measured on ``firm escalation raise``, which
    # has this shape today; this verb does not ship it.
    db_path = get_db_path(workspace)
    if not db_path.exists():
        print(json.dumps({"ok": False, "error": (
            f".firm/firm.db not found at {db_path} — run 'firm init {workspace}' "
            f"first, or pass --workspace pointing at the firm"
        )}), file=sys.stderr)
        return 1

    data: dict[str, Any] = {
        "requesting_member_id": requester,
        "action": action,
        "target_entity_type": target_entity_type,
        "target_entity_id": target_entity_id,
    }
    if context:
        data["context"] = context
    if expires_at:
        data["expires_at"] = expires_at

    try:
        with db_connection(workspace) as conn:
            result = request_gate(conn, resolve_firm_id(conn, firm_id), data)
    except Exception as exc:  # service raises ValueError/TypeError on bad input
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, **dict(result)}, default=str))
    return 0
