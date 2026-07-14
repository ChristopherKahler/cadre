"""Hub-level (board-scoped) extension registry.

Firm extensions install into ONE firm's dashboard (``views.json`` — the
squad path). Framework extensions are portfolio-wide surfaces — a chat rail,
future rails — registered here ONCE and rendered generically by the hub.
Core never names an addon: the registry is data, and every entry is a plain
JSON file the operator can read or delete by hand. No secrets, no code —
v1 entries are links only ``{id, title, icon, url}``; anything executable
stays in the addon's own installer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from firm.secrets.vault import cadre_home

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_URL_RE = re.compile(r"^https?://[^\s\"'<>]+$")


def registry_dir() -> Path:
    path = cadre_home() / "hub-extensions"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def validate(package: Any) -> tuple[dict[str, str] | None, str]:
    """(entry, "") for a valid hub manifest, (None, reason) otherwise.
    Strict on purpose — a rejected upload names exactly what's wrong."""
    if not isinstance(package, dict):
        return None, "package must be a JSON object"
    ext_id = str(package.get("id") or "")
    if not _ID_RE.match(ext_id):
        return None, "id must be a lowercase slug (a-z, 0-9, hyphens)"
    title = str(package.get("title") or "").strip()
    if not 1 <= len(title) <= 80:
        return None, "title is required (max 80 chars)"
    url = str(package.get("url") or "").strip()
    if not _URL_RE.match(url):
        return None, "url must be http(s):// with no spaces or quotes"
    icon = str(package.get("icon") or "").strip()[:8]
    return {"id": ext_id, "title": title, "url": url, "icon": icon}, ""


def save(entry: dict[str, str]) -> Path:
    path = registry_dir() / f"{entry['id']}.json"
    path.write_text(json.dumps(entry, indent=1), encoding="utf-8")
    return path


def load_all() -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in registry_dir().glob("*.json"):
        try:
            entry, err = validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
        if entry is not None:   # a hand-broken file just doesn't render
            entries.append(entry)
    return sorted(entries, key=lambda e: e["title"].lower())


def remove(ext_id: str) -> bool:
    if not _ID_RE.match(ext_id or ""):
        return False
    path = registry_dir() / f"{ext_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True
