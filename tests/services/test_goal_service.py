"""Tests for firm.services.goal — Goal entity service."""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find, get
from firm.services.goal import create_goal, list_goals, update_goal, view_goal
from firm.services.operation import create_operation


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


# ---------------------------------------------------------------------------
# AC-3: Goal create with parent_ref linkage and Records
# ---------------------------------------------------------------------------


def test_create_goal() -> None:
    conn = _fresh_conn()
    op = create_operation(conn, "chrisai", {"name": "Content Pipeline"})
    goal = create_goal(conn, "chrisai", {
        "target": "Publish 10 blog posts",
        "parent_entity_type": "operation",
        "parent_entity_id": op["id"],
    })
    assert goal["id"] == "GOAL-001"
    assert goal["target"] == "Publish 10 blog posts"
    assert goal["parent_entity_type"] == "operation"
    assert goal["status"] == "active"

    # Verify Records entry
    records = find(conn, "records", event_type="goal.created")
    assert len(records) == 1
    assert records[0]["target_entity_id"] == "GOAL-001"


def test_create_goal_links_parent() -> None:
    """Goal create appends goal ID to operation.goal_ids."""
    conn = _fresh_conn()
    op = create_operation(conn, "chrisai", {"name": "Content Pipeline"})
    g1 = create_goal(conn, "chrisai", {
        "target": "Goal A",
        "parent_entity_type": "operation",
        "parent_entity_id": op["id"],
    })
    g2 = create_goal(conn, "chrisai", {
        "target": "Goal B",
        "parent_entity_type": "operation",
        "parent_entity_id": op["id"],
    })

    updated_op = get(conn, "operation", op["id"])
    assert updated_op is not None
    assert g1["id"] in updated_op["goal_ids"]
    assert g2["id"] in updated_op["goal_ids"]
    assert len(updated_op["goal_ids"]) == 2


def test_create_goal_invalid_parent() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="not found"):
        create_goal(conn, "chrisai", {
            "target": "Bad Goal",
            "parent_entity_type": "operation",
            "parent_entity_id": "OPS-999",
        })


def test_list_goals_status_filter() -> None:
    conn = _fresh_conn()
    op = create_operation(conn, "chrisai", {"name": "Content Pipeline"})
    create_goal(conn, "chrisai", {
        "target": "Active Goal",
        "parent_entity_type": "operation",
        "parent_entity_id": op["id"],
    })
    g2 = create_goal(conn, "chrisai", {
        "target": "Achieved Goal",
        "parent_entity_type": "operation",
        "parent_entity_id": op["id"],
    })
    update_goal(conn, g2["id"], {"status": "achieved"})

    active = list_goals(conn, "chrisai", status="active")
    assert len(active) == 1
    assert active[0]["target"] == "Active Goal"


def test_view_goal() -> None:
    conn = _fresh_conn()
    op = create_operation(conn, "chrisai", {"name": "Content Pipeline"})
    created = create_goal(conn, "chrisai", {
        "target": "Publish 10 posts",
        "parent_entity_type": "operation",
        "parent_entity_id": op["id"],
    })
    viewed = view_goal(conn, created["id"])
    assert viewed["id"] == created["id"]
    assert viewed["target"] == "Publish 10 posts"


# ---------------------------------------------------------------------------
# AC-4: Goal update with metric and status tracking
# ---------------------------------------------------------------------------


def test_update_goal_status_transition() -> None:
    conn = _fresh_conn()
    op = create_operation(conn, "chrisai", {"name": "Content Pipeline"})
    goal = create_goal(conn, "chrisai", {
        "target": "Publish 10 posts",
        "parent_entity_type": "operation",
        "parent_entity_id": op["id"],
    })
    updated = update_goal(conn, goal["id"], {"status": "achieved"})
    assert updated["status"] == "achieved"

    records = find(conn, "records", event_type="goal.status_transition")
    assert len(records) == 1
    assert records[0]["details"] == {"from": "active", "to": "achieved"}


def test_update_goal_metric_change() -> None:
    conn = _fresh_conn()
    op = create_operation(conn, "chrisai", {"name": "Content Pipeline"})
    goal = create_goal(conn, "chrisai", {
        "target": "Publish 10 posts",
        "parent_entity_type": "operation",
        "parent_entity_id": op["id"],
        "metric": {"type": "count", "value": 10, "unit": "posts", "current": 0},
    })
    updated = update_goal(
        conn, goal["id"],
        {"metric": {"type": "count", "value": 10, "unit": "posts", "current": 3}},
    )
    assert updated["metric"]["current"] == 3

    records = find(conn, "records", event_type="goal.metric_updated")
    assert len(records) == 1
    details = records[0]["details"]
    assert details["from"]["current"] == 0
    assert details["to"]["current"] == 3
