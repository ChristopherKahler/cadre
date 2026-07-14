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


def _mock_spawn_result(text="Done. AC-1 satisfied.", cost=0.05,
                       init_tools=None, mcp_servers=None, tool_calls=None):
    """Build a SpawnResult whose stdout is valid stream-json.

    init_tools / mcp_servers land on the init event when given (None = the
    key is absent, like a stream from an older claude). tool_calls (list of
    tool names) adds assistant tool_use events mid-stream.
    """
    init: dict = {"type": "system", "subtype": "init", "session_id": "ses-mock"}
    if init_tools is not None:
        init["tools"] = init_tools
    if mcp_servers is not None:
        init["mcp_servers"] = mcp_servers
    init_line = json.dumps(init)
    tool_use_lines = [
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": name, "id": f"tu-{i}", "input": {}},
            ]},
        })
        for i, name in enumerate(tool_calls or [])
    ]
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
    stdout = "\n".join([init_line, *tool_use_lines, assistant_line, result_line])
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


# ═══════════════════════════════════════════════════════════════════════════
# Final-text persistence — a deliverable never evaporates (RUN-051)
# ═══════════════════════════════════════════════════════════════════════════


def _outputs_of(run):
    outputs = run["outputs"]
    if isinstance(outputs, str):
        outputs = json.loads(outputs)
    return outputs


class TestFinalTextPersistence:
    """wastelander RUN-051 regression: a completed run's final message text
    must land in member_run.outputs, not exist only as process stdout."""

    def _seed(self, conn, validation_config=None):
        _add_contract(conn, "CON-001", validation_config=validation_config)
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")
        return get(conn, "member", "MEM-001")

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_completed_run_persists_final_text(self, mock_spawn):
        deliverable = "Canon check verdict: chapter 18 is consistent."
        mock_spawn.return_value = _mock_spawn_result(text=deliverable)

        conn = _fresh_conn()
        member = self._seed(conn)
        result = make_runner("chrisai", "/tmp")(conn, member)

        assert result["status"] == "completed"
        run = get(conn, "member_run", result["run_id"])
        assert _outputs_of(run) == [{"type": "final_text", "text": deliverable}]

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_failed_validation_run_persists_final_text_too(self, mock_spawn):
        mock_spawn.return_value = _mock_spawn_result(text="short")

        conn = _fresh_conn()
        member = self._seed(conn, validation_config={
            "enabled": True,
            "max_retries": 0,
            "validators": ["min_word_count"],
        })
        result = make_runner("chrisai", "/tmp")(conn, member)

        assert result["status"] == "failed"
        run = get(conn, "member_run", result["run_id"])
        assert _outputs_of(run) == [{"type": "final_text", "text": "short"}]

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_timed_out_run_persists_partial_text(self, mock_spawn):
        partial = _mock_spawn_result(text="half a deliverable")
        partial.timed_out = True
        partial.returncode = None
        mock_spawn.return_value = partial

        conn = _fresh_conn()
        member = self._seed(conn)
        result = make_runner("chrisai", "/tmp")(conn, member)

        assert result["status"] == "timed_out"
        run = get(conn, "member_run", result["run_id"])
        assert _outputs_of(run) == [
            {"type": "final_text", "text": "half a deliverable"},
        ]


# ═══════════════════════════════════════════════════════════════════════════
# MCP startup guard — a run without its firm tools is visibly degraded
# ═══════════════════════════════════════════════════════════════════════════


