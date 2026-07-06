"""Schema tests for migration 002_entities.

Verifies AC-1..AC-5 from Plan 01-02:
- All 14 entity tables exist
- Firm-scoped FKs enforce with ON DELETE CASCADE
- Immutable tables (comment, records, usage_event) reject UPDATE and DELETE
- CHECK constraints on enum-like status fields reject invalid values
- Required indexes exist
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from firm.core.migrate import apply_migrations


EXPECTED_ENTITY_TABLES = {
    "firm",
    "contract",
    "member",
    "goal",
    "operation",
    "project",
    "unit",
    "comment",
    "member_run",
    "usage_event",
    "gate",
    "records",
    "firm_secret",
    "document",
    "budget_period",
    "escalation",
    "pulse_lock",
    "pulse_request",
}


def _fresh_migrated_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    return conn


def _seed_firm(conn: sqlite3.Connection, id: str = "chrisai") -> str:
    conn.execute(
        "INSERT INTO firm (id, name) VALUES (?, ?)", (id, f"Firm {id}")
    )
    return id


def _insert(conn: sqlite3.Connection, table: str, **kwargs: Any) -> None:
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    conn.execute(sql, tuple(kwargs.values()))


# ---------------------------------------------------------------------------
# AC-1: All 14 entity tables exist
# ---------------------------------------------------------------------------

def test_all_14_entity_tables_exist() -> None:
    conn = _fresh_migrated_conn()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE '\\_%' ESCAPE '\\'"
        ).fetchall()
        names = {r[0] for r in rows}
        assert names == EXPECTED_ENTITY_TABLES, (
            f"Missing: {EXPECTED_ENTITY_TABLES - names}  "
            f"Unexpected: {names - EXPECTED_ENTITY_TABLES}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-2: Firm-scoped FKs enforce with ON DELETE CASCADE
# ---------------------------------------------------------------------------

def test_member_insert_with_unknown_firm_id_fails() -> None:
    conn = _fresh_migrated_conn()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _insert(
                conn,
                "member",
                id="MEM-001",
                firm_id="nope",
                name="Orphan",
                role="Worker",
            )
    finally:
        conn.close()


def test_firm_delete_cascades_to_scoped_entities() -> None:
    conn = _fresh_migrated_conn()
    try:
        _seed_firm(conn)
        _insert(
            conn, "member",
            id="MEM-001", firm_id="chrisai", name="Quill", role="Writer",
        )
        _insert(
            conn, "operation",
            id="OPS-001", firm_id="chrisai", name="Blog Engine",
        )
        _insert(
            conn, "project",
            id="PROJ-001", firm_id="chrisai", operation_id="OPS-001",
            name="Blog v1", status="in_progress", due_date="2026-12-31",
        )
        _insert(
            conn, "unit",
            id="UNIT-001", firm_id="chrisai", project_id="PROJ-001",
            name="First post",
        )

        conn.execute("DELETE FROM firm WHERE id = 'chrisai'")

        for table in ("member", "operation", "project", "unit"):
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            assert count == 0, f"{table} was not cascade-deleted"
    finally:
        conn.close()


def test_project_requires_existing_operation() -> None:
    conn = _fresh_migrated_conn()
    try:
        _seed_firm(conn)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(
                conn, "project",
                id="PROJ-001", firm_id="chrisai", operation_id="OPS-DOES-NOT-EXIST",
                name="Bad", status="in_progress", due_date="2026-12-31",
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-3: Immutable tables reject UPDATE and DELETE
# ---------------------------------------------------------------------------

def _seed_minimal_for_immutables(conn: sqlite3.Connection) -> None:
    _seed_firm(conn)
    _insert(
        conn, "member",
        id="MEM-001", firm_id="chrisai", name="Quill", role="Writer",
    )
    _insert(
        conn, "operation",
        id="OPS-001", firm_id="chrisai", name="Engine",
    )
    _insert(
        conn, "project",
        id="PROJ-001", firm_id="chrisai", operation_id="OPS-001",
        name="P", status="in_progress", due_date="2026-12-31",
    )
    _insert(
        conn, "unit",
        id="UNIT-001", firm_id="chrisai", project_id="PROJ-001", name="U",
    )
    _insert(
        conn, "member_run",
        id="RUN-001", firm_id="chrisai", member_id="MEM-001",
        unit_id="UNIT-001", status="running",
        started_at="2026-04-15T10:00:00-05:00",
    )


def test_comment_update_rejected() -> None:
    conn = _fresh_migrated_conn()
    try:
        _seed_minimal_for_immutables(conn)
        _insert(
            conn, "comment",
            id="COM-001", firm_id="chrisai",
            parent_entity_type="unit", parent_entity_id="UNIT-001",
            author_type="member", author_id="MEM-001",
            body="original body",
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE comment SET body = 'mutated' WHERE id = 'COM-001'"
            )
        body = conn.execute(
            "SELECT body FROM comment WHERE id = 'COM-001'"
        ).fetchone()[0]
        assert body == "original body"
    finally:
        conn.close()


def test_comment_delete_rejected() -> None:
    conn = _fresh_migrated_conn()
    try:
        _seed_minimal_for_immutables(conn)
        _insert(
            conn, "comment",
            id="COM-001", firm_id="chrisai",
            parent_entity_type="unit", parent_entity_id="UNIT-001",
            author_type="board", author_id=None,
            body="approved",
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM comment WHERE id = 'COM-001'")
        count = conn.execute(
            "SELECT COUNT(*) FROM comment WHERE id = 'COM-001'"
        ).fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_records_update_rejected() -> None:
    conn = _fresh_migrated_conn()
    try:
        _seed_minimal_for_immutables(conn)
        _insert(
            conn, "records",
            id="LOG-001", firm_id="chrisai",
            event_type="unit.status_transition",
            actor_type="member", actor_id="MEM-001",
            target_entity_type="unit", target_entity_id="UNIT-001",
            details='{"from":"pending","to":"in_progress"}',
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE records SET event_type = 'whatever' WHERE id = 'LOG-001'"
            )
    finally:
        conn.close()


def test_records_delete_rejected() -> None:
    conn = _fresh_migrated_conn()
    try:
        _seed_minimal_for_immutables(conn)
        _insert(
            conn, "records",
            id="LOG-001", firm_id="chrisai",
            event_type="e", actor_type="system", actor_id=None,
            target_entity_type="unit", target_entity_id="UNIT-001",
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM records WHERE id = 'LOG-001'")
    finally:
        conn.close()


def test_usage_event_update_rejected() -> None:
    conn = _fresh_migrated_conn()
    try:
        _seed_minimal_for_immutables(conn)
        _insert(
            conn, "usage_event",
            id="USG-001", firm_id="chrisai", member_id="MEM-001",
            run_id="RUN-001", unit_id="UNIT-001",
            timestamp="2026-04-15T10:00:00-05:00", plan="api",
            tokens_in=100, tokens_out=50,
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE usage_event SET tokens_in = 999 WHERE id = 'USG-001'"
            )
    finally:
        conn.close()


def test_usage_event_delete_rejected() -> None:
    conn = _fresh_migrated_conn()
    try:
        _seed_minimal_for_immutables(conn)
        _insert(
            conn, "usage_event",
            id="USG-001", firm_id="chrisai", member_id="MEM-001",
            timestamp="2026-04-15T10:00:00-05:00", plan="api",
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM usage_event WHERE id = 'USG-001'")
    finally:
        conn.close()


def test_member_run_is_mutable() -> None:
    """member_run has a running→completed lifecycle and must remain mutable."""
    conn = _fresh_migrated_conn()
    try:
        _seed_minimal_for_immutables(conn)
        conn.execute(
            "UPDATE member_run SET status = 'completed', "
            "ended_at = '2026-04-15T10:15:00-05:00' "
            "WHERE id = 'RUN-001'"
        )
        status = conn.execute(
            "SELECT status FROM member_run WHERE id = 'RUN-001'"
        ).fetchone()[0]
        assert status == "completed"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-4: CHECK constraints
# ---------------------------------------------------------------------------

def test_unit_status_rejects_invalid_value() -> None:
    conn = _fresh_migrated_conn()
    try:
        _seed_minimal_for_immutables(conn)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(
                conn, "unit",
                id="UNIT-002", firm_id="chrisai", project_id="PROJ-001",
                name="Bad", status="banana",
            )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "status",
    ["pending", "in_progress", "blocked", "in_review", "done", "cancelled"],
)
def test_unit_status_accepts_valid_values(status: str) -> None:
    conn = _fresh_migrated_conn()
    try:
        _seed_minimal_for_immutables(conn)
        _insert(
            conn, "unit",
            id=f"UNIT-{status}", firm_id="chrisai", project_id="PROJ-001",
            name="Valid", status=status,
        )
    finally:
        conn.close()


def test_member_status_rejects_invalid_value() -> None:
    conn = _fresh_migrated_conn()
    try:
        _seed_firm(conn)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(
                conn, "member",
                id="MEM-X", firm_id="chrisai", name="X", role="X",
                status="vacationing",
            )
    finally:
        conn.close()


def test_project_status_rejects_invalid_value() -> None:
    conn = _fresh_migrated_conn()
    try:
        _seed_firm(conn)
        _insert(
            conn, "operation",
            id="OPS-001", firm_id="chrisai", name="E",
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert(
                conn, "project",
                id="PROJ-X", firm_id="chrisai", operation_id="OPS-001",
                name="X", status="not-real-status", due_date="2026-12-31",
            )
    finally:
        conn.close()


def test_gate_status_rejects_invalid_value() -> None:
    conn = _fresh_migrated_conn()
    try:
        _seed_firm(conn)
        _insert(
            conn, "member",
            id="MEM-001", firm_id="chrisai", name="M", role="R",
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert(
                conn, "gate",
                id="GATE-X", firm_id="chrisai",
                requesting_member_id="MEM-001",
                action="publish", target_entity_type="unit",
                target_entity_id="UNIT-Z",
                status="unknown",
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-5: Required indexes exist
# ---------------------------------------------------------------------------

REQUIRED_INDEXES = {
    # firm-scoped on every scoped table
    "idx_contract_firm_id",
    "idx_member_firm_id",
    "idx_goal_firm_id",
    "idx_operation_firm_id",
    "idx_project_firm_id",
    "idx_unit_firm_id",
    "idx_comment_firm_id",
    "idx_member_run_firm_id",
    "idx_usage_event_firm_id",
    "idx_gate_firm_id",
    "idx_records_firm_id",
    "idx_firm_secret_firm_id",
    "idx_document_firm_id",
    # polymorphic parent refs
    "idx_goal_parent",
    "idx_comment_parent",
    # work-filtering
    "idx_unit_status",
    "idx_unit_assignee",
    "idx_unit_claimed_by",
    "idx_unit_project",
    # run/usage attribution
    "idx_member_run_member",
    "idx_usage_event_member",
    "idx_usage_event_run",
}


def test_required_indexes_exist() -> None:
    conn = _fresh_migrated_conn()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        present = {r[0] for r in rows}
        missing = REQUIRED_INDEXES - present
        assert not missing, f"Missing required indexes: {missing}"
    finally:
        conn.close()
