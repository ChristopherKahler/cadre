"""Goal entity service — create, propose, list, view, update.

Goals are measurable outcomes attached to any entity via polymorphic parent_ref.
Creating a goal appends its ID to the parent entity's goal_ids array (for
entities that support it: operation, project, unit).

The Board creates goals. A Member proposes one, which raises a Gate, and the
goal is created only when the Board approves that Gate.

ID prefix: GOAL-NNN
Records events: goal.created, goal.status_transition, goal.metric_updated
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from firm.core import repo
from firm.services._id import next_id
from firm.services._records import log_event
from firm.services._validate import require_exists, validate_parent_ref, validate_status
from firm.services.authority import require_authority, require_board_only
from firm.services.gate import request_gate

GOAL_STATUSES = ["active", "achieved", "abandoned"]

#: The Gate action a goal proposal raises. ``gate._resolve_gate`` creates the
#: goal from that Gate's context when the Board approves it.
PROPOSE_GOAL_ACTION = "create-goal"

#: What a Member that tries to create a goal is told instead.
GOAL_CREATE_HINT = (
    "goals are set by the Board; propose yours instead: `firm goal propose "
    "\"<target>\" --parent-type <type> --parent-id <id> --reasoning "
    "\"<why this metric proves your outcome>\"`"
)

# Tables that have a goal_ids JSON column for denormalized linkage.
_TABLES_WITH_GOAL_IDS = frozenset({"operation", "project", "unit"})


def create_goal(
    conn: sqlite3.Connection,
    firm_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Create a Goal with parent_ref validation, goal_ids linkage, and Records.

    Args:
        conn: SQLite connection.
        firm_id: Firm scope.
        data: Must include 'target', 'parent_entity_type', 'parent_entity_id'.
              Optional: level, metric, status.

    Returns:
        The created goal row as a dict.

    Raises:
        ValueError: If required fields missing or parent entity invalid.
        AuthorityError: If any identified Member calls it, key or not.
    """
    # Board-only, not require_authority like update_goal below. A Member's
    # route to a new goal is a create-goal Gate, and deciding a Gate is
    # Board-only even with the authority key; a key holder that could create
    # goals here would skip its own proposal Gate. The Board's approval reaches
    # this function with no identity, so approving a proposal still creates it.
    require_board_only("goal.create", hint=GOAL_CREATE_HINT)

    for required in ("target", "parent_entity_type", "parent_entity_id"):
        if required not in data:
            raise ValueError(f"'{required}' is required for goal creation")

    # Validate parent entity exists
    parent = validate_parent_ref(
        conn, data["parent_entity_type"], data["parent_entity_id"]
    )

    goal_id = next_id(conn, "goal", firm_id)

    # Build row
    row_data: dict[str, Any] = {
        "id": goal_id,
        "firm_id": firm_id,
        "target": data["target"],
        "parent_entity_type": data["parent_entity_type"],
        "parent_entity_id": data["parent_entity_id"],
    }
    for field in ("level", "metric", "status"):
        if field in data:
            row_data[field] = data[field]

    created = repo.create(conn, "goal", row_data)

    # Records entry
    log_event(
        conn,
        firm_id=firm_id,
        event_type="goal.created",
        actor={"type": "board", "id": None},
        target_ref={"type": "goal", "id": goal_id},
    )

    # Append goal ID to parent entity's goal_ids (if supported)
    parent_table = data["parent_entity_type"]
    if parent_table in _TABLES_WITH_GOAL_IDS:
        current_ids = parent.get("goal_ids") or []
        current_ids.append(goal_id)
        repo.update(
            conn, parent_table, data["parent_entity_id"],
            {"goal_ids": current_ids},
        )

    return created


