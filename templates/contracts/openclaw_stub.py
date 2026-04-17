"""OpenClaw Contract runtime stub.

Copy to ``src/firm/contracts/openclaw.py`` and implement the 3 Protocol
methods (invoke, status, cancel). Register with the runtime resolver by
setting ``Contract.runtime_type = "openclaw"`` on any Contract that should
use this runtime.

Registration example (in your project setup):

    from firm.contracts.registry import register_runtime
    from firm.contracts.openclaw import OpenClawRuntime

    register_runtime("openclaw", OpenClawRuntime())

See docs/contracts.md for the full walkthrough.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from firm.contracts import ContractRuntime, InvokeResult, RunHandle, RunStatus


class OpenClawRuntime:
    """OpenClaw-backed ContractRuntime. Satisfies the ContractRuntime Protocol.

    Structural typing: this class does not inherit ContractRuntime; Python
    recognizes it as a valid implementation because the method signatures
    match.
    """

    def invoke(
        self,
        conn: sqlite3.Connection,
        contract: dict[str, Any],
        member: dict[str, Any],
        unit: dict[str, Any],
        *,
        cwd: str,
    ) -> InvokeResult:
        """Start an OpenClaw invocation for the given Member + Unit.

        TODO: Implement this runtime:
        - Build the prompt from contract.skill_loadout + unit context
        - Spawn your OpenClaw process (subprocess / API call / etc.)
        - Capture stdout / stderr / exit code
        - Return an InvokeResult with handle + output + prompt_snapshot
        """
        raise NotImplementedError("OpenClawRuntime.invoke — implement OpenClaw spawn")

    def status(self, handle: RunHandle) -> RunStatus:
        """Return current lifecycle state for a previously-invoked run.

        TODO: Look up the handle's run_id / pid and map the runtime's
        native status to one of: running, completed, failed, timed_out,
        cancelled.
        """
        raise NotImplementedError("OpenClawRuntime.status — implement status lookup")

    def cancel(self, handle: RunHandle) -> bool:
        """Abort a running invocation. Return True on successful cancel.

        TODO: Terminate the OpenClaw process or API session associated
        with the handle. Return False if already terminal or not found.
        """
        raise NotImplementedError("OpenClawRuntime.cancel — implement cancellation")


# Protocol conformance check (optional — runs at import time if uncommented):
# _: ContractRuntime = OpenClawRuntime()
