"""Pure utilities for hook renderers — no I/O, stdlib only.

These helpers are intentionally separate from ``session_pulse`` so that
``unit_completion`` and ``run_record`` (future plans) can reuse them without
reaching through the SessionStart surface.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Literal

#: Tables allowed as polymorphic targets, per the CHECK constraint on
#: ``gate.target_entity_type`` (002_entities.sql line 303). Other hooks with
#: different CHECK constraints (comment, records) use different allowlists —
#: this dispatcher serves the gate/goal/comment use case. Callers supply the
#: entity_type from an already-validated DB column, so we treat the allowlist
#: as defensive rather than authoritative.
_POLYMORPHIC_TARGET_TYPES: frozenset[str] = frozenset({
    "firm", "member", "operation", "project", "unit",
    "goal", "document", "firm_secret", "contract",
})

#: The ``goal`` table has no ``name`` column; it stores the goal target in
#: ``target``. This map routes entity_type → the column to read as a human-
#: facing label. Default (absent from map) is ``name``.
_NAME_COLUMN: dict[str, str] = {
    "goal": "target",
}


def resolve_entity_name(
    conn: sqlite3.Connection, entity_type: str, entity_id: str
) -> str | None:
    """Return the human-readable name for a polymorphic entity reference.

    ``gate.target_entity_type`` and ``goal.parent_entity_type`` columns can
    each point at different tables. Callers render a human-readable label
    (e.g. ``"Blog post #14 draft"`` for a Unit); this helper centralises the
    table dispatch and column selection.

    Returns ``None`` if ``entity_type`` is unknown or the row does not exist.
    Callers typically render ``"(target missing)"`` on ``None``.
    """
    if entity_type not in _POLYMORPHIC_TARGET_TYPES:
        return None
    name_col = _NAME_COLUMN.get(entity_type, "name")
    row = conn.execute(
        f"SELECT {name_col} AS label FROM {entity_type} WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if row is None:
        return None
    label = row["label"] if isinstance(row, sqlite3.Row) else row[0]
    return label if label else None


# SQLite's ``datetime('now')`` produces second-resolution naive UTC strings of
# the form ``YYYY-MM-DD HH:MM:SS``. Python 3.11+'s ``datetime.fromisoformat``
# accepts this format natively.
_UTC = timezone.utc


def _parse_sqlite_ts(ts: str) -> datetime:
    """Parse a SQLite ``datetime('now')`` string as naive UTC."""
    # fromisoformat tolerates both " " and "T" separators in 3.11+.
    return datetime.fromisoformat(ts)


def _utcnow_naive() -> datetime:
    """Naive UTC ``now`` — matches SQLite's stored timestamp shape."""
    return datetime.now(_UTC).replace(tzinfo=None)


def time_ago(ts_iso: str, now: datetime | None = None) -> str:
    """Render a SQLite timestamp as a short relative label.

    >>> time_ago("2026-04-15 20:00:00", now=datetime(2026, 4, 15, 20, 0, 3))
    'just now'
    >>> time_ago("2026-04-15 17:00:00", now=datetime(2026, 4, 15, 20, 30, 0))
    '3h ago'

    Second-resolution; future timestamps render as ``"in Xh"`` / ``"in Xd"``
    so BRIEF §2.3's "DUE IN Nd" line formats cleanly against the same helper.
    """
    ref = now if now is not None else _utcnow_naive()
    ts = _parse_sqlite_ts(ts_iso)
    delta = ref - ts
    total_s = int(delta.total_seconds())
    in_future = total_s < 0
    total_s = abs(total_s)
    if total_s < 60:
        return "just now"
    minutes = total_s // 60
    if minutes < 60:
        return f"in {minutes}m" if in_future else f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"in {hours}h" if in_future else f"{hours}h ago"
    days = hours // 24
    return f"in {days}d" if in_future else f"{days}d ago"


ExpiryClass = Literal["EXPIRED", "URGENT", "STANDARD"]


def classify_expiry(expires_at: str | None, now: datetime | None = None) -> ExpiryClass:
    """Bucket a Gate expiry into render sections per BRIEF §2.2.

    - ``expires_at < now``       → ``"EXPIRED"``
    - ``expires_at < now + 24h`` → ``"URGENT"``
    - else                       → ``"STANDARD"``
    - ``None`` expires_at        → ``"STANDARD"`` (no deadline = no urgency)
    """
    if expires_at is None:
        return "STANDARD"
    ref = now if now is not None else _utcnow_naive()
    ts = _parse_sqlite_ts(expires_at)
    if ts < ref:
        return "EXPIRED"
    if ts < ref + timedelta(hours=24):
        return "URGENT"
    return "STANDARD"
