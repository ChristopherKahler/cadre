"""``firm backup`` — snapshot the firm database to versionable JSON.

Thin CLI over :func:`firm.core.snapshot.take`. Prints the snapshot path and
per-table counts as JSON. Commit the file and a bad write becomes a diff.
"""

from __future__ import annotations

import json
from pathlib import Path

from firm.core.db import connect, get_db_path
from firm.core.snapshot import take


def run_backup(workspace: Path, *, label: str = "manual") -> int:
    workspace = workspace.expanduser().resolve()
    db_path = get_db_path(workspace)
    if not db_path.exists():
        print(json.dumps({
            "ok": False, "reason": "db-not-found", "workspace": str(workspace),
        }))
        return 1

    conn = connect(db_path)
    try:
        path = take(conn, workspace, label=label)
    finally:
        conn.close()

    data = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps({
        "ok": True,
        "snapshot": str(path.relative_to(workspace)),
        "rows": sum(data["counts"].values()),
        "counts": data["counts"],
    }))
    return 0
