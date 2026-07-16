"""Gate entity service — request, approve, reject, list, view.

Gates are the Board's decision checkpoints. Members request permission to
take significant actions; the Board approves or rejects. Not standard CRUD —
Gates are created via request, then resolved via approve/reject.

ID prefix: GATE-NNN
Records events: gate.requested, gate.approved, gate.rejected, gate.dismissed
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from firm import notify
from firm.core import repo
from firm.services._id import next_id
from firm.services._records import log_event
from firm.services._validate import require_exists, validate_parent_ref
from firm.services.authority import require_board_only

#: Gate decisions are the Board's alone. The authority key deliberately does
#: NOT unlock these: a Member deciding its own approval request would make the
#: Gate ceremony rather than control. Members request (request_gate) and raise
#: blockers (firm_escalate); the Board decides.
_GATE_DECISION_HINT = (
    "gate decisions are the Board's alone — the authority key does not unlock "
    "them; use firm_request_gate to ask, or firm_escalate to raise a blocker"
)

GATE_STATUSES = ["pending", "approved", "rejected", "expired", "revoked"]


def request_gate(
    conn: sqlite3.Connection,
    firm_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Request a Gate (create with pending status).

    Args:
        conn: SQLite connection.
        firm_id: Firm scope.
        data: Must include 'requesting_member_id', 'action',
              'target_entity_type', 'target_entity_id'. Optional:
              context, expires_at.

    Returns:
        The created gate row as a dict.

    Raises:
        ValueError: If required fields missing, member doesn't exist,
                    or target entity invalid.
    """
    for required in (
        "requesting_member_id", "action",
        "target_entity_type", "target_entity_id",
    ):
        if required not in data:
            raise ValueError(f"'{required}' is required for gate request")

    # Validate requesting member exists
    require_exists(conn, "member", data["requesting_member_id"])

    # Validate target entity exists
    validate_parent_ref(
        conn, data["target_entity_type"], data["target_entity_id"]
    )

    gate_id = next_id(conn, "gate", firm_id)

    # Build row
    row_data: dict[str, Any] = {
        "id": gate_id,
        "firm_id": firm_id,
        "requesting_member_id": data["requesting_member_id"],
        "action": data["action"],
        "target_entity_type": data["target_entity_type"],
        "target_entity_id": data["target_entity_id"],
    }
    for field in ("context", "expires_at"):
        if field in data:
            row_data[field] = data[field]

    created = repo.create(conn, "gate", row_data)

    # Board notification — structural, best-effort (a Gate IS a board item;
    # delivery failure must never block the request itself). A gate is a
    # single created row, so this fires exactly once per gate — reminder
    # cadence for lingering gates stays with the Board Proxy pulse.
    member = repo.get(conn, "member", data["requesting_member_id"])
    dm_lines = [
        f"🚪 Gate {gate_id} requested by {member['name']} ({member['id']})",
        f"*{data['action'][:300]}*",
        f"On: {data['target_entity_type']} {data['target_entity_id']}",
    ]
    if data.get("context"):
        dm_lines.append(str(data["context"])[:400])
    outcome = notify.send_board_dm(conn, firm_id, "\n".join(dm_lines))

    # Records entry
    log_event(
        conn,
        firm_id=firm_id,
        event_type="gate.requested",
        actor={"type": "member", "id": data["requesting_member_id"]},
        target_ref={"type": "gate", "id": gate_id},
        details={"notified": outcome["sent"], "notify_reason": outcome["reason"]},
    )

    return created


