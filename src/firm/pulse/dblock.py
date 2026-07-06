"""DB-row pulse lock — one live pulse per firm across ALL machines.

Replaces the machine-local ``.firm/pulse.lock`` flock: in multiplayer every
player's machine pulses against the same shared database, so the overlap
guard has to live IN that database. A holder row with a heartbeat; a holder
that stops beating past the TTL is presumed dead and its lock is stolen by
the next acquirer (a killed pulse never wedges the table).
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

LOCK_TTL_SEC = 600  # no heartbeat for 10 min => holder presumed dead


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def make_holder_id() -> str:
    """Globally unique lock-holder identity (host:pid:nonce)."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def acquire(conn: Any, firm_id: str, holder: str) -> bool:
    """Try to take the firm's pulse lock. Returns True on success.

    Atomic under concurrency: the PRIMARY KEY upsert serializes claimers;
    the ON CONFLICT update only fires when the current holder's heartbeat
    is stale (steal-on-dead), so a live pulse is never displaced.
    """
    now = _now().isoformat()
    stale_cutoff = (_now() - timedelta(seconds=LOCK_TTL_SEC)).isoformat()
    conn.execute(
        "INSERT INTO pulse_lock (firm_id, holder, acquired_at, heartbeat_at)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(firm_id) DO UPDATE SET holder=excluded.holder,"
        " acquired_at=excluded.acquired_at, heartbeat_at=excluded.heartbeat_at"
        " WHERE pulse_lock.heartbeat_at < ?",
        (firm_id, holder, now, now, stale_cutoff),
    )
    conn.commit()
    row = conn.execute(
        "SELECT holder FROM pulse_lock WHERE firm_id = ?", (firm_id,),
    ).fetchone()
    return bool(row) and row[0] == holder


def heartbeat(conn: Any, firm_id: str, holder: str) -> bool:
    """Refresh the holder's heartbeat. Returns False if the lock was lost."""
    cur = conn.execute(
        "UPDATE pulse_lock SET heartbeat_at = ? WHERE firm_id = ? AND holder = ?",
        (_now().isoformat(), firm_id, holder),
    )
    conn.commit()
    return cur.rowcount > 0


def release(conn: Any, firm_id: str, holder: str) -> None:
    """Drop the lock if we still hold it (a stolen lock is left alone)."""
    conn.execute(
        "DELETE FROM pulse_lock WHERE firm_id = ? AND holder = ?",
        (firm_id, holder),
    )
    conn.commit()


def current_holder(conn: Any, firm_id: str) -> str | None:
    row = conn.execute(
        "SELECT holder FROM pulse_lock WHERE firm_id = ?", (firm_id,),
    ).fetchone()
    return row[0] if row else None
