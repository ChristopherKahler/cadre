"""Unit tests for firm.hooks.run_record.on_run_end().

Covers AC-1 through AC-4 from 02-04-PLAN:
    AC-1: member_run + usage_event + records commit atomically
    AC-2: unit.outputs merged when unit_id present; skipped when absent
    AC-3: mid-transaction rollback leaves DB clean
    AC-4: credential redaction on error/notes before write
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.hooks.run_record import on_run_end


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    return conn


def _seed(
    conn: sqlite3.Connection,
    *,
    run_id: str = "RUN-001",
    unit_id: str | None = "UNIT-001",
    unit_outputs: list[Any] | None = None,
    run_status: str = "running",
    extra_run: bool = True,
) -> None:
    """Seed firm + member + operation + project + unit + member_run(s).

    *extra_run* adds RUN-002 with no unit_id for the 3-write-only test.
    """
    create(conn, "firm", {
        "id": "chrisai", "name": "ChrisAI",
        "operator": {"name": "Chris Kahler", "role": "Board / Founder"},
    })
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai",
        "name": "Quill", "role": "Blog Author",
    })
    create(conn, "operation", {
        "id": "OPS-001", "firm_id": "chrisai",
        "name": "Content Publishing",
    })
    create(conn, "project", {
        "id": "PRJ-010", "firm_id": "chrisai",
        "operation_id": "OPS-001",
        "name": "Q2 Blog Push",
        "status": "in_progress",
        "due_date": "2026-06-30",
    })
    create(conn, "unit", {
        "id": "UNIT-001", "firm_id": "chrisai",
        "project_id": "PRJ-010",
        "name": "Draft blog post #14",
        "status": "in_progress",
        "assignee_member_id": "MEM-001",
        "outputs": unit_outputs,
    })
    create(conn, "member_run", {
        "id": run_id, "firm_id": "chrisai",
        "member_id": "MEM-001",
        "unit_id": unit_id,
        "status": run_status,
        "started_at": "2026-04-15 16:00:00",
    })
    if extra_run:
        create(conn, "member_run", {
            "id": "RUN-002", "firm_id": "chrisai",
            "member_id": "MEM-001",
            "unit_id": None,
            "status": "running",
            "started_at": "2026-04-15 17:00:00",
        })


def _row(conn: sqlite3.Connection, table: str, row_id: str) -> dict[str, Any] | None:
    r = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    return dict(r) if r else None


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _records_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM records ORDER BY id").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Regression: timezone-aware timestamps (field failure 2026-07-08)
# A naive ended_at from on_run_end mixed with the runner's aware started_at
# crashed the dashboard's duration calc and 500'd the whole firm-state render.
# ---------------------------------------------------------------------------

def test_on_run_end_writes_tz_aware_ended_at() -> None:
    from datetime import datetime
    conn = _fresh_conn()
    _seed(conn, extra_run=False)
    result = on_run_end(
        conn, firm_id="chrisai", run_id="RUN-001", final_status="completed"
    )
    assert result["ok"] is True
    row = _row(conn, "member_run", "RUN-001")
    assert row is not None and row["ended_at"]
    parsed = datetime.fromisoformat(row["ended_at"])
    assert parsed.tzinfo is not None, f"ended_at not tz-aware: {row['ended_at']!r}"


def test_run_duration_sec_handles_mixed_naive_and_aware() -> None:
    from firm.dashboard.server import _run_duration_sec
    # naive started, aware ended
    assert _run_duration_sec(
        {"started_at": "2026-04-15 16:00:00", "ended_at": "2026-04-15T16:00:30+00:00"}
    ) == pytest.approx(30.0)
    # aware started, naive ended (the exact 2026-07-08 shape)
    assert _run_duration_sec(
        {"started_at": "2026-04-15T16:00:00+00:00", "ended_at": "2026-04-15 16:00:30"}
    ) == pytest.approx(30.0)


def _unit_outputs(conn: sqlite3.Connection, unit_id: str) -> list[Any]:
    row = conn.execute(
        "SELECT outputs FROM unit WHERE id = ?", (unit_id,)
    ).fetchone()
    raw = row[0] if row else None
    return json.loads(raw) if raw else []


# ---------------------------------------------------------------------------
# AC-1: happy path — 4 writes commit atomically
# ---------------------------------------------------------------------------

def test_completes_run_with_full_usage() -> None:
    conn = _fresh_conn()
    try:
        _seed(conn, unit_outputs=[{"old": "artifact"}])
        result = on_run_end(
            conn, firm_id="chrisai", run_id="RUN-001",
            final_status="completed",
            outputs=[{"path": "post.md"}],
            usage={
                "plan": "claude_pro_200", "model": "opus-4",
                "tokens_in": 50000, "tokens_out": 12000,
                "cache_read_tokens": 30000, "cache_create_tokens": 5000,
                "dollar_equivalent": 1.25,
                "window_percent_consumed": 42.0,
                "window_id": "win-abc",
            },
            now="2026-04-15 18:00:00",
        )
        assert result["ok"] is True
        assert result["records_id"] == "LOG-001"
        assert result["wrote"]["member_run"] is True
        assert result["wrote"]["usage_event"] is True
        assert result["wrote"]["unit"] is True
        assert result["wrote"]["records"] is True

        run = _row(conn, "member_run", "RUN-001")
        assert run["status"] == "completed"
        assert run["ended_at"] == "2026-04-15 18:00:00"
        assert json.loads(run["outputs"]) == [{"path": "post.md"}]

        usg = _row(conn, "usage_event", "USG-001")
        assert usg is not None
        assert usg["plan"] == "claude_pro_200"
        assert usg["tokens_in"] == 50000
        assert usg["tokens_out"] == 12000

        assert _count(conn, "records") == 1
        assert _unit_outputs(conn, "UNIT-001") == [
            {"old": "artifact"}, {"path": "post.md"},
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-2: unit_id absent — 3 writes only
# ---------------------------------------------------------------------------

def test_completes_run_without_unit_id() -> None:
    conn = _fresh_conn()
    try:
        _seed(conn)
        result = on_run_end(
            conn, firm_id="chrisai", run_id="RUN-002",
            final_status="completed",
            outputs=[{"result": "ok"}],
            now="2026-04-15 18:00:00",
        )
        assert result["ok"] is True
        assert result["wrote"]["unit"] is False
        assert result["wrote"]["member_run"] is True
        assert result["wrote"]["usage_event"] is True
        assert result["wrote"]["records"] is True

        run = _row(conn, "member_run", "RUN-002")
        assert run["status"] == "completed"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Structured failure
# ---------------------------------------------------------------------------

def test_run_not_found_returns_reason() -> None:
    conn = _fresh_conn()
    try:
        _seed(conn)
        result = on_run_end(
            conn, firm_id="chrisai", run_id="RUN-999",
            final_status="completed",
        )
        assert result == {
            "ok": False, "reason": "run-not-found", "run_id": "RUN-999",
        }
        assert _count(conn, "records") == 0
        assert _count(conn, "usage_event") == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Failed run persists error
# ---------------------------------------------------------------------------

def test_failed_run_persists_error() -> None:
    conn = _fresh_conn()
    try:
        _seed(conn)
        err = {"message": "timeout", "code": 504}
        result = on_run_end(
            conn, firm_id="chrisai", run_id="RUN-001",
            final_status="failed", error=err,
            now="2026-04-15 18:00:00",
        )
        assert result["ok"] is True
        run = _row(conn, "member_run", "RUN-001")
        assert run["status"] == "failed"
        assert json.loads(run["error"]) == {"message": "timeout", "code": 504}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-4: credential redaction
# ---------------------------------------------------------------------------

def test_redacts_api_key_from_error() -> None:
    conn = _fresh_conn()
    try:
        _seed(conn)
        err = {"message": "auth failed", "api_key": "sk-abc123"}
        on_run_end(
            conn, firm_id="chrisai", run_id="RUN-001",
            final_status="failed", error=err,
            now="2026-04-15 18:00:00",
        )
        run = _row(conn, "member_run", "RUN-001")
        persisted = json.loads(run["error"])
        assert persisted["api_key"] == "[REDACTED]"
        assert persisted["message"] == "auth failed"
    finally:
        conn.close()


def test_redacts_token_from_notes() -> None:
    conn = _fresh_conn()
    try:
        _seed(conn)
        on_run_end(
            conn, firm_id="chrisai", run_id="RUN-001",
            final_status="completed",
            notes="auth_token=abc123 was used",
            now="2026-04-15 18:00:00",
        )
        run = _row(conn, "member_run", "RUN-001")
        assert "abc123" not in run["notes"]
        assert "[REDACTED]" in run["notes"]
    finally:
        conn.close()


def test_redaction_does_not_mutate_input() -> None:
    conn = _fresh_conn()
    try:
        _seed(conn)
        err = {"message": "fail", "api_key": "sk-secret"}
        original_key = err["api_key"]
        on_run_end(
            conn, firm_id="chrisai", run_id="RUN-001",
            final_status="failed", error=err,
            now="2026-04-15 18:00:00",
        )
        assert err["api_key"] == original_key
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Usage event edge cases
# ---------------------------------------------------------------------------

def test_usage_event_nulls_when_usage_missing() -> None:
    conn = _fresh_conn()
    try:
        _seed(conn)
        on_run_end(
            conn, firm_id="chrisai", run_id="RUN-001",
            final_status="completed",
            now="2026-04-15 18:00:00",
        )
        usg = _row(conn, "usage_event", "USG-001")
        assert usg is not None
        assert usg["plan"] == "custom"
        assert usg["tokens_in"] is None
        assert usg["tokens_out"] is None
        assert usg["model"] is None
    finally:
        conn.close()


def test_usage_event_has_all_token_fields() -> None:
    conn = _fresh_conn()
    try:
        _seed(conn)
        on_run_end(
            conn, firm_id="chrisai", run_id="RUN-001",
            final_status="completed",
            usage={
                "plan": "api", "model": "sonnet-4",
                "tokens_in": 1000, "tokens_out": 500,
                "cache_read_tokens": 200, "cache_create_tokens": 100,
                "dollar_equivalent": 0.05,
                "window_percent_consumed": 10.5,
                "window_id": "win-001",
            },
            now="2026-04-15 18:00:00",
        )
        usg = _row(conn, "usage_event", "USG-001")
        assert usg["plan"] == "api"
        assert usg["model"] == "sonnet-4"
        assert usg["tokens_in"] == 1000
        assert usg["tokens_out"] == 500
        assert usg["cache_read_tokens"] == 200
        assert usg["cache_create_tokens"] == 100
        assert usg["dollar_equivalent"] == 0.05
        assert usg["window_percent_consumed"] == 10.5
        assert usg["window_id"] == "win-001"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-2: unit outputs merge
# ---------------------------------------------------------------------------

def test_unit_outputs_merged_not_replaced() -> None:
    conn = _fresh_conn()
    try:
        _seed(conn, unit_outputs=[{"old": 1}])
        on_run_end(
            conn, firm_id="chrisai", run_id="RUN-001",
            final_status="completed",
            outputs=[{"new": 2}, {"new": 3}],
            now="2026-04-15 18:00:00",
        )
        merged = _unit_outputs(conn, "UNIT-001")
        assert merged == [{"old": 1}, {"new": 2}, {"new": 3}]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Records row shape
# ---------------------------------------------------------------------------

def test_records_row_has_correct_event_type() -> None:
    conn = _fresh_conn()
    try:
        _seed(conn)
        on_run_end(
            conn, firm_id="chrisai", run_id="RUN-001",
            final_status="completed",
            now="2026-04-15 18:00:00",
        )
        recs = _records_rows(conn)
        assert len(recs) == 1
        assert recs[0]["event_type"] == "member_run.ended"
        assert recs[0]["actor_type"] == "member"
        assert recs[0]["actor_id"] == "MEM-001"
        assert recs[0]["target_entity_type"] == "member_run"
        assert recs[0]["target_entity_id"] == "RUN-001"
    finally:
        conn.close()


def test_records_id_increments() -> None:
    conn = _fresh_conn()
    try:
        _seed(conn)
        on_run_end(
            conn, firm_id="chrisai", run_id="RUN-001",
            final_status="completed",
            now="2026-04-15 18:00:00",
        )
        on_run_end(
            conn, firm_id="chrisai", run_id="RUN-002",
            final_status="completed",
            now="2026-04-15 18:05:00",
        )
        recs = _records_rows(conn)
        assert len(recs) == 2
        assert recs[0]["id"] == "LOG-001"
        assert recs[1]["id"] == "LOG-002"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-3: mid-transaction rollback
# ---------------------------------------------------------------------------

def test_mid_transaction_rollback() -> None:
    """Force usage_event INSERT to fail via DB trigger; verify all writes
    roll back (member_run.status stays 'running', no usage_event, no
    records, unit.outputs unchanged)."""
    conn = _fresh_conn()
    try:
        _seed(conn, unit_outputs=[{"original": True}])
        conn.execute(
            """
            CREATE TRIGGER test_block_usage_event_insert
            BEFORE INSERT ON usage_event
            BEGIN
                SELECT RAISE(ABORT, 'simulated mid-transaction failure');
            END
            """
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError, match="simulated"):
            on_run_end(
                conn, firm_id="chrisai", run_id="RUN-001",
                final_status="completed",
                outputs=[{"should": "not persist"}],
                now="2026-04-15 18:00:00",
            )

        run = _row(conn, "member_run", "RUN-001")
        assert run["status"] == "running"
        assert _count(conn, "usage_event") == 0
        assert _count(conn, "records") == 0
        assert _unit_outputs(conn, "UNIT-001") == [{"original": True}]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Details payload + now override
# ---------------------------------------------------------------------------

def test_run_id_threading_via_records_details() -> None:
    conn = _fresh_conn()
    try:
        _seed(conn)
        on_run_end(
            conn, firm_id="chrisai", run_id="RUN-001",
            final_status="completed",
            outputs=[{"a": 1}],
            now="2026-04-15 18:00:00",
        )
        rec = _records_rows(conn)[0]
        details = json.loads(rec["details"])
        assert details["run_id"] == "RUN-001"
        assert details["final_status"] == "completed"
        assert details["outputs_count"] == 1
        assert details["had_error"] is False
    finally:
        conn.close()


def test_now_override_deterministic() -> None:
    conn = _fresh_conn()
    try:
        _seed(conn)
        on_run_end(
            conn, firm_id="chrisai", run_id="RUN-001",
            final_status="completed",
            now="2026-04-15 18:30:00",
        )
        run = _row(conn, "member_run", "RUN-001")
        assert run["ended_at"] == "2026-04-15 18:30:00"

        usg = _row(conn, "usage_event", "USG-001")
        assert usg["timestamp"] == "2026-04-15 18:30:00"

        rec = _records_rows(conn)[0]
        assert rec["timestamp"] == "2026-04-15 18:30:00"
    finally:
        conn.close()
