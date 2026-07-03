"""Tests for firm.contracts.interface — Protocol + value types."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from firm.contracts.interface import (
    ContractRuntime,
    InvokeResult,
    RunHandle,
    RunStatus,
)


# ---------------------------------------------------------------------------
# RunStatus
# ---------------------------------------------------------------------------

class TestRunStatus:
    def test_enum_values(self):
        assert RunStatus.running.value == "running"
        assert RunStatus.completed.value == "completed"
        assert RunStatus.failed.value == "failed"
        assert RunStatus.timed_out.value == "timed_out"
        assert RunStatus.cancelled.value == "cancelled"

    def test_enum_member_count(self):
        assert len(RunStatus) == 5


# ---------------------------------------------------------------------------
# RunHandle
# ---------------------------------------------------------------------------

class TestRunHandle:
    def test_defaults(self):
        h = RunHandle()
        assert h.run_id is None
        assert h.pid is None
        assert h.metadata == {}

    def test_with_values(self):
        h = RunHandle(run_id="RUN-001", pid=12345, metadata={"timeout_sec": 300})
        assert h.run_id == "RUN-001"
        assert h.pid == 12345
        assert h.metadata["timeout_sec"] == 300


# ---------------------------------------------------------------------------
# InvokeResult
# ---------------------------------------------------------------------------

class TestInvokeResult:
    def test_fields(self):
        r = InvokeResult(
            handle=RunHandle(pid=99),
            stdout="output",
            stderr="err",
            returncode=0,
            timed_out=False,
            prompt_snapshot="prompt text",
        )
        assert r.handle.pid == 99
        assert r.stdout == "output"
        assert r.stderr == "err"
        assert r.returncode == 0
        assert r.timed_out is False
        assert r.prompt_snapshot == "prompt text"

    def test_prompt_snapshot_defaults_empty(self):
        r = InvokeResult(
            handle=RunHandle(),
            stdout="",
            stderr="",
            returncode=None,
            timed_out=False,
        )
        assert r.prompt_snapshot == ""


# ---------------------------------------------------------------------------
# ContractRuntime Protocol
# ---------------------------------------------------------------------------

class _FakeRuntime:
    """Minimal implementation satisfying the Protocol."""

    def invoke(
        self,
        conn: sqlite3.Connection,
        contract: dict[str, Any],
        member: dict[str, Any],
        unit: dict[str, Any],
        *,
        cwd: str,
    ) -> InvokeResult:
        return InvokeResult(
            handle=RunHandle(),
            stdout="",
            stderr="",
            returncode=0,
            timed_out=False,
        )

    def status(self, handle: RunHandle) -> RunStatus:
        return RunStatus.completed

    def cancel(self, handle: RunHandle) -> bool:
        return False


class TestContractRuntime:
    def test_runtime_checkable(self):
        """A class implementing all 3 methods satisfies the Protocol."""
        assert isinstance(_FakeRuntime(), ContractRuntime)

    def test_non_runtime_fails(self):
        """An object missing methods does not satisfy the Protocol."""
        assert not isinstance(object(), ContractRuntime)
