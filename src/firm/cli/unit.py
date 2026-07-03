"""``firm unit complete`` — mark a Unit done and trigger the completion handler.

Thin CLI wrapper around ``firm.hooks.unit_completion.on_unit_done``. The
slash command ``/unit:complete`` (Phase 3) will wrap this verb; until then
operators invoke it directly for testing and manual completions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.core.db import connect, get_db_path
from firm.services.unit import complete_unit


def _preview_resolved_acs(
    project: dict[str, Any] | None, unit_id: str,
) -> list[str]:
    """Return the ids of acceptance_criteria that *would* flip for *unit_id*."""
    if project is None:
        return []
    ac_list = project.get("acceptance_criteria") or []
    if not isinstance(ac_list, list):
        return []
    out: list[str] = []
    for entry in ac_list:
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("resolved_by") == unit_id
            and entry.get("resolved") is not True
            and entry.get("id")
        ):
            out.append(str(entry["id"]))
    return out


def run_unit_complete(
    workspace: Path,
    unit_id: str,
    member_id: str,
    *,
    run_id: str | None = None,
    dry_run: bool = False,
    firm_id: str = "chrisai",
) -> int:
    """Complete *unit_id* in the workspace firm DB.

    ``dry-run`` performs the reads and prints the planned changes without
    opening a write transaction. On success returns 0; structured failures
    (unit not found, project missing) return 1 and print to stderr.
    """
    workspace = workspace.expanduser().resolve()
    db_path = get_db_path(workspace)
    if not db_path.exists():
        print(
            f"Error: .firm/firm.db not found at {db_path}. "
            f"Run 'firm init {workspace}' first.",
            file=sys.stderr,
        )
        return 1

    conn = connect(db_path)
    try:
        unit = repo.get(conn, "unit", unit_id)
        if unit is None:
            print(
                f"Error: unit-not-found: {unit_id}",
                file=sys.stderr,
            )
            return 1
        prior_status = unit.get("status", "unknown")
        project_id = unit["project_id"]
        project = repo.get(conn, "project", project_id)

        if dry_run:
            planned_ac = _preview_resolved_acs(project, unit_id)
            print(f"[dry-run] would complete {unit_id} (prior status: {prior_status})")
            print(f"[dry-run] project: {project_id}")
            print(f"[dry-run] would write records row: event_type=unit.status_transition, "
                  f"actor={member_id}")
            ac_display = ", ".join(planned_ac) if planned_ac else "(none)"
            print(f"[dry-run] would resolve AC: {ac_display}")
            if project is None:
                print("[dry-run] WARNING: project row missing; live run would return "
                      "project-missing error")
            return 0

        # Route through the service so the status flip, audit record, and AC
        # rollup stay one transaction — calling on_unit_done directly left
        # unit.status untouched and pulses re-dispatched finished work.
        result = complete_unit(
            conn,
            firm_id,
            unit_id,
            member_id,
            run_id=run_id,
        )
        if not result.get("ok"):
            reason = result.get("reason", "unknown")
            print(f"Error: {reason} — {json.dumps(result)}", file=sys.stderr)
            return 1

        resolved = result.get("resolved_ac_ids") or []
        ac_display = ", ".join(resolved) if resolved else "(none)"
        print(f"completed {unit_id} (status: {prior_status} -> done) — resolved AC: {ac_display}")
        print(f"records: {result['records_id']}")
        return 0
    finally:
        conn.close()