class TestMcpStartupGuard:
    """ESC-004 / RUN-051 regression: when the workspace .mcp.json declares a
    firm server that provably never connects in the spawned claude, the run
    row must carry a visible mcp_degraded warning instead of silently
    completing. RUN-053/054/055 counter-regression: the init event races
    ahead of MCP connections under systemd-run timing, so ``pending`` at
    init must NEVER flag a run by itself."""

    def _firm_workspace(self, tmp_path):
        (tmp_path / ".mcp.json").write_text(json.dumps(
            {"mcpServers": {"firm": {"command": "bash", "args": ["-lc", "x"]}}}
        ))
        return str(tmp_path)

    def _fake_mcp_log(self, monkeypatch, cache_root, workspace, lines):
        """Stand up a claude-cli cache dir for *workspace* with one firm log."""
        import firm.pulse.runner as runner_mod
        log_dir = cache_root / workspace.replace("/", "-") / "mcp-logs-firm"
        log_dir.mkdir(parents=True)
        (log_dir / "2026-07-10T00-00-00-000Z.jsonl").write_text(
            "\n".join(json.dumps(e) for e in lines)
        )
        monkeypatch.setattr(runner_mod, "_MCP_LOG_ROOT", str(cache_root))

    def _seed(self, conn):
        _add_contract(conn, "CON-001")
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")
        return get(conn, "member", "MEM-001")

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_missing_firm_mcp_flags_run_degraded(self, mock_spawn, tmp_path):
        mock_spawn.return_value = _mock_spawn_result(
            init_tools=["Bash", "Read", "Write"],
            mcp_servers=[{"name": "firm", "status": "failed"}],
        )

        conn = _fresh_conn()
        member = self._seed(conn)
        result = make_runner("chrisai", self._firm_workspace(tmp_path))(conn, member)

        assert result["status"] == "completed"  # degraded, not dead
        assert result["mcp_degraded"] == ["firm"]
        run = get(conn, "member_run", result["run_id"])
        notes = json.loads(run["notes"])
        assert notes["warning"] == "mcp_degraded"
        assert notes["missing_mcp_servers"] == ["firm"]
        assert notes["server_status"] == {"firm": "failed"}

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_connected_firm_mcp_not_flagged(self, mock_spawn, tmp_path):
        mock_spawn.return_value = _mock_spawn_result(
            init_tools=["Bash", "mcp__firm__unit_create", "mcp__firm__create_document"],
            mcp_servers=[{"name": "firm", "status": "connected"}],
        )

        conn = _fresh_conn()
        member = self._seed(conn)
        result = make_runner("chrisai", self._firm_workspace(tmp_path))(conn, member)

        assert result["status"] == "completed"
        assert "mcp_degraded" not in result
        run = get(conn, "member_run", result["run_id"])
        assert run["notes"] is None

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_guard_disarms_when_no_init_info(self, mock_spawn, tmp_path):
        # Stream without tools/mcp_servers on init (older claude) — the
        # guard must not false-fail on absence of evidence.
        mock_spawn.return_value = _mock_spawn_result()

        conn = _fresh_conn()
        member = self._seed(conn)
        result = make_runner("chrisai", self._firm_workspace(tmp_path))(conn, member)

        assert result["status"] == "completed"
        assert "mcp_degraded" not in result

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_guard_disarms_without_mcp_json(self, mock_spawn, tmp_path):
        # Workspace declares no MCP servers → nothing to expect, no warning
        # even when the init reports zero MCP tools.
        mock_spawn.return_value = _mock_spawn_result(
            init_tools=["Bash"], mcp_servers=[],
        )

        conn = _fresh_conn()
        member = self._seed(conn)
        result = make_runner("chrisai", str(tmp_path))(conn, member)

        assert result["status"] == "completed"
        assert "mcp_degraded" not in result

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_pending_at_init_not_flagged(self, mock_spawn, tmp_path):
        # RUN-053/054/055: init snapshot said "pending", the server connected
        # ~500ms later, and the member used its tools — flagging this made
        # three healthy production runs look degraded. Pending with no
        # affirmative failure evidence must stay silent.
        mock_spawn.return_value = _mock_spawn_result(
            init_tools=["Bash", "Read"],
            mcp_servers=[{"name": "firm", "status": "pending"}],
        )

        conn = _fresh_conn()
        member = self._seed(conn)
        result = make_runner("chrisai", self._firm_workspace(tmp_path))(conn, member)

        assert result["status"] == "completed"
        assert "mcp_degraded" not in result
        run = get(conn, "member_run", result["run_id"])
        assert run["notes"] is None

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_pending_with_firm_tool_call_not_flagged(self, mock_spawn, tmp_path):
        # A successful mcp__firm__* call in the stream is absolute proof the
        # toolset was reachable, whatever the init snapshot said.
        mock_spawn.return_value = _mock_spawn_result(
            init_tools=["Bash"],
            mcp_servers=[{"name": "firm", "status": "pending"}],
            tool_calls=["mcp__firm__firm_create_document"],
        )

        conn = _fresh_conn()
        member = self._seed(conn)
        result = make_runner("chrisai", self._firm_workspace(tmp_path))(conn, member)

        assert "mcp_degraded" not in result

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_pending_with_log_no_connect_flagged(self, mock_spawn, tmp_path, monkeypatch):
        # Affirmative failure: this session's own MCP debug log has entries
        # but never a successful connect — pending was real this time.
        mock_spawn.return_value = _mock_spawn_result(
            init_tools=["Bash"],
            mcp_servers=[{"name": "firm", "status": "pending"}],
        )
        (tmp_path / "ws").mkdir()
        workspace = self._firm_workspace(tmp_path / "ws")
        self._fake_mcp_log(monkeypatch, tmp_path / "cache", workspace, [
            {"sessionId": "ses-mock", "debug": "Starting connection with timeout of 30000ms"},
            {"sessionId": "ses-mock", "debug": "Connection failed: spawn error"},
        ])

        conn = _fresh_conn()
        member = self._seed(conn)
        result = make_runner("chrisai", workspace)(conn, member)

        assert result["mcp_degraded"] == ["firm"]
        run = get(conn, "member_run", result["run_id"])
        notes = json.loads(run["notes"])
        assert notes["warning"] == "mcp_degraded"
        assert notes["evidence"]["firm"] == "init=pending, log=no-connect"

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_pending_with_log_connect_not_flagged(self, mock_spawn, tmp_path, monkeypatch):
        # The log's "Successfully connected" clears a pending server even
        # when the member never called a firm tool (text-only unit).
        mock_spawn.return_value = _mock_spawn_result(
            init_tools=["Bash"],
            mcp_servers=[{"name": "firm", "status": "pending"}],
        )
        (tmp_path / "ws").mkdir()
        workspace = self._firm_workspace(tmp_path / "ws")
        self._fake_mcp_log(monkeypatch, tmp_path / "cache", workspace, [
            {"sessionId": "ses-mock", "debug": "Starting connection with timeout of 30000ms"},
            {"sessionId": "ses-mock", "debug": "Successfully connected (transport: stdio) in 540ms"},
        ])

        conn = _fresh_conn()
        member = self._seed(conn)
        result = make_runner("chrisai", workspace)(conn, member)

        assert "mcp_degraded" not in result

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_failed_status_with_log_connect_not_flagged(self, mock_spawn, tmp_path, monkeypatch):
        # Even a "failed" init snapshot yields to a logged successful connect
        # for this session (e.g. reconnect after a first attempt died).
        mock_spawn.return_value = _mock_spawn_result(
            init_tools=["Bash"],
            mcp_servers=[{"name": "firm", "status": "failed"}],
        )
        (tmp_path / "ws").mkdir()
        workspace = self._firm_workspace(tmp_path / "ws")
        self._fake_mcp_log(monkeypatch, tmp_path / "cache", workspace, [
            {"sessionId": "ses-mock", "debug": "Successfully connected (transport: stdio) in 540ms"},
        ])

        conn = _fresh_conn()
        member = self._seed(conn)
        result = make_runner("chrisai", workspace)(conn, member)

        assert "mcp_degraded" not in result

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_other_sessions_log_lines_are_ignored(self, mock_spawn, tmp_path, monkeypatch):
        # A concurrent Board session's connect in the same workspace must not
        # vouch for this run: only ses-mock entries count. Failed status +
        # foreign connect line → still degraded.
        mock_spawn.return_value = _mock_spawn_result(
            init_tools=["Bash"],
            mcp_servers=[{"name": "firm", "status": "failed"}],
        )
        (tmp_path / "ws").mkdir()
        workspace = self._firm_workspace(tmp_path / "ws")
        self._fake_mcp_log(monkeypatch, tmp_path / "cache", workspace, [
            {"sessionId": "ses-other", "debug": "Successfully connected (transport: stdio) in 300ms"},
        ])

        conn = _fresh_conn()
        member = self._seed(conn)
        result = make_runner("chrisai", workspace)(conn, member)

        assert result["mcp_degraded"] == ["firm"]


