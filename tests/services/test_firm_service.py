"""Tests for firm.services.firm_svc — Firm entity service."""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import find
from firm.services.firm_svc import create_firm, firm_status, update_firm, view_firm


def _fresh_conn() -> sqlite3.Connection:
    """In-memory DB with migrations but NO pre-seeded firm."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    return conn


# ---------------------------------------------------------------------------
# AC-5: Firm create/view/update with Records and status aggregation
# ---------------------------------------------------------------------------


def test_create_firm() -> None:
    conn = _fresh_conn()
    firm = create_firm(conn, {"id": "chrisai", "name": "ChrisAI"})
    assert firm["id"] == "chrisai"
    assert firm["name"] == "ChrisAI"

    # Verify Records entry
    records = find(conn, "records", firm_id="chrisai")
    events = [r["event_type"] for r in records]
    assert "firm.created" in events
    firm_record = [r for r in records if r["event_type"] == "firm.created"][0]
    assert firm_record["target_entity_id"] == "chrisai"


def test_create_firm_missing_name() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="'name' is required"):
        create_firm(conn, {"id": "bad-firm"})


def test_view_firm() -> None:
    conn = _fresh_conn()
    create_firm(conn, {"id": "chrisai", "name": "ChrisAI"})
    viewed = view_firm(conn, "chrisai")
    assert viewed["id"] == "chrisai"
    assert viewed["name"] == "ChrisAI"


def test_view_firm_not_found() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="not found"):
        view_firm(conn, "nonexistent")


def test_update_firm() -> None:
    conn = _fresh_conn()
    create_firm(conn, {"id": "chrisai", "name": "ChrisAI"})
    updated = update_firm(conn, "chrisai", {"description": "AI-powered firm"})
    assert updated["description"] == "AI-powered firm"

    # Verify Records has firm.updated
    records = find(conn, "records", firm_id="chrisai")
    events = [r["event_type"] for r in records]
    assert "firm.updated" in events


def test_firm_status_empty() -> None:
    conn = _fresh_conn()
    create_firm(conn, {"id": "chrisai", "name": "ChrisAI"})
    status = firm_status(conn, "chrisai")
    assert status["firm_id"] == "chrisai"
    assert status["member_count"] == 0
    assert status["operation_count"] == 0
    assert status["project_count"] == 0
    assert status["unit_count"] == 0
    assert status["goal_count"] == 0
    assert status["gate_count"] == 0


def test_firm_status_with_entities() -> None:
    conn = _fresh_conn()
    create_firm(conn, {"id": "chrisai", "name": "ChrisAI"})

    from firm.services.member import create_member
    from firm.services.operation import create_operation

    create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    create_member(conn, "chrisai", {"name": "Sterling", "role": "CMO"})
    create_operation(conn, "chrisai", {
        "name": "Content Pipeline",
        "owner_member_id": "MEM-001",
    })

    status = firm_status(conn, "chrisai")
    assert status["member_count"] == 2
    assert status["operation_count"] == 1
    assert status["project_count"] == 0
    assert status["unit_count"] == 0
