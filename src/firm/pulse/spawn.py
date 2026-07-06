"""Process spawn for PULSE Member runs.

Wraps ``claude --print --output-format stream-json --verbose`` in a managed
subprocess with timeout enforcement and PID tracking for abort support.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
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

_CLAUDE_FLAGS: list[str] = [
    "--print",
    "--output-format", "stream-json",
    "--verbose",
    "--dangerously-skip-permissions",
]


def resolve_claude_bin() -> tuple[str | None, str]:
    """Resolve the claude binary Members run on.

    Order: ``$CADRE_CLAUDE_BIN`` (explicit, must be executable) → ``shutil.which``.
    Returns (path-or-None, detail) — detail carries the honest failure reason so
    callers never surface a bare EACCES as a permissions bug.
    """
    env_bin = os.environ.get("CADRE_CLAUDE_BIN")
    if env_bin:
        if os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
            return env_bin, f"CADRE_CLAUDE_BIN={env_bin}"
        return None, (
            f"CADRE_CLAUDE_BIN={env_bin} is not an executable file — "
            "fix the env var or unset it to fall back to PATH lookup"
        )
    found = shutil.which("claude")
    if found:
        return found, f"PATH resolution: {found}"
    return None, (
        "no runnable `claude` on PATH and CADRE_CLAUDE_BIN unset — "
        "the Member runtime is not wired (set CADRE_CLAUDE_BIN to an "
        "executable claude, e.g. the nvm bin path)"
    )


def spawn_member_run(
    prompt: str,
    *,
    timeout_sec: int = 300,
    cwd: str | None = None,
    model: str | None = None,
) -> SpawnResult:
    """Spawn a ``claude --print`` process and capture output on completion.

    Args:
        prompt: The assembled one-shot prompt string.
        timeout_sec: Maximum wall-clock seconds before SIGTERM.
        cwd: Working directory for the child process.
        model: Optional ``--model`` override from the Member's Contract
            (``pulse_config.model``) — the per-contract cost lever; cheap
            roles don't need the top model. None = runtime default.

    Returns:
        SpawnResult with captured stdout/stderr and process metadata.
    """
    claude_bin, resolve_detail = resolve_claude_bin()
    if claude_bin is None:
        return SpawnResult(
            returncode=-1,
            stdout="",
            stderr=f"spawn aborted before exec: {resolve_detail}",
            pid=None,
            timed_out=False,
        )

    cmd = [claude_bin, *_CLAUDE_FLAGS]
    if model:
        cmd += ["--model", model]
    cmd += ["-p", prompt]

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
            stderr=(
                f"claude failed to exec ({exc}) — resolved via {resolve_detail}; "
                "the binary is likely not runnable from this host (e.g. a Windows "
                ".exe symlink without interop, or wrong arch)"
            ),
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
