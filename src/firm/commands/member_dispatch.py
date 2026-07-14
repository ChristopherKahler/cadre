"""Generic member dispatch — pre-flight/post-flight for any Member.

Extracts the dispatch pattern established by Quill (Phase 4) into
a reusable module. Any Member with a Contract and skill_loadout can
use these functions for stage dispatch with member_run tracking.

Usage from skill run.md files:
    python3 -m firm.commands.member_dispatch preflight <member_id> <stage>
    python3 -m firm.commands.member_dispatch postflight <run_id> <status>
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from firm.contracts.dispatch import resolve_stage
from firm.core import repo
from firm.core.db import connect, get_db_path, resolve_firm_id
from firm.services._id import next_id


def preflight(
    conn: sqlite3.Connection,
    member_id: str,
    stage: str,
    firm_id: str = "",
) -> dict[str, Any]:
    """Resolve stage, find active unit, create member_run.

    Args:
        conn: SQLite connection with migrations applied.
        member_id: The Member dispatching (e.g. "MEM-002").
        stage: Stage name from Contract skill_loadout (e.g. "audit").
        firm_id: Firm scope.

    Returns:
        Dict with:
          resolved_cmd: str — the skill command to execute
          unit: dict|None — active unit if one exists
          run_id: str|None — member_run ID if unit exists
          member_id: str — echo back for convenience

    Raises:
        ValueError: If member, contract, or stage not found.
    """
    firm_id = resolve_firm_id(conn, firm_id or None)
    resolved_cmd = resolve_stage(conn, member_id, stage)

    # Find active units claimed by this member
    units = repo.find(conn, "unit", claimed_by=member_id)
    active_units = [
        dict(u) for u in units
        if u.get("status") in ("pending", "in_progress")
    ]

    unit = active_units[0] if active_units else None
    run_id = None

    if unit:
        run_id = next_id(conn, "member_run", firm_id)
        repo.create(conn, "member_run", {
            "id": run_id,
            "firm_id": firm_id,
            "member_id": member_id,
            "unit_id": unit["id"],
            "status": "running",
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "invocation_source": "manual",
        })

    return {
        "resolved_cmd": resolved_cmd,
        "unit": unit,
        "run_id": run_id,
        "member_id": member_id,
    }


def postflight(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
) -> dict[str, Any]:
    """Finalize a member_run after stage execution.

    Args:
        conn: SQLite connection.
        run_id: The member_run ID from preflight.
        status: Final status — "completed" or "failed".

    Returns:
        Dict with run_id and status.

    Raises:
        ValueError: If run_id not found or status invalid.
    """
    if status not in ("completed", "failed"):
        raise ValueError(f"Invalid status {status!r} — must be 'completed' or 'failed'")

    existing = repo.get(conn, "member_run", run_id)
    if not existing:
        raise ValueError(f"member_run {run_id!r} not found")

    repo.update(conn, "member_run", run_id, {
        "status": status,
        "ended_at": datetime.now(tz=timezone.utc).isoformat(),
    })

    return {"run_id": run_id, "status": status}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    """CLI: python3 -m firm.commands.member_dispatch <command> <args...>"""
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: member_dispatch <preflight|postflight> <args...>"}))
        sys.exit(1)

    command = sys.argv[1]
    db_path = get_db_path(Path.cwd())
    conn = connect(db_path)

    try:
        if command == "preflight":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "Usage: member_dispatch preflight <member_id> <stage>"}))
                sys.exit(1)
            member_id = sys.argv[2]
            stage = sys.argv[3]
            result = preflight(conn, member_id, stage)
            # Serialize unit dict for JSON output
            if result["unit"]:
                result["unit"] = {
                    "id": result["unit"]["id"],
                    "name": result["unit"]["name"],
                    "project_id": result["unit"].get("project_id"),
                }
            print(json.dumps(result))

        elif command == "postflight":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "Usage: member_dispatch postflight <run_id> <status>"}))
                sys.exit(1)
            run_id = sys.argv[2]
            status = sys.argv[3]
            result = postflight(conn, run_id, status)
            print(json.dumps(result))

        else:
            print(json.dumps({"error": f"Unknown command {command!r}. Use 'preflight' or 'postflight'."}))
            sys.exit(1)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    _cli_main()
