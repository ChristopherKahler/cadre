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

import contextlib
import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.core.db import connect, db_is_remote, get_db_path, resolve_firm_id
from firm.pulse import dblock
from firm.pulse.environment import pulse_environment
from firm.pulse.orchestrator import pulse
from firm.pulse.runner import make_runner
from firm.pulse.spawn import _active_pids

_QUEUE_LOCK_WAIT_SEC = 1800   # how long a claimer waits for the table to free up
_QUEUE_RETRY_SEC = 10


def _exit_with(result: dict[str, Any]) -> int:
    """Print the pulse's result line and return the exit code that goes with it.

    A scheduler that starts a pulse sees only the exit code, and every reader of
    the output takes the LAST stdout line as the result, so both come from this
    one object: 0 only when ``ok`` is exactly True, 1 for anything else. Ways
    out used to disagree with their own line -- a missing database and a failed
    Member run both exited 0 under ``ok: false`` -- and a scheduler recorded
    those pulses as clean runs (#128). Every return that ends ``firm pulse``
    comes through here; ``tests/test_pulse_exit_contract.py`` fails when one
    does not.
    """
    print(json.dumps(result, default=str))
    return 0 if result.get("ok") is True else 1


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
        0 when the printed result says ``ok: true``, 1 otherwise.
    """
    try:
        workspace = workspace.expanduser().resolve()

        # Abort mode: kill tracked processes + resolve the DB lock holder
        if abort:
            return _handle_abort(workspace, firm_id)

        db_path = get_db_path(workspace)
        if not db_is_remote() and not db_path.exists():
            return _exit_with({
                "ok": False,
                "reason": "db-not-found",
                "workspace": str(workspace),
            })

        rconn = connect(db_path)
        try:
            firm_id = resolve_firm_id(rconn, firm_id)
        except ValueError as exc:
            return _exit_with({"ok": False, "reason": "firm-id-unresolved",
                               "message": str(exc)})
        finally:
            rconn.close()

        # The pulse makes its own environment whole before anything below
        # resolves a tool or a token: a timer unit starts it with systemd's bare
        # PATH and without the firm vault, so the notify rail and the preflight
        # both failed on every unattended pulse (#107). A dry run spawns nothing
        # and probes nothing, so it keeps the environment it was given.
        environment = (contextlib.nullcontext() if dry_run
                       else pulse_environment(workspace, db_path, firm_id))
        with environment:
            return _run_resolved(workspace, db_path, firm_id, dry_run=dry_run,
                                 only=only, drain_queue=drain_queue)
    except Exception as exc:
        # Anything no branch caught -- a database file that will not open, a
        # lock query that fails -- used to leave a traceback and no result line,
        # so a reader of the last stdout line got nothing (#128 U4).
        return _exit_with({"ok": False, "reason": "error", "message": str(exc)})


def _run_resolved(
    workspace: Path, db_path: Path, firm_id: str, *,
    dry_run: bool, only: str | None, drain_queue: bool,
) -> int:
    """The rest of ``run_pulse``, once the firm is known."""
    # Preflight: don't spawn N doomed subprocesses (and write N failed
    # member_run rows) when the Member runtime isn't wired at all.
    if not dry_run:
        from firm.pulse.spawn import resolve_claude_bin

        claude_bin, resolve_detail = resolve_claude_bin()
        if claude_bin is None:
            return _exit_with({
                "ok": False,
                "reason": "runtime-not-wired",
                "detail": resolve_detail,
            })

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
            return _exit_with({
                "ok": False,
                "reason": "pulse-already-running",
                "detail": ("another live pulse holds the pulse_lock row for "
                           f"{firm_id!r}; wait for it or `firm pulse --abort`"),
            })
        _start_heartbeat(db_path, firm_id, holder, stop_beat)

    conn = None
    try:
        # Opened inside the try: the lock and its heartbeat are already taken,
        # and a connection that failed before the try skipped the finally that
        # lets them go, so the lock sat held for its TTL (#128, osprey's G1).
        conn = connect(db_path)
        return _exit_with(
            _pulse_once(conn, workspace, firm_id, dry_run=dry_run, only=only))
    except Exception as exc:
        return _exit_with({"ok": False, "reason": "error", "message": str(exc)})
    finally:
        if conn is not None:
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

    # A pulse is exactly what moves the roster and the unit board, so it is
    # where the firm's exports have to be refreshed — the manifest's post_tool
    # handlers re-ingest these three files on write, and its session_start
    # ingest reads them at every Member's next session. Skipped on a dry run,
    # which is read-only by contract and must leave no trace. Never raises: an
    # export failure must not turn a pulse that ran into a pulse that errored.
    if not dry_run:
        from firm.services import base_export
        exported = base_export.export(workspace, firm_id, conn=conn)
        if not exported.get("ok"):
            # Said out loud rather than swallowed. A firm whose exports stopped
            # updating looks identical to one whose graph is simply quiet, and
            # those need different fixes.
            output_export_note = exported.get("reason", "")
        else:
            output_export_note = ""
    else:
        exported, output_export_note = {"ok": None}, ""

    output: dict[str, Any] = {
        # Any error fails the pulse: a Member run that failed or timed out, or a
        # configured notify rail that does not resolve -- the only three writers
        # of summary.errors (pulse/orchestrator.py). This was
        # `not (errors and not ran)`, so a failed Member beside one that worked
        # printed ok and exited 0 (#128). `ran`, `errors` and `error_details`
        # still tell a partial failure from a total one.
        "ok": not summary.errors,
        "dry_run": summary.dry_run,
        "ran": len(summary.ran),
        "skipped": len(summary.skipped),
        "errors": len(summary.errors),
    }
    # Three states, three shapes (law 7): a list, possibly empty, when the query
    # ran; absent only beside the reason it could not.
    try:
        output["stranded_units"] = _stranded_units(conn, firm_id)
    except Exception as exc:
        output["stranded_units_error"] = f"{type(exc).__name__}: {exc}"
    if denied:
        output["policy_denials_ingested"] = denied

    if exported.get("ok") is not None:
        output["base_export"] = bool(exported.get("ok"))
        if output_export_note:
            output["base_export_reason"] = output_export_note

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


def _stranded_units(conn: Any, firm_id: str) -> list[dict[str, Any]]:
    """Open Units no active Member's queue counts, so no pulse will ever run them.

    The definition is ``compute_load``'s (``pulse/orchestrator.py``), read from
    the other side: a pending or in-progress Unit is workable only when an
    active Member of the firm has claimed it, or has it assigned, unclaimed and
    pending. A firm whose every Member skips at ``load=0`` while Units like
    these sit on its board prints the same ``ok: true, ran: 0`` as a firm with
    nothing to do, and those need opposite responses (#128 C2). Read-only, so a
    dry run reports them too.
    """
    statuses = {m["id"]: m.get("status")
                for m in repo.find(conn, "member", firm_id=firm_id)}
    active = {member_id for member_id, s in statuses.items() if s == "active"}

    def who(member_id: str) -> str:
        name = member_id if member_id else repr(member_id)
        status = statuses.get(member_id)
        if status is None:
            return f"{name}, who is not a Member of this firm"
        return f"{name}, who is {status}"

    stranded: list[dict[str, Any]] = []
    for unit in repo.find(conn, "unit", firm_id=firm_id):
        status = unit.get("status")
        if status not in ("pending", "in_progress"):
            continue
        assignee, claimed = unit.get("assignee_member_id"), unit.get("claimed_by")
        # compute_load's two clauses exactly, never by truthiness: claimed_by
        # EQUALS an active Member's id, or claimed_by IS NULL while the Unit is
        # pending and assigned to one. An empty id is neither (osprey's G1).
        if claimed in active:
            continue
        if claimed is None and status == "pending" and assignee in active:
            continue
        if claimed is not None:
            reason = f"claimed by {who(claimed)}"
        elif status == "in_progress":
            reason = "in progress with no claim"
        elif assignee is not None:
            reason = f"assigned to {who(assignee)}"
        else:
            reason = "no assignee and no claim"
        stranded.append({"id": unit["id"], "status": status,
                         "assignee_member_id": assignee, "claimed_by": claimed,
                         "reason": reason})
    return sorted(stranded, key=lambda u: str(u["id"]))


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
        conn = None
        try:
            # Inside the try for the same reason as in _run_resolved: a failed
            # connection must still release the lock and complete the request.
            conn = connect(db_path)
            output = _pulse_once(conn, workspace, firm_id)
            results.append({"request": req["id"], **output})
        except Exception as exc:
            results.append({"request": req["id"], "ok": False, "error": str(exc)})
        finally:
            if conn is not None:
                conn.close()
            stop_beat.set()
            rconn = connect(db_path)
            try:
                dblock.release(rconn, firm_id, holder)
                pulse_queue.complete(rconn, req["id"])
            finally:
                rconn.close()

    # An abandoned or failed request makes this ok: false, and the drain now
    # exits on it like every pulse (#128 U1): it used to return 0 regardless.
    return _exit_with({
        "ok": all(r.get("ok", False) for r in results) if results else True,
        "drained": len(results),
        "results": results,
    })


def _pid_alive(pid: int) -> bool:
    if sys.platform.startswith("win"):
        # os.kill(pid, 0) on Windows TERMINATES the process (anything but the
        # CTRL_* events routes to TerminateProcess) — probe via OpenProcess.
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
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

    CLEARING THE LOCK AND CLOSING THE ORPHANED RUNS IS NOT DONE HERE. Both are
    :func:`firm.pulse.cleanup.release_and_finalize`'s, and this function held a
    second copy of them until PR 142 landed on this file (#141; the debt was
    recorded in that module's docstring rather than left to be found). Two
    copies of the rule that decides when a lock may be cleared are two rules,
    and a fix to one leaves the other deciding the old way with nothing in the
    suite to say so.

    What stays here is what belongs to abort and to nothing else: SIGTERM for
    the holder, the count of what was signalled, and the difference between
    ``cleared`` -- a holder this abort killed -- and ``stale-cleared``, a holder
    already dead when abort looked. The cleanup never signals anything, so it
    cannot tell those two apart and correctly calls both ``cleared``.
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
        # "no-db" means abort could not look at any lock, which is not "nothing
        # is running" (that is lock: none). This printed ok: true and exited 0,
        # so a mistyped --workspace reported a successful abort while the real
        # firm's pulse kept going (#128, osprey's G0 item 1). Same words as
        # run_pulse uses for the same missing database.
        result.update({"ok": False, "reason": "db-not-found",
                       "workspace": str(workspace), "lock": "no-db"})
        return _exit_with(result)

    # THE READ AND THE SIGNAL ARE ABORT'S; the clear and the close are the
    # cleanup's. That means the holder is read twice -- once here to decide
    # whether to signal it, once inside `release_and_finalize` to decide
    # whether it may be cleared. Two reads of one row in a CLI invocation is
    # the price of one rule about when a lock may be cleared, and it is worth
    # paying: the alternative is this function deciding that for itself again.
    was_alive = False
    conn = connect(db_path)
    try:
        try:
            firm_id = resolve_firm_id(conn, firm_id)
        except ValueError as exc:
            # Printed ok: true beside exit 1 (#128 U2).
            result.update({"ok": False, "reason": "firm-id-unresolved",
                           "lock": "firm-id-unresolved", "message": str(exc)})
            return _exit_with(result)
        holder = dblock.current_holder(conn, firm_id)
        if holder is not None:
            result["holder"] = holder
            host, pid_str, _nonce = holder.split(":", 2)
            if host == socket.gethostname() and _pid_alive(int(pid_str)):
                was_alive = True
                os.kill(int(pid_str), signal.SIGTERM)
                result["aborted"] += 1
                for _ in range(10):  # grace: let it exit and release the lock
                    time.sleep(0.5)
                    if not _pid_alive(int(pid_str)):
                        break
    finally:
        conn.close()

    # A signalled process cannot finalize its own row, so abort owns it --
    # `finalize_live_runs=True`. Without it the run stays status='running'
    # forever and every "is this firm busy" reader believes a run that is
    # already dead. `wait_seconds=0` because the grace window above has already
    # been given; the cleanup waiting again would double it.
    from firm.pulse import cleanup as pulse_cleanup

    outcome = pulse_cleanup.release_and_finalize(
        workspace, firm_id, by="firm pulse --abort", wait_seconds=0.0,
        finalize_live_runs=True)

    # ABORT'S OWN VOCABULARY, which `test_pulse_exit_contract.py` pins. The
    # cleanup cannot tell a holder abort killed from one that was already dead,
    # because it never signalled anything -- abort can, and `was_alive` is how.
    lock = outcome.get("lock")
    if lock == "cleared":
        result["lock"] = "cleared" if was_alive else "stale-cleared"
    elif lock == "held-by-a-live-pulse":
        result["lock"] = "signalled"
        result["message"] = ("holder signalled, still exiting; "
                             "lock left for its own release")
    elif lock == "remote-holder":
        result["lock"] = "remote-holder"
        result["message"] = ("lock held from another machine; "
                             "its TTL frees it if the holder is dead")
    else:
        result["lock"] = lock
        if outcome.get("reason"):
            result["message"] = outcome["reason"]
    if lock != "remote-holder" and "runs_finalized" in outcome:
        # OMITTED for a remote holder, which is what abort did before it
        # delegated: those runs belong to the other machine, and abort never
        # looked at them. The cleanup always initialises the key, so copying it
        # unconditionally would put `runs_finalized: []` on a remote-holder
        # result -- a number abort never had the standing to report. Absent and
        # empty are different claims (avocet, 18:51).
        result["runs_finalized"] = outcome["runs_finalized"]
    if outcome.get("runs_not_finalized"):
        # One bad row never stops the rest, and never disappears either. This
        # used to be a `warn` line of its own on stdout, printed from the
        # private finalizer. It CANNOT be printed from here: every reader of
        # this command takes the LAST stdout line as the result (#128), and
        # `test_every_way_out_of_the_pulse_goes_through_the_exit_function`
        # failed on exactly that when this was a print -- a guard catching a
        # real defect in the change that introduced it. Carried in the result
        # instead, where it reaches the same readers and cannot get between
        # them and their answer.
        result["runs_not_finalized"] = outcome["runs_not_finalized"]

    return _exit_with(result)
