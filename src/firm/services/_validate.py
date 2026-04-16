"""Shared validation helpers for the firm service layer.

All validators raise ValueError with entity-aware messages on failure.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from firm.core.repo import ALL_TABLES, get

# Polymorphic ref tables — valid parent_entity_type / target_entity_type values.
# Different entities allow different parent types; these are the superset.
POLYMORPHIC_TABLES: frozenset[str] = frozenset({
    "firm", "member", "operation", "project", "unit",
    "goal", "gate", "document", "firm_secret", "contract",
    "comment",
})


def require_exists(
    conn: sqlite3.Connection, table: str, id: str
) -> dict[str, Any]:
    """Fetch a row by ID or raise ValueError.

    Returns:
        The entity row as a dict.

    Raises:
        ValueError: If the row does not exist.
    """
    row = get(conn, table, id)
    if row is None:
        raise ValueError(f"{table} {id!r} not found")
    return row


def validate_status(status: str, allowed: list[str]) -> None:
    """Raise ValueError if status is not in the allowed set.

    Args:
        status: The status value to validate.
        allowed: List of valid status strings.

    Raises:
        ValueError: With message listing allowed values.
    """
    if status not in allowed:
        raise ValueError(
            f"Invalid status {status!r} — must be one of: {', '.join(allowed)}"
        )


def validate_parent_ref(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
) -> dict[str, Any]:
    """Validate a polymorphic reference (parent_entity_type + parent_entity_id).

    Args:
        conn: SQLite connection.
        entity_type: The type field (must be a known entity table).
        entity_id: The ID field (must exist in the named table).

    Returns:
        The target entity row as a dict.

    Raises:
        ValueError: If the type is invalid or the target doesn't exist.
    """
    if entity_type not in POLYMORPHIC_TABLES:
        raise ValueError(
            f"Invalid entity type {entity_type!r} — must be one of: "
            f"{', '.join(sorted(POLYMORPHIC_TABLES))}"
        )
    if entity_type not in ALL_TABLES:
        raise ValueError(f"Entity type {entity_type!r} has no corresponding table")
    return require_exists(conn, entity_type, entity_id)


def validate_fk(
    conn: sqlite3.Connection, table: str, id: str | None
) -> dict[str, Any] | None:
    """Validate a foreign key reference if the ID is not None.

    Args:
        conn: SQLite connection.
        table: The target table.
        id: The FK value. If None, returns None (FK is optional).

    Returns:
        The referenced row, or None if id was None.

    Raises:
        ValueError: If id is not None and the target doesn't exist.
    """
    if id is None:
        return None
    return require_exists(conn, table, id)
