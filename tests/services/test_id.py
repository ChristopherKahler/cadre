"""Tests for firm.services._id — unified ID generation."""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.services._id import PREFIX_REGISTRY, next_id


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


# ---------------------------------------------------------------------------
# AC-1: next_id generates correct prefixed sequential IDs
# ---------------------------------------------------------------------------


def test_first_id_is_001() -> None:
    conn = _fresh_conn()
    assert next_id(conn, "member", "chrisai") == "MEM-001"


def test_sequential_ids() -> None:
    conn = _fresh_conn()
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai", "name": "Quill", "role": "Writer",
    })
    assert next_id(conn, "member", "chrisai") == "MEM-002"
    create(conn, "member", {
        "id": "MEM-002", "firm_id": "chrisai", "name": "Sterling", "role": "CMO",
    })
    assert next_id(conn, "member", "chrisai") == "MEM-003"


def test_global_uniqueness_across_firms() -> None:
    """IDs are globally unique (not firm-scoped) since id is a PRIMARY KEY."""
    conn = _fresh_conn()
    create(conn, "firm", {"id": "otherfirm", "name": "Other"})
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai", "name": "Quill", "role": "Writer",
    })
    # Global count: 1 member exists, so next is MEM-002 regardless of firm_id
    assert next_id(conn, "member", "otherfirm") == "MEM-002"
    assert next_id(conn, "member", "chrisai") == "MEM-002"


def test_all_registered_prefixes() -> None:
    """Every registered table produces the expected prefix."""
    conn = _fresh_conn()
    for table, prefix in PREFIX_REGISTRY.items():
        result = next_id(conn, table, "chrisai")
        assert result == f"{prefix}-001", f"{table} -> {result}"


def test_unknown_table_raises() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="Unknown table"):
        next_id(conn, "nonexistent_table", "chrisai")


def test_sub_unit_prefix() -> None:
    conn = _fresh_conn()
    assert next_id(conn, "unit", "chrisai", is_sub_unit=True) == "SUB-001"
    assert next_id(conn, "unit", "chrisai", is_sub_unit=False) == "UNIT-001"


def test_sub_unit_shares_count_with_unit() -> None:
    """SUB and UNIT share the same unit table count."""
    conn = _fresh_conn()
    # Seed required parent entities for unit FK
    create(conn, "operation", {
        "id": "OPS-001", "firm_id": "chrisai", "name": "Ops",
        "status": "active",
    })
    create(conn, "project", {
        "id": "PROJ-001", "firm_id": "chrisai", "operation_id": "OPS-001",
        "name": "Proj", "status": "in_progress", "due_date": "2026-12-31",
    })
    create(conn, "unit", {
        "id": "UNIT-001", "firm_id": "chrisai", "project_id": "PROJ-001",
        "name": "U1", "status": "pending",
    })
    # After 1 unit, both UNIT and SUB should return -002
    assert next_id(conn, "unit", "chrisai") == "UNIT-002"
    assert next_id(conn, "unit", "chrisai", is_sub_unit=True) == "SUB-002"


def test_zero_padding() -> None:
    """IDs are zero-padded to 3 digits."""
    conn = _fresh_conn()
    result = next_id(conn, "member", "chrisai")
    assert result == "MEM-001"
    # The format is always 3+ digits
    parts = result.split("-")
    assert len(parts[1]) >= 3