def propose_goal(
    conn: sqlite3.Connection,
    firm_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Propose a Goal for the Board to approve, by raising a create-goal Gate.

    Nothing binds here. The goal is created when the Board approves the Gate,
    from the payload this writes into the Gate's context. This is the one path
    behind the ``firm goal propose`` CLI verb and the firm MCP tool
    ``firm_propose_goal``.

    Args:
        conn: SQLite connection.
        firm_id: Firm scope.
        data: Must include 'requesting_member_id', 'target',
              'parent_entity_type', 'parent_entity_id' and 'reasoning'.
              Optional: metric.

    Returns:
        The created gate row as a dict.

    Raises:
        ValueError: If the proposer, target, parent or reasoning is missing,
            or the member or parent entity does not exist.
    """
    member_id = str(data.get("requesting_member_id") or "").strip()
    if not member_id:
        raise ValueError(
            "no member identity: a goal proposal records which Member is "
            "asking. Members propose from inside their runs, where "
            "$CADRE_MEMBER_ID is set; the Board sets goals with `firm goal create`"
        )
    for required in ("target", "parent_entity_type", "parent_entity_id", "reasoning"):
        if not str(data.get(required) or "").strip():
            raise ValueError(f"'{required}' is required to propose a goal")

    payload = {
        "target": data["target"],
        "parent_entity_type": data["parent_entity_type"],
        "parent_entity_id": data["parent_entity_id"],
        "metric": data.get("metric") or "",
        "reasoning": data["reasoning"],
    }
    return request_gate(conn, firm_id, {
        "requesting_member_id": member_id,
        "action": PROPOSE_GOAL_ACTION,
        "target_entity_type": data["parent_entity_type"],
        "target_entity_id": data["parent_entity_id"],
        "context": json.dumps(payload),
    })


def member_goal_state(
    conn: sqlite3.Connection,
    firm_id: str,
    member_id: str,
) -> tuple[str, dict[str, Any] | None]:
    """Whether *member_id* has a goal yet, or is waiting on a proposal.

    Returns one of:
        ("pending", gate): a goal proposal from this Member awaits the Board.
        ("bound", goal): an active goal is attached to this Member, or a
            proposal of theirs was approved and its goal is still active.
        ("none", None): neither, so the Member has a goal to propose.
    """
    proposals = [
        gate for gate in repo.find(
            conn, "gate", firm_id=firm_id, requesting_member_id=member_id,
        )
        if gate.get("action") == PROPOSE_GOAL_ACTION
    ]
    for gate in proposals:
        if gate.get("status") == "pending":
            return "pending", gate

    active = repo.find(conn, "goal", firm_id=firm_id, status="active")
    for goal in active:
        if (goal.get("parent_entity_type") == "member"
                and goal.get("parent_entity_id") == member_id):
            return "bound", goal

    # A proposal may attach to something other than the Member (an operation
    # it runs, say). Approval creates that goal from the Gate's payload, so the
    # payload is how to find it again.
    for gate in proposals:
        if gate.get("status") != "approved":
            continue
        try:
            payload = json.loads(gate.get("context") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        for goal in active:
            if all(goal.get(key) == payload.get(key) for key in
                   ("target", "parent_entity_type", "parent_entity_id")):
                return "bound", goal

    return "none", None


def list_goals(
    conn: sqlite3.Connection,
    firm_id: str,
    *,
    status: str | None = None,
    level: str | None = None,
    parent_type: str | None = None,
) -> list[dict[str, Any]]:
    """List goals with optional status, level, and parent_type filters.

    Returns:
        List of goal dicts sorted by created_at.
    """
    filters: dict[str, Any] = {"firm_id": firm_id}
    if status is not None:
        filters["status"] = status
    if level is not None:
        filters["level"] = level
    if parent_type is not None:
        filters["parent_entity_type"] = parent_type
    return repo.find(conn, "goal", **filters)


def view_goal(
    conn: sqlite3.Connection,
    goal_id: str,
) -> dict[str, Any]:
    """View a goal by ID. Raises ValueError if not found."""
    return require_exists(conn, "goal", goal_id)


def update_goal_metric(
    conn: sqlite3.Connection,
    goal_id: str,
    *,
    current: Any = None,
    value: Any = None,
    unit: str | None = None,
    metric_type: str | None = None,
    deadline: str | None = None,
    trend: str | None = None,
) -> dict[str, Any]:
    """Merge fields into the goal's metric JSON and persist via update_goal.

    Shapes the ``{"type", "value", "unit", "current", "deadline", "trend"}``
    object the session-pulse banner parser expects. Unspecified fields keep
    their existing values. A legacy bare-string metric (e.g. "ig_followers")
    is absorbed as the ``type`` key rather than discarded. The repo layer
    hydrates JSON metrics to dicts on read and serializes on write.

    Raises:
        ValueError: If goal not found, or no metric field provided.
        AuthorityError: If an identified Member caller lacks the authority key.
    """
    # Gated independently of update_goal below: this is a public entry point,
    # and the boundary must not vanish if the delegation is ever refactored.
    require_authority(conn, "goal.update_metric")

    existing = require_exists(conn, "goal", goal_id)

    raw = existing.get("metric")
    metric: dict[str, Any] = {}
    if isinstance(raw, dict):
        metric = dict(raw)
    elif isinstance(raw, str) and raw:
        metric = {"type": raw}

    updates = {
        "current": current,
        "value": value,
        "unit": unit,
        "type": metric_type,
        "deadline": deadline,
        "trend": trend,
    }
    provided = {k: v for k, v in updates.items() if v is not None}
    if not provided:
        raise ValueError(
            "No metric fields provided — pass at least one of "
            "current/value/unit/metric_type/deadline/trend"
        )
    metric.update(provided)

    return update_goal(conn, goal_id, {"metric": metric})


def update_goal(
    conn: sqlite3.Connection,
    goal_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Update a goal with status transition and metric change tracking.

    Raises:
        ValueError: If goal not found or invalid status.
        AuthorityError: If an identified Member caller lacks the authority key.
    """
    require_authority(conn, "goal.update")

    existing = require_exists(conn, "goal", goal_id)

    # Validate status if changing
    if "status" in data:
        validate_status(data["status"], GOAL_STATUSES)

    # Detect changes before update
    old_status = existing.get("status")
    new_status = data.get("status")
    old_metric = existing.get("metric")
    new_metric = data.get("metric")

    updated = repo.update(conn, "goal", goal_id, data)
    assert updated is not None, "goal disappeared after require_exists"

    firm_id = existing["firm_id"]

    # Log status transition if changed
    if new_status is not None and new_status != old_status:
        log_event(
            conn,
            firm_id=firm_id,
            event_type="goal.status_transition",
            actor={"type": "board", "id": None},
            target_ref={"type": "goal", "id": goal_id},
            details={"from": old_status, "to": new_status},
        )

    # Log metric change if changed
    if new_metric is not None and new_metric != old_metric:
        log_event(
            conn,
            firm_id=firm_id,
            event_type="goal.metric_updated",
            actor={"type": "board", "id": None},
            target_ref={"type": "goal", "id": goal_id},
            details={"from": old_metric, "to": new_metric},
        )

    return updated
