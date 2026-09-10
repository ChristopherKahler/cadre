#!/usr/bin/env python3
"""SessionStart:startup entrypoint for Cadre session-pulse.

THE ONE COPY. `cadre init --install-hooks` installs this exact file, byte for
byte, into `<workspace>/.claude/hooks/cadre-session-pulse.py`, and the
end-to-end test runs this exact file. Do not add a second copy anywhere; a
guard test fails the build if one appears.

There used to be two: this script under `install/`, and a `_HOOK_TEMPLATE`
string literal inside `firm/cli/install_hooks.py`. The test ran the first and
users got the second, and they drifted 123 diff lines apart. Commit 0d9965a
fixed a Windows encoding defect in the copy the test ran and never reached a
single user, with the suite green the whole time.

Reads Claude Code's stdin JSON payload, resolves the workspace from `cwd`,
opens `.firm/firm.db`, and prints the tags `firm.hooks.session_pulse.render`
produces.

Contract:
- Exit 0 always. The hook must never block session start.
- Silent when there is no firm here: no `.firm/firm.db`, nothing to say.
- LOUD on stderr when there IS a firm here and the hook cannot serve it.
  Silence used to cover that case too, which made a broken hook and an empty
  directory indistinguishable.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _force_utf8_streams() -> None:
    """Take stdin and stdout off the platform locale encoding.

    On Windows a piped stdout defaults to cp1252, and the pulse output carries
    an em dash today and could carry anything tomorrow. Measured on Windows 10
    / Python 3.12.6 with a U+2192 in the output: as shipped the write raises
    UnicodeEncodeError and the hook exits 1 having printed nothing; with the
    streams reconfigured it writes the character and exits 0.

    stdin matters for the same reason. Claude Code sends the payload as UTF-8,
    and a cp1252 decode of a workspace path raises UnicodeDecodeError, which is
    a ValueError, which the caller below catches and answers with Path.cwd().
    Silently the wrong workspace.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # already replaced, or not a real stream: nothing to do


def _resolve_workspace() -> Path:
    """Read stdin JSON and return the workspace path.

    An empty or unparseable payload falls back to the process working
    directory. That fallback is a hazard worth knowing about: a hook invoked
    from the wrong place reads the wrong firm rather than saying so.
    """
    try:
        payload_raw = sys.stdin.read()
        if not payload_raw.strip():
            return Path.cwd()
        payload = json.loads(payload_raw)
    except (json.JSONDecodeError, ValueError, OSError):
        return Path.cwd()
    cwd = payload.get("cwd")
    return Path(cwd) if cwd else Path.cwd()


def _recorded_package_path(workspace: Path) -> str | None:
    """The package path `cadre init --install-hooks` recorded for this firm.

    The hook is registered as bare `python3`, deliberately: the command lands
    in `.claude/settings.json`, which firms commit to git, so an absolute
    machine path there would be right on one machine and wrong on every clone.
    The machine-specific part lives in `.firm/` instead, where machine state
    already lives, and this is the same shape as the FIRM_SRC hatch below.
    """
    marker = workspace / ".firm" / "python-path"
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _firm_package_candidates(workspace: Path) -> list[Path]:
    """Where `firm` might live, most explicit first.

    1. `$FIRM_SRC` — explicit override for installers and tests
    2. `.firm/python-path` — recorded at install time
    3. `<workspace>/src` — repo-root install
    4. `<workspace>/apps/agent-company-architecture/src` — satellite install
    """
    candidates: list[Path] = []
    env_src = os.environ.get("FIRM_SRC")
    if env_src:
        candidates.append(Path(env_src))
    recorded = _recorded_package_path(workspace)
    if recorded:
        candidates.append(Path(recorded))
    candidates.append(workspace / "src")
    candidates.append(workspace / "apps" / "agent-company-architecture" / "src")
    return candidates


def _add_firm_package_to_path(workspace: Path) -> list[str]:
    """Put `firm` on sys.path if we can find it. Returns what was tried.

    The return value is the diagnostic: when the import fails anyway, main()
    reports these paths rather than going quiet. A pip-installed package needs
    none of them, so a miss here is not itself an error.
    """
    tried: list[str] = []
    for candidate in _firm_package_candidates(workspace):
        tried.append(str(candidate))
        if (candidate / "firm" / "__init__.py").exists():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return tried
    return tried


def main() -> int:
    _force_utf8_streams()
    workspace = _resolve_workspace()

    db_path = workspace / ".firm" / "firm.db"
    if not db_path.exists():
        return 0  # no firm here: silence is the right answer

    tried = _add_firm_package_to_path(workspace)

    try:
        from firm.core.db import db_connection, resolve_firm_id
        from firm.hooks.session_pulse import render
    except ImportError as exc:
        # A firm IS here and we cannot serve it. Say so. This used to return 0
        # in silence, which the contract reads as "no firm here", so an
        # operator saw nothing and concluded nothing was wrong.
        sys.stderr.write(
            "cadre session-pulse: {} has a firm database, but the cadre "
            "package could not be imported ({}).\n"
            "  interpreter: {}\n"
            "  paths tried: {}\n"
            "  Re-run `cadre init . --install-hooks` from the environment "
            "cadre is installed in to re-record the path.\n".format(
                workspace, exc, sys.executable,
                ", ".join(tried) or "(none)")
        )
        return 0  # loud, but never block session start

    firm_id = os.environ.get("FIRM_ID")
    # FIRM_NOW_OVERRIDE is a test hatch: an ISO timestamp that freezes
    # time-ago and expiry-class rendering for the deterministic golden test.
    # Production invocations leave it unset; render() defaults to real utcnow.
    now_override_raw = os.environ.get("FIRM_NOW_OVERRIDE")
    now_override = None
    if now_override_raw:
        try:
            from datetime import datetime as _dt
            now_override = _dt.fromisoformat(now_override_raw)
        except ValueError:
            now_override = None

    try:
        with db_connection(workspace) as conn:
            output = render(conn, resolve_firm_id(conn, firm_id),
                            now=now_override)
    except Exception as exc:  # noqa: BLE001 - never block session start
        sys.stderr.write(
            "cadre session-pulse: could not read the firm at {} ({}: {}).\n"
            .format(workspace, type(exc).__name__, exc))
        return 0

    if output:
        sys.stdout.write(output)
        if not output.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Absolute last-resort guard: the hook must never block session start.
        sys.exit(0)
