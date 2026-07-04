"""``firm pulse`` — run the PULSE activation cycle.

Connects to the firm DB, runs ``orchestrator.pulse()`` with the runner
callback that chains prompt → spawn → parse → validate → budget, and
prints a JSON summary.
"""

from __future__ import annotations

import json
import signal
from pathlib import Path
from typing import Any

from firm.core.db import connect, get_db_path
from firm.pulse.orchestrator import pulse
from firm.pulse.runner import make_runner
from firm.pulse.spawn import _active_pids


def run_pulse(
    workspace: Path,
    *,
    dry_run: bool = False,
    abort: bool = False,
    firm_id: str = "chrisai",
) -> int:
    """Run a single PULSE cycle for the workspace.

    Args:
        workspace: Root of the firm workspace.
        dry_run: If True, show who would activate without spawning.
        abort: If True, send SIGTERM to tracked PIDs and exit.
        firm_id: Firm scope.

    Returns:
        0 on success, 1 on unhandled error.
    """
    workspace = workspace.expanduser().resolve()

    # Abort mode: kill tracked processes
    if abort:
        return _handle_abort()

    db_path = get_db_path(workspace)
    if not db_path.exists():
        print(json.dumps({
            "ok": False,
            "reason": "db-not-found",
            "workspace": str(workspace),
        }))
        return 0

    # Preflight: don't spawn N doomed subprocesses (and write N failed
    # member_run rows) when the Member runtime isn't wired at all.
    if not dry_run:
        from firm.pulse.spawn import resolve_claude_bin

        claude_bin, resolve_detail = resolve_claude_bin()
        if claude_bin is None:
            print(json.dumps({
                "ok": False,
                "reason": "runtime-not-wired",
                "detail": resolve_detail,
            }))
            return 1

    conn = connect(db_path)
    try:
        runner = make_runner(firm_id, str(workspace))
        summary = pulse(conn, firm_id, runner, dry_run=dry_run)

        output: dict[str, Any] = {
            "ok": not (summary.errors and not summary.ran),
            "dry_run": summary.dry_run,
            "ran": len(summary.ran),
            "skipped": len(summary.skipped),
            "errors": len(summary.errors),
        }

        if summary.reaped:
            output["reaped"] = summary.reaped

        if summary.ran:
            output["ran_details"] = [
                {
                    "member": r["member"]["id"] if isinstance(r.get("member"), dict) else None,
                    "result": r.get("result"),
                }
                for r in summary.ran
            ]

        if summary.errors:
            output["error_details"] = [
                {
                    "member": e["member"]["id"] if isinstance(e.get("member"), dict) else None,
                    "error": e.get("error"),
                }
                for e in summary.errors
            ]

        print(json.dumps(output, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "reason": "error", "message": str(exc)}))
        return 1
    finally:
        conn.close()


def _handle_abort() -> int:
    """Send SIGTERM to all tracked PIDs."""
    if not _active_pids:
        print(json.dumps({"ok": True, "aborted": 0, "message": "No active processes"}))
        return 0

    aborted = 0
    for pid, proc in list(_active_pids.items()):
        try:
            proc.send_signal(signal.SIGTERM)
            aborted += 1
        except (ProcessLookupError, OSError):
            pass  # Already dead

    print(json.dumps({"ok": True, "aborted": aborted}))
    return 0
