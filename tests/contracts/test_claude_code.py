"""Tests for firm.contracts.claude_code — ClaudeCodeRuntime adapter."""

from __future__ import annotations

import json
import sqlite3
from unittest import mock

import pytest

from firm.contracts.claude_code import ClaudeCodeRuntime
from firm.contracts.interface import ContractRuntime, InvokeResult, RunStatus
from firm.core.migrate import apply_migrations
from firm.core.repo import create
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


def _seed(conn: sqlite3.Connection) -> tuple[dict, dict, dict]:
    """Create contract + member + unit, return as dicts."""
    contract = create(conn, "contract", {
        "id": "CON-001",
        "firm_id": "chrisai",
        "name": "Quill Contract",
        "runtime_type": "claude_code",
        "pulse_config": json.dumps({"timeout_sec": 120}),
    })
    op = create(conn, "operation", {
        "id": "OP-001",
        "firm_id": "chrisai",
        "name": "Content",
        "status": "active",
    })
    member = create(conn, "member", {
        "id": "MEM-001",
        "firm_id": "chrisai",
        "name": "Quill",
        "role": "Blog Author",
        "status": "active",
        "contract_id": "CON-001",
    })
    project = create(conn, "project", {
        "id": "PRJ-001",
        "firm_id": "chrisai",
        "operation_id": "OP-001",
        "name": "Blog v1",
        "status": "in_progress",
        "due_date": "2026-12-31",
    })
    unit = create(conn, "unit", {
        "id": "UNT-001",
        "firm_id": "chrisai",
        "project_id": "PRJ-001",
        "name": "Write blog post",
        "status": "pending",
        "claimed_by": "MEM-001",
        "depends_on": [],
    })
    return contract, member, unit


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocol:
    def test_satisfies_contract_runtime(self):
        assert isinstance(ClaudeCodeRuntime(), ContractRuntime)


# ---------------------------------------------------------------------------
# invoke
# ---------------------------------------------------------------------------

class TestInvoke:
    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    @mock.patch("firm.contracts.claude_code.assemble_prompt")
    def test_invoke_delegates_to_prompt_and_spawn(self, mock_prompt, mock_spawn):
        conn = _fresh_conn()
        contract, member, unit = _seed(conn)

        mock_prompt.return_value = "assembled prompt"
        mock_spawn.return_value = SpawnResult(
            returncode=0,
            stdout='{"text":"hello"}',
            stderr="",
            pid=42,
            timed_out=False,
        )

        runtime = ClaudeCodeRuntime()
        result = runtime.invoke(conn, contract, member, unit, cwd="/tmp")

        mock_prompt.assert_called_once_with(
            conn, "chrisai", "MEM-001", "UNT-001", cwd="/tmp",
        )
        mock_spawn.assert_called_once_with(
            "assembled prompt", timeout_sec=120, cwd="/tmp", model=None,
            member_id="MEM-001", firm_id="chrisai",
        )

        assert isinstance(result, InvokeResult)
        assert result.handle.pid == 42
        assert result.stdout == '{"text":"hello"}'
        assert result.returncode == 0
        assert result.timed_out is False
        assert result.prompt_snapshot == "assembled prompt"

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    @mock.patch("firm.contracts.claude_code.assemble_prompt")
    def test_invoke_timeout_result(self, mock_prompt, mock_spawn):
        conn = _fresh_conn()
        contract, member, unit = _seed(conn)

        mock_prompt.return_value = "prompt"
        mock_spawn.return_value = SpawnResult(
            returncode=None,
            stdout="",
            stderr="",
            pid=99,
            timed_out=True,
        )

        result = ClaudeCodeRuntime().invoke(conn, contract, member, unit, cwd="/tmp")
        assert result.timed_out is True
        assert result.returncode is None
        assert result.handle.pid == 99

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    @mock.patch("firm.contracts.claude_code.assemble_prompt")
    def test_invoke_process_error(self, mock_prompt, mock_spawn):
        conn = _fresh_conn()
        contract, member, unit = _seed(conn)

        mock_prompt.return_value = "prompt"
        mock_spawn.return_value = SpawnResult(
            returncode=1,
            stdout="",
            stderr="claude: error",
            pid=50,
            timed_out=False,
        )

        result = ClaudeCodeRuntime().invoke(conn, contract, member, unit, cwd="/tmp")
        assert result.returncode == 1
        assert result.stderr == "claude: error"

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    @mock.patch("firm.contracts.claude_code.assemble_prompt")
    def test_invoke_metadata_carries_timeout(self, mock_prompt, mock_spawn):
        conn = _fresh_conn()
        contract, member, unit = _seed(conn)

        mock_prompt.return_value = "prompt"
        mock_spawn.return_value = SpawnResult(
            returncode=0, stdout="", stderr="", pid=1, timed_out=False,
        )

        result = ClaudeCodeRuntime().invoke(conn, contract, member, unit, cwd="/tmp")
        assert result.handle.metadata["timeout_sec"] == 120


# ---------------------------------------------------------------------------
# _get_timeout
# ---------------------------------------------------------------------------

class TestGetTimeout:
    def test_extracts_from_pulse_config(self):
        contract = {"pulse_config": json.dumps({"timeout_sec": 600})}
        assert ClaudeCodeRuntime._get_timeout(contract) == 600

    def test_default_when_no_config(self):
        assert ClaudeCodeRuntime._get_timeout({}) == 300

    def test_default_when_json_string_malformed(self):
        assert ClaudeCodeRuntime._get_timeout({"pulse_config": "not json"}) == 300

    def test_handles_dict_pulse_config(self):
        contract = {"pulse_config": {"timeout_sec": 90}}
        assert ClaudeCodeRuntime._get_timeout(contract) == 90


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

class TestStatus:
    def test_no_pid_returns_completed(self):
        from firm.contracts.interface import RunHandle
        handle = RunHandle(pid=None)
        assert ClaudeCodeRuntime().status(handle) == RunStatus.completed

    @mock.patch.dict("firm.pulse.spawn._active_pids", {42: mock.Mock()})
    def test_active_pid_returns_running(self):
        from firm.contracts.interface import RunHandle
        handle = RunHandle(pid=42)
        assert ClaudeCodeRuntime().status(handle) == RunStatus.running

    def test_dead_pid_returns_completed(self):
        from firm.contracts.interface import RunHandle
        handle = RunHandle(pid=99999)
        assert ClaudeCodeRuntime().status(handle) == RunStatus.completed


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------

class TestCancel:
    def test_no_pid_returns_false(self):
        from firm.contracts.interface import RunHandle
        assert ClaudeCodeRuntime().cancel(RunHandle(pid=None)) is False

    def test_unknown_pid_returns_false(self):
        from firm.contracts.interface import RunHandle
        assert ClaudeCodeRuntime().cancel(RunHandle(pid=99999)) is False

    @mock.patch.dict("firm.pulse.spawn._active_pids", clear=True)
    def test_active_pid_killed_and_returns_true(self):
        from firm.contracts.interface import RunHandle
        from firm.pulse.spawn import _active_pids

        fake_proc = mock.Mock()
        _active_pids[42] = fake_proc

        result = ClaudeCodeRuntime().cancel(RunHandle(pid=42))
        assert result is True
        fake_proc.kill.assert_called_once()
        assert 42 not in _active_pids
