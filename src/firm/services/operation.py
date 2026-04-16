"""Operation entity service — create, list, view, update.

Operations are the strategic layer in the Firm framework. They own Projects,
link to Goals, and provide the high-level container for bounded work.

ID prefix: OPS-NNN
Records events: operation.created, operation.status_transition
"""

from __future__ import annotations

import sqlite3
from typing import Any

from firm.core import repo
from firm.services._id import next_id
from firm.services._records import log_event
from firm.services._validate import require_exists, validate_fk, validate_status

OPERATION_STATUSES = ["active", "paused", "retired"]


def create_operation(
    conn: sqlite3.Connection,
    firm_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Create an Operation with FK validation and Records entry.

    Args:
        conn: SQLite connection with migrations applied.
        firm_id: Firm scope.
        data: Must include 'name'. Optional: description, owner_member_id,
              priority, category, goal_ids, acceptance_criteria.

    Returns:
        The created operation row as a dict.

    Raises:
        ValueError: If required fields missing or FK validation fails.
    """
    if "name" not in data:
        raise ValueError("'name' is required for operation creation")

    operation_id = next_id(conn, "operation", firm_id)

    # Validate FK references
    validate_fk(conn, "member", data.get("owner_member_id"))

    # Build row
    row_data: dict[str, Any] = {
        "id": operation_id,
        "firm_id": firm_id,
        "name": data["name"],
    }
    for field in (
        "description",
        "owner_member_id",
        "priority",
        "category",
        "goal_ids",
        "acceptance_criteria",
    ):
        if field in data:
            row_data[field] = data[field]

    created = repo.create(conn, "operation", row_data)

    # Records entry
    log_event(
        conn,
        firm_id=firm_id,
        event_type="operation.created",
        actor={"type": "board", "id": None},
        target_ref={"type": "operation", "id": operation_id},
    )

    return created


def list_operations(
    conn: sqlite3.Connection,
    firm_id: str,
    *,
    status: str | None = None,
    category: str | None = None,
    owner: str | None = None,
) -> list[dict[str, Any]]:
    """List operations with optional status, category, and owner filters.

    Returns:
        List of operation dicts sorted by created_at.
    """
    filters: dict[str, Any] = {"firm_id": firm_id}
    if status is not None:
        filters["status"] = status
    if category is not None:
        filters["category"] = category
    if owner is not None:
        filters["owner_member_id"] = owner
    return repo.find(conn, "operation", **filters)


def view_operation(
    conn: sqlite3.Connection,
    operation_id: str,
) -> dict[str, Any]:
    """View an operation by ID. Raises ValueError if not found."""
    return require_exists(conn, "operation", operation_id)


def update_operation(
    conn: sqlite3.Connection,
    operation_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Update an operation with FK validation and status transition logging.

    Raises:
        ValueError: If operation not found, FK validation fails, or invalid status.
    """
    existing = require_exists(conn, "operation", operation_id)

    # Validate status if changing
    if "status" in data:
        validate_status(data["status"], OPERATION_STATUSES)

    # Validate FK references if changing
    if "owner_member_id" in data:
        validate_fk(conn, "member", data["owner_member_id"])

    # Detect status transition before update
    old_status = existing.get("status")
    new_status = data.get("status")

    updated = repo.update(conn, "operation", operation_id, data)
    assert updated is not None, "operation disappeared after require_exists"

    # Log status transition if changed
    if new_status is not None and new_status != old_status:
        log_event(
            conn,
            firm_id=existing["firm_id"],
            event_type="operation.status_transition",
            actor={"type": "board", "id": None},
            target_ref={"type": "operation", "id": operation_id},
            details={"from": old_status, "to": new_status},
        )

    return updated
