"""Process spawn for PULSE Member runs.

Wraps ``claude --print --output-format stream-json --verbose`` in a managed
subprocess with timeout enforcement and PID tracking for abort support.
"""

from __future__ import annotations

import dataclasses
import json
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
    # Members' MCP surface is EXACTLY the firm workspace's .mcp.json (passed
    # via --mcp-config below), never the operator's user-scope/plugin fleet.
    # Without strict, headless spawns inherited the operator's entire personal
    # MCP config (Gmail/Slack/Drive/... — 387 tools measured on wastelander,
    # 2026-07-10) under --dangerously-skip-permissions: a loadout-discipline
    # violation and a prompt-size tax on every run. Strict is unconditional:
    # a firm with no .mcp.json means Members get no MCP servers, by design.
    "--strict-mcp-config",
]


LEAN = "lean"
FULL = "full"


def full_load(cwd: str | None) -> bool:
    """LEGACY firm-default posture: ``.firm/spawn.json`` → ``{"full": true}``.

    The original, firm-only posture store — kept as a READ-ONLY back-compat
    fallback so firms founded with this file keep their chosen posture. It is
    now the THIRD tier of :func:`resolve_posture`, consulted only when neither
    the member nor the firm row states a posture. Nothing writes it anymore:
    the Board's switches write ``firm.loadout_posture`` / ``member.loadout_posture``
    through ``services/posture.py``, and an explicit DB posture shadows this file.
    """
    if not cwd:
        return False
    try:
        with open(os.path.join(cwd, ".firm", "spawn.json"), encoding="utf-8") as f:
            return bool(json.load(f).get("full"))
    except (OSError, ValueError):
        return False


def _stated(row: dict | None) -> str | None:
    """The posture a firm/member row explicitly states, or None to inherit.

    Anything unrecognised reads as None — an unparseable posture must inherit
    the safe direction, never fall through to FULL.
    """
    if not row:
        return None
    value = row.get("loadout_posture")
    return value if value in (LEAN, FULL) else None


def resolve_posture(
    member: dict | None = None,
    firm: dict | None = None,
    cwd: str | None = None,
) -> str:
    """Effective trust posture for ONE member run.

    Resolution order — first explicit answer wins, and every fallthrough lands
    on the safe direction:

        1. ``member.loadout_posture``  — the Board's per-member override
        2. ``firm.loadout_posture``    — the Board's firm default
        3. ``.firm/spawn.json {"full": true}`` — legacy firm default (read-only)
        4. LEAN                        — the default, and the safe path

    FULL is only ever reached by an affirmative statement at some tier. There
    is deliberately no path here where absent/malformed state yields FULL: the
    whole point of this control is that the dangerous posture is deliberate.
    """
    stated = _stated(member) or _stated(firm)
    if stated:
        return stated
    return FULL if full_load(cwd) else LEAN


def mcp_config_path(cwd: str | None) -> str | None:
    """Absolute path to the firm workspace's ``.mcp.json``, or None.

    Passed explicitly as ``--mcp-config`` so the firm MCP server loads
    deterministically in headless mode — project-scope ``.mcp.json``
    auto-loading depends on per-project trust state in ``~/.claude.json``
    and has shifted across claude versions; Member runs must not.
    """
    if not cwd:
        return None
    path = os.path.join(cwd, ".mcp.json")
    return path if os.path.isfile(path) else None


def expected_mcp_servers(cwd: str | None) -> list[str]:
    """Server names the firm's ``.mcp.json`` declares — the toolset every
    Member run is entitled to. Used by the runner's MCP startup guard.
    Malformed or absent config yields [] (guard disarms; never false-fails).
    """
    path = mcp_config_path(cwd)
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    return list(servers)


