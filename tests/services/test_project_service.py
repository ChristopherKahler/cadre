"""Tests for firm.services.project — Project entity service."""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find, get
from firm.services.member import create_member
from firm.services.operation import create_operation
from firm.services.project import (
    create_project,
    list_projects,
    update_project,
    view_project,
)


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


def _seed_operation(conn: sqlite3.Connection) -> dict:
    """Create a default operation for project tests."""
    return create_operation(conn, "chrisai", {"name": "Content Pipeline"})


# ---------------------------------------------------------------------------
# AC-3: Project create with operation linkage and Records
# ---------------------------------------------------------------------------


def test_create_project() -> None:
    conn = _fresh_conn()
    op = _seed_operation(conn)
    proj = create_project(
        conn,
        "chrisai",
        {"name": "Blog Redesign", "operation_id": op["id"], "due_date": "2026-05-01"},
    )
    assert proj["id"] == "PROJ-001"
    assert proj["name"] == "Blog Redesign"
    assert proj["operation_id"] == op["id"]
    assert proj["status"] == "in_progress"  # default
    assert proj["due_date"] == "2026-05-01"

    # Verify Records entry (operation.created + project.created)
    records = find(conn, "records", firm_id="chrisai")
    events = [r["event_type"] for r in records]
    assert "project.created" in events


def test_create_project_links_operation() -> None:
    """Project create appends project ID to operation.project_ids."""
    conn = _fresh_conn()
    op = _seed_operation(conn)
    proj1 = create_project(
        conn,
        "chrisai",
        {"name": "Proj A", "operation_id": op["id"], "due_date": "2026-05-01"},
    )
    proj2 = create_project(
        conn,
        "chrisai",
        {"name": "Proj B", "operation_id": op["id"], "due_date": "2026-06-01"},
    )

    # Re-read operation to check project_ids
    updated_op = get(conn, "operation", op["id"])
    assert updated_op is not None
    assert proj1["id"] in updated_op["project_ids"]
    assert proj2["id"] in updated_op["project_ids"]
    assert len(updated_op["project_ids"]) == 2


def test_create_project_invalid_operation() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="not found"):
        create_project(
            conn,
            "chrisai",
            {"name": "Bad Proj", "operation_id": "OPS-999", "due_date": "2026-05-01"},
        )


def test_create_project_with_owner() -> None:
    conn = _fresh_conn()
    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    op = _seed_operation(conn)
    proj = create_project(
        conn,
        "chrisai",
        {
            "name": "Blog Redesign",
            "operation_id": op["id"],
            "due_date": "2026-05-01",
            "owner_member_id": member["id"],
        },
    )
    assert proj["owner_member_id"] == member["id"]


# ---------------------------------------------------------------------------
# AC-4: Project list, view, update with status transition
# ---------------------------------------------------------------------------


def test_list_projects_operation_filter() -> None:
    conn = _fresh_conn()
    op1 = create_operation(conn, "chrisai", {"name": "Op A"})
    op2 = create_operation(conn, "chrisai", {"name": "Op B"})
    create_project(
        conn, "chrisai",
        {"name": "Proj A", "operation_id": op1["id"], "due_date": "2026-05-01"},
    )
    create_project(
        conn, "chrisai",
        {"name": "Proj B", "operation_id": op2["id"], "due_date": "2026-06-01"},
    )

    filtered = list_projects(conn, "chrisai", operation_id=op1["id"])
    assert len(filtered) == 1
    assert filtered[0]["name"] == "Proj A"


def test_view_project() -> None:
    conn = _fresh_conn()
    op = _seed_operation(conn)
    created = create_project(
        conn, "chrisai",
        {"name": "Blog Redesign", "operation_id": op["id"], "due_date": "2026-05-01"},
    )
    viewed = view_project(conn, created["id"])
    assert viewed["id"] == created["id"]
    assert viewed["name"] == "Blog Redesign"


def test_view_project_not_found() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="not found"):
        view_project(conn, "PROJ-999")


def test_update_project_status_transition() -> None:
    conn = _fresh_conn()
    op = _seed_operation(conn)
    proj = create_project(
        conn, "chrisai",
        {"name": "Blog Redesign", "operation_id": op["id"], "due_date": "2026-05-01"},
    )
    updated = update_project(conn, proj["id"], {"status": "done"})
    assert updated["status"] == "done"

    # Verify Records has project.status_transition
    records = find(conn, "records", firm_id="chrisai")
    events = [r["event_type"] for r in records]
    assert "project.status_transition" in events

    transition = [r for r in records if r["event_type"] == "project.status_transition"][0]
    assert transition["details"] == {"from": "in_progress", "to": "done"}
