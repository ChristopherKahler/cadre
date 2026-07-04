"""``firm goal update`` — refresh a Goal's metric from the command line.

Thin CLI wrapper around ``firm.services.goal.update_goal_metric``. This is
the real entry point the goal-health banner refers to — until it existed,
metric refreshes required hand-writing the JSON shape the banner parser
expects (Board Proxy field report COM-010).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from firm.core.db import connect, get_db_path
from firm.services.goal import update_goal_metric


def _num(v: str | None) -> Any:
    """Coerce numeric-looking CLI strings so metric JSON holds numbers."""
    if v is None:
        return None
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except ValueError:
        return v


def run_goal_update(
    workspace: Path,
    goal_id: str,
    *,
    current: str | None = None,
    value: str | None = None,
    unit: str | None = None,
    metric_type: str | None = None,
    deadline: str | None = None,
    trend: str | None = None,
) -> int:
    """Update *goal_id*'s metric in the workspace firm DB.

    Returns 0 on success; 1 with a JSON error line on structured failure.
    """
    workspace = workspace.expanduser().resolve()
    db_path = get_db_path(workspace)
    if not db_path.exists():
        print(json.dumps({
            "ok": False,
            "reason": "db-not-found",
            "workspace": str(workspace),
        }), file=sys.stderr)
        return 1

    conn = connect(db_path)
    try:
        updated = update_goal_metric(
            conn,
            goal_id,
            current=_num(current),
            value=_num(value),
            unit=unit,
            metric_type=metric_type,
            deadline=deadline,
            trend=trend,
        )
        print(json.dumps({
            "ok": True,
            "goal_id": goal_id,
            "metric": updated.get("metric"),
        }, default=str))
        return 0
    except ValueError as exc:
        print(json.dumps({"ok": False, "reason": "error", "message": str(exc)}), file=sys.stderr)
        return 1
    finally:
        conn.close()
