"""Turn-request queue — a submitted turn never silently fizzles on the lock.

Board actions (and anything else that wants "pulse when the table frees up")
insert a ``pulse_request`` row instead of spinning on the pulse lock. A
claimer process (``cadre pulse --drain-queue``) atomically claims the oldest
pending request, waits for the DB pulse lock, runs the pulse, and marks the
request done — across however many machines are pointed at the shared DB.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def request_pulse(conn: Any, firm_id: str, *, requested_by: str | None = None,
                  note: str | None = None) -> dict:
    """Enqueue a pulse request. Caller owns the commit."""
    cur = conn.execute(
        "INSERT INTO pulse_request (firm_id, requested_by, note, status,"
        " requested_at) VALUES (?, ?, ?, 'pending', ?)",
        (firm_id, requested_by, note, _now()),
    )
    return {"id": int(cur.lastrowid or 0), "firm_id": firm_id, "status": "pending"}


def claim_next(conn: Any, firm_id: str, holder: str) -> dict | None:
    """Atomically claim the oldest pending request; None when the queue is
    empty. Safe under concurrent claimers (the WHERE status='pending' guard
    makes the UPDATE first-writer-wins)."""
    cur = conn.execute(
        "UPDATE pulse_request SET status='claimed', claimed_by=?, claimed_at=?"
        " WHERE id = (SELECT id FROM pulse_request WHERE firm_id=? AND"
        " status='pending' ORDER BY id LIMIT 1) AND status='pending'"
        " RETURNING id, requested_by, note, requested_at",
        (holder, _now(), firm_id),
    )
    row = cur.fetchone()
    conn.commit()
    if not row:
        return None
    return {"id": row[0], "requested_by": row[1], "note": row[2],
            "requested_at": row[3]}


def complete(conn: Any, request_id: int) -> None:
    conn.execute(
        "UPDATE pulse_request SET status='done', completed_at=? WHERE id=?",
        (_now(), request_id),
    )
    conn.commit()


def abandon(conn: Any, request_id: int, note: str | None = None) -> None:
    """Mark a claimed request abandoned (claimer gave up — e.g. lock wait
    timed out). It stays visible in the queue history."""
    conn.execute(
        "UPDATE pulse_request SET status='abandoned', completed_at=?,"
        " note=COALESCE(?, note) WHERE id=?",
        (_now(), note, request_id),
    )
    conn.commit()


def pending(conn: Any, firm_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, status, requested_by, requested_at FROM pulse_request"
        " WHERE firm_id=? AND status IN ('pending', 'claimed') ORDER BY id",
        (firm_id,),
    ).fetchall()
    return [{"id": r[0], "status": r[1], "requested_by": r[2],
             "requested_at": r[3]} for r in rows]
