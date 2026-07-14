"""Rail — the shared state store every rail provider rides on.

A rail turns a surface (Slack, Co-Board Chat — both Cadre OS addons) into
the boardroom. The addons ship the daemons; this module owns only the
per-provider state they all share.

Operator-level state lives under ``~/.cadre/rail/<provider>/`` (``CADRE_HOME``
aware, same root as the vault):

* ``config.json`` — channel, allowlist, mode, timeouts. NO secrets: tokens
  live in the secrets vault at the global tier and travel vault → env →
  process, never disk (founding decision).
* ``threads.json`` — thread ⇄ session map, plain JSON so the operator can
  read what the rail believes.

"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from firm.secrets.vault import cadre_home

THREAD_MAX_AGE_DAYS = 30

CONFIG_DEFAULTS: dict[str, Any] = {
    "channel_id": "",
    "allowlist": [],
    "mode": "approve",            # "approve" | "skip"
    "firms_root": "",
    "turn_timeout_sec": 1800,     # board turns think — 30m before SIGKILL
    "approve_timeout_sec": 300,   # unanswered 👍/👎 → deny
    "full_load": False,           # True drops --strict-mcp-config (spawn.json semantics)
    "ack_posts": True,            # instant "on it" reply in the thread per turn
    "model": "",                  # --model for board turns; "" = account default
    "midflight_relay": True,      # steer a live turn via `base relay ping`
    "updates": True,              # in-turn proactive `say` posts (rail protocol)
}


def rail_home() -> Path:
    return cadre_home() / "rail"


def provider_dir(provider: str) -> Path:
    path = rail_home() / provider
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _write_private_json(path: Path, data: Any) -> None:
    """Atomic 0600 write — tmp + replace, same idiom as the vault."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def load_config(provider: str) -> dict[str, Any]:
    """Config with defaults layered under whatever is on disk."""
    path = provider_dir(provider) / "config.json"
    merged = dict(CONFIG_DEFAULTS)
    if path.exists():
        try:
            merged.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return merged


def save_config(provider: str, config: dict[str, Any]) -> Path:
    path = provider_dir(provider) / "config.json"
    _write_private_json(path, config)
    return path


def load_threads(provider: str) -> dict[str, dict[str, Any]]:
    path = provider_dir(provider) / "threads.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_threads(provider: str, threads: dict[str, dict[str, Any]]) -> None:
    _write_private_json(provider_dir(provider) / "threads.json", threads)


def prune_threads(
    threads: dict[str, dict[str, Any]],
    *,
    max_age_days: int = THREAD_MAX_AGE_DAYS,
    now: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Drop entries whose last turn is older than *max_age_days*.

    A pruned thread isn't an error later — the daemon opens a fresh session
    and says so in the reply.
    """
    now = time.time() if now is None else now
    cutoff = now - max_age_days * 86400
    return {
        ts: entry for ts, entry in threads.items()
        if float(entry.get("last_turn", 0)) >= cutoff
    }



