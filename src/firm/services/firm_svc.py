"""Firm entity service — create, view, update, status aggregation.

Firm is the root entity. IDs are user-chosen (e.g., "chrisai"), not
auto-generated. Status aggregation provides a dashboard summary of
entity counts within the firm.

Records events: firm.created, firm.updated
"""

from __future__ import annotations

import sqlite3
from typing import Any

from firm.core import repo
from firm.services._records import log_event
from firm.services._validate import require_exists


def create_firm(
    conn: sqlite3.Connection,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Create a Firm with Records entry.

    Args:
        conn: SQLite connection.
        data: Must include 'id' and 'name'. Optional: description, operator,
              north_star, core_values, vision, partners, schedule.

    Returns:
        The created firm row as a dict.

    Raises:
        ValueError: If 'id' or 'name' missing.
    """
    for required in ("id", "name"):
        if required not in data:
            raise ValueError(f"'{required}' is required for firm creation")

    row_data: dict[str, Any] = {
        "id": data["id"],
        "name": data["name"],
    }
    for field in (
        "description", "operator", "north_star", "core_values",
        "vision", "partners", "schedule",
    ):
        if field in data:
            row_data[field] = data[field]

    created = repo.create(conn, "firm", row_data)

    log_event(
        conn,
        firm_id=created["id"],
        event_type="firm.created",
        actor={"type": "board", "id": None},
        target_ref={"type": "firm", "id": created["id"]},
    )

    return created


def view_firm(
    conn: sqlite3.Connection,
    firm_id: str,
) -> dict[str, Any]:
    """View a firm by ID. Raises ValueError if not found."""
    return require_exists(conn, "firm", firm_id)


def update_firm(
    conn: sqlite3.Connection,
    firm_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Update a firm and log Records entry.

    Raises:
        ValueError: If firm not found.
    """
    existing = require_exists(conn, "firm", firm_id)

    updated = repo.update(conn, "firm", firm_id, data)
    assert updated is not None, "firm disappeared after require_exists"

    log_event(
        conn,
        firm_id=existing["id"],
        event_type="firm.updated",
        actor={"type": "board", "id": None},
        target_ref={"type": "firm", "id": firm_id},
    )

    return updated


_STATUS_TABLES = ["member", "operation", "project", "unit", "goal", "gate"]


def firm_status(
    conn: sqlite3.Connection,
    firm_id: str,
) -> dict[str, Any]:
    """Aggregate entity counts for a firm.

    Returns:
        Dict with firm_id and count fields for each entity type:
        member_count, operation_count, project_count, unit_count,
        goal_count, gate_count.

    Raises:
        ValueError: If firm not found.
    """
    require_exists(conn, "firm", firm_id)

    result: dict[str, Any] = {"firm_id": firm_id}
    for table in _STATUS_TABLES:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()
        result[f"{table}_count"] = row[0] if row else 0

    return result