def test_mcp_log_verdict_unit(tmp_path):
    import firm.pulse.runner as runner_mod
    from firm.pulse.runner import _mcp_log_verdict

    ws = "/home/someone/firms/demo"
    log_dir = tmp_path / ws.replace("/", "-") / "mcp-logs-firm"
    log_dir.mkdir(parents=True)
    logf = log_dir / "2026-07-10T00-00-00-000Z.jsonl"

    orig_root = runner_mod._MCP_LOG_ROOT
    runner_mod._MCP_LOG_ROOT = str(tmp_path)
    try:
        # no session entries at all → None (cannot assert)
        logf.write_text(json.dumps({"sessionId": "other", "debug": "Successfully connected"}))
        assert _mcp_log_verdict(ws, "ses-1", "firm") is None
        # session entries without a connect → False (affirmative failure)
        logf.write_text(json.dumps({"sessionId": "ses-1", "debug": "Starting connection"}))
        assert _mcp_log_verdict(ws, "ses-1", "firm") is False
        # session connect → True
        logf.write_text("\n".join([
            json.dumps({"sessionId": "ses-1", "debug": "Starting connection"}),
            json.dumps({"sessionId": "ses-1", "debug": "Successfully connected (transport: stdio) in 540ms"}),
        ]))
        assert _mcp_log_verdict(ws, "ses-1", "firm") is True
        # missing dir / missing session id → None
        assert _mcp_log_verdict("/nowhere/at/all", "ses-1", "firm") is None
        assert _mcp_log_verdict(ws, None, "firm") is None
    finally:
        runner_mod._MCP_LOG_ROOT = orig_root


