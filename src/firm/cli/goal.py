"""``firm goal create|propose|update`` — goal authority, from the terminal.

Thin CLI wrappers around ``firm.services.goal``. ``update`` refreshes a
Goal's metric (the goal-health banner's entry point — Board Proxy field
report COM-010). ``create`` authors a goal outright: it is a BOARD surface,
and the service refuses any identified Member. ``propose`` is the Member's
route: it raises a Gate, and the goal exists only once the Board approves it
(fork 008: goals were the only entity where a Member had more authority than
the Board). It calls the same service as the firm MCP tool
``firm_propose_goal``, so it works in firms that load no firm MCP server.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from firm.core.db import connect, db_connection, get_db_path, resolve_firm_id
from firm.services.authority import AuthorityError, caller_member_id
from firm.services.goal import create_goal, propose_goal, update_goal_metric


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
    except AuthorityError as exc:
        # A Member is refused; the payload's hint names `firm goal propose`.
        print(json.dumps({"ok": False, **exc.payload}), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(json.dumps({"ok": False, "reason": "error",
                          "message": str(exc)}), file=sys.stderr)
        return 1
    finally:
        conn.close()


def run_goal_propose(
    workspace: Path,
    target: str,
    *,
    parent_entity_type: str,
    parent_entity_id: str,
    reasoning: str,
    metric: str | None = None,
    member_id: str | None = None,
    firm_id: str | None = None,
) -> int:
    """Propose a goal for the Board to approve. JSON to stdout, errors to
    stderr. Returns 0 on success, 1 on a structured failure.

    Conventions match ``firm gate request``: member from ``--member`` or
    ``$CADRE_MEMBER_ID``, firm from ``--firm-id`` or the workspace's db, and
    the identity and db checks run before any connection is opened, because
    opening one creates an empty ``.firm/firm.db``.
    """
    proposer = member_id or caller_member_id()
    if not proposer:
        print(json.dumps({"ok": False, "error": (
            "no member identity — pass --member MEM-xxx, or run inside a Member "
            "session where $CADRE_MEMBER_ID is set. A goal proposal records WHO "
            "is asking; the Board sets goals with `firm goal create`"
        )}), file=sys.stderr)
        return 1

    db_path = get_db_path(workspace)
    if not db_path.exists():
        print(json.dumps({"ok": False, "error": (
            f".firm/firm.db not found at {db_path} — run 'firm init {workspace}' "
            f"first, or pass --workspace pointing at the firm"
        )}), file=sys.stderr)
        return 1

    data: dict[str, Any] = {
        "requesting_member_id": proposer,
        "target": target,
        "parent_entity_type": parent_entity_type,
        "parent_entity_id": parent_entity_id,
        "reasoning": reasoning,
    }
    if metric:
        data["metric"] = metric

    try:
        with db_connection(workspace) as conn:
            gate = propose_goal(conn, resolve_firm_id(conn, firm_id), data)
    except Exception as exc:  # service raises ValueError/TypeError on bad input
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, **dict(gate)}, default=str))
    return 0


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
