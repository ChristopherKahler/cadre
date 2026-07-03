"""Tests for firm.services.gate — Gate entity service."""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find
from firm.services.gate import (
    approve_gate,
    list_gates,
    reject_gate,
    request_gate,
    view_gate,
)
from firm.services.member import create_member
from firm.services.operation import create_operation


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


def _seed(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """Create a member and an operation for gate tests."""
    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    op = create_operation(conn, "chrisai", {"name": "Content Pipeline"})
    return member, op


# ---------------------------------------------------------------------------
# AC-1: Gate request with member/target validation and Records
# ---------------------------------------------------------------------------


def test_request_gate() -> None:
    conn = _fresh_conn()
    member, op = _seed(conn)
    gate = request_gate(conn, "chrisai", {
        "requesting_member_id": member["id"],
        "action": "hire_member",
        "target_entity_type": "operation",
        "target_entity_id": op["id"],
    })
    assert gate["id"] == "GATE-001"
    assert gate["status"] == "pending"
    assert gate["requesting_member_id"] == member["id"]
    assert gate["target_entity_type"] == "operation"

    # Verify Records entry
    records = find(conn, "records", event_type="gate.requested")
    assert len(records) == 1
    assert records[0]["target_entity_id"] == "GATE-001"
    assert records[0]["actor_type"] == "member"


def test_request_gate_invalid_member() -> None:
    conn = _fresh_conn()
    _, op = _seed(conn)
    with pytest.raises(ValueError, match="not found"):
        request_gate(conn, "chrisai", {
            "requesting_member_id": "MEM-999",
            "action": "hire_member",
            "target_entity_type": "operation",
            "target_entity_id": op["id"],
        })


def test_request_gate_invalid_target() -> None:
    conn = _fresh_conn()
    member, _ = _seed(conn)
    with pytest.raises(ValueError, match="not found"):
        request_gate(conn, "chrisai", {
            "requesting_member_id": member["id"],
            "action": "hire_member",
            "target_entity_type": "operation",
            "target_entity_id": "OPS-999",
        })


# ---------------------------------------------------------------------------
# AC-2: Gate approve/reject with status guard and Records
# ---------------------------------------------------------------------------


def test_approve_gate() -> None:
    conn = _fresh_conn()
    member, op = _seed(conn)
    gate = request_gate(conn, "chrisai", {
        "requesting_member_id": member["id"],
        "action": "hire_member",
        "target_entity_type": "operation",
        "target_entity_id": op["id"],
    })
    approved = approve_gate(conn, gate["id"], {"approver_comment": "Looks good"})
    assert approved["status"] == "approved"
    assert approved["approver_type"] == "board"
    assert approved["decided_at"] is not None
    assert approved["approver_comment"] == "Looks good"

    records = find(conn, "records", event_type="gate.approved")
    assert len(records) == 1


def test_reject_gate() -> None:
    conn = _fresh_conn()
    member, op = _seed(conn)
    gate = request_gate(conn, "chrisai", {
        "requesting_member_id": member["id"],
        "action": "hire_member",
        "target_entity_type": "operation",
        "target_entity_id": op["id"],
    })
    rejected = reject_gate(conn, gate["id"])
    assert rejected["status"] == "rejected"
    assert rejected["decided_at"] is not None

    records = find(conn, "records", event_type="gate.rejected")
    assert len(records) == 1


def test_approve_non_pending() -> None:
    conn = _fresh_conn()
    member, op = _seed(conn)
    gate = request_gate(conn, "chrisai", {
        "requesting_member_id": member["id"],
        "action": "hire_member",
        "target_entity_type": "operation",
        "target_entity_id": op["id"],
    })
    approve_gate(conn, gate["id"])
    with pytest.raises(ValueError, match="not 'pending'"):
        approve_gate(conn, gate["id"])


def test_list_gates_status_filter() -> None:
    conn = _fresh_conn()
    member, op = _seed(conn)
    g1 = request_gate(conn, "chrisai", {
        "requesting_member_id": member["id"],
        "action": "action_a",
        "target_entity_type": "operation",
        "target_entity_id": op["id"],
    })
    request_gate(conn, "chrisai", {
        "requesting_member_id": member["id"],
        "action": "action_b",
        "target_entity_type": "operation",
        "target_entity_id": op["id"],
    })
    approve_gate(conn, g1["id"])

    pending = list_gates(conn, "chrisai", status="pending")
    assert len(pending) == 1
    assert pending[0]["action"] == "action_b"

    approved = list_gates(conn, "chrisai", status="approved")
    assert len(approved) == 1


def test_view_gate() -> None:
    conn = _fresh_conn()
    member, op = _seed(conn)
    gate = request_gate(conn, "chrisai", {
        "requesting_member_id": member["id"],
        "action": "hire_member",
        "target_entity_type": "operation",
        "target_entity_id": op["id"],
    })
    viewed = view_gate(conn, gate["id"])
    assert viewed["id"] == gate["id"]
    assert viewed["action"] == "hire_member"
