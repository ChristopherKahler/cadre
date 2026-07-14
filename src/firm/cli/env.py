"""`cadre env` — the firm secrets vault from the terminal.

Same store the dashboard's Variables page edits. ``exec`` is the universal
consumption wrapper: ``cadre env exec -- <cmd>`` runs any firm tool with
the merged vault injected, so config files (e.g. .mcp.json) never need to
carry a secret or a ${VAR} reference.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path

from firm.secrets.provider import (
    FIRM_TIER,
    GLOBAL_TIER,
    resolve_provider,
    validate_key,
)
from firm.secrets.vault import VaultError


def _tier(global_flag: bool) -> str:
    return GLOBAL_TIER if global_flag else FIRM_TIER


def run_env_set(
    workspace: Path, key: str, value: str | None, global_tier: bool,
) -> int:
    provider = resolve_provider()
    try:
        key = validate_key(key)
        if value is None:
            value = getpass.getpass(f"{key}=")   # hidden prompt — no echo, no history
        if not value:
            print(json.dumps({"ok": False, "error": "empty value — nothing stored"}))
            return 1
        provider.set(workspace, key, value, _tier(global_tier))
    except (ValueError, VaultError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps({
        "ok": True, "key": key, "tier": _tier(global_tier),
        "provider": provider.name,
    }))
    return 0


def run_env_unset(workspace: Path, key: str, global_tier: bool) -> int:
    provider = resolve_provider()
    try:
        provider.unset(workspace, key, _tier(global_tier))
    except (ValueError, VaultError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps({"ok": True, "key": key, "tier": _tier(global_tier)}))
    return 0


def run_env_list(workspace: Path, show: bool) -> int:
    provider = resolve_provider()
    try:
        entries = provider.entries(workspace)
    except VaultError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    for e in entries:
        shadow = "  (overridden by firm)" if e.overridden else ""
        value = e.value if show else "••••" + e.value[-4:] if len(e.value) >= 10 else "••••••"
        print(f"{e.tier:<6} {e.key}={value}{shadow}")
    if not entries:
        print("(vault is empty)", file=sys.stderr)
    return 0


def run_env_exec(workspace: Path, cmd: list[str]) -> int:
    """Replace this process with *cmd*, vault injected. Existing process
    env wins on collision (same setdefault contract as .env loading) so a
    shell override still overrides."""
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print(json.dumps({"ok": False, "error": "usage: cadre env exec -- <cmd> [args…]"}))
        return 1
    provider = resolve_provider()
    try:
        merged = provider.resolve(workspace)
    except VaultError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    env = dict(merged)
    env.update(os.environ)
    try:
        os.execvpe(cmd[0], cmd, env)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": f"cannot exec {cmd[0]!r}: {exc}"}))
        return 1


def run_env_import(workspace: Path, scrub: bool) -> int:
    """Plaintext .env → firm vault (dashboardless path; the dashboard has
    the same button). Records are written only via the dashboard route —
    CLI import on a workspace without a DB connection stays db-free."""
    from firm.sysconfig.service import _parse_env_file
    provider = resolve_provider()
    env_path = workspace / ".env"
    pairs = _parse_env_file(env_path)
    if not pairs:
        print(json.dumps({"ok": False, "error": "no importable KEY=VALUE lines in .env"}))
        return 1
    try:
        for k, v in pairs.items():
            provider.set(workspace, k, v, FIRM_TIER)
        merged = provider.resolve(workspace)
    except (ValueError, VaultError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    missing = [k for k, v in pairs.items() if merged.get(k) != v]
    if missing:
        print(json.dumps({"ok": False,
                          "error": f"verification failed for {missing}"}))
        return 1
    if scrub:
        env_path.unlink()
    print(json.dumps({"ok": True, "imported": sorted(pairs),
                      "count": len(pairs), "scrubbed": scrub,
                      "provider": provider.name}))
    return 0
