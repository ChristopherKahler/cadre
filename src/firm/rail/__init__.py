"""Rail — chat rails from the Board's messaging apps into headless Co-Board sessions.

A rail turns a messaging surface (Slack today, Telegram next) into the
boardroom: a top-level message in the Board's channel opens a headless
``/boardroom`` session at the firms root, every reply in that thread resumes
the same session (``--resume``), and each answer lands back in the thread.
The daemon is the only inbound path, and the allowlist is structural — a
non-allowlisted user's messages are dropped in code, never left to prompts.

Operator-level state lives under ``~/.cadre/rail/<provider>/`` (``CADRE_HOME``
aware, same root as the vault):

* ``config.json`` — channel, allowlist, mode, timeouts. NO secrets: tokens
  live in the secrets vault at the global tier and travel vault → env →
  process, never disk (founding decision).
* ``threads.json`` — thread ⇄ session map, plain JSON so the operator can
  read what the rail believes.

Slack Web API calls here are form-encoded on purpose: Slack's GET-style
methods (``conversations.info``, ``reactions.get``) silently drop JSON
bodies — a lesson already paid for once in the operator's slack bridge.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from firm.secrets.vault import cadre_home

_HTTP_TIMEOUT_SEC = 30
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


def slack_call(method: str, token: str, **params: Any) -> dict[str, Any]:
    """One Slack Web API call, form-encoded. Never raises — transport
    failures come back as ``{"ok": False, "error": ...}`` so callers handle
    Slack errors and network errors through one shape."""
    encoded: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            encoded[key] = json.dumps(value)
        else:
            encoded[key] = str(value)
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=urllib.parse.urlencode(encoded).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        result = json.loads(body)
        return result if isinstance(result, dict) else {"ok": False, "error": "non-object response"}
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"transport: {exc}"}


def chunk_text(text: str, limit: int = 3800) -> list[str]:
    """Split *text* for Slack messages, preferring newline boundaries."""
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks
