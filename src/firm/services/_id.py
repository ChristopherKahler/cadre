"""Unified ID generation for all firm entities.

Pattern: PREFIX-NNN where NNN = COUNT(*) + 1 for rows with matching firm_id.
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
}

SUB_UNIT_PREFIX = "SUB"


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

    # Count ALL rows in the table (not firm-scoped) because `id` is a global
    # PRIMARY KEY. Firm-scoped counts would collide when multiple firms exist.
    # The firm_id parameter is kept in the signature for forward-compatibility
    # (e.g., if we switch to firm-scoped sequence tables later).
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    n = (row[0] or 0) + 1
    return f"{prefix}-{n:03d}"
