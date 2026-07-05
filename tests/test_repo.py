"""Tests for firm.core.repo — the generic CRUD repository."""

from __future__ import annotations

import sqlite3
import time

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import (
    ALL_TABLES,
    IMMUTABLE_TABLES,
    ImmutableTableError,
    create,
    delete,
    get,
    find,
    update,
)


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    # Seed a firm so member/etc inserts have a valid FK target
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


# ---------------------------------------------------------------------------
# AC-1: basic CRUD round-trip
# ---------------------------------------------------------------------------

def test_create_and_get_roundtrip() -> None:
    conn = _fresh_conn()
    try:
        created = create(
            conn,
            "member",
            {
                "id": "MEM-001",
                "firm_id": "chrisai",
                "name": "Quill",
                "role": "Writer",
            },
        )
        assert created["id"] == "MEM-001"
        assert created["name"] == "Quill"
        assert created["created_at"] is not None
        assert created["updated_at"] is not None

        fetched = get(conn, "member", "MEM-001")
        assert fetched is not None
        assert fetched["name"] == "Quill"
    finally:
        conn.close()


def test_get_returns_none_for_missing() -> None:
    conn = _fresh_conn()
    try:
        assert get(conn, "member", "MEM-DOES-NOT-EXIST") is None
    finally:
        conn.close()


def test_list_with_filters() -> None:
    conn = _fresh_conn()
    try:
        create(conn, "firm", {"id": "other", "name": "Other"})
        create(conn, "member", {
            "id": "MEM-001", "firm_id": "chrisai", "name": "A", "role": "R",
        })
        create(conn, "member", {
            "id": "MEM-002", "firm_id": "chrisai", "name": "B", "role": "R",
        })
        create(conn, "member", {
            "id": "MEM-003", "firm_id": "other", "name": "C", "role": "R",
        })

        chrisai_members = find(conn, "member", firm_id="chrisai")
        ids = {m["id"] for m in chrisai_members}
        assert ids == {"MEM-001", "MEM-002"}
    finally:
        conn.close()


def test_list_filter_none_matches_null() -> None:
    conn = _fresh_conn()
    try:
        # reports_to_member_id is nullable; default is NULL
        create(conn, "member", {
            "id": "MEM-001", "firm_id": "chrisai", "name": "Q", "role": "R",
        })
        create(conn, "member", {
            "id": "MEM-002", "firm_id": "chrisai", "name": "R", "role": "R",
            "reports_to_member_id": "MEM-001",
        })
        top_level = find(conn, "member", reports_to_member_id=None)
        assert {m["id"] for m in top_level} == {"MEM-001"}
    finally:
        conn.close()


def test_list_orders_by_created_at() -> None:
    conn = _fresh_conn()
    try:
        create(conn, "member", {
            "id": "MEM-003", "firm_id": "chrisai", "name": "Third", "role": "R",
        })
        # created_at default is datetime('now') which has second resolution;
        # sleep to ensure ordering is not a tie.
        time.sleep(1.1)
        create(conn, "member", {
            "id": "MEM-001", "firm_id": "chrisai", "name": "First", "role": "R",
        })
        time.sleep(1.1)
        create(conn, "member", {
            "id": "MEM-002", "firm_id": "chrisai", "name": "Second", "role": "R",
        })
        ordered = find(conn, "member", firm_id="chrisai")
        names = [m["name"] for m in ordered]
        assert names == ["Third", "First", "Second"]
    finally:
        conn.close()


def test_delete_returns_rowcount() -> None:
    conn = _fresh_conn()
    try:
        create(conn, "member", {
            "id": "MEM-001", "firm_id": "chrisai", "name": "Q", "role": "R",
        })
        assert delete(conn, "member", "MEM-001") == 1
        assert delete(conn, "member", "MEM-001") == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-2: JSON column round-trip
# ---------------------------------------------------------------------------

def test_json_column_roundtrip_list() -> None:
    conn = _fresh_conn()
    try:
        skills = ["humanizer", "blog:write", "blog:repurpose"]
        created = create(conn, "member", {
            "id": "MEM-001", "firm_id": "chrisai", "name": "Q", "role": "R",
            "suggested_skills": skills,
        })
        assert created["suggested_skills"] == skills

        fetched = get(conn, "member", "MEM-001")
        assert fetched is not None
        assert fetched["suggested_skills"] == skills
    finally:
        conn.close()


def test_json_column_roundtrip_dict() -> None:
    conn = _fresh_conn()
    try:
        create(conn, "member", {
            "id": "MEM-001", "firm_id": "chrisai", "name": "Q", "role": "R",
        })
        create(conn, "operation", {
            "id": "OPS-001", "firm_id": "chrisai", "name": "E",
        })
        create(conn, "project", {
            "id": "PROJ-001", "firm_id": "chrisai", "operation_id": "OPS-001",
            "name": "P", "status": "in_progress", "due_date": "2026-12-31",
        })
        acs = [
            {"id": "AC-1", "condition": "Min 1500 words", "resolved": False, "resolved_by": None},
            {"id": "AC-2", "condition": "CTA placed", "resolved": True, "resolved_by": "UNIT-002"},
        ]
        created = create(conn, "unit", {
            "id": "UNIT-001", "firm_id": "chrisai", "project_id": "PROJ-001",
            "name": "Write post",
            "acceptance_criteria": acs,
        })
        assert created["acceptance_criteria"] == acs

        fetched = get(conn, "unit", "UNIT-001")
        assert fetched is not None
        assert fetched["acceptance_criteria"] == acs
    finally:
        conn.close()


