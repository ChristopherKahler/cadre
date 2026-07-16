"""Unit entity service — create, list, view, update, checkout, release, complete.

Units are atomic work items within Projects. The most complex entity service:
wraps core.units (atomic checkout, release, cycle detection) and
hooks.unit_completion (on_unit_done audit + AC rollup).

ID prefix: UNIT-NNN
Records events: unit.created, unit.status_transition, unit.checked_out, unit.released, unit.completed
"""

from __future__ import annotations

import sqlite3
from typing import Any

from firm.core import repo
from firm.core.units import checkout, create_with_deps, release, set_dependencies
from firm.hooks.unit_completion import on_unit_done
from firm.services._id import next_id
from firm.services._records import log_event
from firm.services._validate import require_exists, validate_fk, validate_status
from firm.services.authority import require_authority

UNIT_STATUSES = [
    "pending", "in_progress", "blocked", "in_review", "done", "cancelled",
]

UNIT_PRIORITIES = ["urgent", "high", "medium", "low"]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_unit(
    conn: sqlite3.Connection,
    firm_id: str,
    data: dict[str, Any],
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a Unit with project linkage, FK validation, cycle check, and Records.

    Args:
        conn: SQLite connection.
        firm_id: Firm scope.
        data: Must include 'name', 'project_id'. Optional: description,
              assignee_member_id, parent_unit_id, status, priority, rank,
              goal_ids, acceptance_criteria, depends_on, due_date, outputs, tags.
        actor: Who is creating it, e.g. {"type": "member", "id": "MEM-004"}.
               Defaults to the Board. A Member queueing its own follow-up work
               must pass itself — Records has to carry who actually queued the
               work, not whoever the default happens to be. Mirrors
               :func:`firm.services.document.update_document`'s actor.

    Returns:
        The created unit row as a dict.

    Raises:
        ValueError: If required fields missing or FK validation fails.
        CycleError: If depends_on would create a cycle.
    """
    for required in ("name", "project_id"):
        if required not in data:
            raise ValueError(f"'{required}' is required for unit creation")

    # Validate project exists (required FK)
    project = require_exists(conn, "project", data["project_id"])

    # Validate optional FKs
    validate_fk(conn, "member", data.get("assignee_member_id"))
    validate_fk(conn, "unit", data.get("parent_unit_id"))

    unit_id = next_id(conn, "unit", firm_id)

    row_data: dict[str, Any] = {
        "id": unit_id,
        "firm_id": firm_id,
        "name": data["name"],
        "project_id": data["project_id"],
    }
    for field in (
        "description", "assignee_member_id", "parent_unit_id", "status",
        "priority", "rank", "goal_ids", "acceptance_criteria", "depends_on",
        "due_date", "outputs", "tags", "model",
    ):
        if field in data:
            row_data[field] = data[field]

    # create_with_deps handles cycle validation if depends_on present
    if data.get("depends_on"):
        created = create_with_deps(conn, row_data)
    else:
        created = repo.create(conn, "unit", row_data)

    log_event(
        conn,
        firm_id=firm_id,
        event_type="unit.created",
        actor=actor or {"type": "board", "id": None},
        target_ref={"type": "unit", "id": unit_id},
    )

    # Append unit_id to project.unit_ids (denormalized)
    current_ids = project.get("unit_ids") or []
    current_ids.append(unit_id)
    repo.update(conn, "project", data["project_id"], {"unit_ids": current_ids})

    return created


def list_units(
    conn: sqlite3.Connection,
    firm_id: str,
    *,
    status: str | None = None,
    project_id: str | None = None,
    claimed_by: str | None = None,
    assignee: str | None = None,
) -> list[dict[str, Any]]:
    """List units with optional filters.

    Returns:
        List of unit dicts sorted by created_at.
    """
    filters: dict[str, Any] = {"firm_id": firm_id}
    if status is not None:
        filters["status"] = status
    if project_id is not None:
        filters["project_id"] = project_id
    if claimed_by is not None:
        filters["claimed_by"] = claimed_by
    if assignee is not None:
        filters["assignee_member_id"] = assignee
    return repo.find(conn, "unit", **filters)


def view_unit(
    conn: sqlite3.Connection,
    unit_id: str,
) -> dict[str, Any]:
    """View a unit by ID. Raises ValueError if not found."""
    return require_exists(conn, "unit", unit_id)


def update_unit(
    conn: sqlite3.Connection,
    unit_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Update a unit with FK validation, status transition logging, and cycle check.

    Raises:
        ValueError: If unit not found, invalid status, or FK fails.
        CycleError: If depends_on update would create a cycle.
    """
    existing = require_exists(conn, "unit", unit_id)

    # Validate status if changing
    if "status" in data:
        validate_status(data["status"], UNIT_STATUSES)

    # Validate FKs if changing
    if "assignee_member_id" in data:
        validate_fk(conn, "member", data["assignee_member_id"])
    if "parent_unit_id" in data:
        validate_fk(conn, "unit", data["parent_unit_id"])

    # Handle depends_on update with cycle validation
    if "depends_on" in data:
        set_dependencies(conn, unit_id, data["depends_on"])
        # Remove from data to avoid double-write (set_dependencies already wrote it)
        data = {k: v for k, v in data.items() if k != "depends_on"}

    # Detect status transition before update
    old_status = existing.get("status")
    new_status = data.get("status")

    if data:  # May be empty after depends_on removal
        updated = repo.update(conn, "unit", unit_id, data)
    else:
        updated = repo.get(conn, "unit", unit_id)
    assert updated is not None, "unit disappeared after require_exists"

    # Log status transition if changed
    if new_status is not None and new_status != old_status:
        log_event(
            conn,
            firm_id=existing["firm_id"],
            event_type="unit.status_transition",
            actor={"type": "board", "id": None},
            target_ref={"type": "unit", "id": unit_id},
            details={"from": old_status, "to": new_status},
        )

    return updated


# ---------------------------------------------------------------------------
# Lifecycle operations
# ---------------------------------------------------------------------------


def checkout_unit(
    conn: sqlite3.Connection,
    unit_id: str,
    member_id: str,
) -> dict[str, Any] | None:
    """Atomically claim a Unit for a Member.

    Returns the checked-out row, or None if already claimed or not found.
    """
    existing = repo.get(conn, "unit", unit_id)
    if existing is None:
        return None
    result = checkout(conn, unit_id, member_id)
    if result is None:
        return None

    log_event(
        conn,
        firm_id=existing["firm_id"],
        event_type="unit.checked_out",
        actor={"type": "member", "id": member_id},
        target_ref={"type": "unit", "id": unit_id},
        details={"member_id": member_id},
    )

    return result


def release_unit(
    conn: sqlite3.Connection,
    unit_id: str,
) -> dict[str, Any] | None:
    """Release a Unit's claim. Returns the released row, or None if not found."""
    existing = repo.get(conn, "unit", unit_id)
    if existing is None:
        return None

    result = release(conn, unit_id)
    if result is None:
        return None

    log_event(
        conn,
        firm_id=existing["firm_id"],
        event_type="unit.released",
        actor={"type": "board", "id": None},
        target_ref={"type": "unit", "id": unit_id},
    )

    return result


def complete_unit(
    conn: sqlite3.Connection,
    firm_id: str,
    unit_id: str,
    member_id: str,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Transition a Unit to done, invoke completion handler.

    Wraps: repo.update(status=done) + on_unit_done(audit + AC rollup).

    Returns:
        Result dict from on_unit_done with ok, records_id, resolved_ac_ids.

    Raises:
        ValueError: If unit not found.
        AuthorityError: If an identified Member caller lacks the authority key.
            The pulse runner completes validated Units under
            authority.system_context() — the harness is not a Member.
    """
    require_authority(conn, "unit.complete")

    existing = require_exists(conn, "unit", unit_id)
    prior_status = existing["status"]

    # Transition status to done
    repo.update(conn, "unit", unit_id, {"status": "done"})

    # Invoke completion handler (audit record + AC rollup)
    result = on_unit_done(
        conn,
        firm_id=firm_id,
        unit_id=unit_id,
        member_id=member_id,
        prior_status=prior_status,
        run_id=run_id,
    )

    # Service-level Records entry
    log_event(
        conn,
        firm_id=firm_id,
        event_type="unit.completed",
        actor={"type": "member", "id": member_id},
        target_ref={"type": "unit", "id": unit_id},
        details={"prior_status": prior_status, "member_id": member_id},
    )

    return result
