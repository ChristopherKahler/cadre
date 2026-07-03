"""Tests for firm.services._records — Records auto-entry."""

from __future__ import annotations

import sqlite3

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find
from firm.services._records import log_event


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


# ---------------------------------------------------------------------------
# AC-3: log_event writes immutable Records entries
# ---------------------------------------------------------------------------


def test_log_event_creates_record() -> None:
    conn = _fresh_conn()
    row = log_event(
        conn,
        firm_id="chrisai",
        event_type="member.created",
        actor={"type": "board", "id": None},
        target_ref={"type": "member", "id": "MEM-001"},
    )
    assert row["id"] == "LOG-001"
    assert row["event_type"] == "member.created"
    assert row["actor_type"] == "board"
    assert row["actor_id"] is None
    assert row["target_entity_type"] == "member"
    assert row["target_entity_id"] == "MEM-001"
    assert row["details"] is None
    assert row["run_id"] is None


def test_log_event_sequential_ids() -> None:
    conn = _fresh_conn()
    r1 = log_event(
        conn,
        firm_id="chrisai",
        event_type="member.created",
        actor={"type": "board", "id": None},
        target_ref={"type": "member", "id": "MEM-001"},
    )
    r2 = log_event(
        conn,
        firm_id="chrisai",
        event_type="unit.created",
        actor={"type": "member", "id": "MEM-001"},
        target_ref={"type": "unit", "id": "UNIT-001"},
    )
    assert r1["id"] == "LOG-001"
    assert r2["id"] == "LOG-002"


def test_log_event_with_details() -> None:
    conn = _fresh_conn()
    details = {"from": "pending", "to": "in_progress"}
    row = log_event(
        conn,
        firm_id="chrisai",
        event_type="unit.status_transition",
        actor={"type": "member", "id": "MEM-001"},
        target_ref={"type": "unit", "id": "UNIT-001"},
        details=details,
    )
    assert row["details"] == details


def test_log_event_with_run_id() -> None:
    conn = _fresh_conn()
    # Seed a member and member_run so the FK is valid
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai", "name": "Quill", "role": "Writer",
    })
    create(conn, "member_run", {
        "id": "RUN-001", "firm_id": "chrisai", "member_id": "MEM-001",
        "status": "completed", "started_at": "2026-04-15T14:00:00",
    })
    row = log_event(
        conn,
        firm_id="chrisai",
        event_type="unit.completed",
        actor={"type": "member", "id": "MEM-001"},
        target_ref={"type": "unit", "id": "UNIT-001"},
        run_id="RUN-001",
    )
    assert row["run_id"] == "RUN-001"


def test_log_event_member_actor() -> None:
    conn = _fresh_conn()
    row = log_event(
        conn,
        firm_id="chrisai",
        event_type="gate.approved",
        actor={"type": "member", "id": "MEM-002"},
        target_ref={"type": "gate", "id": "GATE-001"},
    )
    assert row["actor_type"] == "member"
    assert row["actor_id"] == "MEM-002"


def test_log_event_ids_are_globally_unique() -> None:
    """IDs are globally unique (not firm-scoped) since id is a PRIMARY KEY."""
    conn = _fresh_conn()
    create(conn, "firm", {"id": "otherfirm", "name": "Other"})
    r1 = log_event(
        conn,
        firm_id="chrisai",
        event_type="member.created",
        actor={"type": "board", "id": None},
        target_ref={"type": "member", "id": "MEM-001"},
    )
    r2 = log_event(
        conn,
        firm_id="otherfirm",
        event_type="member.created",
        actor={"type": "board", "id": None},
        target_ref={"type": "member", "id": "MEM-001"},
    )
    assert r1["id"] == "LOG-001"
    assert r2["id"] == "LOG-002"  # Global sequence — no PK collision


def test_log_event_persists_in_db() -> None:
    conn = _fresh_conn()
    log_event(
        conn,
        firm_id="chrisai",
        event_type="operation.created",
        actor={"type": "board", "id": None},
        target_ref={"type": "operation", "id": "OPS-001"},
    )
    rows = find(conn, "records", firm_id="chrisai")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "operation.created"