def approve_gate(
    conn: sqlite3.Connection,
    gate_id: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Approve a pending Gate.

    Args:
        conn: SQLite connection.
        gate_id: The gate to approve.
        data: Optional dict with 'approver_comment'.

    Raises:
        ValueError: If gate not found or not in pending status.
        AuthorityError: If any identified Member calls it — key or not.
    """
    require_board_only("gate.approve", hint=_GATE_DECISION_HINT)
    return _resolve_gate(conn, gate_id, "approved", data)


def reject_gate(
    conn: sqlite3.Connection,
    gate_id: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reject a pending Gate.

    Raises:
        AuthorityError: If any identified Member calls it — key or not.
        ValueError: If gate not found or not in pending status.
    """
    require_board_only("gate.reject", hint=_GATE_DECISION_HINT)
    return _resolve_gate(conn, gate_id, "rejected", data)


def dismiss_gate(
    conn: sqlite3.Connection,
    gate_id: str,
) -> dict[str, Any]:
    """Dismiss a Gate's *notification* without touching its decision.

    Clears the gate from the Board's attention surface (hub ``needs_you`` /
    pending-gate badge) while leaving ``status`` exactly as it was — a pending
    gate stays pending and fully resolvable. This is the notification layer,
    NOT a decision: approve/reject remain the only ways to decide a gate. It
    exists so "make the badge go away" is no longer only expressible as a
    reject, which silently destroyed live decisions.

    Idempotent: dismissing again just refreshes ``dismissed_at``.

    Raises:
        ValueError: If gate not found.
    """
    existing = require_exists(conn, "gate", gate_id)
    updated = repo.update(
        conn, "gate", gate_id,
        {"dismissed_at": datetime.now(tz=timezone.utc).isoformat()},
    )
    assert updated is not None, "gate disappeared after require_exists"

    log_event(
        conn,
        firm_id=existing["firm_id"],
        event_type="gate.dismissed",
        actor={"type": "board", "id": None},
        target_ref={"type": "gate", "id": gate_id},
    )

    return updated


def _resolve_gate(
    conn: sqlite3.Connection,
    gate_id: str,
    resolution: str,
    data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Internal: resolve a gate to approved or rejected."""
    existing = require_exists(conn, "gate", gate_id)

    if existing["status"] != "pending":
        raise ValueError(
            f"Gate {gate_id!r} is {existing['status']!r}, not 'pending' — "
            f"cannot {resolution.rstrip('ed').rstrip('v')}e"
        )

    update_data: dict[str, Any] = {
        "status": resolution,
        "approver_type": "board",
        "approver_id": None,
        "decided_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    if data and "approver_comment" in data:
        update_data["approver_comment"] = data["approver_comment"]

    updated = repo.update(conn, "gate", gate_id, update_data)
    assert updated is not None, "gate disappeared after require_exists"

    # A create-goal gate is a PROPOSAL — approval is what makes it real.
    # Materialized here, in the same transaction as the resolution, so every
    # approval surface (dashboard, CLI, co-board) yields the goal without
    # reimplementing it. A Member may argue for the number; only the Board
    # sets it — this is the moment it gets set.
    if resolution == "approved" and existing.get("action") == "create-goal":
        from firm.services import goal as goal_svc
        try:
            payload = json.loads(existing.get("context") or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = None
        if not isinstance(payload, dict) or not payload.get("target"):
            raise ValueError(
                f"gate {gate_id!r} carries an unreadable create-goal payload — "
                "reject it and have the Member re-propose")
        goal = goal_svc.create_goal(conn, existing["firm_id"], {
            "target": payload.get("target"),
            "parent_entity_type": payload.get("parent_entity_type"),
            "parent_entity_id": payload.get("parent_entity_id"),
            **({"metric": payload["metric"]} if payload.get("metric") else {}),
        })
        updated["goal"] = goal

    # Records entry
    log_event(
        conn,
        firm_id=existing["firm_id"],
        event_type=f"gate.{resolution}",
        actor={"type": "board", "id": None},
        target_ref={"type": "gate", "id": gate_id},
    )

    return updated


def list_gates(
    conn: sqlite3.Connection,
    firm_id: str,
    *,
    status: str | None = None,
    requesting_member_id: str | None = None,
) -> list[dict[str, Any]]:
    """List gates with optional status and requester filters.

    Returns:
        List of gate dicts sorted by created_at.
    """
    filters: dict[str, Any] = {"firm_id": firm_id}
    if status is not None:
        filters["status"] = status
    if requesting_member_id is not None:
        filters["requesting_member_id"] = requesting_member_id
    return repo.find(conn, "gate", **filters)


def view_gate(
    conn: sqlite3.Connection,
    gate_id: str,
) -> dict[str, Any]:
    """View a gate by ID. Raises ValueError if not found."""
    return require_exists(conn, "gate", gate_id)
