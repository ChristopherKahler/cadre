"""Versionable JSON snapshots of a firm database.

Proven necessary 2026-07-12: Board-locked tooling decisions written straight
into crows-and-pawns' db were overwritten by a seed resync, and nothing —
not git, not ``records``, not ``firm_rev`` — could recover the original text.
It had to be reconstructed from spec prose.

A snapshot is a plain-text dump of every entity table under
``.firm/snapshots/``, deliberately committable (firm .gitignores exclude
``.firm/*.db``, not the directory) — so any bad write becomes a git diff
instead of a loss. Deterministic ordering keeps those diffs readable.

Restore is deliberately manual: read the JSON, write back through the
service layer. An automated restore verb would be a second write path
around the audit trail.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Transient coordination state — meaningless to recover, noisy to diff.
# Note on secrets: NO secret value lives in this database. firm_secret is a
# registry (names, env_var_name, rotation cadence — verified schema, no value
# column) and notify_config stores env var NAMES; actual values live only in
# the Fernet vault file. If a value-bearing column is ever added, it must be
# excluded here before it ships.
_SKIP_TABLES = {"sqlite_sequence", "pulse_lock", "firm_rev"}


def snapshots_dir(workspace: Path) -> Path:
    return workspace / ".firm" / "snapshots"


def take(conn: Any, workspace: Path, label: str = "manual") -> Path:
    """Dump every entity table to one timestamped JSON file. Returns the path.

    Table names come from ``sqlite_master`` (never caller input), rows are
    ordered by ``id`` where one exists so re-snapshots diff cleanly.
    """
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-") or "manual"

    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

    entities: dict[str, list[dict[str, Any]]] = {}
    for t in tables:
        if t in _SKIP_TABLES:
            continue
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info({t})")]
        order = "id" if "id" in cols else cols[0]
        rows = conn.execute(f"SELECT * FROM {t} ORDER BY {order}").fetchall()
        entities[t] = [dict(zip(cols, tuple(r))) for r in rows]

    out_dir = snapshots_dir(workspace)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{ts}-{safe}.json"
    # Same second, same label (a seed re-run in a tight loop): suffix, never clobber.
    n = 1
    while path.exists():
        n += 1
        path = out_dir / f"{ts}-{safe}-{n}.json"

    path.write_text(json.dumps({
        "taken_at": ts,
        "label": label,
        "counts": {t: len(rs) for t, rs in sorted(entities.items())},
        "entities": entities,
    }, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return path
