"""Unit tests for firm.hooks.unit_completion.on_unit_done().

Covers AC-1 through AC-4 from 02-03-PLAN:
    AC-1: records row written + matching AC flipped
    AC-2: no-match AC is a no-op on project
    AC-3: unit-not-found returns structured error, writes nothing
    AC-4: project-missing returns structured error + transactional rollback
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.hooks.unit_completion import on_unit_done


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    return conn


def _seed_minimal(
    conn: sqlite3.Connection,
    *,
    project_ac: list[dict[str, Any]] | None,
    unit_status: str = "in_progress",
    unit_id: str = "UNIT-100",
    project_id: str = "PRJ-010",
    member_id: str = "MEM-001",
) -> None:
    """Seed just enough rows to exercise unit-completion.

    Firm → Operation → Project (with AC list) → Member → Unit.
    """
    create(conn, "firm", {
        "id": "chrisai",
        "name": "ChrisAI",
        "operator": {"name": "Chris Kahler", "role": "Board / Founder"},
    })
    create(conn, "member", {
        "id": member_id, "firm_id": "chrisai",
        "name": "Quill", "role": "Blog Author",
    })
    create(conn, "operation", {
        "id": "OPS-001", "firm_id": "chrisai",
        "name": "Content Publishing",
    })
    create(conn, "project", {
        "id": project_id, "firm_id": "chrisai",
        "operation_id": "OPS-001",
        "name": "Q2 Blog Push",
        "status": "in_progress",
        "due_date": "2026-06-30",
        "acceptance_criteria": project_ac,
    })
    create(conn, "unit", {
        "id": unit_id, "firm_id": "chrisai",
        "project_id": project_id,
        "name": "Draft blog post #14",
        "status": unit_status,
        "assignee_member_id": member_id,
    })


def _records_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM records ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def _project_ac(conn: sqlite3.Connection, project_id: str) -> Any:
    row = conn.execute(
        "SELECT acceptance_criteria FROM project WHERE id = ?", (project_id,)
    ).fetchone()
    raw = row[0]
    if raw is None:
        return None
    return json.loads(raw)


# ---------------------------------------------------------------------------
# AC-1: happy path
# ---------------------------------------------------------------------------

def test_happy_path_flips_matching_ac() -> None:
    conn = _fresh_conn()
    try:
        _seed_minimal(conn, project_ac=[
            {"id": "AC-1", "text": "Schema compiles",
             "resolved": False, "resolved_by": "UNIT-100"},
            {"id": "AC-2", "text": "Tests pass",
             "resolved": False, "resolved_by": "UNIT-101"},
            {"id": "AC-3", "text": "Docs updated",
             "resolved": True, "resolved_by": "UNIT-100"},
        ])

        result = on_unit_done(
            conn, firm_id="chrisai",
            unit_id="UNIT-100", member_id="MEM-001",
            prior_status="in_progress",
        )

        assert result["ok"] is True
        assert result["resolved_ac_ids"] == ["AC-1"]
        assert result["records_id"] == "LOG-001"
        assert result["unit_id"] == "UNIT-100"
        assert result["project_id"] == "PRJ-010"

        ac_after = _project_ac(conn, "PRJ-010")
        assert ac_after[0]["resolved"] is True  # AC-1 flipped
        assert ac_after[0]["id"] == "AC-1"
        assert ac_after[1]["resolved"] is False  # AC-2 unchanged
        assert ac_after[2]["resolved"] is True  # AC-3 already-true stays

        records = _records_rows(conn)
        assert len(records) == 1
        rec = records[0]
        assert rec["event_type"] == "unit.status_transition"
        assert rec["actor_type"] == "member"
        assert rec["actor_id"] == "MEM-001"
        assert rec["target_entity_type"] == "unit"
        assert rec["target_entity_id"] == "UNIT-100"
        assert rec["firm_id"] == "chrisai"
    finally:
        conn.close()


def test_already_resolved_ac_is_idempotent() -> None:
    """Re-running the hook on a project whose AC rows are already resolved
    produces no new flips but still writes a records row."""
    conn = _fresh_conn()
    try:
        _seed_minimal(conn, project_ac=[
            {"id": "AC-1", "text": "Done",
             "resolved": True, "resolved_by": "UNIT-100"},
        ])
        result = on_unit_done(
            conn, firm_id="chrisai",
            unit_id="UNIT-100", member_id="MEM-001",
            prior_status="in_progress",
        )
        assert result["ok"] is True
        assert result["resolved_ac_ids"] == []  # nothing to flip

        ac_after = _project_ac(conn, "PRJ-010")
        assert ac_after[0]["resolved"] is True  # still true, untouched
        assert len(_records_rows(conn)) == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-2: no-match AC list is a no-op on project
# ---------------------------------------------------------------------------

def test_no_matching_ac_writes_records_only() -> None:
    conn = _fresh_conn()
    try:
        _seed_minimal(conn, project_ac=[
            {"id": "AC-1", "text": "Belongs to other unit",
             "resolved": False, "resolved_by": "UNIT-999"},
        ])
        ac_before = _project_ac(conn, "PRJ-010")

        result = on_unit_done(
            conn, firm_id="chrisai",
            unit_id="UNIT-100", member_id="MEM-001",
            prior_status="in_progress",
        )

        assert result["ok"] is True
        assert result["resolved_ac_ids"] == []
        assert _project_ac(conn, "PRJ-010") == ac_before  # byte-identical
        assert len(_records_rows(conn)) == 1
    finally:
        conn.close()


def test_empty_acceptance_criteria_list() -> None:
    conn = _fresh_conn()
    try:
        _seed_minimal(conn, project_ac=[])
        result = on_unit_done(
            conn, firm_id="chrisai",
            unit_id="UNIT-100", member_id="MEM-001",
            prior_status="pending",
        )
        assert result["ok"] is True
        assert result["resolved_ac_ids"] == []
        assert len(_records_rows(conn)) == 1
    finally:
        conn.close()


def test_null_acceptance_criteria() -> None:
    conn = _fresh_conn()
    try:
        _seed_minimal(conn, project_ac=None)
        result = on_unit_done(
            conn, firm_id="chrisai",
            unit_id="UNIT-100", member_id="MEM-001",
            prior_status="pending",
        )
        assert result["ok"] is True
        assert result["resolved_ac_ids"] == []
        assert len(_records_rows(conn)) == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-3: unit-not-found
# ---------------------------------------------------------------------------

def test_unit_not_found_returns_structured_err() -> None:
    conn = _fresh_conn()
    try:
        _seed_minimal(conn, project_ac=[])
        result = on_unit_done(
            conn, firm_id="chrisai",
            unit_id="UNIT-404", member_id="MEM-001",
            prior_status="pending",
        )
        assert result == {
            "ok": False, "reason": "unit-not-found", "unit_id": "UNIT-404",
        }
    finally:
        conn.close()


def test_unit_not_found_writes_nothing() -> None:
    conn = _fresh_conn()
    try:
        _seed_minimal(conn, project_ac=[
            {"id": "AC-1", "resolved": False, "resolved_by": "UNIT-404"},
        ])
        ac_before = _project_ac(conn, "PRJ-010")

        on_unit_done(
            conn, firm_id="chrisai",
            unit_id="UNIT-404", member_id="MEM-001",
            prior_status="in_progress",
        )

        assert _records_rows(conn) == []
        assert _project_ac(conn, "PRJ-010") == ac_before
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-4: project-missing + transactional atomicity
# ---------------------------------------------------------------------------

def test_project_missing_returns_structured_err() -> None:
    """Unit exists but references a deleted project — return structured err, no writes."""
    conn = _fresh_conn()
    try:
        _seed_minimal(conn, project_ac=[])
        # Orphan the unit: disable FK briefly, delete the project, re-enable.
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM project WHERE id = 'PRJ-010'")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")

        result = on_unit_done(
            conn, firm_id="chrisai",
            unit_id="UNIT-100", member_id="MEM-001",
            prior_status="in_progress",
        )
        assert result["ok"] is False
        assert result["reason"] == "project-missing"
        assert result["unit_id"] == "UNIT-100"
        assert result["project_id"] == "PRJ-010"
        assert _records_rows(conn) == []
    finally:
        conn.close()


def test_transaction_rolls_back_on_mid_failure() -> None:
    """Force an exception during the project UPDATE via a DB-level trigger;
    verify the records INSERT rolls back and no row is committed."""
    conn = _fresh_conn()
    try:
        _seed_minimal(conn, project_ac=[
            {"id": "AC-1", "resolved": False, "resolved_by": "UNIT-100"},
        ])
        conn.execute(
            """
            CREATE TRIGGER test_block_project_update
            BEFORE UPDATE ON project
            BEGIN
                SELECT RAISE(ABORT, 'simulated mid-transaction failure');
            END
            """
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError, match="simulated"):
            on_unit_done(
                conn, firm_id="chrisai",
                unit_id="UNIT-100", member_id="MEM-001",
                prior_status="in_progress",
            )

        # Neither write committed.
        assert _records_rows(conn) == []
        ac_after = _project_ac(conn, "PRJ-010")
        assert ac_after[0]["resolved"] is False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Details payload shape + optional run_id + now override
# ---------------------------------------------------------------------------

def test_records_details_payload_shape() -> None:
    conn = _fresh_conn()
    try:
        _seed_minimal(conn, project_ac=[
            {"id": "AC-1", "resolved": False, "resolved_by": "UNIT-100"},
            {"id": "AC-2", "resolved": False, "resolved_by": "UNIT-100"},
        ])
        on_unit_done(
            conn, firm_id="chrisai",
            unit_id="UNIT-100", member_id="MEM-001",
            prior_status="in_review",
        )
        rec = _records_rows(conn)[0]
        details = json.loads(rec["details"])
        assert details == {
            "prior_status": "in_review",
            "new_status": "done",
            "project_id": "PRJ-010",
            "resolved_ac_ids": ["AC-1", "AC-2"],
        }
    finally:
        conn.close()


def test_run_id_threads_through() -> None:
    conn = _fresh_conn()
    try:
        _seed_minimal(conn, project_ac=[])
        # member_run FK exists; create a Run row so the records.run_id FK resolves.
        create(conn, "member_run", {
            "id": "RUN-001", "firm_id": "chrisai",
            "member_id": "MEM-001", "status": "completed",
            "started_at": "2026-04-15 16:00:00",
        })
        on_unit_done(
            conn, firm_id="chrisai",
            unit_id="UNIT-100", member_id="MEM-001",
            prior_status="in_progress",
            run_id="RUN-001",
        )
        rec = _records_rows(conn)[0]
        assert rec["run_id"] == "RUN-001"
    finally:
        conn.close()


def test_now_param_is_deterministic() -> None:
    conn = _fresh_conn()
    try:
        _seed_minimal(conn, project_ac=[])
        on_unit_done(
            conn, firm_id="chrisai",
            unit_id="UNIT-100", member_id="MEM-001",
            prior_status="in_progress",
            now="2026-04-15 17:30:00",
        )
        rec = _records_rows(conn)[0]
        assert rec["timestamp"] == "2026-04-15 17:30:00"
    finally:
        conn.close()