# ═══════════════════════════════════════════════════════════════════════════
# Claim ordering — priority steers, insertion order does not (2026-07-13)
# ═══════════════════════════════════════════════════════════════════════════

class TestClaimOrdering:
    """The Board's 'adjust Unit priorities' charter power must be connected
    to the claim scan. Field failure 2026-07-13 (crows-and-pawns): Wrench
    claimed a medium unit with a lower rowid while the Board had set
    UNT-BOARDBUILD to high specifically to steer it there."""

    def _unit_with(self, conn, unit_id, project_id, member_id, **fields):
        from firm.core.repo import create
        return create(conn, "unit", {
            "id": unit_id,
            "firm_id": "chrisai",
            "project_id": project_id,
            "name": f"Unit {unit_id}",
            "status": "pending",
            "assignee_member_id": member_id,
            "depends_on": [],
            **fields,
        })

    def test_high_priority_beats_earlier_rowid(self):
        from firm.pulse.runner import _find_member_unit
        conn = _fresh_conn()
        _add_contract(conn, "CON-001")
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        self._unit_with(conn, "UNT-SPRITEKIT", "PRJ-001", "MEM-001", priority="medium")
        self._unit_with(conn, "UNT-BOARDBUILD", "PRJ-001", "MEM-001", priority="high")
        unit = _find_member_unit(conn, "MEM-001")
        assert unit is not None
        assert unit["id"] == "UNT-BOARDBUILD"

    def test_rank_breaks_priority_ties(self):
        from firm.pulse.runner import _find_member_unit
        conn = _fresh_conn()
        _add_contract(conn, "CON-001")
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        self._unit_with(conn, "UNT-A", "PRJ-001", "MEM-001", priority="high", rank=2)
        self._unit_with(conn, "UNT-B", "PRJ-001", "MEM-001", priority="high", rank=1)
        unit = _find_member_unit(conn, "MEM-001")
        assert unit is not None
        assert unit["id"] == "UNT-B"

    def test_insertion_order_is_last_tiebreak(self):
        from firm.pulse.runner import _find_member_unit
        conn = _fresh_conn()
        _add_contract(conn, "CON-001")
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        self._unit_with(conn, "UNT-FIRST", "PRJ-001", "MEM-001", priority="medium")
        self._unit_with(conn, "UNT-SECOND", "PRJ-001", "MEM-001", priority="medium")
        unit = _find_member_unit(conn, "MEM-001")
        assert unit is not None
        assert unit["id"] == "UNT-FIRST"

    def test_preclaimed_unit_still_wins_over_priority(self):
        """The Board's claimed_by targeting lever must survive the sort
        (tapir's live workaround — do not break it)."""
        from firm.pulse.runner import _find_member_unit
        conn = _fresh_conn()
        _add_contract(conn, "CON-001")
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        self._unit_with(conn, "UNT-HIGH", "PRJ-001", "MEM-001", priority="high")
        self._unit_with(
            conn, "UNT-TARGET", "PRJ-001", "MEM-001",
            priority="low", claimed_by="MEM-001",
        )
        unit = _find_member_unit(conn, "MEM-001")
        assert unit is not None
        assert unit["id"] == "UNT-TARGET"


