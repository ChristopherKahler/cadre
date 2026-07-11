"""Unified ID generation for all firm entities.

Pattern: PREFIX-NNN where NNN = highest existing numeric suffix + 1 (MAX-based).
Matches the LOG-NNN and USG-NNN patterns from Phase 2 hooks.

Not concurrency-safe (v1 single-operator). Flag for Phase 6 MCP.
"""

from __future__ import annotations

import sqlite3

PREFIX_REGISTRY: dict[str, str] = {
    "budget_period": "BP",
    "member": "MEM",
    "member_run": "RUN",
    "operation": "OPS",
    "project": "PROJ",
    "unit": "UNIT",
    "gate": "GATE",
    "goal": "GOAL",
    "comment": "COM",
    "contract": "CON",
    "document": "DOC",
    "records": "LOG",
    "usage_event": "USG",
    "firm_secret": "KEY",
    "escalation": "ESC",
}

SUB_UNIT_PREFIX = "SUB"


def max_numeric_suffix(conn: sqlite3.Connection, table: str, prefix: str) -> int:
    """Highest numeric suffix among ``{prefix}-NNN`` ids in *table* (0 if none).

    SQLite ``substr`` is 1-based: the numeric tail starts at len(prefix)+2,
    right past the prefix and its dash. Non-numeric tails CAST to 0.
    """
    row = conn.execute(
        f"SELECT MAX(CAST(substr(id, ?) AS INTEGER)) FROM {table} WHERE id LIKE ?",
        (len(prefix) + 2, f"{prefix}-%"),
    ).fetchone()
    return row[0] or 0


def next_id(
    conn: sqlite3.Connection,
    table: str,
    firm_id: str,
    *,
    is_sub_unit: bool = False,
) -> str:
    """Generate next sequential ID for an entity.

    Args:
        conn: SQLite connection with migrations applied.
        table: Entity table name (must be in PREFIX_REGISTRY).
        firm_id: Firm scope for the sequence.
        is_sub_unit: If True and table is "unit", use SUB prefix instead of UNIT.

    Returns:
        Prefixed ID string like "MEM-001", "UNIT-042", "SUB-003".

    Raises:
        ValueError: If table is not in PREFIX_REGISTRY.
    """
    if table not in PREFIX_REGISTRY:
        raise ValueError(
            f"Unknown table {table!r} — not in PREFIX_REGISTRY. "
            f"Known: {sorted(PREFIX_REGISTRY)}"
        )

    prefix = SUB_UNIT_PREFIX if (table == "unit" and is_sub_unit) else PREFIX_REGISTRY[table]

    # MAX-based, not COUNT-based: `id` is a global PRIMARY KEY, and COUNT+1
    # collides whenever the row count lags the highest suffix — a deleted row,
    # or a firm-scoped counter against a shared id space (field failure
    # 2026-07-11: run_record's firm-scoped COUNT minted a duplicate USG id and
    # crashed `firm run end` mid-hook). The highest existing suffix is the
    # only safe floor. The firm_id parameter is kept in the signature for
    # forward-compatibility (e.g., firm-scoped sequence tables later).
    if table == "unit":
        # UNIT and SUB share one sequence (same table's id space).
        n = max(
            max_numeric_suffix(conn, table, PREFIX_REGISTRY["unit"]),
            max_numeric_suffix(conn, table, SUB_UNIT_PREFIX),
        ) + 1
    else:
        n = max_numeric_suffix(conn, table, prefix) + 1
    return f"{prefix}-{n:03d}"
