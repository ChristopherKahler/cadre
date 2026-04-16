"""Unit completion handler.

Callable entrypoint invoked when a Unit transitions to ``done``. Writes an
immutable ``records`` row capturing the status transition and flips
``resolved: true`` on any ``project.acceptance_criteria`` entries whose
``resolved_by`` matches the completed Unit's id.

v1 trigger is manual — a slash command (Phase 3) or CLI verb wraps this.
Auto-hooking via PostToolUse is deferred to Phase 6 (MCP).

Atomicity note: the records INSERT and project UPDATE run inside a single
manual transaction (raw SQL, not ``repo.create``/``repo.update``) so a mid-
write failure rolls back both. ``repo.create`` commits internally, which
would defeat the ``with conn:`` transaction boundary the AC-4 rollback
invariant requires.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from firm.core import repo


def _next_records_id(conn: sqlite3.Connection, firm_id: str) -> str:
    """Return the next ``LOG-NNN`` id scoped to *firm_id*.

    Sequential per firm; count-based generation is safe because ``records`` is
    immutable (no deletes shrink the count).
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM records WHERE firm_id = ?", (firm_id,)
    ).fetchone()
    n = (row[0] or 0) + 1
    return f"LOG-{n:03d}"


def on_unit_done(
    conn: sqlite3.Connection,
    *,
    firm_id: str,
    unit_id: str,
    member_id: str,
    prior_status: str,
    run_id: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Handle a Unit's transition to ``done``.

    The caller is responsible for having already mutated ``unit.status`` to
    ``done`` (typically via ``repo.update``) BEFORE invoking this function.
    ``on_unit_done`` is concerned only with the audit record + AC rollup, not
    the status flip itself.

    Args:
        conn: Open SQLite connection.
        firm_id: Firm scope for the records row and the id sequence.
        unit_id: The Unit that just completed.
        member_id: Member who completed it (actor on the records row).
        prior_status: Status value before the transition (recorded in details).
        run_id: Optional member_run id linking the transition to a Run.
        now: Optional ISO-like timestamp override for deterministic tests. If
            None, SQLite's ``datetime('now')`` default fires.

    Returns:
        A summary dict. On success::

            {"ok": True, "records_id": "LOG-001", "resolved_ac_ids": ["AC-1"],
             "unit_id": ..., "project_id": ...}

        On structured failure (unit or project missing)::

            {"ok": False, "reason": "unit-not-found"|"project-missing", ...}

        Other errors (e.g. DB unavailable, FK violation) propagate.
    """
    unit = repo.get(conn, "unit", unit_id)
    if unit is None:
        return {"ok": False, "reason": "unit-not-found", "unit_id": unit_id}

    project_id = unit["project_id"]
    project = repo.get(conn, "project", project_id)
    if project is None:
        return {
            "ok": False,
            "reason": "project-missing",
            "unit_id": unit_id,
            "project_id": project_id,
        }

    resolved_ac_ids, mutated_ac_list = _compute_ac_flips(
        project.get("acceptance_criteria"), unit_id
    )

    records_id = _next_records_id(conn, firm_id)
    details_json = json.dumps({
        "prior_status": prior_status,
        "new_status": "done",
        "project_id": project_id,
        "resolved_ac_ids": resolved_ac_ids,
    })

    try:
        if now is None:
            conn.execute(
                """
                INSERT INTO records
                    (id, firm_id, event_type, actor_type, actor_id,
                     target_entity_type, target_entity_id, details, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    records_id, firm_id, "unit.status_transition",
                    "member", member_id, "unit", unit_id,
                    details_json, run_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO records
                    (id, firm_id, event_type, actor_type, actor_id,
                     target_entity_type, target_entity_id, details, run_id,
                     timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    records_id, firm_id, "unit.status_transition",
                    "member", member_id, "unit", unit_id,
                    details_json, run_id, now,
                ),
            )
        if resolved_ac_ids:
            conn.execute(
                """
                UPDATE project
                SET acceptance_criteria = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (json.dumps(mutated_ac_list), project_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "ok": True,
        "records_id": records_id,
        "resolved_ac_ids": resolved_ac_ids,
        "unit_id": unit_id,
        "project_id": project_id,
    }


def _compute_ac_flips(
    ac_list: Any, unit_id: str,
) -> tuple[list[str], list[Any]]:
    """Walk the acceptance_criteria list, flipping matching unresolved entries.

    Returns (resolved_ac_ids, mutated_list). Non-dict entries are passed
    through unchanged. Entries already ``resolved: true`` are left alone
    (idempotent). Entries without an ``id`` still flip, but aren't listed in
    the returned ids.
    """
    if not ac_list:
        return [], []
    if not isinstance(ac_list, list):
        return [], list(ac_list) if isinstance(ac_list, (tuple,)) else []

    resolved_ids: list[str] = []
    mutated: list[Any] = []
    for entry in ac_list:
        if not isinstance(entry, dict):
            mutated.append(entry)
            continue
        matches = entry.get("resolved_by") == unit_id
        already_done = entry.get("resolved") is True
        if matches and not already_done:
            flipped = {**entry, "resolved": True}
            mutated.append(flipped)
            ac_id = entry.get("id")
            if ac_id:
                resolved_ids.append(str(ac_id))
        else:
            mutated.append(entry)
    return resolved_ids, mutated
