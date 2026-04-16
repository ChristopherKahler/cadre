"""Process spawn for PULSE Member runs.

Wraps ``claude --print --output-format stream-json --verbose`` in a managed
subprocess with timeout enforcement and PID tracking for abort support.
"""

from __future__ import annotations

import dataclasses
import subprocess


# ---------------------------------------------------------------------------
# PID tracking (module-level, runtime-only)
# ---------------------------------------------------------------------------

_active_pids: dict[int, subprocess.Popen[str]] = {}
"""Map of PID → Popen handle for in-flight Member runs.

Populated during spawn, cleaned after completion or timeout.
Used by 03.2-06 ``firm pulse --abort`` to send SIGTERM.
"""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class SpawnResult:
    """Outcome of a single ``claude --print`` invocation."""

    returncode: int | None
    stdout: str
    stderr: str
    pid: int | None
    timed_out: bool = False


# ---------------------------------------------------------------------------
# Spawn
# ---------------------------------------------------------------------------

_CLAUDE_ARGS: list[str] = [
    "claude",
    "--print",
    "--output-format", "stream-json",
    "--verbose",
    "--dangerously-skip-permissions",
]


def spawn_member_run(
    prompt: str,
    *,
    timeout_sec: int = 300,
    cwd: str | None = None,
) -> SpawnResult:
    """Spawn a ``claude --print`` process and capture output on completion.

    Args:
        prompt: The assembled one-shot prompt string.
        timeout_sec: Maximum wall-clock seconds before SIGTERM.
        cwd: Working directory for the child process.

    Returns:
        SpawnResult with captured stdout/stderr and process metadata.
    """
    cmd = [*_CLAUDE_ARGS, "-p", prompt]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )
    except (FileNotFoundError, OSError) as exc:
        return SpawnResult(
            returncode=-1,
            stdout="",
            stderr=str(exc),
            pid=None,
            timed_out=False,
        )

    # Track PID for abort support
    _active_pids[proc.pid] = proc

    try:
        stdout, stderr = proc.communicate(timeout=timeout_sec)
        return SpawnResult(
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            pid=proc.pid,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        return SpawnResult(
            returncode=None,
            stdout=stdout,
            stderr=stderr,
            pid=proc.pid,
            timed_out=True,
        )
    finally:
        _active_pids.pop(proc.pid, None)
