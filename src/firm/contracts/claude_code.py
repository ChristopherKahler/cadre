"""Claude Code Contract runtime — ``claude --print`` subprocess invocation.

Implements ``ContractRuntime`` by delegating to the existing PULSE spawn
and prompt-assembly infrastructure.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from firm.contracts.interface import InvokeResult, RunHandle, RunStatus
from firm.pulse.prompt import assemble_prompt
from firm.pulse.spawn import _active_pids, spawn_member_run


class ClaudeCodeRuntime:
    """Contract runtime for ``claude --print`` subprocess invocation."""

    def invoke(
        self,
        conn: sqlite3.Connection,
        contract: dict[str, Any],
        member: dict[str, Any],
        unit: dict[str, Any],
        *,
        cwd: str,
        run_id: str | None = None,
    ) -> InvokeResult:
        firm_id = member["firm_id"]
        member_id = member["id"]
        unit_id = unit["id"]

        timeout = self._get_timeout(contract)
        # The Unit outranks the Contract: the Contract says who this Member
        # IS, the Unit says what THIS work is worth. unit.model ?? contract
        # model ?? session default — the per-run half of the cost lever.
        model = str(unit.get("model") or "") or self._get_model(contract)
        prompt = assemble_prompt(conn, firm_id, member_id, unit_id, cwd=cwd)
        spawn_result = spawn_member_run(
            prompt, timeout_sec=timeout, cwd=cwd, model=model,
            member_id=member_id, firm_id=firm_id, run_id=run_id,
        )

        return InvokeResult(
            handle=RunHandle(
                run_id=run_id,
                pid=spawn_result.pid,
                metadata={"timeout_sec": timeout, "model": model},
            ),
            stdout=spawn_result.stdout,
            stderr=spawn_result.stderr,
            returncode=spawn_result.returncode,
            timed_out=spawn_result.timed_out,
            prompt_snapshot=prompt,
        )

    def status(self, handle: RunHandle) -> RunStatus:
        if handle.pid is None:
            return RunStatus.completed
        if handle.pid in _active_pids:
            return RunStatus.running
        return RunStatus.completed

    def cancel(self, handle: RunHandle) -> bool:
        if handle.pid is None:
            return False
        proc = _active_pids.get(handle.pid)
        if proc is None:
            return False
        proc.kill()
        _active_pids.pop(handle.pid, None)
        return True

    @staticmethod
    def _pulse_config(contract: dict[str, Any]) -> dict[str, Any]:
        pc = contract.get("pulse_config")
        if isinstance(pc, str):
            try:
                pc = json.loads(pc)
            except (json.JSONDecodeError, TypeError):
                pc = None
        return pc if isinstance(pc, dict) else {}

    @staticmethod
    def _get_timeout(contract: dict[str, Any]) -> int:
        """Extract timeout_sec from contract.pulse_config, default 300."""
        return int(ClaudeCodeRuntime._pulse_config(contract).get("timeout_sec", 300))

    @staticmethod
    def _get_model(contract: dict[str, Any]) -> str | None:
        """Extract the model override from contract.pulse_config (cost lever)."""
        model = ClaudeCodeRuntime._pulse_config(contract).get("model")
        return str(model) if model else None
