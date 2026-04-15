"""Tests for firm.core.units — atomic Unit checkout and cycle detection."""

from __future__ import annotations

import sqlite3

import pytest

from firm.core import repo
from firm.core.migrate import apply_migrations
from firm.core.units import (
    CycleError,
    checkout,
    create_with_deps,
    release,
    set_dependencies,
    validate_no_cycle,
)


def _seeded_conn() -> sqlite3.Connection:
    """Connection with migrations + one firm + one member + one operation + one project."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    repo.create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    repo.create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai", "name": "Quill", "role": "Writer",
    })
    repo.create(conn, "member", {
        "id": "MEM-002", "firm_id": "chrisai", "name": "Other", "role": "Writer",
    })
    repo.create(conn, "operation", {
        "id": "OPS-001", "firm_id": "chrisai", "name": "Blog",
    })
    repo.create(conn, "project", {
        "id": "PROJ-001", "firm_id": "chrisai", "operation_id": "OPS-001",
        "name": "Blog v1", "status": "in_progress", "due_date": "2026-12-31",
    })
    return conn


def _make_unit(conn: sqlite3.Connection, unit_id: str, **overrides) -> dict:
    data = {
        "id": unit_id,
        "firm_id": "chrisai",
        "project_id": "PROJ-001",
        "name": f"Unit {unit_id}",
    }
    data.update(overrides)
    return repo.create(conn, "unit", data)


# ---------------------------------------------------------------------------
# AC-4: Atomic checkout
# ---------------------------------------------------------------------------

def test_checkout_succeeds_when_unclaimed() -> None:
    conn = _seeded_conn()
    try:
        _make_unit(conn, "UNIT-001")
        claimed = checkout(conn, "UNIT-001", "MEM-001")
        assert claimed is not None
        assert claimed["claimed_by"] == "MEM-001"
        assert claimed["claimed_at"] is not None
        assert claimed["status"] == "in_progress"  # pending → in_progress
    finally:
        conn.close()


def test_checkout_returns_none_when_already_claimed() -> None:
    conn = _seeded_conn()
    try:
        _make_unit(conn, "UNIT-001")
        first = checkout(conn, "UNIT-001", "MEM-001")
        assert first is not None
        second = checkout(conn, "UNIT-001", "MEM-002")
        assert second is None
        # Claim unchanged
        row = repo.get(conn, "unit", "UNIT-001")
        assert row is not None
        assert row["claimed_by"] == "MEM-001"
    finally:
        conn.close()


def test_checkout_preserves_status_when_not_pending() -> None:
    conn = _seeded_conn()
    try:
        _make_unit(conn, "UNIT-001", status="blocked")
        claimed = checkout(conn, "UNIT-001", "MEM-001")
        assert claimed is not None
        assert claimed["claimed_by"] == "MEM-001"
        assert claimed["status"] == "blocked"  # preserved
    finally:
        conn.close()


def test_checkout_nonexistent_unit_returns_none() -> None:
    conn = _seeded_conn()
    try:
        result = checkout(conn, "UNIT-GHOST", "MEM-001")
        assert result is None
    finally:
        conn.close()


def test_checkout_fake_member_raises_integrityerror() -> None:
    conn = _seeded_conn()
    try:
        _make_unit(conn, "UNIT-001")
        with pytest.raises(sqlite3.IntegrityError):
            checkout(conn, "UNIT-001", "MEM-NOT-REAL")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------

def test_release_clears_claim() -> None:
    conn = _seeded_conn()
    try:
        _make_unit(conn, "UNIT-001")
        checkout(conn, "UNIT-001", "MEM-001")
        released = release(conn, "UNIT-001")
        assert released is not None
        assert released["claimed_by"] is None
        assert released["claimed_at"] is None
    finally:
        conn.close()


def test_release_does_not_revert_status() -> None:
    conn = _seeded_conn()
    try:
        _make_unit(conn, "UNIT-001")
        checkout(conn, "UNIT-001", "MEM-001")  # pending → in_progress
        released = release(conn, "UNIT-001")
        assert released is not None
        assert released["status"] == "in_progress"  # status unchanged by release
    finally:
        conn.close()


def test_release_nonexistent_returns_none() -> None:
    conn = _seeded_conn()
    try:
        assert release(conn, "UNIT-GHOST") is None
    finally:
        conn.close()


def test_reclaim_after_release_works() -> None:
    """After release, another Member should be able to claim."""
    conn = _seeded_conn()
    try:
        _make_unit(conn, "UNIT-001")
        checkout(conn, "UNIT-001", "MEM-001")
        release(conn, "UNIT-001")
        claimed = checkout(conn, "UNIT-001", "MEM-002")
        assert claimed is not None
        assert claimed["claimed_by"] == "MEM-002"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-5: Cycle detection
# ---------------------------------------------------------------------------

def test_validate_no_cycle_accepts_simple_chain() -> None:
    """A → B → C: no cycle."""
    conn = _seeded_conn()
    try:
        _make_unit(conn, "UNIT-A")
        _make_unit(conn, "UNIT-B", depends_on=["UNIT-A"])
        # Now propose UNIT-C depending on UNIT-B — no cycle
        validate_no_cycle(conn, "UNIT-C", ["UNIT-B"])
    finally:
        conn.close()


def test_validate_no_cycle_detects_self_loop() -> None:
    conn = _seeded_conn()
    try:
        with pytest.raises(CycleError):
            validate_no_cycle(conn, "UNIT-A", ["UNIT-A"])
    finally:
        conn.close()


def test_validate_no_cycle_detects_indirect_cycle() -> None:
    """Existing: UNIT-A depends on UNIT-C, UNIT-B depends on UNIT-A.
    Proposed: UNIT-C depends on UNIT-B → cycle: C → B → A → C."""
    conn = _seeded_conn()
    try:
        _make_unit(conn, "UNIT-C")
        _make_unit(conn, "UNIT-A", depends_on=["UNIT-C"])
        _make_unit(conn, "UNIT-B", depends_on=["UNIT-A"])
        with pytest.raises(CycleError) as exc_info:
            validate_no_cycle(conn, "UNIT-C", ["UNIT-B"])
        msg = str(exc_info.value)
        assert "UNIT-C" in msg
        assert "UNIT-B" in msg
        assert "UNIT-A" in msg
    finally:
        conn.close()


def test_validate_no_cycle_handles_missing_unit_gracefully() -> None:
    """Depending on a unit that doesn't exist is a soft-ref — no cycle, no crash."""
    conn = _seeded_conn()
    try:
        validate_no_cycle(conn, "UNIT-A", ["UNIT-GHOST", "UNIT-ALSO-GHOST"])
    finally:
        conn.close()


