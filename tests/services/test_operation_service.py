"""Tests for firm.services.operation — Operation entity service."""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find
from firm.services.member import create_member
from firm.services.operation import (
    create_operation,
    list_operations,
    update_operation,
    view_operation,
)


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


# ---------------------------------------------------------------------------
# AC-1: Operation create with validation and Records
# ---------------------------------------------------------------------------


def test_create_operation() -> None:
    conn = _fresh_conn()
    op = create_operation(conn, "chrisai", {"name": "Content Pipeline"})
    assert op["id"] == "OPS-001"
    assert op["name"] == "Content Pipeline"
    assert op["firm_id"] == "chrisai"
    assert op["status"] == "active"

    # Verify Records entry
    records = find(conn, "records", firm_id="chrisai")
    assert len(records) == 1
    assert records[0]["event_type"] == "operation.created"
    assert records[0]["target_entity_id"] == "OPS-001"


def test_create_operation_with_owner() -> None:
    conn = _fresh_conn()
    member = create_member(conn, "chrisai", {"name": "Sterling", "role": "CMO"})
    op = create_operation(
        conn,
        "chrisai",
        {"name": "Content Pipeline", "owner_member_id": member["id"]},
    )
    assert op["owner_member_id"] == "MEM-001"


def test_create_operation_invalid_owner() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="not found"):
        create_operation(
            conn,
            "chrisai",
            {"name": "Bad Op", "owner_member_id": "MEM-999"},
        )


# ---------------------------------------------------------------------------
# AC-2: Operation list, view, update with status transition
# ---------------------------------------------------------------------------


def test_list_operations_status_filter() -> None:
    conn = _fresh_conn()
    create_operation(conn, "chrisai", {"name": "Active Op"})
    op2 = create_operation(conn, "chrisai", {"name": "Paused Op"})
    update_operation(conn, op2["id"], {"status": "paused"})

    active = list_operations(conn, "chrisai", status="active")
    assert len(active) == 1
    assert active[0]["name"] == "Active Op"

    paused = list_operations(conn, "chrisai", status="paused")
    assert len(paused) == 1
    assert paused[0]["name"] == "Paused Op"


def test_list_operations_category_filter() -> None:
    conn = _fresh_conn()
    create_operation(conn, "chrisai", {"name": "Op A", "category": "content"})
    create_operation(conn, "chrisai", {"name": "Op B", "category": "engineering"})

    content = list_operations(conn, "chrisai", category="content")
    assert len(content) == 1
    assert content[0]["name"] == "Op A"


def test_view_operation() -> None:
    conn = _fresh_conn()
    created = create_operation(conn, "chrisai", {"name": "Content Pipeline"})
    viewed = view_operation(conn, created["id"])
    assert viewed["id"] == created["id"]
    assert viewed["name"] == "Content Pipeline"


def test_view_operation_not_found() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="not found"):
        view_operation(conn, "OPS-999")


def test_update_operation_status_transition() -> None:
    conn = _fresh_conn()
    op = create_operation(conn, "chrisai", {"name": "Content Pipeline"})
    updated = update_operation(conn, op["id"], {"status": "paused"})
    assert updated["status"] == "paused"

    # Verify Records has both created + status_transition
    records = find(conn, "records", firm_id="chrisai")
    events = [r["event_type"] for r in records]
    assert "operation.created" in events
    assert "operation.status_transition" in events

    transition = [r for r in records if r["event_type"] == "operation.status_transition"][0]
    assert transition["details"] == {"from": "active", "to": "paused"}
