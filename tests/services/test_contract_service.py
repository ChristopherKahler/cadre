"""Tests for firm.services.contract — Contract entity service."""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find
from firm.services.contract import create_contract, update_contract, view_contract
from firm.services.member import create_member


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


# ---------------------------------------------------------------------------
# AC-4: Contract create/view/update with validation and Records
# ---------------------------------------------------------------------------


def test_create_contract() -> None:
    conn = _fresh_conn()
    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    contract = create_contract(
        conn,
        "chrisai",
        {
            "name": "Quill Runtime",
            "member_id": member["id"],
            "runtime_type": "claude_code",
        },
    )
    assert contract["id"] == "CON-001"
    assert contract["name"] == "Quill Runtime"
    assert contract["runtime_type"] == "claude_code"
    assert contract["member_id"] == member["id"]

    # Verify Records entry (member.created + contract.created)
    records = find(conn, "records", firm_id="chrisai")
    events = [r["event_type"] for r in records]
    assert "contract.created" in events
    contract_record = [r for r in records if r["event_type"] == "contract.created"][0]
    assert contract_record["target_entity_id"] == "CON-001"


def test_create_contract_invalid_member() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="not found"):
        create_contract(
            conn,
            "chrisai",
            {
                "name": "Bad Contract",
                "member_id": "MEM-999",
                "runtime_type": "claude_code",
            },
        )


def test_create_contract_invalid_runtime() -> None:
    conn = _fresh_conn()
    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    with pytest.raises(ValueError, match="Invalid status"):
        create_contract(
            conn,
            "chrisai",
            {
                "name": "Bad Runtime",
                "member_id": member["id"],
                "runtime_type": "invalid_runtime",
            },
        )


def test_view_contract() -> None:
    conn = _fresh_conn()
    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    created = create_contract(
        conn,
        "chrisai",
        {
            "name": "Quill Runtime",
            "member_id": member["id"],
            "runtime_type": "claude_code",
        },
    )
    viewed = view_contract(conn, created["id"])
    assert viewed["id"] == created["id"]
    assert viewed["name"] == "Quill Runtime"


def test_update_contract() -> None:
    conn = _fresh_conn()
    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    contract = create_contract(
        conn,
        "chrisai",
        {
            "name": "Quill Runtime",
            "member_id": member["id"],
            "runtime_type": "claude_code",
        },
    )
    updated = update_contract(conn, contract["id"], {"name": "Quill Runtime v2"})
    assert updated["name"] == "Quill Runtime v2"

    # Verify Records has contract.updated
    records = find(conn, "records", firm_id="chrisai")
    events = [r["event_type"] for r in records]
    assert "contract.updated" in events
