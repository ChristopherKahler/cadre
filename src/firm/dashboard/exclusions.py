"""Global exclusions — the operator's cadre-ignore list.

One config above every firm and every firms root: the MCP servers, skills,
commands, and CLIs the operator never wants offered during founding. Excluded
items are noise until the day one is needed — the equip/train flow shows them
in a collapsed drawer and un-excludes in place, so the list is reversible in
real time without ever leaving the flow.

Machine tier on purpose (``~/.cadre/exclusions.json``): the inventory being
excluded lives at the operator level, and the founding agent's arsenal is
built before any firm — or firms root — is in scope.
"""

from __future__ import annotations

import json
from pathlib import Path

KINDS = ("mcp", "skills", "commands", "clis")


def _path() -> Path:
    return Path.home() / ".cadre" / "exclusions.json"


def load() -> dict[str, list[str]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {k: sorted({str(n) for n in (data.get(k) or [])}) for k in KINDS}


def toggle(kind: str, name: str, excluded: bool) -> dict[str, list[str]]:
    """Add or remove one item; returns the full config after the change."""
    if kind not in KINDS:
        raise ValueError(f"unknown exclusion kind {kind!r}")
    name = str(name).strip()
    if not name:
        raise ValueError("empty name")
    data = load()
    names = set(data[kind])
    (names.add if excluded else names.discard)(name)
    data[kind] = sorted(names)
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")
    return data


def excluded_set(kind: str) -> set[str]:
    return set(load().get(kind) or [])
