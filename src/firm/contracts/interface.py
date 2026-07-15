"""Contract runtime interface types.

Defines the 3-method Protocol (invoke / status / cancel) that every
Contract runtime must satisfy, plus the data types exchanged across the
boundary.
"""

from __future__ import annotations

import dataclasses
import enum
import sqlite3
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------

class RunStatus(enum.Enum):
    """Lifecycle state of a Contract invocation."""

    running = "running"
    completed = "completed"
    failed = "failed"
    timed_out = "timed_out"
    cancelled = "cancelled"


@dataclasses.dataclass
class RunHandle:
    """Opaque handle returned by ``invoke`` for ``status`` / ``cancel``.

    Runtimes populate whichever fields are meaningful for their backend.
    """

    run_id: str | None = None
    pid: int | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class InvokeResult:
    """Result of a single ``ContractRuntime.invoke`` call.

    Carries everything the caller (PULSE runner) needs for post-processing:
    the raw output, process metadata, and the prompt snapshot for audit.
    """

    handle: RunHandle
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool
    prompt_snapshot: str = ""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ContractRuntime(Protocol):
    """3-method interface for runtime-swappable Contract execution.

    Runtimes implement this Protocol (structural typing — no inheritance
    required) to plug into the framework.
    """

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
        """Start execution of a Member on a Unit.

        Args:
            conn: SQLite connection for DB reads (prompt assembly, etc.).
            contract: The Contract row dict.
            member: The Member row dict.
            unit: The Unit row dict.
            cwd: Working directory for the execution environment.
            run_id: The member_run id for this invocation. Runtimes should make
                it available to the execution environment (e.g. exported as
                ``CADRE_RUN_ID``) so tools the Member shells out to can attribute
                their spend/output to the run without threading the id.

        Returns:
            InvokeResult with process output and metadata.
        """
        ...

    def status(self, handle: RunHandle) -> RunStatus:
        """Check the current state of a running invocation."""
        ...

    def cancel(self, handle: RunHandle) -> bool:
        """Abort a running invocation. Returns True if successfully cancelled."""
        ...
