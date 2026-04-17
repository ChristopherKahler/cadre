"""Install Cadre hooks into a Claude Code workspace.

Ships the session-pulse hook as an embedded template (so `pip install cadre`
users don't need the repo cloned). Registers the hook in the workspace's
`.claude/settings.json` under `hooks.SessionStart`. Idempotent.

Unit-completion is NOT installed as a Claude Code hook — it's a callable
function invoked from `firm unit complete` (Phase 2 decision).
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

HOOK_SCRIPT_NAME = "cadre-session-pulse.py"
HOOK_COMMAND = f"python3 $CLAUDE_PROJECT_DIR/.claude/hooks/{HOOK_SCRIPT_NAME}"

_HOOK_TEMPLATE = '''#!/usr/bin/env python3
"""SessionStart:startup entrypoint for Cadre session-pulse.

Installed by `cadre init --install-hooks` into <workspace>/.claude/hooks/.
Reads Claude Code's stdin JSON payload, resolves the workspace from `cwd`,
opens `.firm/firm.db`, and prints tags rendered by
`firm.hooks.session_pulse.render`.

Contract:
- Exit 0 always — hook must never block session start.
- Silent on any failure (missing .firm/, malformed JSON, import error, etc.).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _resolve_workspace() -> Path | None:
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
    # Package may be pip-installed — let normal import resolution try.
    return True


def main() -> int:
    workspace = _resolve_workspace()
    if workspace is None:
        return 0

    db_path = workspace / ".firm" / "firm.db"
    if not db_path.exists():
        return 0

    _add_firm_package_to_path(workspace)

    try:
        from firm.core.db import db_connection
        from firm.hooks.session_pulse import render
    except ImportError:
        return 0

    firm_id = os.environ.get("FIRM_ID", "chrisai")
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
        if not output.endswith("\\n"):
            sys.stdout.write("\\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
'''


def _load_settings(settings_path: Path) -> dict:
    if not settings_path.exists():
        return {}
    raw = settings_path.read_text()
    if not raw.strip():
        return {}
    return json.loads(raw)


def _register_hook(settings: dict) -> bool:
    """Add the hook entry if not present. Returns True if modified."""
    hooks = settings.setdefault("hooks", {})
    session_start = hooks.setdefault("SessionStart", [])
    for entry in session_start:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks", []) or []:
            if isinstance(hook, dict) and hook.get("command") == HOOK_COMMAND:
                return False
    session_start.append({
        "matcher": "startup",
        "hooks": [{"type": "command", "command": HOOK_COMMAND}],
    })
    return True


def install_hooks(workspace: Path) -> tuple[int, list[str]]:
    """Install cadre-session-pulse hook + register in settings.json.

    Returns (exit_code, list of status messages).
    """
    messages: list[str] = []

    hooks_dir = workspace / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / HOOK_SCRIPT_NAME

    if dest.exists():
        messages.append(f"Hook already installed: {dest}")
    else:
        dest.write_text(_HOOK_TEMPLATE)
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        messages.append(f"Installed hook: {dest}")

    settings_path = workspace / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings = _load_settings(settings_path)
    if _register_hook(settings):
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        messages.append(f"Registered hook in {settings_path}")
    else:
        messages.append(f"Hook already registered in {settings_path}")

    return 0, messages