def test_json_column_null_roundtrips_none() -> None:
    conn = _fresh_conn()
    try:
        # suggested_skills not set → stays NULL → returns None
        created = create(conn, "member", {
            "id": "MEM-001", "firm_id": "chrisai", "name": "Q", "role": "R",
        })
        assert created["suggested_skills"] is None
        fetched = get(conn, "member", "MEM-001")
        assert fetched is not None
        assert fetched["suggested_skills"] is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-3: updated_at auto-touch + immutable rejection
# ---------------------------------------------------------------------------

def test_update_touches_updated_at() -> None:
    conn = _fresh_conn()
    try:
        created = create(conn, "member", {
            "id": "MEM-001", "firm_id": "chrisai", "name": "Q", "role": "R",
        })
        original_updated_at = created["updated_at"]
        time.sleep(1.1)  # datetime('now') has second resolution
        updated = update(conn, "member", "MEM-001", {"role": "Editor"})
        assert updated is not None
        assert updated["role"] == "Editor"
        assert updated["updated_at"] > original_updated_at
    finally:
        conn.close()


def test_update_missing_id_returns_none() -> None:
    conn = _fresh_conn()
    try:
        assert update(conn, "member", "MEM-DOES-NOT-EXIST", {"role": "X"}) is None
    finally:
        conn.close()


def test_update_immutable_table_raises() -> None:
    conn = _fresh_conn()
    try:
        # Seed a comment first, then try to update
        create(conn, "member", {
            "id": "MEM-001", "firm_id": "chrisai", "name": "Q", "role": "R",
        })
        create(conn, "operation", {
            "id": "OPS-001", "firm_id": "chrisai", "name": "E",
        })
        create(conn, "project", {
            "id": "PROJ-001", "firm_id": "chrisai", "operation_id": "OPS-001",
            "name": "P", "status": "in_progress", "due_date": "2026-12-31",
        })
        create(conn, "unit", {
            "id": "UNIT-001", "firm_id": "chrisai", "project_id": "PROJ-001",
            "name": "U",
        })
        create(conn, "comment", {
            "id": "COM-001", "firm_id": "chrisai",
            "parent_entity_type": "unit", "parent_entity_id": "UNIT-001",
            "author_type": "member", "author_id": "MEM-001",
            "body": "original",
        })
        with pytest.raises(ImmutableTableError):
            update(conn, "comment", "COM-001", {"body": "mutated"})
    finally:
        conn.close()


def test_delete_immutable_raises_via_trigger() -> None:
    conn = _fresh_conn()
    try:
        create(conn, "member", {
            "id": "MEM-001", "firm_id": "chrisai", "name": "Q", "role": "R",
        })
        create(conn, "operation", {
            "id": "OPS-001", "firm_id": "chrisai", "name": "E",
        })
        create(conn, "project", {
            "id": "PROJ-001", "firm_id": "chrisai", "operation_id": "OPS-001",
            "name": "P", "status": "in_progress", "due_date": "2026-12-31",
        })
        create(conn, "unit", {
            "id": "UNIT-001", "firm_id": "chrisai", "project_id": "PROJ-001",
            "name": "U",
        })
        create(conn, "records", {
            "id": "LOG-001", "firm_id": "chrisai",
            "event_type": "unit.status_transition",
            "actor_type": "member", "actor_id": "MEM-001",
            "target_entity_type": "unit", "target_entity_id": "UNIT-001",
        })
        with pytest.raises(sqlite3.IntegrityError):
            delete(conn, "records", "LOG-001")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Injection / misuse guards
# ---------------------------------------------------------------------------

def test_unknown_table_raises_valueerror() -> None:
    conn = _fresh_conn()
    try:
        with pytest.raises(ValueError):
            create(conn, "haxxor; DROP TABLE member;--", {"id": "X"})
        with pytest.raises(ValueError):
            get(conn, "haxxor", "X")
        with pytest.raises(ValueError):
            update(conn, "haxxor", "X", {"name": "N"})
        with pytest.raises(ValueError):
            delete(conn, "haxxor", "X")
        with pytest.raises(ValueError):
            find(conn, "haxxor")
    finally:
        conn.close()


def test_unknown_column_in_create_raises_valueerror() -> None:
    conn = _fresh_conn()
    try:
        with pytest.raises(ValueError):
            create(conn, "member", {
                "id": "MEM-X", "firm_id": "chrisai",
                "name": "N", "role": "R",
                "not_a_real_column": 42,
            })
    finally:
        conn.close()


def test_unknown_column_in_list_raises_valueerror() -> None:
    conn = _fresh_conn()
    try:
        with pytest.raises(ValueError):
            find(conn, "member", bogus_filter="x")
    finally:
        conn.close()


def test_create_without_id_raises() -> None:
    conn = _fresh_conn()
    try:
        with pytest.raises(ValueError):
            create(conn, "member", {
                "firm_id": "chrisai", "name": "N", "role": "R",
            })
    finally:
        conn.close()


def test_registries_cover_all_tables() -> None:
    """Sanity: IMMUTABLE_TABLES is a subset of ALL_TABLES; JSON_COLUMNS keys
    are all in ALL_TABLES."""
    from firm.core.repo import JSON_COLUMNS
    assert IMMUTABLE_TABLES <= ALL_TABLES
    assert set(JSON_COLUMNS.keys()) <= ALL_TABLES
    assert len(ALL_TABLES) == 16  # 14 entity tables + budget_period (003_pulse) + escalation (004)
