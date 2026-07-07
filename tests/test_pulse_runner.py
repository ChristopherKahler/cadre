"""Tests for firm.pulse.runner — orchestrator callback pipeline."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from unittest import mock

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find, get
from firm.pulse.runner import make_runner
from firm.pulse.spawn import SpawnResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


def _add_contract(conn, contract_id, *, validation_config=None, budget_config=None, pulse_config=None):
    return create(conn, "contract", {
        "id": contract_id,
        "firm_id": "chrisai",
        "name": f"Contract {contract_id}",
        "runtime_type": "claude_code",
        "validation_config": validation_config,
        "budget_config": budget_config,
        "pulse_config": pulse_config,
    })


def _add_member(conn, member_id, *, contract_id=None):
    return create(conn, "member", {
        "id": member_id,
        "firm_id": "chrisai",
        "name": f"Member {member_id}",
        "role": "worker",
        "status": "active",
        "contract_id": contract_id,
    })


def _add_project(conn, project_id):
    op = create(conn, "operation", {
        "id": f"op-{project_id}",
        "firm_id": "chrisai",
        "name": f"Op for {project_id}",
        "status": "active",
    })
    return create(conn, "project", {
        "id": project_id,
        "firm_id": "chrisai",
        "operation_id": op["id"],
        "name": f"Project {project_id}",
        "status": "in_progress",
        "due_date": "2026-12-31",
    })


def _add_unit(conn, unit_id, project_id, *, claimed_by=None):
    return create(conn, "unit", {
        "id": unit_id,
        "firm_id": "chrisai",
        "project_id": project_id,
        "name": f"Unit {unit_id}",
        "status": "pending",
        "claimed_by": claimed_by,
        "depends_on": [],
    })


def _add_budget_period(conn, bp_id, member_id, *, run_count=0, total_cost_usd=0.0):
    return create(conn, "budget_period", {
        "id": bp_id,
        "firm_id": "chrisai",
        "member_id": member_id,
        "period_start": "2026-04-01T00:00:00+00:00",
        "period_end": "2026-04-30T23:59:59+00:00",
        "run_count": run_count,
        "total_cost_usd": total_cost_usd,
        "status": "active",
    })


def _mock_spawn_result(text="Done. AC-1 satisfied.", cost=0.05):
    """Build a SpawnResult whose stdout is valid stream-json."""
    init_line = json.dumps({"type": "system", "subtype": "init", "session_id": "ses-mock"})
    assistant_line = json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    })
    result_line = json.dumps({
        "type": "result",
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        "total_cost_usd": cost,
        "stop_reason": "end_turn",
        "is_error": False,
    })
    stdout = "\n".join([init_line, assistant_line, result_line])
    return SpawnResult(returncode=0, stdout=stdout, stderr="", pid=123, timed_out=False)


# ═══════════════════════════════════════════════════════════════════════════
# Successful run
# ═══════════════════════════════════════════════════════════════════════════


class TestSuccessfulRun:

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_creates_and_finalizes_member_run(self, mock_spawn):
        mock_spawn.return_value = _mock_spawn_result()

        conn = _fresh_conn()
        _add_contract(conn, "CON-001")
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")

        runner = make_runner("chrisai", "/tmp")
        member = get(conn, "member", "MEM-001")
        result = runner(conn, member)

        assert result["status"] == "completed"
        assert "run_id" in result

        # Verify member_run in DB
        run = get(conn, "member_run", result["run_id"])
        assert run["status"] == "completed"
        assert run["invocation_source"] == "pulse"
        assert run["prompt_snapshot"] is not None
        assert run["ended_at"] is not None

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_returns_usage_and_cost(self, mock_spawn):
        mock_spawn.return_value = _mock_spawn_result(cost=0.12)

        conn = _fresh_conn()
        _add_contract(conn, "CON-001")
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")

        runner = make_runner("chrisai", "/tmp")
        member = get(conn, "member", "MEM-001")
        result = runner(conn, member)

        assert result["cost"] == 0.12
        assert result["usage"]["input_tokens"] == 1000


# ═══════════════════════════════════════════════════════════════════════════
# Budget blocking
# ═══════════════════════════════════════════════════════════════════════════


class TestBudgetBlocking:

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_budget_exceeded_skips(self, mock_spawn):
        conn = _fresh_conn()
        _add_contract(conn, "CON-001", budget_config={
            "limits": {"max_runs_per_period": 5},
        })
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")
        _add_budget_period(conn, "BP-001", "MEM-001", run_count=5)

        runner = make_runner("chrisai", "/tmp")
        member = get(conn, "member", "MEM-001")
        result = runner(conn, member)

        assert result["skipped"] is True
        assert "budget" in result["reason"]
        mock_spawn.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# No units
# ═══════════════════════════════════════════════════════════════════════════


class TestNoUnits:

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_no_claimed_units_skips(self, mock_spawn):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001")

        runner = make_runner("chrisai", "/tmp")
        member = get(conn, "member", "MEM-001")
        result = runner(conn, member)

        assert result["skipped"] is True
        assert "no units" in result["reason"]
        mock_spawn.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# Timeout handling
# ═══════════════════════════════════════════════════════════════════════════


class TestTimeoutHandling:

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_timeout_sets_timed_out_status(self, mock_spawn):
        mock_spawn.return_value = SpawnResult(
            returncode=None, stdout="", stderr="", pid=99, timed_out=True,
        )

        conn = _fresh_conn()
        _add_contract(conn, "CON-001")
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")

        runner = make_runner("chrisai", "/tmp")
        member = get(conn, "member", "MEM-001")
        result = runner(conn, member)

        assert result["status"] == "timed_out"
        run = get(conn, "member_run", result["run_id"])
        assert run["status"] == "timed_out"


# ═══════════════════════════════════════════════════════════════════════════
# Validation + retry
# ═══════════════════════════════════════════════════════════════════════════


class TestValidationRetry:

    @mock.patch("firm.pulse.runner.spawn_member_run")
    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_validation_failure_retries(self, mock_invoke_spawn, mock_retry_spawn):
        # Initial invoke via adapter: short text (fails min_word_count)
        mock_invoke_spawn.return_value = _mock_spawn_result(text="short")
        # Retry via runner's direct spawn: long text with AC marker
        long_text = " ".join(["word"] * 200) + " AC-1 satisfied"
        mock_retry_spawn.return_value = _mock_spawn_result(text=long_text)

        conn = _fresh_conn()
        _add_contract(conn, "CON-001", validation_config={
            "enabled": True,
            "max_retries": 1,
            "validators": ["min_word_count", "ac_self_report"],
        })
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")

        runner = make_runner("chrisai", "/tmp")
        member = get(conn, "member", "MEM-001")
        result = runner(conn, member)

        assert result["status"] == "completed"
        assert result["validation_passed"] is True
        assert mock_invoke_spawn.call_count == 1  # Initial invoke
        assert mock_retry_spawn.call_count == 1   # Retry

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_validation_failure_no_retry(self, mock_spawn):
        mock_spawn.return_value = _mock_spawn_result(text="short")

        conn = _fresh_conn()
        _add_contract(conn, "CON-001", validation_config={
            "enabled": True,
            "max_retries": 0,
            "validators": ["min_word_count"],
        })
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")

        runner = make_runner("chrisai", "/tmp")
        member = get(conn, "member", "MEM-001")
        result = runner(conn, member)

        assert result["status"] == "failed"
        assert result["validation_passed"] is False
        assert mock_spawn.call_count == 1


# ═══════════════════════════════════════════════════════════════════════════
# Crash safety — no leaked 'running' rows (ESC-D)
# ═══════════════════════════════════════════════════════════════════════════


class TestCrashSafety:

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_exception_mid_run_closes_row_as_failed(self, mock_spawn):
        mock_spawn.side_effect = RuntimeError("host teardown mid-invoke")

        conn = _fresh_conn()
        _add_contract(conn, "CON-001")
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")

        runner = make_runner("chrisai", "/tmp")
        member = get(conn, "member", "MEM-001")
        with pytest.raises(RuntimeError):
            runner(conn, member)

        runs = find(conn, "member_run", member_id="MEM-001")
        assert len(runs) == 1
        assert runs[0]["status"] == "failed"
        assert runs[0]["ended_at"] is not None
        error = json.loads(runs[0]["error"])
        assert error["type"] == "runner_exception"
        assert error["exception"] == "RuntimeError"


# ═══════════════════════════════════════════════════════════════════════════
# Retry accounting — every attempt is a billed, closed run row
# ═══════════════════════════════════════════════════════════════════════════


class TestRetryAccounting:

    @mock.patch("firm.pulse.runner.spawn_member_run")
    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_retry_gets_own_row_and_both_attempts_billed(self, mock_invoke_spawn, mock_retry_spawn):
        mock_invoke_spawn.return_value = _mock_spawn_result(text="short", cost=0.10)
        long_text = " ".join(["word"] * 200) + " AC-1 satisfied"
        mock_retry_spawn.return_value = _mock_spawn_result(text=long_text, cost=0.25)

        conn = _fresh_conn()
        _add_contract(conn, "CON-001", validation_config={
            "enabled": True,
            "max_retries": 1,
            "validators": ["min_word_count", "ac_self_report"],
        })
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")

        runner = make_runner("chrisai", "/tmp")
        result = runner(conn, get(conn, "member", "MEM-001"))

        assert result["status"] == "completed"

        runs = sorted(find(conn, "member_run", member_id="MEM-001"), key=lambda r: r["id"])
        assert len(runs) == 2
        first, second = runs
        assert first["status"] == "failed"
        assert json.loads(first["error"])["type"] == "validation_failed"
        assert second["status"] == "completed"
        assert second["retry_of_run_id"] == first["id"]

        events = find(conn, "usage_event", member_id="MEM-001")
        assert len(events) == 2
        by_run = {e["run_id"]: e["dollar_equivalent"] for e in events}
        assert by_run[first["id"]] == 0.10
        assert by_run[second["id"]] == 0.25
        assert all(e["unit_id"] == "UNT-001" for e in events)

        period = find(conn, "budget_period", member_id="MEM-001")[0]
        assert abs(period["total_cost_usd"] - 0.35) < 1e-9

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_successful_run_usage_event_linked(self, mock_spawn):
        mock_spawn.return_value = _mock_spawn_result()
        conn = _fresh_conn()
        _add_contract(conn, "CON-001")
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")

        runner = make_runner("chrisai", "/tmp")
        result = runner(conn, get(conn, "member", "MEM-001"))

        events = find(conn, "usage_event", member_id="MEM-001")
        assert len(events) == 1
        assert events[0]["run_id"] == result["run_id"]
        assert events[0]["unit_id"] == "UNT-001"


# ---------------------------------------------------------------------------
# Deliverable registration: a completed unit's written file becomes a Document
# so it lands in the Board's review surface (wastelander ch18 pilot fix).
# ---------------------------------------------------------------------------

from firm.pulse.runner import (  # noqa: E402
    _register_deliverables,
    _wants_deliverable_registration,
)
from firm.services.member import create_member  # noqa: E402
from firm.services.operation import create_operation  # noqa: E402
from firm.services.project import create_project  # noqa: E402
from firm.services.unit import create_unit  # noqa: E402


def _conn_with_unit():
    conn = _fresh_conn()
    create_member(conn, "chrisai", {"name": "Wren", "role": "Novelist"})
    create_operation(conn, "chrisai", {"name": "Novel", "owner_member_id": "MEM-001"})
    create_project(conn, "chrisai", {
        "name": "Drafting", "operation_id": "OPS-001", "due_date": "2026-12-31",
    })
    create_unit(conn, "chrisai", {"name": "Draft ch18", "project_id": "PROJ-001"})
    return conn


def test_wants_deliverable_registration():
    assert _wants_deliverable_registration(
        {"validators": [{"name": "file_exists", "require_written": True}]}) is True
    assert _wants_deliverable_registration({"validators": ["file_exists"]}) is False
    assert _wants_deliverable_registration(None) is False
    # tolerates JSON string form
    assert _wants_deliverable_registration(
        '{"validators":[{"name":"file_exists","require_written":true}]}') is True


def test_register_deliverables_creates_document(tmp_path):
    conn = _conn_with_unit()
    f = tmp_path / "ch18-the-frame-v1.md"
    f.write_text("prose")
    parsed = {"tool_calls": [{"name": "Write", "input": {"file_path": str(f)}}]}
    cfg = {"validators": [{"name": "file_exists", "require_written": True}]}
    unit = get(conn, "unit", "UNIT-001")

    _register_deliverables(conn, "chrisai", unit, "MEM-001", parsed, cfg, str(tmp_path))
    docs = find(conn, "document", firm_id="chrisai")
    assert len(docs) == 1
    assert docs[0]["content_path"] == "ch18-the-frame-v1.md"
    assert docs[0]["parent_entity_id"] == "UNIT-001"
    assert docs[0]["author_id"] == "MEM-001"

    # idempotent — a retry writing the same path does not duplicate
    _register_deliverables(conn, "chrisai", unit, "MEM-001", parsed, cfg, str(tmp_path))
    assert len(find(conn, "document", firm_id="chrisai")) == 1


def test_register_deliverables_skips_without_optin(tmp_path):
    conn = _conn_with_unit()
    f = tmp_path / "scratch.md"
    f.write_text("x")
    parsed = {"tool_calls": [{"name": "Write", "input": {"file_path": str(f)}}]}
    unit = get(conn, "unit", "UNIT-001")
    # no require_written → no registration
    _register_deliverables(conn, "chrisai", unit, "MEM-001", parsed,
                           {"validators": ["file_exists"]}, str(tmp_path))
    assert find(conn, "document", firm_id="chrisai") == []
