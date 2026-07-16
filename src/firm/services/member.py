"""Member entity service — create, list, view, update.

Members are the core workforce entity in the Firm framework. PULSE activates
them, slash commands manage them. Every other Wave 2 service depends on
Members existing.

ID prefix: MEM-NNN
Records events: member.created, member.status_transition
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from firm.core import repo
from firm.services._id import next_id
from firm.services._records import log_event
from firm.services._validate import require_exists, validate_fk, validate_status
from firm.services.authority import require_authority

MEMBER_STATUSES = ["active", "paused", "retired"]


def create_member(
    conn: sqlite3.Connection,
    firm_id: str,
    data: dict[str, Any],
    *,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Create a Member with FK validation, Records entry, and instructions auto-gen.

    Args:
        conn: SQLite connection with migrations applied.
        firm_id: Firm scope.
        data: Must include 'name' and 'role'. Optional: description,
              reports_to_member_id, contract_id, suggested_skills,
              suggested_domains, budget.
        cwd: Workspace root for instructions file. Defaults to os.getcwd().

    Returns:
        The created member row as a dict.

    Raises:
        ValueError: If required fields missing or FK validation fails.
        AuthorityError: If an identified Member caller lacks the authority key.
    """
    require_authority(conn, "member.create")

    if "name" not in data or "role" not in data:
        raise ValueError("'name' and 'role' are required for member creation")

    member_id = next_id(conn, "member", firm_id)

    # Validate FK references
    validate_fk(conn, "member", data.get("reports_to_member_id"))
    validate_fk(conn, "contract", data.get("contract_id"))

    # Build row
    row_data: dict[str, Any] = {
        "id": member_id,
        "firm_id": firm_id,
        "name": data["name"],
        "role": data["role"],
    }
    for field in (
        "description",
        "reports_to_member_id",
        "contract_id",
        "suggested_skills",
        "suggested_domains",
        "budget",
    ):
        if field in data:
            row_data[field] = data[field]

    created = repo.create(conn, "member", row_data)

    # Records entry
    log_event(
        conn,
        firm_id=firm_id,
        event_type="member.created",
        actor={"type": "board", "id": None},
        target_ref={"type": "member", "id": member_id},
    )

    # Auto-generate instructions file (best-effort)
    workspace = cwd or os.getcwd()
    _auto_gen_instructions(
        member_id, data["role"], data.get("description"), workspace
    )

    return created


def list_members(
    conn: sqlite3.Connection,
    firm_id: str,
    *,
    status: str | None = None,
    reports_to: str | None = None,
) -> list[dict[str, Any]]:
    """List members with optional status and reports_to filters.

    Returns:
        List of member dicts sorted by created_at.
    """
    filters: dict[str, Any] = {"firm_id": firm_id}
    if status is not None:
        filters["status"] = status
    if reports_to is not None:
        filters["reports_to_member_id"] = reports_to
    return repo.find(conn, "member", **filters)


def view_member(
    conn: sqlite3.Connection,
    member_id: str,
) -> dict[str, Any]:
    """View a member by ID. Raises ValueError if not found."""
    return require_exists(conn, "member", member_id)


def update_member(
    conn: sqlite3.Connection,
    member_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Update a member with FK validation and status transition logging.

    Raises:
        ValueError: If member not found, FK validation fails, or invalid status.
        AuthorityError: If an identified Member caller lacks the authority key.
    """
    require_authority(conn, "member.update")

    existing = require_exists(conn, "member", member_id)

    # Validate status if changing
    if "status" in data:
        validate_status(data["status"], MEMBER_STATUSES)

    # Validate FK references if changing
    if "reports_to_member_id" in data:
        validate_fk(conn, "member", data["reports_to_member_id"])
    if "contract_id" in data:
        validate_fk(conn, "contract", data["contract_id"])

    # Detect status transition before update
    old_status = existing.get("status")
    new_status = data.get("status")

    updated = repo.update(conn, "member", member_id, data)
    assert updated is not None, "member disappeared after require_exists"

    # Log status transition if changed
    if new_status is not None and new_status != old_status:
        log_event(
            conn,
            firm_id=existing["firm_id"],
            event_type="member.status_transition",
            actor={"type": "board", "id": None},
            target_ref={"type": "member", "id": member_id},
            details={"from": old_status, "to": new_status},
        )

    return updated


# ---------------------------------------------------------------------------
# Hierarchy queries
# ---------------------------------------------------------------------------


def get_direct_reports(
    conn: sqlite3.Connection,
    firm_id: str,
    member_id: str,
) -> list[dict[str, Any]]:
    """Return Members whose reports_to_member_id equals member_id.

    Args:
        conn: SQLite connection.
        firm_id: Firm scope.
        member_id: The manager's member ID.

    Returns:
        List of member dicts who report to this member. Empty if none.
    """
    return repo.find(conn, "member", firm_id=firm_id, reports_to_member_id=member_id)


def get_management_chain(
    conn: sqlite3.Connection,
    member_id: str,
) -> list[dict[str, Any]]:
    """Walk up the reports_to chain from member to root (Board).

    Returns:
        Ordered list of manager dicts, from immediate manager to root.
        Empty list if member reports directly to the Board (no manager).

    Raises:
        ValueError: If member_id not found.
    """
    member = require_exists(conn, "member", member_id)

    chain: list[dict[str, Any]] = []
    current = member
    seen: set[str] = {member_id}

    for _ in range(10):  # Safety cap prevents infinite loops from corrupt data
        manager_id = current.get("reports_to_member_id")
        if not manager_id:
            break
        if manager_id in seen:
            break  # Circular reference — stop walking
        seen.add(manager_id)
        manager = repo.get(conn, "member", manager_id)
        if not manager:
            break  # Dangling FK — stop walking
        chain.append(dict(manager))
        current = manager

    return chain


def can_delegate_to(
    conn: sqlite3.Connection,
    manager_id: str,
    assignee_id: str,
) -> bool:
    """Check if manager can delegate work to assignee (direct report).

    Returns True if assignee's reports_to_member_id == manager_id
    and both Members exist and are active.

    Raises:
        ValueError: If either Member not found.
    """
    manager = require_exists(conn, "member", manager_id)
    assignee = require_exists(conn, "member", assignee_id)

    if manager.get("status") != "active" or assignee.get("status") != "active":
        return False

    return assignee.get("reports_to_member_id") == manager_id


def _auto_gen_instructions(
    member_id: str,
    role: str,
    description: str | None,
    cwd: str,
) -> None:
    """Auto-generate instructions file at .firm/instructions/{id}.md.

    Best-effort: skips silently if .firm/ directory doesn't exist.
    Does not overwrite existing files.
    """
    firm_dir = os.path.join(cwd, ".firm")
    if not os.path.isdir(firm_dir):
        return

    instructions_dir = os.path.join(firm_dir, "instructions")
    os.makedirs(instructions_dir, exist_ok=True)

    path = os.path.join(instructions_dir, f"{member_id}.md")
    if os.path.exists(path):
        return

    desc_line = f"\n{description}\n" if description else ""

    content = (
        f"# {member_id} Instructions\n"
        f"\n"
        f"**Role:** {role}\n"
        f"{desc_line}\n"
        f"## Guidelines\n"
        f"\n"
        f"- Complete assigned Units according to their acceptance criteria\n"
        f"- Report completion status and list outputs produced\n"
        f"- Do not modify files outside your assigned scope\n"
        f"- If blocked, report the blocker clearly\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