def _is_execable(path: str) -> bool:
    """True only if the kernel can actually exec this file.

    ``shutil.which`` checks the execute BIT, which a broken shim satisfies.
    The nvm-installed ``claude`` on some machines is a shell stub with no
    shebang pointing at a ``claude.exe`` that was never installed; exec'ing it
    raises OSError(errno.ENOEXEC) and the founding agent dies with a message
    that reads like a Cadre bug. Accept an ELF image or a real shebang, which
    is what execve itself will accept.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    return head[:4] == b"\x7fELF" or head[:2] == b"#!"


def resolve_claude_bin() -> tuple[str | None, str]:
    """Resolve the claude binary Members run on.

    Order: ``$CADRE_CLAUDE_BIN`` (explicit, must be executable) -> every
    ``claude`` on PATH, first one the kernel can actually exec -> the native
    install at ``~/.local/bin/claude``. Returns (path-or-None, detail); detail
    carries the honest failure reason so callers never surface a bare EACCES
    as a permissions bug, or an ENOEXEC as a missing binary.
    """
    env_bin = os.environ.get("CADRE_CLAUDE_BIN")
    if env_bin:
        if os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
            if not _is_execable(env_bin):
                return None, (
                    f"CADRE_CLAUDE_BIN={env_bin} has the execute bit but is "
                    "neither an ELF binary nor a script with a shebang -- "
                    "exec would fail with ENOEXEC"
                )
            return env_bin, f"CADRE_CLAUDE_BIN={env_bin}"
        return None, (
            f"CADRE_CLAUDE_BIN={env_bin} is not an executable file -- "
            "fix the env var or unset it to fall back to PATH lookup"
        )

    # Walk the whole PATH rather than taking shutil.which's first hit: a
    # systemd-spawned hub inherits a different PATH order than the operator's
    # login shell, and the first hit there can be the dead nvm shim.
    rejected: list[str] = []
    seen: set[str] = set()
    entries = (os.environ.get("PATH") or "").split(os.pathsep)
    entries.append(os.path.join(os.path.expanduser("~"), ".local", "bin"))
    for directory in entries:
        if not directory:
            continue
        cand = os.path.join(directory, "claude")
        if cand in seen:
            continue
        seen.add(cand)
        if not (os.path.isfile(cand) and os.access(cand, os.X_OK)):
            continue
        if _is_execable(cand):
            note = f"PATH resolution: {cand}"
            if rejected:
                note += f" (skipped un-execable: {', '.join(rejected)})"
            return cand, note
        rejected.append(cand)

    if rejected:
        return None, (
            "every `claude` found is un-execable (no ELF header, no shebang): "
            + ", ".join(rejected)
            + " -- install the native CLI or point CADRE_CLAUDE_BIN at one"
        )
    return None, (
        "no runnable `claude` on PATH and CADRE_CLAUDE_BIN unset -- "
        "the Member runtime is not wired (set CADRE_CLAUDE_BIN to an "
        "executable claude, e.g. ~/.local/bin/claude)"
    )


def spawn_member_run(
    prompt: str,
    *,
    timeout_sec: int = 300,
    cwd: str | None = None,
    model: str | None = None,
    member_id: str | None = None,
    firm_id: str | None = None,
    run_id: str | None = None,
    posture: str | None = None,
) -> SpawnResult:
    """Spawn a ``claude --print`` process and capture output on completion.

    Args:
        prompt: The assembled one-shot prompt string.
        timeout_sec: Maximum wall-clock seconds before SIGTERM.
        cwd: Working directory for the child process.
        posture: Effective loadout posture for THIS member (``"lean"``/``"full"``),
            resolved by the caller that holds the member + firm rows (the
            ClaudeCodeRuntime adapter). ``None`` resolves the firm-level answer
            from ``cwd`` alone — the back-compat path for callers with no
            member context. Only ``"full"`` drops ``--strict-mcp-config``.
        model: Optional ``--model`` override from the Member's Contract
            (``pulse_config.model``) — the per-contract cost lever; cheap
            roles don't need the top model. None = runtime default.
        member_id: Exported as ``CADRE_MEMBER_ID`` into the child env so
            tools the Member shells out to (e.g. squad) resolve the acting
            member deterministically instead of trusting a claimed name.
        firm_id: Exported as ``FIRM_ID`` alongside it.
        run_id: Exported as ``CADRE_RUN_ID`` so those tools attribute their
            spend/output to THIS run (gen_spend.record reads it as a fallback).

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
    # Posture is resolved per MEMBER (override → firm default → legacy file →
    # lean); only an affirmative FULL unbounds the loadout. An unresolved or
    # unrecognised posture keeps strict — the flag comes off on purpose or not
    # at all.
    if (posture or resolve_posture(cwd=cwd)) == FULL:
        cmd.remove("--strict-mcp-config")
    mcp_config = mcp_config_path(cwd)
    if mcp_config:
        cmd += ["--mcp-config", mcp_config]
    if model:
        cmd += ["--model", model]
    cmd += ["-p", prompt]

    env = dict(os.environ)
    if cwd:
        # Firm vault → child env (member run + every MCP server it spawns).
        # setdefault: an operator's explicit shell export still wins.
        try:
            from pathlib import Path

            from firm.secrets.provider import resolve_provider
            for k, v in resolve_provider().resolve(Path(cwd)).items():
                env.setdefault(k, v)
        except Exception:
            pass   # vault is additive — it must never block a member run
    # Board credentials never enter a Member run: the dashboard's POST gate
    # (X-Cadre-Board-Token) would be meaningless if the token rode in on the
    # inherited shell env or a future vault entry.
    env.pop("CADRE_BOARD_TOKEN", None)
    if member_id:
        env["CADRE_MEMBER_ID"] = member_id
    if firm_id:
        env["FIRM_ID"] = firm_id
    if run_id:
        # Tools the Member shells out to (gen_spend loggers, squad) attribute
        # their work to THIS run without threading the id through every call.
        env["CADRE_RUN_ID"] = run_id

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=env,
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
