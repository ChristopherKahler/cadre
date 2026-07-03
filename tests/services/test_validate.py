"""Tests for firm.services._validate — shared validation helpers."""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.services._validate import (
    require_exists,
    validate_fk,
    validate_parent_ref,
    validate_status,
)


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


# ---------------------------------------------------------------------------
# require_exists
# ---------------------------------------------------------------------------


def test_require_exists_returns_row() -> None:
    conn = _fresh_conn()
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai", "name": "Quill", "role": "Writer",
    })
    row = require_exists(conn, "member", "MEM-001")
    assert row["id"] == "MEM-001"
    assert row["name"] == "Quill"


def test_require_exists_raises_on_missing() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="member 'MEM-999' not found"):
        require_exists(conn, "member", "MEM-999")


# ---------------------------------------------------------------------------
# validate_status
# ---------------------------------------------------------------------------


def test_validate_status_passes_valid() -> None:
    validate_status("active", ["active", "paused", "retired"])


def test_validate_status_raises_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid status 'deleted'"):
        validate_status("deleted", ["active", "paused", "retired"])


def test_validate_status_error_lists_allowed() -> None:
    with pytest.raises(ValueError, match="active, paused, retired"):
        validate_status("bad", ["active", "paused", "retired"])


# ---------------------------------------------------------------------------
# validate_parent_ref
# ---------------------------------------------------------------------------


def test_validate_parent_ref_valid() -> None:
    conn = _fresh_conn()
    row = validate_parent_ref(conn, "firm", "chrisai")
    assert row["id"] == "chrisai"


def test_validate_parent_ref_invalid_type() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="Invalid entity type"):
        validate_parent_ref(conn, "nonexistent", "chrisai")


def test_validate_parent_ref_missing_target() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="member 'MEM-999' not found"):
        validate_parent_ref(conn, "member", "MEM-999")


def test_validate_parent_ref_accepts_all_polymorphic_types() -> None:
    """All common polymorphic types are accepted (type check only, not existence)."""
    conn = _fresh_conn()
    # Just test the type validation, not existence
    for entity_type in ["firm", "member", "operation", "project", "unit", "goal", "gate", "document"]:
        # These will fail on existence (except firm), but should NOT fail on type
        if entity_type == "firm":
            validate_parent_ref(conn, entity_type, "chrisai")
        else:
            with pytest.raises(ValueError, match="not found"):
                validate_parent_ref(conn, entity_type, "NONEXISTENT")


# ---------------------------------------------------------------------------
# validate_fk
# ---------------------------------------------------------------------------


def test_validate_fk_none_returns_none() -> None:
    conn = _fresh_conn()
    assert validate_fk(conn, "member", None) is None


def test_validate_fk_valid_returns_row() -> None:
    conn = _fresh_conn()
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai", "name": "Quill", "role": "Writer",
    })
    row = validate_fk(conn, "member", "MEM-001")
    assert row is not None
    assert row["id"] == "MEM-001"


def test_validate_fk_missing_raises() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="member 'MEM-999' not found"):
        validate_fk(conn, "member", "MEM-999")
