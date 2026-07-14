"""The Armory — the operator's machine-wide inventory, persisted.

Founding and Train survey the machine and throw the result away. The Armory
runs the same discovery surveys once and keeps the structured result at
``~/.cadre/inventory.json`` (machine tier, beside ``exclusions.json`` — the
arsenal exists before any firm or firms-root does). Consumers:

  - the founding arsenal (``founding._inventory`` renders its prompt from here)
  - the Floor's equip picker (``GET /api/inventory``)
  - anything else that asks "what does this machine actually have"

It stores DISPLAY/SEARCH truth only: masked env previews, identity lines,
descriptions. It never persists a runnable spec or a secret — the equip write
path resolves specs live through ``discovery.raw_specs`` at the moment of
equipping, so nothing here can go stale into a firm's config.

Freshness: the file-backed scans (MCP, skills, commands) are cheap and re-run
on every sync. CLI identity probes are the slow, networked part; the result
carries ``cli_verified_at`` so consumers whose promise is "probed moments ago"
(founding) can demand a re-probe with ``ensure(max_cli_age_sec=...)``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# picker/loadout kind → exclusions.json kind (its historical plural)
_EX_KIND = {"mcp": "mcp", "skills": "skills", "commands": "commands", "cli": "clis"}


def _path() -> Path:
    return Path.home() / ".cadre" / "inventory.json"


def sync() -> dict[str, Any]:
    """Survey the machine and persist the result. Returns the fresh inventory."""
    from firm.dashboard import discovery

    know = discovery.knowledge_survey(None)
    now = datetime.now(tz=timezone.utc).isoformat()
    inv = {
        "generated_at": now,
        "cli_verified_at": now,
        # machine tier — surveyed against a workspace with no .mcp.json so no
        # firm's own servers leak in as "equipped"
        "mcp": discovery.mcp_survey(_path().parent)["servers"],
        "skills": know["skills"],
        "commands": know["commands"],
        "cli": discovery.cli_survey(),
    }
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(inv, indent=1), encoding="utf-8")
    return inv


def load() -> dict[str, Any] | None:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def ensure(max_cli_age_sec: float | None = None) -> dict[str, Any]:
    """The inventory — synced if missing, re-probed when the CLI identity
    claims are older than *max_cli_age_sec* (founding's "probed moments ago")."""
    inv = load()
    if inv is None:
        return sync()
    if max_cli_age_sec is not None:
        try:
            ts = datetime.fromisoformat(str(inv.get("cli_verified_at") or ""))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (datetime.now(tz=timezone.utc) - ts).total_seconds()
        except ValueError:
            age = float("inf")
        if age > max_cli_age_sec:
            return sync()
    return inv


def view(
    kind: str | None = None, q: str = "", include_excluded: bool = False,
) -> dict[str, Any]:
    """The inventory for a consumer — exclusion flags applied live (they can
    change without a resync), optional kind filter and substring search.
    Excluded items are dropped unless asked for: the same boundary the
    founding arsenal honors."""
    from firm.dashboard import exclusions

    inv = ensure()
    ex = exclusions.load()
    ql = q.strip().lower()

    def _hit(entry: dict[str, Any]) -> bool:
        if not ql:
            return True
        hay = " ".join(
            str(entry.get(k) or "")
            for k in ("name", "description", "what", "detail", "command", "source")
        ).lower()
        return ql in hay

    out: dict[str, Any] = {
        "generated_at": inv.get("generated_at"),
        "cli_verified_at": inv.get("cli_verified_at"),
    }
    for k in ("mcp", "skills", "commands", "cli"):
        if kind and k != kind:
            out[k] = []
            continue
        excluded_names = set(ex.get(_EX_KIND[k]) or [])
        items = []
        for it in inv.get(k) or []:
            if not isinstance(it, dict):
                continue
            entry = dict(it)
            entry["excluded"] = entry.get("name") in excluded_names
            if entry["excluded"] and not include_excluded:
                continue
            if not _hit(entry):
                continue
            items.append(entry)
        out[k] = items
    return out
