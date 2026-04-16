"""Records auto-entry for the firm service layer.

Writes immutable audit trail entries to the ``records`` table on
significant operations (entity create, status transitions, gate decisions,
etc.). Called internally by entity service modules.

Uses raw SQL INSERT because ``records`` is immutable (DB triggers reject
UPDATE/DELETE) and ``repo.create`` commits internally — which is fine here
since each log_event is a standalone write, not part of a multi-table
transaction.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from firm.core import repo
from firm.services._id import next_id


def log_event(
    conn: sqlite3.Connection,
    *,
    firm_id: str,
    event_type: str,
    actor: dict[str, Any],
    target_ref: dict[str, Any],
    details: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Write an immutable Records entry.

    Args:
        conn: SQLite connection with migrations applied.
        firm_id: Firm scope.
        event_type: Dotted string like "member.created", "unit.status_transition".
        actor: {"type": "board"|"member"|"system", "id": str|None}.
        target_ref: {"type": str, "id": str} — the entity the event applies to.
        details: Event-specific payload (JSON-serializable dict). Optional.
        run_id: Link to a member_run if the event happened during a Run. Optional.

    Returns:
        The created records row as a dict.
    """
    record_id = next_id(conn, "records", firm_id)

    data: dict[str, Any] = {
        "id": record_id,
        "firm_id": firm_id,
        "event_type": event_type,
        "actor_type": actor["type"],
        "actor_id": actor.get("id"),
        "target_entity_type": target_ref["type"],
        "target_entity_id": target_ref["id"],
        "details": json.dumps(details) if details is not None else None,
        "run_id": run_id,
    }

    return repo.create(conn, "records", data)
