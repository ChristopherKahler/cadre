"""Goal entity service — create, list, view, update.

Goals are measurable outcomes attached to any entity via polymorphic parent_ref.
Creating a goal appends its ID to the parent entity's goal_ids array (for
entities that support it: operation, project, unit).

ID prefix: GOAL-NNN
Records events: goal.created, goal.status_transition, goal.metric_updated
"""

from __future__ import annotations

import sqlite3
from typing import Any

from firm.core import repo
from firm.services._id import next_id
from firm.services._records import log_event
from firm.services._validate import require_exists, validate_parent_ref, validate_status

GOAL_STATUSES = ["active", "achieved", "abandoned"]

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
    """
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
    """
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
    """
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
