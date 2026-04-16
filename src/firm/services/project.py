"""Project entity service — create, list, view, update.

Projects are bounded deliverables within Operations. They own Units (atomic
work items). Creating a project appends its ID to the parent operation's
project_ids array for denormalized access.

ID prefix: PROJ-NNN
Records events: project.created, project.status_transition
"""

from __future__ import annotations

import sqlite3
from typing import Any

from firm.core import repo
from firm.services._id import next_id
from firm.services._records import log_event
from firm.services._validate import require_exists, validate_fk, validate_status

PROJECT_STATUSES = [
    "in_progress", "blocked", "paused", "in_review", "done", "cancelled",
]


def create_project(
    conn: sqlite3.Connection,
    firm_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Create a Project with operation linkage, FK validation, and Records entry.

    Args:
        conn: SQLite connection.
        firm_id: Firm scope.
        data: Must include 'name', 'operation_id', 'due_date'. Optional:
              description, owner_member_id, priority, tags, goal_ids,
              acceptance_criteria.

    Returns:
        The created project row as a dict.

    Raises:
        ValueError: If required fields missing, operation doesn't exist,
                    or FK validation fails.
    """
    for required in ("name", "operation_id", "due_date"):
        if required not in data:
            raise ValueError(
                f"'{required}' is required for project creation"
            )

    # Validate operation exists (required FK)
    operation = require_exists(conn, "operation", data["operation_id"])

    # Validate optional FK references
    validate_fk(conn, "member", data.get("owner_member_id"))

    project_id = next_id(conn, "project", firm_id)

    # Build row — default status to "in_progress"
    row_data: dict[str, Any] = {
        "id": project_id,
        "firm_id": firm_id,
        "name": data["name"],
        "operation_id": data["operation_id"],
        "due_date": data["due_date"],
        "status": data.get("status", "in_progress"),
    }
    for field in (
        "description",
        "owner_member_id",
        "priority",
        "tags",
        "goal_ids",
        "acceptance_criteria",
    ):
        if field in data:
            row_data[field] = data[field]

    created = repo.create(conn, "project", row_data)

    # Records entry
    log_event(
        conn,
        firm_id=firm_id,
        event_type="project.created",
        actor={"type": "board", "id": None},
        target_ref={"type": "project", "id": project_id},
    )

    # Append project ID to operation.project_ids
    current_ids = operation.get("project_ids") or []
    current_ids.append(project_id)
    repo.update(conn, "operation", data["operation_id"], {
        "project_ids": current_ids,
    })

    return created


def list_projects(
    conn: sqlite3.Connection,
    firm_id: str,
    *,
    status: str | None = None,
    operation_id: str | None = None,
    owner: str | None = None,
) -> list[dict[str, Any]]:
    """List projects with optional status, operation_id, and owner filters.

    Returns:
        List of project dicts sorted by created_at.
    """
    filters: dict[str, Any] = {"firm_id": firm_id}
    if status is not None:
        filters["status"] = status
    if operation_id is not None:
        filters["operation_id"] = operation_id
    if owner is not None:
        filters["owner_member_id"] = owner
    return repo.find(conn, "project", **filters)


def view_project(
    conn: sqlite3.Connection,
    project_id: str,
) -> dict[str, Any]:
    """View a project by ID. Raises ValueError if not found."""
    return require_exists(conn, "project", project_id)


def update_project(
    conn: sqlite3.Connection,
    project_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Update a project with FK validation and status transition logging.

    Raises:
        ValueError: If project not found, FK validation fails, or invalid status.
    """
    existing = require_exists(conn, "project", project_id)

    # Validate status if changing
    if "status" in data:
        validate_status(data["status"], PROJECT_STATUSES)

    # Validate FK references if changing
    if "owner_member_id" in data:
        validate_fk(conn, "member", data["owner_member_id"])
    if "operation_id" in data:
        require_exists(conn, "operation", data["operation_id"])

    # Detect status transition before update
    old_status = existing.get("status")
    new_status = data.get("status")

    updated = repo.update(conn, "project", project_id, data)
    assert updated is not None, "project disappeared after require_exists"

    # Log status transition if changed
    if new_status is not None and new_status != old_status:
        log_event(
            conn,
            firm_id=existing["firm_id"],
            event_type="project.status_transition",
            actor={"type": "board", "id": None},
            target_ref={"type": "project", "id": project_id},
            details={"from": old_status, "to": new_status},
        )

    return updated