# ═══════════════════════════════════════════════════════════════════════════
# Failure billing — a dead run's tokens still hit the ledger (2026-07-13)
# ═══════════════════════════════════════════════════════════════════════════

def _timed_out_spawn_with_usage():
    """A run killed mid-flight: assistant events carry per-message usage,
    no terminal result event ever arrives (RUN-004's shape)."""
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "ses-t"}),
        json.dumps({
            "type": "assistant",
            "message": {
                "usage": {"input_tokens": 9000, "output_tokens": 800},
                "content": [{"type": "text", "text": "working on the toolchain"}],
            },
        }),
        json.dumps({
            "type": "assistant",
            "message": {
                "usage": {"input_tokens": 11000, "output_tokens": 1200},
                "content": [{"type": "tool_use", "name": "Bash", "id": "t1",
                             "input": {"command": "curl -O templates.tpz"}}],
            },
        }),
    ]
    return SpawnResult(
        returncode=None, stdout="\n".join(lines), stderr="", pid=99,
        timed_out=True,
    )


class TestFailureBilling:
    """Timed-out and crashed runs burned real tokens; every firm's spend
    figure was a floor that understated by exactly its failures (RUN-004,
    2026-07-13: ~20min of tokens, zero usage_events)."""

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_timeout_creates_usage_event(self, mock_spawn):
        mock_spawn.return_value = _timed_out_spawn_with_usage()
        conn = _fresh_conn()
        _add_contract(conn, "CON-001")
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")

        runner = make_runner("chrisai", "/tmp")
        result = runner(conn, get(conn, "member", "MEM-001"))

        assert result["status"] == "timed_out"
        events = find(conn, "usage_event", member_id="MEM-001")
        assert len(events) == 1
        assert events[0]["tokens_in"] == 20000
        assert events[0]["tokens_out"] == 2000
        period = find(conn, "budget_period", member_id="MEM-001")[0]
        assert period["run_count"] == 1
        assert period["total_input_tokens"] == 20000

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_process_error_creates_usage_event(self, mock_spawn):
        crashed = _timed_out_spawn_with_usage()
        mock_spawn.return_value = SpawnResult(
            returncode=1, stdout=crashed.stdout, stderr="boom", pid=99,
            timed_out=False,
        )
        conn = _fresh_conn()
        _add_contract(conn, "CON-001")
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")

        runner = make_runner("chrisai", "/tmp")
        result = runner(conn, get(conn, "member", "MEM-001"))

        assert result["status"] == "failed"
        events = find(conn, "usage_event", member_id="MEM-001")
        assert len(events) == 1
        assert events[0]["tokens_in"] == 20000
