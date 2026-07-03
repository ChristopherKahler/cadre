"""Tests for firm.services.document — Document entity service."""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find
from firm.services.document import (
    create_document,
    list_documents,
    update_document,
    view_document,
)


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


# ---------------------------------------------------------------------------
# AC-3: Document create with parent validation, required fields, Records
# ---------------------------------------------------------------------------


def test_create_document() -> None:
    conn = _fresh_conn()
    doc = create_document(
        conn,
        "chrisai",
        {
            "name": "Architecture Spec",
            "type": "spec",
            "content_path": "docs/architecture.md",
            "parent_entity_type": "firm",
            "parent_entity_id": "chrisai",
        },
    )
    assert doc["id"] == "DOC-001"
    assert doc["name"] == "Architecture Spec"
    assert doc["type"] == "spec"
    assert doc["content_path"] == "docs/architecture.md"
    assert doc["version"] == 1
    assert doc["status"] == "active"

    # Verify Records entry
    records = find(conn, "records", firm_id="chrisai")
    events = [r["event_type"] for r in records]
    assert "document.created" in events


def test_create_document_invalid_parent() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="not found"):
        create_document(
            conn,
            "chrisai",
            {
                "name": "Bad Doc",
                "type": "notes",
                "content_path": "docs/bad.md",
                "parent_entity_type": "member",
                "parent_entity_id": "MEM-999",
            },
        )


def test_create_document_missing_fields() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="'content_path' is required"):
        create_document(
            conn,
            "chrisai",
            {
                "name": "No Path",
                "type": "notes",
                "parent_entity_type": "firm",
                "parent_entity_id": "chrisai",
            },
        )


# ---------------------------------------------------------------------------
# AC-3 continued: List and view
# ---------------------------------------------------------------------------


def test_list_documents_by_parent() -> None:
    conn = _fresh_conn()
    from firm.services.member import create_member

    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})

    create_document(conn, "chrisai", {
        "name": "Firm Doc", "type": "plan", "content_path": "docs/firm.md",
        "parent_entity_type": "firm", "parent_entity_id": "chrisai",
    })
    create_document(conn, "chrisai", {
        "name": "Member Doc", "type": "notes", "content_path": "docs/quill.md",
        "parent_entity_type": "member", "parent_entity_id": member["id"],
    })

    firm_docs = list_documents(conn, "chrisai", parent_type="firm", parent_id="chrisai")
    assert len(firm_docs) == 1
    assert firm_docs[0]["name"] == "Firm Doc"


def test_list_documents_by_status() -> None:
    conn = _fresh_conn()
    create_document(conn, "chrisai", {
        "name": "Active Doc", "type": "spec", "content_path": "docs/active.md",
        "parent_entity_type": "firm", "parent_entity_id": "chrisai",
    })
    doc2 = create_document(conn, "chrisai", {
        "name": "Archived Doc", "type": "notes", "content_path": "docs/archived.md",
        "parent_entity_type": "firm", "parent_entity_id": "chrisai",
    })
    update_document(conn, doc2["id"], {"status": "archived"})

    active = list_documents(conn, "chrisai", status="active")
    assert len(active) == 1
    assert active[0]["name"] == "Active Doc"

    archived = list_documents(conn, "chrisai", status="archived")
    assert len(archived) == 1
    assert archived[0]["name"] == "Archived Doc"


def test_view_document() -> None:
    conn = _fresh_conn()
    created = create_document(
        conn,
        "chrisai",
        {
            "name": "View Me",
            "type": "design",
            "content_path": "docs/view.md",
            "parent_entity_type": "firm",
            "parent_entity_id": "chrisai",
        },
    )
    viewed = view_document(conn, created["id"])
    assert viewed["id"] == created["id"]
    assert viewed["name"] == "View Me"


# ---------------------------------------------------------------------------
# AC-4: Document update with status transition and version tracking
# ---------------------------------------------------------------------------


def test_update_document_status_transition() -> None:
    conn = _fresh_conn()
    doc = create_document(
        conn,
        "chrisai",
        {
            "name": "Lifecycle Doc",
            "type": "plan",
            "content_path": "docs/lifecycle.md",
            "parent_entity_type": "firm",
            "parent_entity_id": "chrisai",
        },
    )
    updated = update_document(conn, doc["id"], {"status": "archived"})
    assert updated["status"] == "archived"

    # Verify status_transition Records
    records = find(conn, "records", firm_id="chrisai")
    transition = [r for r in records if r["event_type"] == "document.status_transition"]
    assert len(transition) == 1
    assert transition[0]["target_entity_id"] == doc["id"]


def test_update_document_version_bump() -> None:
    conn = _fresh_conn()
    doc = create_document(
        conn,
        "chrisai",
        {
            "name": "Versioned Doc",
            "type": "spec",
            "content_path": "docs/v1.md",
            "parent_entity_type": "firm",
            "parent_entity_id": "chrisai",
        },
    )
    assert doc["version"] == 1

    updated = update_document(conn, doc["id"], {"content_path": "docs/v2.md"})
    assert updated["version"] == 2
    assert updated["content_path"] == "docs/v2.md"

    # Verify document.updated Records
    records = find(conn, "records", firm_id="chrisai")
    update_records = [r for r in records if r["event_type"] == "document.updated"]
    assert len(update_records) == 1


def test_update_document_invalid_status() -> None:
    conn = _fresh_conn()
    doc = create_document(
        conn,
        "chrisai",
        {
            "name": "Status Doc",
            "type": "notes",
            "content_path": "docs/status.md",
            "parent_entity_type": "firm",
            "parent_entity_id": "chrisai",
        },
    )
    with pytest.raises(ValueError, match="Invalid status"):
        update_document(conn, doc["id"], {"status": "deleted"})
