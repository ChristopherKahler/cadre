#!/usr/bin/env python3
"""Idempotent installer for firm-session-pulse in a Claude Code workspace.

Usage::

    python3 install/hook-installer.py <workspace-path>

Copies ``install/firm-session-pulse.py`` to ``<workspace>/.claude/hooks/``
and registers it under ``hooks.SessionStart`` in ``<workspace>/.claude/
settings.json``. Running twice is a no-op.
"""

from __future__ import annotations

import argparse
import json
import shutil
import stat
import sys
from pathlib import Path

HOOK_COMMAND = "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/firm-session-pulse.py"
HOOK_SCRIPT_NAME = "firm-session-pulse.py"


def _install_script(repo_root: Path, workspace: Path) -> Path:
    source = repo_root / "install" / HOOK_SCRIPT_NAME
    if not source.exists():
        raise FileNotFoundError(f"Hook template missing: {source}")
    hooks_dir = workspace / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / HOOK_SCRIPT_NAME
    shutil.copyfile(source, dest)
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dest


def _load_settings(settings_path: Path) -> dict:
    if not settings_path.exists():
        return {}
    raw = settings_path.read_text()
    if not raw.strip():
        return {}
    return json.loads(raw)


def _register_hook(settings: dict) -> bool:
    """Add the hook entry if not already present. Returns True if modified."""
    hooks = settings.setdefault("hooks", {})
    session_start = hooks.setdefault("SessionStart", [])

    for entry in session_start:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks", []) or []:
            if isinstance(hook, dict) and hook.get("command") == HOOK_COMMAND:
                return False  # already installed

    session_start.append({
        "matcher": "startup",
        "hooks": [{"type": "command", "command": HOOK_COMMAND}],
    })
    return True


def install(workspace: Path, repo_root: Path) -> tuple[bool, Path]:
    """Run the full install. Returns (modified, installed_script_path)."""
    dest = _install_script(repo_root, workspace)
    settings_path = workspace / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings = _load_settings(settings_path)
    modified = _register_hook(settings)
    if modified:
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return modified, dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, help="Target workspace root")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Override agent-company-architecture repo root (auto-detected)",
    )
    args = parser.parse_args(argv)

    workspace = args.workspace.resolve()
    if not workspace.exists():
        print(f"Workspace not found: {workspace}", file=sys.stderr)
        return 2

    modified, dest = install(workspace, args.repo_root)
    if modified:
        print(f"Installed firm-session-pulse → {dest}")
    else:
        print(f"Already installed at {dest} — no changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
