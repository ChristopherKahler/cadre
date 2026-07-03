"""Tests for firm.services.unit — Unit entity service (CRUD + lifecycle)."""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find, get
from firm.core.units import CycleError
from firm.services.unit import (
    checkout_unit,
    complete_unit,
    create_unit,
    list_units,
    release_unit,
    update_unit,
    view_unit,
)
from firm.services.member import create_member
from firm.services.operation import create_operation
from firm.services.project import create_project


def _fresh_conn() -> sqlite3.Connection:
    """In-memory DB with firm, 2 members, 1 operation, 1 project."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    create_member(conn, "chrisai", {"name": "Sterling", "role": "CMO"})
    create_operation(conn, "chrisai", {"name": "Content Pipeline", "owner_member_id": "MEM-001"})
    create_project(conn, "chrisai", {
        "name": "Blog v1", "operation_id": "OPS-001", "due_date": "2026-12-31",
    })
    return conn


# ---------------------------------------------------------------------------
# AC-1: Unit create with project linkage, FKs, cycle check, Records
# ---------------------------------------------------------------------------


def test_create_unit() -> None:
    conn = _fresh_conn()
    unit = create_unit(conn, "chrisai", {
        "name": "Write intro post",
        "project_id": "PROJ-001",
    })
    assert unit["id"] == "UNIT-001"
    assert unit["name"] == "Write intro post"
    assert unit["project_id"] == "PROJ-001"
    assert unit["status"] == "pending"

    # Verify Records entry
    records = find(conn, "records", firm_id="chrisai")
    events = [r["event_type"] for r in records]
    assert "unit.created" in events


def test_create_unit_appends_to_project() -> None:
    conn = _fresh_conn()
    create_unit(conn, "chrisai", {
        "name": "Unit A", "project_id": "PROJ-001",
    })
    project = get(conn, "project", "PROJ-001")
    assert project is not None
    assert "UNIT-001" in (project.get("unit_ids") or [])


def test_create_unit_with_depends_on() -> None:
    conn = _fresh_conn()
    u1 = create_unit(conn, "chrisai", {
        "name": "First", "project_id": "PROJ-001",
    })
    u2 = create_unit(conn, "chrisai", {
        "name": "Second", "project_id": "PROJ-001",
        "depends_on": [u1["id"]],
    })
    assert u2["depends_on"] == [u1["id"]]


def test_create_unit_invalid_project() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="not found"):
        create_unit(conn, "chrisai", {
            "name": "Bad", "project_id": "PROJ-999",
        })


def test_create_unit_cycle() -> None:
    conn = _fresh_conn()
    u1 = create_unit(conn, "chrisai", {
        "name": "Self-dep", "project_id": "PROJ-001",
    })
    with pytest.raises(CycleError):
        create_unit(conn, "chrisai", {
            "name": "Cycle", "project_id": "PROJ-001",
            "depends_on": [u1["id"], "UNIT-002"],  # UNIT-002 is the ID it'll get
        })


# ---------------------------------------------------------------------------
# AC-2: Unit list/view/update with status transitions
# ---------------------------------------------------------------------------


def test_list_units_by_status() -> None:
    conn = _fresh_conn()
    create_unit(conn, "chrisai", {"name": "Pending", "project_id": "PROJ-001"})
    u2 = create_unit(conn, "chrisai", {"name": "Blocked", "project_id": "PROJ-001"})
    update_unit(conn, u2["id"], {"status": "blocked"})

    pending = list_units(conn, "chrisai", status="pending")
    assert len(pending) == 1
    assert pending[0]["name"] == "Pending"


def test_list_units_by_project() -> None:
    conn = _fresh_conn()
    create_unit(conn, "chrisai", {"name": "In PROJ-001", "project_id": "PROJ-001"})
    units = list_units(conn, "chrisai", project_id="PROJ-001")
    assert len(units) == 1


def test_view_unit() -> None:
    conn = _fresh_conn()
    created = create_unit(conn, "chrisai", {"name": "View me", "project_id": "PROJ-001"})
    viewed = view_unit(conn, created["id"])
    assert viewed["id"] == created["id"]
    assert viewed["name"] == "View me"


def test_update_unit_status_transition() -> None:
    conn = _fresh_conn()
    unit = create_unit(conn, "chrisai", {"name": "Transition", "project_id": "PROJ-001"})
    updated = update_unit(conn, unit["id"], {"status": "in_progress"})
    assert updated["status"] == "in_progress"

    records = find(conn, "records", firm_id="chrisai")
    transition = [r for r in records if r["event_type"] == "unit.status_transition"]
    assert len(transition) == 1


def test_update_unit_invalid_status() -> None:
    conn = _fresh_conn()
    unit = create_unit(conn, "chrisai", {"name": "Bad status", "project_id": "PROJ-001"})
    with pytest.raises(ValueError, match="Invalid status"):
        update_unit(conn, unit["id"], {"status": "deleted"})


def test_update_unit_depends_on() -> None:
    conn = _fresh_conn()
    u1 = create_unit(conn, "chrisai", {"name": "First", "project_id": "PROJ-001"})
    u2 = create_unit(conn, "chrisai", {"name": "Second", "project_id": "PROJ-001"})
    updated = update_unit(conn, u2["id"], {"depends_on": [u1["id"]]})
    assert updated["depends_on"] == [u1["id"]]


# ---------------------------------------------------------------------------
# AC-3: Unit checkout/release/complete
# ---------------------------------------------------------------------------


def test_checkout_unit() -> None:
    conn = _fresh_conn()
    unit = create_unit(conn, "chrisai", {"name": "Claim me", "project_id": "PROJ-001"})
    claimed = checkout_unit(conn, unit["id"], "MEM-001")
    assert claimed is not None
    assert claimed["claimed_by"] == "MEM-001"
    assert claimed["status"] == "in_progress"  # pending → in_progress on checkout

    records = find(conn, "records", firm_id="chrisai")
    events = [r["event_type"] for r in records]
    assert "unit.checked_out" in events


def test_checkout_unit_already_claimed() -> None:
    conn = _fresh_conn()
    unit = create_unit(conn, "chrisai", {"name": "Double claim", "project_id": "PROJ-001"})
    checkout_unit(conn, unit["id"], "MEM-001")
    second = checkout_unit(conn, unit["id"], "MEM-002")
    assert second is None


def test_release_unit() -> None:
    conn = _fresh_conn()
    unit = create_unit(conn, "chrisai", {"name": "Release me", "project_id": "PROJ-001"})
    checkout_unit(conn, unit["id"], "MEM-001")
    released = release_unit(conn, unit["id"])
    assert released is not None
    assert released["claimed_by"] is None

    records = find(conn, "records", firm_id="chrisai")
    events = [r["event_type"] for r in records]
    assert "unit.released" in events


def test_complete_unit() -> None:
    conn = _fresh_conn()
    unit = create_unit(conn, "chrisai", {"name": "Finish me", "project_id": "PROJ-001"})
    checkout_unit(conn, unit["id"], "MEM-001")
    result = complete_unit(conn, "chrisai", unit["id"], "MEM-001")
    assert result["ok"] is True

    # Verify unit status is now done
    done_unit = view_unit(conn, unit["id"])
    assert done_unit["status"] == "done"

    # Verify Records
    records = find(conn, "records", firm_id="chrisai")
    events = [r["event_type"] for r in records]
    assert "unit.completed" in events
