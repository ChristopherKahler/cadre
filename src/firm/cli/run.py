"""``firm run end`` -- finalize a Member Run and write audit records.

Thin CLI wrapper around ``firm.hooks.run_record.on_run_end``.  The slash
commands ``/member:run`` (Phase 3) and ``/quill:run`` (Phase 4) will wrap
this verb; until then operators invoke it directly for testing.

``firm run end`` does NOT change the Member's availability -- that is a
caller concern (Phase 3 slash commands orchestrate the full lifecycle).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from firm.core.db import connect, get_db_path
from firm.hooks.run_record import on_run_end


def run_run_end(
    workspace: Path,
    run_id: str,
    *,
    final_status: str,
    outputs_json: str | None = None,
    usage_json: str | None = None,
    error_json: str | None = None,
    notes: str | None = None,
    dry_run: bool = False,
    firm_id: str = "chrisai",
) -> int:
    """Finalize *run_id* in the workspace firm DB.

    Returns 0 on success or structured failure (run-not-found).  Returns 1
    only on unhandled exceptions.  Output is always JSON to stdout for
    programmatic consumption.
    """
    workspace = workspace.expanduser().resolve()
    db_path = get_db_path(workspace)
    if not db_path.exists():
        print(json.dumps({
            "ok": False,
            "reason": "db-not-found",
            "workspace": str(workspace),
        }))
        return 0

    outputs: list[Any] | None = json.loads(outputs_json) if outputs_json else None
    usage: dict[str, Any] | None = json.loads(usage_json) if usage_json else None
    error: dict[str, Any] | None = json.loads(error_json) if error_json else None

    conn = connect(db_path)
    try:
        if dry_run:
            return _dry_run(conn, run_id=run_id, final_status=final_status,
                            outputs=outputs)

        result = on_run_end(
            conn,
            firm_id=firm_id,
            run_id=run_id,
            final_status=final_status,
            outputs=outputs,
            usage=usage,
            notes=notes,
            error=error,
        )
        print(json.dumps(result))
        return 0
    finally:
        conn.close()


def _dry_run(
    conn: Any,
    *,
    run_id: str,
    final_status: str,
    outputs: list[Any] | None,
) -> int:
    """Print what ``on_run_end`` would change, without writing."""
    run_row = conn.execute(
        "SELECT * FROM member_run WHERE id = ?", (run_id,)
    ).fetchone()
    if run_row is None:
        print(json.dumps({
            "ok": False, "reason": "run-not-found", "run_id": run_id,
        }))
        return 0

    run = dict(run_row)
    unit_id = run.get("unit_id")

    records_count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    usg_count = conn.execute("SELECT COUNT(*) FROM usage_event").fetchone()[0]

    print(f"[dry-run] would finalize {run_id} "
          f"(status: {run['status']} -> {final_status})")
    print(f"[dry-run] member: {run['member_id']}, firm: {run['firm_id']}")

    if unit_id:
        unit_row = conn.execute(
            "SELECT outputs FROM unit WHERE id = ?", (unit_id,)
        ).fetchone()
        existing = json.loads(unit_row[0]) if unit_row and unit_row[0] else []
        add_count = len(outputs) if outputs else 0
        print(f"[dry-run] would merge outputs into unit {unit_id} "
              f"(current: {len(existing)} items, would add: {add_count})")
    else:
        print("[dry-run] no unit_id -- unit outputs merge skipped")

    print(f"[dry-run] would write usage_event row: USG-{usg_count + 1:03d}")
    print(f"[dry-run] would write records row: "
          f"LOG-{records_count + 1:03d} (event_type=member_run.ended)")
    print(f"[dry-run] row counts: records={records_count}->{records_count + 1}, "
          f"usage_event={usg_count}->{usg_count + 1}")
    return 0
