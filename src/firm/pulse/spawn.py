"""Process spawn for PULSE Member runs.

Wraps ``claude --print --output-format stream-json --verbose`` in a managed
subprocess with timeout enforcement and PID tracking for abort support.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys


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


# Header prefixes an exec will accept, by platform. A shebang travels
# everywhere POSIX; the native image format does not travel at all.
#
# Enumerating only ELF is what made Cadre unusable off Linux: every Mach-O
# binary on macOS and every PE binary on Windows read as un-execable, so
# resolve_claude_bin refused the real `claude`, spawn_member_run returned
# "spawn aborted before exec", and `firm pulse` reported "runtime-not-wired"
# on a machine where the runtime was installed and fine. The operator-facing
# message even named ELF, which is meaningless on the host reading it.
_SHEBANG = b"#!"
_ELF = b"\x7fELF"
_MACHO: tuple[bytes, ...] = (
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",   # Mach-O 32-bit, BE / LE
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",   # Mach-O 64-bit, BE / LE
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",   # universal ("fat"), BE / LE
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",   # universal 64-bit, BE / LE
)


def _native_format() -> str:
    """What this host's exec accepts, for operator-facing messages."""
    if sys.platform == "darwin":
        return "Mach-O"
    if sys.platform == "win32":
        return "PE"
    return "ELF"


def _is_execable(path: str) -> bool:
    """True only if this host can actually exec this file.

    ``shutil.which`` checks the execute BIT, which a broken shim satisfies.
    The nvm-installed ``claude`` on some machines is a shell stub with no
    shebang pointing at a ``claude.exe`` that was never installed; exec'ing it
    raises OSError(errno.ENOEXEC) and the founding agent dies with a message
    that reads like a Cadre bug. So sniff the header for what execve itself
    will accept -- but for THIS kernel, not for Linux.

    Windows is deliberately not header-sniffed. ENOEXEC-on-a-shebang-less-stub
    is a POSIX failure mode; Windows decides executability by extension, and a
    ``.cmd``/``.bat`` wrapper carries no magic number at all. CreateProcess
    either resolves the file or raises, and spawn_member_run already reports
    that honestly -- a header gate there rejects working wrappers and buys
    nothing.

    Read at call time rather than import time so the platform branch is
    testable from any host.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    if sys.platform == "win32":
        return True
    if sys.platform == "darwin":
        return head.startswith((_SHEBANG, *_MACHO))
    return head.startswith((_SHEBANG, _ELF))


def _candidate_names() -> list[str]:
    """File names a ``claude`` on PATH can actually have on this host.

    POSIX executables carry no extension, so one name is enough there.
    Windows resolves a bare command name through PATHEXT and will not run an
    extension-less file from a PATH lookup at all. Looking only for "claude"
    therefore found nothing on Windows while ``claude.EXE`` sat in the very
    same directory, and the resolver told the operator there was no runnable
    claude on PATH while they were looking straight at one. That is the same
    false-reason failure as the ELF-only header sniff, at a second site, and
    it hits the DEFAULT path rather than the configured one: a new operator
    has claude.EXE on PATH and has never heard of CADRE_CLAUDE_BIN.

    PATHEXT is semicolon-separated on every Windows, so it is split on ";"
    rather than os.pathsep -- os.pathsep follows the host running the code,
    which is not the same thing once the platform branch is under test.
    """
    if sys.platform != "win32":
        return ["claude"]
    raw = os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD"
    names = ["claude" + ext.strip() for ext in raw.split(";") if ext.strip()]
    # A full path to an extension-less PE image still execs, so keep the bare
    # name as a last resort rather than dropping a case that used to work.
    names.append("claude")
    return names


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
                    f"neither a {_native_format()} image nor a script with a "
                    "shebang -- exec would fail with ENOEXEC"
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
    names = _candidate_names()
    for directory in entries:
        if not directory:
            continue
        for name in names:
            cand = os.path.join(directory, name)
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
            f"every `claude` found is un-execable (no {_native_format()} "
            "header, no shebang): "
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

    # --- the Member's relay title -----------------------------------------
    # base assigns every headless session a relay codename from a name pool
    # ("tapir", "puffin") and prints a ~7 KB wake contract for it. A firm whose
    # Members are all called after small mammals cannot be steered by name from
    # the Boardroom, so the title is pinned to <firm>-<member> here.
    #
    # Two things had to be true for that to work, both measured against base
    # 0.15.0 (md5 052b9a95d6afbd938ddf21aba26b0601) on 2026-09-10:
    #
    # 1. WT_SESSION must not reach the child. base binds a title partly by that
    #    GUID, so a second session presenting the same one RECLAIMS the title
    #    already bound to it instead of taking the pinned name. Measured: with
    #    WT_SESSION unset or distinct per run, three runs took three distinct
    #    pinned titles; with one shared WT_SESSION, run 2 silently reclaimed run
    #    1's title and the registry held a single row. The hub hit this from the
    #    other side and dropped the same variable for the same reason
    #    (ping-chat-hub, src/ping_hub/spawn/wt.py:118-123: a rebooted `heron`
    #    came back as `chris`). A hub started inside Windows Terminal carries one
    #    in its own environment, and `env = dict(os.environ)` above would hand it
    #    to every Member in the pulse.
    #
    # 2. An INHERITED BASE_RELAY_AS must never survive. If the spawner was itself
    #    pinned — the hub pins every gated child — every Member of every firm
    #    would register under the spawner's title, and whichever answered first
    #    would clear relay alerts meant for the others. So this is an assignment,
    #    never a setdefault, and the variable is removed outright when there is
    #    no member to name.
    env.pop("WT_SESSION", None)
    if firm_id and member_id:
        env["BASE_RELAY_AS"] = f"{firm_id}-{member_id}"
    else:
        env.pop("BASE_RELAY_AS", None)

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
