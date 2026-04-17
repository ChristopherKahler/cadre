"""Codex Contract runtime stub.

Copy to ``src/firm/contracts/codex.py`` and implement the 3 Protocol
methods (invoke, status, cancel). Register with the runtime resolver by
setting ``Contract.runtime_type = "codex"`` on any Contract that should
use this runtime.

Registration example:

    from firm.contracts.registry import register_runtime
    from firm.contracts.codex import CodexRuntime

    register_runtime("codex", CodexRuntime())

See docs/contracts.md for the full walkthrough.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from firm.contracts import ContractRuntime, InvokeResult, RunHandle, RunStatus


class CodexRuntime:
    """Codex-backed ContractRuntime. Satisfies the ContractRuntime Protocol."""

    def invoke(
        self,
        conn: sqlite3.Connection,
        contract: dict[str, Any],
        member: dict[str, Any],
        unit: dict[str, Any],
        *,
        cwd: str,
    ) -> InvokeResult:
        """Start a Codex invocation for the given Member + Unit.

        TODO: Implement this runtime:
        - Build the prompt from contract.skill_loadout + unit context
        - Call the Codex CLI / SDK for the session
        - Capture stdout / stderr / exit code
        - Return an InvokeResult with handle + output + prompt_snapshot
        """
        raise NotImplementedError("CodexRuntime.invoke — implement Codex call")

    def status(self, handle: RunHandle) -> RunStatus:
        """Return current lifecycle state for a previously-invoked run.

        TODO: Map Codex session status to the RunStatus enum.
        """
        raise NotImplementedError("CodexRuntime.status — implement status lookup")

    def cancel(self, handle: RunHandle) -> bool:
        """Abort a running invocation. Return True on successful cancel.

        TODO: Cancel the Codex session. Return False if already terminal
        or the handle is not recognized.
        """
        raise NotImplementedError("CodexRuntime.cancel — implement cancellation")


# Protocol conformance check (optional — uncomment to enforce at import time):
# _: ContractRuntime = CodexRuntime()
