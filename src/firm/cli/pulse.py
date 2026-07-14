"""``firm pulse`` — run the PULSE activation cycle.

Connects to the firm DB, runs ``orchestrator.pulse()`` with the runner
callback that chains prompt → spawn → parse → validate → budget, and
prints a JSON summary.

Overlap guard: a DB-row lock (``pulse_lock``) — one live pulse per firm
across ALL machines pointed at the same database (local file or shared
CADRE_DB_URL). Queue mode (``--drain-queue``) claims pending
``pulse_request`` rows and pulses once per request, waiting out whoever
holds the lock — a submitted turn never silently fizzles.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import threading
import time
from pathlib import Path
from typing import Any

from firm.core.db import connect, db_is_remote, get_db_path, resolve_firm_id
from firm.pulse import dblock
from firm.pulse.orchestrator import pulse
from firm.pulse.runner import make_runner
from firm.pulse.spawn import _active_pids

_QUEUE_LOCK_WAIT_SEC = 1800   # how long a claimer waits for the table to free up
_QUEUE_RETRY_SEC = 10


def run_pulse(
    workspace: Path,
    *,
    dry_run: bool = False,
    abort: bool = False,
    firm_id: str | None = None,
    only: str | None = None,
    drain_queue: bool = False,
) -> int:
    """Run a single PULSE cycle for the workspace.

    Args:
        workspace: Root of the firm workspace.
        dry_run: If True, show who would activate without spawning.
        abort: If True, abort the live pulse — SIGTERM in-process children,
            then signal or clear the DB pulse_lock holder — and exit.
        firm_id: Firm scope; None resolves to the firm this workspace's
            db holds (see resolve_firm_id).
        only: Member id — Board-targeted pulse activating only this Member
            (frequency throttle waived for the target).
        drain_queue: Claim pending pulse_request rows and pulse once per
            request, waiting for the lock instead of failing on it.

    Returns:
        0 on success, 1 on unhandled error.
    """
    workspace = workspace.expanduser().resolve()

    # Abort mode: kill tracked processes + resolve the DB lock holder
    if abort:
        return _handle_abort(workspace, firm_id)

    db_path = get_db_path(workspace)
    if not db_is_remote() and not db_path.exists():
        print(json.dumps({
            "ok": False,
            "reason": "db-not-found",
            "workspace": str(workspace),
        }))
        return 0

    rconn = connect(db_path)
    try:
        firm_id = resolve_firm_id(rconn, firm_id)
    except ValueError as exc:
        print(json.dumps({"ok": False, "reason": "firm-id-unresolved",
                          "message": str(exc)}))
        return 1
    finally:
        rconn.close()

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

    if drain_queue:
        return _drain_queue(workspace, db_path, firm_id)

    # Overlap lock (live pulses only — dry-run is read-only): member runs
    # take 20-30 min each, so an hourly cadence CAN overlap a long pulse.
    # DB-row lock, not flock: in multiplayer every player's machine pulses
    # against the same shared DB, so the guard lives IN the DB. A heartbeat
    # thread keeps the lock fresh; a dead holder's lock is stolen after TTL.
    holder = dblock.make_holder_id()
    lock_held = False
    stop_beat = threading.Event()
    if not dry_run:
        lconn = connect(db_path)
        try:
            lock_held = dblock.acquire(lconn, firm_id, holder)
        finally:
            lconn.close()
        if not lock_held:
            print(json.dumps({
                "ok": False,
                "reason": "pulse-already-running",
                "detail": ("another live pulse holds the pulse_lock row for "
                           f"{firm_id!r}; wait for it or `firm pulse --abort`"),
            }))
            return 1
        _start_heartbeat(db_path, firm_id, holder, stop_beat)

    conn = connect(db_path)
    try:
        output = _pulse_once(conn, workspace, firm_id, dry_run=dry_run, only=only)
        print(json.dumps(output, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "reason": "error", "message": str(exc)}))
        return 1
    finally:
        conn.close()
        stop_beat.set()
        if lock_held:
            rconn = connect(db_path)
            try:
                dblock.release(rconn, firm_id, holder)
            finally:
                rconn.close()


def _pulse_once(
    conn: Any, workspace: Path, firm_id: str, *,
    dry_run: bool = False, only: str | None = None,
) -> dict[str, Any]:
    """One pulse cycle → summary dict. Caller owns lock + connection."""
    # Denials the policy gate logged since the last pulse become Records +
    # escalations here — the hook may only append to a file, never open the
    # DB, so the pulse carries its receipts the rest of the way (fork 009).
    denied = 0
    if not dry_run:
        from firm.services import policy as policy_svc
        denied = policy_svc.ingest_denials(conn, workspace, firm_id)

    runner = make_runner(firm_id, str(workspace))
    summary = pulse(conn, firm_id, runner, dry_run=dry_run, only_member_id=only)

    output: dict[str, Any] = {
        "ok": not (summary.errors and not summary.ran),
        "dry_run": summary.dry_run,
        "ran": len(summary.ran),
        "skipped": len(summary.skipped),
        "errors": len(summary.errors),
    }
    if denied:
        output["policy_denials_ingested"] = denied

    if summary.skipped:
        # Aggregate skip reasons so a 0-ran pulse explains itself
        # (the dashboard's pulse feedback reads this).
        reasons: dict[str, int] = {}
        for s in summary.skipped:
            reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
        output["skip_reasons"] = reasons

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

    return output


def _start_heartbeat(
    db_path: Path, firm_id: str, holder: str, stop: threading.Event,
) -> None:
    """Keep the pulse_lock row fresh while the pulse runs (its own
    connection — the pulse's connection is busy for 20-30 min)."""

    def beat() -> None:
        while not stop.wait(60):
            try:
                conn = connect(db_path)
                try:
                    dblock.heartbeat(conn, firm_id, holder)
                finally:
                    conn.close()
            except Exception:
                pass  # a missed beat is fine; TTL is 10 minutes

    threading.Thread(target=beat, daemon=True).start()


def _drain_queue(workspace: Path, db_path: Path, firm_id: str) -> int:
    """Claim pending pulse requests and pulse once per request.

    Waits for the pulse lock (up to _QUEUE_LOCK_WAIT_SEC per request)
    instead of failing on it — this replaces the old systemd wait-wrapper
    loop, and works across machines because both the queue and the lock
    live in the (possibly shared) database.
    """
    from firm.services import pulse_queue

    holder = dblock.make_holder_id()
    results: list[dict[str, Any]] = []
    while True:
        qconn = connect(db_path)
        try:
            req = pulse_queue.claim_next(qconn, firm_id, holder)
        finally:
            qconn.close()
        if req is None:
            break

        # Wait out whoever holds the table.
        got_lock = False
        deadline = time.monotonic() + _QUEUE_LOCK_WAIT_SEC
        while time.monotonic() < deadline:
            lconn = connect(db_path)
            try:
                got_lock = dblock.acquire(lconn, firm_id, holder)
            finally:
                lconn.close()
            if got_lock:
                break
            time.sleep(_QUEUE_RETRY_SEC)

        qconn = connect(db_path)
        try:
            if not got_lock:
                pulse_queue.abandon(qconn, req["id"], note="lock wait timed out")
                results.append({"request": req["id"], "ok": False,
                                "reason": "lock-wait-timeout"})
                continue
        finally:
            qconn.close()

        stop_beat = threading.Event()
        _start_heartbeat(db_path, firm_id, holder, stop_beat)
        conn = connect(db_path)
        try:
            output = _pulse_once(conn, workspace, firm_id)
            results.append({"request": req["id"], **output})
        except Exception as exc:
            results.append({"request": req["id"], "ok": False, "error": str(exc)})
        finally:
            conn.close()
            stop_beat.set()
            rconn = connect(db_path)
            try:
                dblock.release(rconn, firm_id, holder)
                pulse_queue.complete(rconn, req["id"])
            finally:
                rconn.close()

    print(json.dumps({
        "ok": all(r.get("ok", False) for r in results) if results else True,
        "drained": len(results),
        "results": results,
    }, default=str))
    return 0


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _handle_abort(workspace: Path, firm_id: str | None) -> int:
    """Abort a live pulse.

    Two layers. First, SIGTERM any subprocesses tracked in THIS process
    (only populated when abort is called in-process). Then the cross-process
    case every CLI invocation actually hits: read the DB ``pulse_lock`` —
    a live local holder is signalled and given a grace window to release;
    a dead local holder's stale lock is cleared (field failure 2026-07-11:
    a systemd-killed pulse left its lock row, the next pulse bounced off it,
    and abort reported "No active processes" while the table stayed wedged).
    A holder on another machine is reported and left to the TTL steal.
    """
    result: dict[str, Any] = {"ok": True, "aborted": 0}

    for _pid, proc in list(_active_pids.items()):
        try:
            proc.send_signal(signal.SIGTERM)
            result["aborted"] += 1
        except (ProcessLookupError, OSError):
            pass  # Already dead

    db_path = get_db_path(workspace)
    if not db_is_remote() and not db_path.exists():
        result["lock"] = "no-db"
        print(json.dumps(result))
        return 0

    conn = connect(db_path)
    try:
        try:
            firm_id = resolve_firm_id(conn, firm_id)
        except ValueError as exc:
            result["lock"] = "firm-id-unresolved"
            result["message"] = str(exc)
            print(json.dumps(result))
            return 1
        holder = dblock.current_holder(conn, firm_id)
        if holder is None:
            result["lock"] = "none"
        else:
            result["holder"] = holder
            host, pid_str, _nonce = holder.split(":", 2)
            if host != socket.gethostname():
                result["lock"] = "remote-holder"
                result["message"] = ("lock held from another machine; "
                                     "its TTL frees it if the holder is dead")
            elif _pid_alive(int(pid_str)):
                os.kill(int(pid_str), signal.SIGTERM)
                result["aborted"] += 1
                for _ in range(10):  # grace: let it exit and release the lock
                    time.sleep(0.5)
                    if not _pid_alive(int(pid_str)):
                        break
                if _pid_alive(int(pid_str)):
                    result["lock"] = "signalled"
                    result["message"] = ("holder signalled, still exiting; "
                                         "lock left for its own release")
                else:
                    dblock.release(conn, firm_id, holder)
                    result["lock"] = "cleared"
            else:
                dblock.release(conn, firm_id, holder)
                result["lock"] = "stale-cleared"
    finally:
        conn.close()

    print(json.dumps(result))
    return 0