def test_create_with_deps_blocks_cycles() -> None:
    conn = _seeded_conn()
    try:
        _make_unit(conn, "UNIT-A")
        _make_unit(conn, "UNIT-B", depends_on=["UNIT-A"])
        # Try to create UNIT-X with deps that loop back
        with pytest.raises(CycleError):
            create_with_deps(conn, {
                "id": "UNIT-A",  # same id as existing; cycle check hits self-loop via deps
                "firm_id": "chrisai",
                "project_id": "PROJ-001",
                "name": "Cyclic",
                "depends_on": ["UNIT-A"],
            })
        # UNIT-A row should still be the original (unchanged), not a second row
        rows = repo.find(conn, "unit", id="UNIT-A")
        assert len(rows) == 1
    finally:
        conn.close()


def test_create_with_deps_allows_valid_chain() -> None:
    conn = _seeded_conn()
    try:
        _make_unit(conn, "UNIT-A")
        _make_unit(conn, "UNIT-B", depends_on=["UNIT-A"])
        created = create_with_deps(conn, {
            "id": "UNIT-C",
            "firm_id": "chrisai",
            "project_id": "PROJ-001",
            "name": "Third",
            "depends_on": ["UNIT-B"],
        })
        assert created["id"] == "UNIT-C"
        assert created["depends_on"] == ["UNIT-B"]
    finally:
        conn.close()


def test_set_dependencies_updates_and_validates() -> None:
    conn = _seeded_conn()
    try:
        _make_unit(conn, "UNIT-A")
        _make_unit(conn, "UNIT-B")
        _make_unit(conn, "UNIT-C", depends_on=["UNIT-B"])
        # Valid update: C now depends on A also
        updated = set_dependencies(conn, "UNIT-C", ["UNIT-A", "UNIT-B"])
        assert updated is not None
        assert set(updated["depends_on"]) == {"UNIT-A", "UNIT-B"}

        # Now try to set a cycle: UNIT-A depends on UNIT-C → cycle
        with pytest.raises(CycleError):
            set_dependencies(conn, "UNIT-A", ["UNIT-C"])

        # UNIT-A should still have no deps
        row = repo.get(conn, "unit", "UNIT-A")
        assert row is not None
        assert row["depends_on"] in (None, [])
    finally:
        conn.close()


def test_create_with_deps_requires_id() -> None:
    conn = _seeded_conn()
    try:
        with pytest.raises(ValueError):
            create_with_deps(conn, {
                "firm_id": "chrisai",
                "project_id": "PROJ-001",
                "name": "No ID",
            })
    finally:
        conn.close()
