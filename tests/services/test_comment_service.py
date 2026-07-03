"""Tests for firm.services.comment — Comment entity service (immutable)."""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find
from firm.services.comment import create_comment, list_comments, view_comment
from firm.services.member import create_member


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


# ---------------------------------------------------------------------------
# AC-1: Comment create with parent validation, author, reply threading, Records
# ---------------------------------------------------------------------------


def test_create_comment() -> None:
    conn = _fresh_conn()
    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    comment = create_comment(
        conn,
        "chrisai",
        {
            "parent_entity_type": "member",
            "parent_entity_id": member["id"],
            "author_type": "board",
            "body": "Welcome to the firm, Quill.",
        },
    )
    assert comment["id"] == "COM-001"
    assert comment["body"] == "Welcome to the firm, Quill."
    assert comment["parent_entity_type"] == "member"
    assert comment["parent_entity_id"] == member["id"]
    assert comment["author_type"] == "board"

    # Verify Records entry
    records = find(conn, "records", firm_id="chrisai")
    events = [r["event_type"] for r in records]
    assert "comment.created" in events
    comment_record = [r for r in records if r["event_type"] == "comment.created"][0]
    assert comment_record["target_entity_id"] == "COM-001"


def test_create_comment_with_reply() -> None:
    conn = _fresh_conn()
    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    parent_comment = create_comment(
        conn,
        "chrisai",
        {
            "parent_entity_type": "member",
            "parent_entity_id": member["id"],
            "author_type": "board",
            "body": "Initial comment.",
        },
    )
    reply = create_comment(
        conn,
        "chrisai",
        {
            "parent_entity_type": "member",
            "parent_entity_id": member["id"],
            "author_type": "member",
            "author_id": member["id"],
            "body": "Reply to initial.",
            "in_reply_to": parent_comment["id"],
        },
    )
    assert reply["id"] == "COM-002"
    assert reply["in_reply_to"] == parent_comment["id"]
    assert reply["author_type"] == "member"
    assert reply["author_id"] == member["id"]


def test_create_comment_invalid_parent() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="not found"):
        create_comment(
            conn,
            "chrisai",
            {
                "parent_entity_type": "member",
                "parent_entity_id": "MEM-999",
                "author_type": "board",
                "body": "Should fail.",
            },
        )


def test_create_comment_invalid_reply() -> None:
    conn = _fresh_conn()
    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    with pytest.raises(ValueError, match="not found"):
        create_comment(
            conn,
            "chrisai",
            {
                "parent_entity_type": "member",
                "parent_entity_id": member["id"],
                "author_type": "board",
                "body": "Reply to nothing.",
                "in_reply_to": "COM-999",
            },
        )


def test_create_comment_missing_body() -> None:
    conn = _fresh_conn()
    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    with pytest.raises(ValueError, match="'body' is required"):
        create_comment(
            conn,
            "chrisai",
            {
                "parent_entity_type": "member",
                "parent_entity_id": member["id"],
                "author_type": "board",
            },
        )


# ---------------------------------------------------------------------------
# AC-2: Comment list and view with parent filtering
# ---------------------------------------------------------------------------


def test_list_comments_by_parent() -> None:
    conn = _fresh_conn()
    m1 = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    m2 = create_member(conn, "chrisai", {"name": "Sterling", "role": "CMO"})

    create_comment(conn, "chrisai", {
        "parent_entity_type": "member", "parent_entity_id": m1["id"],
        "author_type": "board", "body": "Comment on Quill.",
    })
    create_comment(conn, "chrisai", {
        "parent_entity_type": "member", "parent_entity_id": m2["id"],
        "author_type": "board", "body": "Comment on Sterling.",
    })

    quill_comments = list_comments(conn, "chrisai", parent_type="member", parent_id=m1["id"])
    assert len(quill_comments) == 1
    assert quill_comments[0]["body"] == "Comment on Quill."

    all_comments = list_comments(conn, "chrisai")
    assert len(all_comments) == 2


def test_view_comment() -> None:
    conn = _fresh_conn()
    member = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    created = create_comment(
        conn,
        "chrisai",
        {
            "parent_entity_type": "member",
            "parent_entity_id": member["id"],
            "author_type": "board",
            "body": "View this comment.",
        },
    )
    viewed = view_comment(conn, created["id"])
    assert viewed["id"] == created["id"]
    assert viewed["body"] == "View this comment."
