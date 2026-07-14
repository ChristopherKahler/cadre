"""``firm goal create|update`` — the Board's goal authority, from the terminal.

Thin CLI wrappers around ``firm.services.goal``. ``update`` refreshes a
Goal's metric (the goal-health banner's entry point — Board Proxy field
report COM-010). ``create`` authors a goal outright: it is a BOARD surface —
Members never run the CLI; from inside a run they propose via
``firm_propose_goal``, which raises a Gate (fork 008: goals were the only
entity where a Member had more authority than the Board).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from firm.core.db import connect, get_db_path, resolve_firm_id
from firm.services.goal import create_goal, update_goal_metric


def _num(v: str | None) -> Any:
    """Coerce numeric-looking CLI strings so metric JSON holds numbers."""
    if v is None:
        return None
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except ValueError:
        return v


def run_goal_create(
    workspace: Path,
    target: str,
    *,
    parent_entity_type: str,
    parent_entity_id: str,
    metric: str | None = None,
    level: str | None = None,
    firm_id: str | None = None,
) -> int:
    """Author a goal as the Board. Returns 0 on success, 1 on failure."""
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
        fid = resolve_firm_id(conn, firm_id)
        data: dict[str, Any] = {
            "target": target,
            "parent_entity_type": parent_entity_type,
            "parent_entity_id": parent_entity_id,
        }
        if metric:
            data["metric"] = metric
        if level:
            data["level"] = level
        goal = create_goal(conn, fid, data)
        print(json.dumps({"ok": True, "goal_id": goal["id"],
                          "target": goal.get("target")}, default=str))
        return 0
    except ValueError as exc:
        print(json.dumps({"ok": False, "reason": "error",
                          "message": str(exc)}), file=sys.stderr)
        return 1
    finally:
        conn.close()


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
