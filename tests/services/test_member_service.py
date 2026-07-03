"""Tests for firm.services.member — Member entity service."""

from __future__ import annotations

import os
import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find
from firm.services.member import (
    create_member,
    list_members,
    update_member,
    view_member,
)


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


# ---------------------------------------------------------------------------
# AC-1: Member create with validation and Records
# ---------------------------------------------------------------------------


def test_create_member() -> None:
    conn = _fresh_conn()
    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    assert member["id"] == "MEM-001"
    assert member["name"] == "Quill"
    assert member["role"] == "Writer"
    assert member["firm_id"] == "chrisai"
    assert member["status"] == "active"

    # Verify Records entry
    records = find(conn, "records", firm_id="chrisai")
    assert len(records) == 1
    assert records[0]["event_type"] == "member.created"
    assert records[0]["target_entity_type"] == "member"
    assert records[0]["target_entity_id"] == "MEM-001"


def test_create_member_with_reports_to() -> None:
    conn = _fresh_conn()
    lead = create_member(conn, "chrisai", {"name": "Lead", "role": "Manager"})
    writer = create_member(
        conn,
        "chrisai",
        {"name": "Quill", "role": "Writer", "reports_to_member_id": lead["id"]},
    )
    assert writer["reports_to_member_id"] == "MEM-001"


def test_create_member_invalid_reports_to() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="not found"):
        create_member(
            conn,
            "chrisai",
            {"name": "Quill", "role": "Writer", "reports_to_member_id": "MEM-999"},
        )


def test_create_member_auto_instructions(tmp_path: object) -> None:
    """Instructions file auto-generated at .firm/instructions/{id}.md."""
    workspace = str(tmp_path)
    os.makedirs(os.path.join(workspace, ".firm"))

    conn = _fresh_conn()
    member = create_member(
        conn,
        "chrisai",
        {"name": "Quill", "role": "Blog Author", "description": "Writes blog posts"},
        cwd=workspace,
    )
    path = os.path.join(workspace, ".firm", "instructions", f"{member['id']}.md")
    assert os.path.exists(path)
    content = open(path, encoding="utf-8").read()
    assert "Blog Author" in content
    assert "Writes blog posts" in content


# ---------------------------------------------------------------------------
# AC-2: Member list and view
# ---------------------------------------------------------------------------


def test_list_members_status_filter() -> None:
    conn = _fresh_conn()
    create_member(conn, "chrisai", {"name": "Active", "role": "Worker"})
    m2 = create_member(conn, "chrisai", {"name": "Paused", "role": "Worker"})
    # Pause the second member
    update_member(conn, m2["id"], {"status": "paused"})

    active = list_members(conn, "chrisai", status="active")
    assert len(active) == 1
    assert active[0]["name"] == "Active"

    paused = list_members(conn, "chrisai", status="paused")
    assert len(paused) == 1
    assert paused[0]["name"] == "Paused"


def test_view_member() -> None:
    conn = _fresh_conn()
    created = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    viewed = view_member(conn, created["id"])
    assert viewed["id"] == created["id"]
    assert viewed["name"] == "Quill"
    assert viewed["role"] == "Writer"


def test_view_member_not_found() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="not found"):
        view_member(conn, "MEM-999")


# ---------------------------------------------------------------------------
# AC-3: Member update with status transition Records
# ---------------------------------------------------------------------------


def test_update_member_status_transition() -> None:
    conn = _fresh_conn()
    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    updated = update_member(conn, member["id"], {"status": "paused"})
    assert updated["status"] == "paused"

    # Verify Records has both created + status_transition
    records = find(conn, "records", firm_id="chrisai")
    events = [r["event_type"] for r in records]
    assert "member.created" in events
    assert "member.status_transition" in events

    transition = [r for r in records if r["event_type"] == "member.status_transition"][0]
    assert transition["details"] == {"from": "active", "to": "paused"}


def test_update_member_fk_validation() -> None:
    conn = _fresh_conn()
    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    with pytest.raises(ValueError, match="not found"):
        update_member(conn, member["id"], {"reports_to_member_id": "MEM-999"})
