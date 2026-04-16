#!/usr/bin/env python3
"""SessionStart:startup entrypoint for firm session-pulse.

Copied verbatim to ``<workspace>/.claude/hooks/firm-session-pulse.py`` by
``install/hook-installer.py``. Reads Claude Code's stdin JSON payload,
resolves the workspace from ``cwd``, opens ``.firm/firm.db``, and prints
whatever tags ``firm.hooks.session_pulse.render`` produces.

Contract per ``.paul/phases/02-hook-layer/02-01-BRIEF.md`` §3.1:
- Exit 0 always — hook must never block session start.
- Silent (no stdout, no stderr) on any failure: missing ``.firm/``, malformed
  JSON, import error, DB lock, etc.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _resolve_workspace() -> Path | None:
    """Read stdin JSON and return the workspace path, or None if unavailable."""
    try:
        payload_raw = sys.stdin.read()
        if not payload_raw.strip():
            return Path.cwd()
        payload = json.loads(payload_raw)
    except (json.JSONDecodeError, ValueError, OSError):
        return Path.cwd()
    cwd = payload.get("cwd")
    if cwd:
        return Path(cwd)
    return Path.cwd()


def _add_firm_package_to_path(workspace: Path) -> bool:
    """Make ``firm`` importable if the framework repo is discoverable.

    Search order:
      1. ``$FIRM_SRC`` env var (explicit override for installers/tests)
      2. ``<workspace>/src`` (repo-root install)
      3. ``<workspace>/apps/agent-company-architecture/src`` (satellite install)
    """
    candidates: list[Path] = []
    env_src = os.environ.get("FIRM_SRC")
    if env_src:
        candidates.append(Path(env_src))
    candidates.append(workspace / "src")
    candidates.append(workspace / "apps" / "agent-company-architecture" / "src")

    for candidate in candidates:
        if (candidate / "firm" / "__init__.py").exists():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return True
    return False


def main() -> int:
    workspace = _resolve_workspace()
    if workspace is None:
        return 0

    db_path = workspace / ".firm" / "firm.db"
    if not db_path.exists():
        return 0

    if not _add_firm_package_to_path(workspace):
        return 0

    try:
        from firm.core.db import db_connection
        from firm.hooks.session_pulse import render
    except ImportError:
        return 0

    firm_id = os.environ.get("FIRM_ID", "chrisai")
    # FIRM_NOW_OVERRIDE is a test hatch: an ISO timestamp used to freeze
    # time-ago / expiry-class rendering for deterministic golden-file tests.
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
            output = render(conn, firm_id, now=now_override)
    except Exception:
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
        # Absolute last-resort guard: hook must never block session start.
        sys.exit(0)
