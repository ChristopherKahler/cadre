"""Run-record handler.

Callable entrypoint invoked when a Member Run ends.  Finalizes the
``member_run`` row (mutable), writes an immutable ``usage_event``, optionally
merges run outputs into the parent Unit's ``outputs``, and writes an
immutable ``records`` row for the audit trail.

v1 trigger is manual -- a slash command (Phase 3/4) or CLI verb wraps this.
Auto-hooking is deferred to Phase 6 (MCP).

Atomicity note: all writes (3 or 4 depending on unit_id presence) run inside
a single manual transaction (raw SQL, not ``repo.create``/``repo.update``)
so a mid-write failure rolls back everything.  See 02-03 SUMMARY for the
same pattern and rationale.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from firm.hooks._redact import redact
from firm.hooks.unit_completion import _next_records_id


def _next_usage_event_id(conn: sqlite3.Connection, firm_id: str) -> str:
    """Return the next ``USG-NNN`` id scoped to *firm_id*.

    Sequential per firm; count-based generation mirrors the ``LOG-NNN``
    scheme from unit_completion (same concurrency caveat -- v1
    single-operator).
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM usage_event WHERE firm_id = ?", (firm_id,)
    ).fetchone()
    n = (row[0] or 0) + 1
    return f"USG-{n:03d}"


def on_run_end(
    conn: sqlite3.Connection,
    *,
    firm_id: str,
    run_id: str,
    final_status: str,
    outputs: list[Any] | None = None,
    usage: dict[str, Any] | None = None,
    notes: str | None = None,
    error: dict[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Finalize a Member Run and write the associated audit trail.

    The caller is responsible for having already started the Run (i.e. a
    ``member_run`` row with ``status='running'`` exists).  ``on_run_end``
    finalizes the row, captures usage, rolls outputs up to the Unit (if
    present), and writes the records entry.

    ``on_run_end`` does NOT flip the Member's availability -- that is a
    caller concern (mirrors the ``on_unit_done`` convention where the
    caller owns ``unit.status`` mutation).

    Returns:
        A summary dict.  On success::

            {"ok": True, "run_id": ..., "records_id": "LOG-NNN",
             "wrote": {"member_run": True, "usage_event": True,
                       "unit": True|False, "records": True}}

        On structured failure::

            {"ok": False, "reason": "run-not-found", "run_id": ...}

        Other errors (e.g. DB unavailable) propagate.
    """
    # --- pre-transaction read ------------------------------------------------
    run_row = conn.execute(
        "SELECT * FROM member_run WHERE id = ?", (run_id,)
    ).fetchone()
    if run_row is None:
        return {"ok": False, "reason": "run-not-found", "run_id": run_id}

    run = dict(run_row)
    member_id = run["member_id"]
    unit_id = run.get("unit_id")

    # --- redact before write -------------------------------------------------
    safe_error = redact(error) if error is not None else None
    safe_notes = redact(notes) if notes is not None else None

    # --- prepare ids ---------------------------------------------------------
    records_id = _next_records_id(conn, firm_id)
    usg_id = _next_usage_event_id(conn, firm_id)
    # Always stamp an aware UTC ISO timestamp when the caller omits one. SQLite's
    # datetime('now') is tz-naive; mixing it with the runner's aware timestamps
    # crashed the dashboard duration calc (field failure 2026-07-08 — a naive
    # ended_at written by `cadre run end` 500'd the whole firm-state render).
    if now is None:
        now = datetime.now(tz=timezone.utc).isoformat()
    use_default_ts = False

    # --- usage_event field extraction ----------------------------------------
    u = usage or {}
    plan = u.get("plan", "custom")
    model = u.get("model")
    tokens_in = u.get("tokens_in")
    tokens_out = u.get("tokens_out")
    cache_read = u.get("cache_read_tokens")
    cache_create = u.get("cache_create_tokens")
    dollar_equiv = u.get("dollar_equivalent")
    window_pct = u.get("window_percent_consumed")
    window_id = u.get("window_id")

    # --- unit outputs merge prep ---------------------------------------------
    wrote_unit = False
    merged_outputs_json: str | None = None
    if unit_id is not None:
        unit_row = conn.execute(
            "SELECT outputs FROM unit WHERE id = ?", (unit_id,)
        ).fetchone()
        if unit_row is not None:
            existing_raw = unit_row[0]
            existing_list = json.loads(existing_raw) if existing_raw else []
            merged = existing_list + (outputs or [])
            merged_outputs_json = json.dumps(merged)
            wrote_unit = True

    # --- serialize payloads --------------------------------------------------
    outputs_json = json.dumps(outputs) if outputs is not None else None
    error_json = json.dumps(safe_error) if safe_error is not None else None

    details_json = json.dumps({
        "run_id": run_id,
        "final_status": final_status,
        "outputs_count": len(outputs) if outputs else 0,
        "had_error": error is not None,
    })

    # --- atomic transaction: 3 or 4 writes -----------------------------------
    try:
        # Write 1: UPDATE member_run (mutable)
        if use_default_ts:
            conn.execute(
                """
                UPDATE member_run
                SET status = ?, ended_at = datetime('now'), outputs = ?,
                    error = ?, notes = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (final_status, outputs_json, error_json, safe_notes, run_id),
            )
        else:
            conn.execute(
                """
                UPDATE member_run
                SET status = ?, ended_at = ?, outputs = ?,
                    error = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (final_status, now, outputs_json, error_json, safe_notes,
                 now, run_id),
            )

        # Write 2: INSERT usage_event (immutable)
        if use_default_ts:
            conn.execute(
                """
                INSERT INTO usage_event
                    (id, firm_id, member_id, run_id, unit_id, timestamp,
                     plan, model, tokens_in, tokens_out,
                     cache_read_tokens, cache_create_tokens,
                     dollar_equivalent, window_percent_consumed, window_id)
                VALUES (?, ?, ?, ?, ?, datetime('now'),
                        ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (usg_id, firm_id, member_id, run_id, unit_id,
                 plan, model, tokens_in, tokens_out,
                 cache_read, cache_create,
                 dollar_equiv, window_pct, window_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO usage_event
                    (id, firm_id, member_id, run_id, unit_id, timestamp,
                     plan, model, tokens_in, tokens_out,
                     cache_read_tokens, cache_create_tokens,
                     dollar_equivalent, window_percent_consumed, window_id)
                VALUES (?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (usg_id, firm_id, member_id, run_id, unit_id, now,
                 plan, model, tokens_in, tokens_out,
                 cache_read, cache_create,
                 dollar_equiv, window_pct, window_id),
            )

        # Write 3 (conditional): UPDATE unit.outputs
        if wrote_unit:
            conn.execute(
                """
                UPDATE unit
                SET outputs = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (merged_outputs_json, unit_id),
            )

        # Write 4: INSERT records (immutable)
        if use_default_ts:
            conn.execute(
                """
                INSERT INTO records
                    (id, firm_id, event_type, actor_type, actor_id,
                     target_entity_type, target_entity_id, details, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (records_id, firm_id, "member_run.ended",
                 "member", member_id, "member_run", run_id,
                 details_json, run_id),
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
                (records_id, firm_id, "member_run.ended",
                 "member", member_id, "member_run", run_id,
                 details_json, run_id, now),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "ok": True,
        "run_id": run_id,
        "records_id": records_id,
        "wrote": {
            "member_run": True,
            "usage_event": True,
            "unit": wrote_unit,
            "records": True,
        },
    }
