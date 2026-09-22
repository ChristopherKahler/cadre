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
from firm.pulse import dblock, descendants
from firm.sched import winjob
from firm.pulse.environment import pulse_environment
from firm.pulse.orchestrator import pulse
from firm.pulse.runner import make_runner
from firm.pulse.spawn import _active_pids
# Bound as a module name so `_pulse_once` reads it through this
# module's globals, which is what `test_pulse_exit_contract.py`'s
# unreadable-query leg patches. The definition moved to
# `firm/pulse/stranded.py` when `cli/doctor.py` became its second
# caller (#128 C2); the behaviour did not.
from firm.pulse.stranded import stranded_units as _stranded_units
from firm.services import pulse_ledger

_QUEUE_LOCK_WAIT_SEC = 1800   # how long a claimer waits for the table to free up
_QUEUE_RETRY_SEC = 10


#: What containment answered for THIS pulse, or empty.
#:
#: It rides here rather than being threaded through every return because a
#: pulse has eight ways out and the answer belongs on all of them. `run_pulse`
#: clears it on entry and fills it once, AFTER the abort dispatch -- so an
#: abort, which returns before that line, never carries containment keys it did
#: not earn.
_CONTAINMENT: dict[str, Any] = {}


def _containment_record() -> dict[str, Any]:
    """Put this pulse in a kill-on-close job and say what happened (#148 R2/R3).

    CALLED THROUGH THE MODULE ATTRIBUTE, never a name bound at import: the
    tier-A leg patches `winjob.contain_this_process`, and a name captured by
    `from ... import` at module load would not see the patch -- the leg would
    then measure the real primitive while believing it measured its own.

    NEVER RAISES, and the pulse runs whatever this returns. A host that cannot
    make a job still gets its heartbeat: refusing to run would turn a
    locked-down machine into a firm with no pulse, which is worse than the leak
    #148 closes. #146 FINDING 1 measured what one broken kernel call costs when
    the caller does not guard it -- the launcher stub exited 3 with the command
    never started and an empty log. What this must never do is run quietly, so
    the answer goes on the pulse's own result line, presence-keyed:
    `contained` and `containment_supported` always, `containment_reason` when
    there is one, `containment_flags` when the job's flags could be read back.
    """
    try:
        held = winjob.contain_this_process()
    except Exception as exc:                        # noqa: BLE001
        held = winjob.Containment(
            False, f"containment raised instead of answering: {exc}")
    record: dict[str, Any] = {"contained": held.contained,
                              "containment_supported": held.supported}
    if held.reason:
        record["containment_reason"] = held.reason
    if held.limit_flags is not None:
        record["containment_flags"] = f"0x{held.limit_flags:08x}"
    return record


def _exit_with(result: dict[str, Any], ledger: "_Ledger | None" = None) -> int:
    """Print the pulse's result line and return the exit code that goes with it.

    A scheduler that starts a pulse sees only the exit code, and every reader of
    the output takes the LAST stdout line as the result, so both come from this
    one object: 0 only when ``ok`` is exactly True, 1 for anything else. Ways
    out used to disagree with their own line -- a missing database and a failed
    Member run both exited 0 under ``ok: false`` -- and a scheduler recorded
    those pulses as clean runs (#128). Every return that ends ``firm pulse``
    comes through here; ``tests/test_pulse_exit_contract.py`` fails when one
    does not.

    THE LEDGER ROW CLOSES HERE FOR THE SAME REASON (#128 D3). ``outcome`` is
    read off this same object, in this same function, one statement before the
    exit code is read off it -- so the row and the code cannot disagree about
    how the pulse ended. A close-out beside the pulse's own return would have
    covered only the path that runs a cycle, and the pulse has four other ways
    out. *ledger* is None for every exit before the firm is known, and those
    have no row to close -- and for one path where the firm IS known: an
    exception raised between the row opening and the pulse's try reaches
    ``run_pulse``'s catch-all, which has no ledger to hand over. That row is
    left open on purpose and the next pulse on this host closes it
    ``unclosed``. See ``_Ledger``'s docstring for why that is registered
    rather than restructured.
    """
    # The containment answer rides onto every way out of a pulse (#148 R3).
    # `setdefault`, so a caller that has already said something about
    # containment keeps its own word; empty for an abort, which returns before
    # the record is taken. It is merged BEFORE the ledger closes, so the row
    # and the printed line describe the same object.
    for key, value in _CONTAINMENT.items():
        result.setdefault(key, value)
    if ledger is not None:
        ledger.close(result)
    print(json.dumps(result, default=str))
    return 0 if result.get("ok") is True else 1


class _Ledger:
    """This pulse's row in the ledger, and the note that stands in for it.

    Three states, three shapes, because absent, empty and failed are three
    different claims (law 7): a dry run sets no ``pulse_run`` field at all, a
    recorded pulse sets the row id, and a pulse whose write could not land
    sets ``not recorded: <reason>``. A firm upgraded without ``init`` or
    ``doctor --fix`` has no table, and that is the case this shape exists for.

    Every write is best effort and the pulse's exit code never depends on one:
    a side record that fails must not turn a pulse that ran into a pulse that
    errored. THE LEDGER NEVER MIGRATES -- see pulse_ledger's own docstring for
    why a timer tick must not change the schema under other machines.

    A FOURTH STATE EXISTS AND IS NOT FIXED HERE, registered rather than hidden
    (osprey, pre-G2 item 4; law 37). Between ``open()`` and the try in
    ``_run_resolved`` -- the lock connection, ``dblock.acquire``,
    ``_start_heartbeat`` -- an exception propagates to ``run_pulse``'s
    catch-all, which calls ``_exit_with`` with NO ledger. The row then stays
    open, the next pulse on this host closes it ``unclosed``, and the JSON
    carries no ``pulse_run`` field at all although the firm was known. Fixing
    it means moving this object's lifetime into ``run_pulse`` or adding a
    second try around the lock block, inside a function whose return count
    ``tests/test_pulse_exit_contract.py`` pins -- not worth the head moves for
    a path nobody has measured in the field.
    """

    def __init__(self, db_path: Path, firm_id: str, *, source: str,
                 dry_run: bool) -> None:
        self.db_path = db_path
        self.firm_id = firm_id
        self.source = source
        self.dry_run = dry_run
        self.run_id: int | None = None
        self.note: str | None = None

    def open(self) -> None:
        """Close out what this host left open, then start this pulse's row.

        The close-out runs here rather than on a timer or at exit because the
        next pulse is the only process that is certainly looking: a pulse that
        was killed cannot tidy up after itself, which is the whole reason its
        row is open.
        """
        if self.dry_run:
            return
        holder = dblock.make_holder_id()
        host = socket.gethostname()

        def work() -> int:
            conn = connect(self.db_path)
            try:
                pulse_ledger.close_out_dead_local_runs(
                    conn, host=host, is_alive=_pid_alive)
                return pulse_ledger.open_run(
                    conn, self.firm_id, source=self.source, holder=holder)
            finally:
                conn.close()

        self.run_id, self.note = pulse_ledger.best_effort(work)

    def close(self, result: dict[str, Any]) -> None:
        """Close the row from the pulse's own printed result, and say so in it."""
        if self.dry_run:
            return
        if self.run_id is None:
            result["pulse_run"] = f"not recorded: {self.note or 'no row opened'}"
            return

        def work() -> None:
            conn = connect(self.db_path)
            try:
                pulse_ledger.close_run(conn, self.run_id, result)
            finally:
                conn.close()

        _value, note = pulse_ledger.best_effort(work)
        # A row that opened and could not close is NOT "not recorded": it is on
        # disk, open, and the next pulse on this host will close it as
        # `unclosed`. Saying "not recorded" would send a reader looking for a
        # missing table that is right there.
        result["pulse_run"] = (self.run_id if note is None
                               else f"{self.run_id}, not closed: {note}")


def run_pulse(
    workspace: Path,
    *,
    dry_run: bool = False,
    abort: bool = False,
    firm_id: str | None = None,
    only: str | None = None,
    drain_queue: bool = False,
    source: str | None = None,
) -> int:
    """Run a single PULSE cycle for the workspace.

    Args:
        workspace: Root of the firm workspace.
        dry_run: If True, show who would activate without spawning.
        abort: If True, abort the live pulse — record the holder's tree,
            SIGTERM the holder, re-read after the grace window, and report
            what is still alive; ``ok`` is true only when nothing of that
            run is — and exit.

        firm_id: Firm scope; None resolves to the firm this workspace's
            db holds (see resolve_firm_id).
        only: Member id — Board-targeted pulse activating only this Member
            (frequency throttle waived for the target).
        drain_queue: Claim pending pulse_request rows and pulse once per
            request, waiting for the lock instead of failing on it.
        source: Where this pulse came from, for the ledger -- one of
            ``pulse_ledger.SOURCES``. None records ``unset``, which is what
            every timer installed before the flag existed passes.

    Returns:
        0 when the printed result says ``ok: true``, 1 otherwise.

    Before it spawns its first Member this pulse puts ITSELF in a
    kill-on-close job, so ending the pulse ends its Members whoever started it
    — a scheduled task, the hub's button, or a hand in a terminal (#148 R2).
    A host where that job cannot be made still gets its pulse: containment
    never raises into this function, and what it answered is reported on the
    result line under ``contained`` (R3).
    """
    try:
        # INSIDE the try, with everything else. `run_pulse` is one try with one
        # `except Exception`, and `test_every_way_out_of_the_pulse_goes_through
        # _the_exit_function` pins that shape: a statement above the try can
        # raise into nothing and leave a traceback with no result line, which
        # is #128 U4. These two lines were outside it for one run and that
        # guard caught them -- a guard catching a real defect in the change
        # that introduced it.
        global _CONTAINMENT
        _CONTAINMENT = {}
        workspace = workspace.expanduser().resolve()

        # Abort mode: kill tracked processes + resolve the DB lock holder
        if abort:
            return _handle_abort(workspace, firm_id)

        # CONTAINMENT HAPPENS HERE: after the abort dispatch, before anything
        # is spawned (#148 R2). The order is the property, not the presence of
        # a job -- a child started before the job is assigned is outside it
        # forever, and closing the handle later never reaches it. `winlaunch`
        # already works this way for the launcher; this is the same call for
        # the pulse itself, so a pulse started by a scheduled task, by the
        # hub's button or by a hand in a terminal is contained the same way.
        # The dispatcher deliberately adds no second job: two jobs mean two
        # handles, and the kill would wait for whichever closed last.
        _CONTAINMENT = _containment_record()

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
                                 only=only, drain_queue=drain_queue,
                                 source=source)
    except Exception as exc:
        # Anything no branch caught -- a database file that will not open, a
        # lock query that fails -- used to leave a traceback and no result line,
        # so a reader of the last stdout line got nothing (#128 U4).
        return _exit_with({"ok": False, "reason": "error", "message": str(exc)})


def _run_resolved(
    workspace: Path, db_path: Path, firm_id: str, *,
    dry_run: bool, only: str | None, drain_queue: bool,
    source: str | None = None,
) -> int:
    """The rest of ``run_pulse``, once the firm is known."""
    # The ledger row opens HERE, at the top, because this is the first point
    # at which the firm is known and every live ending below it is an ending
    # of a pulse that started. A row opened later would miss the preflight
    # exit, which is the most common failed pulse a firm ever has. A dry run
    # opens none: it is read-only by contract and leaves no trace (#128 D3).
    # --drain-queue IS the label: the entry point already says where the
    # pulse came from, so nothing passes it a flag and the two cannot drift.
    ledger = _Ledger(
        db_path, firm_id, dry_run=dry_run,
        source=(pulse_ledger.QUEUE if drain_queue
                else (source or pulse_ledger.UNSET)))
    ledger.open()

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
            }, ledger)

    if drain_queue:
        return _drain_queue(workspace, db_path, firm_id, ledger)

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
            }, ledger)
        _start_heartbeat(db_path, firm_id, holder, stop_beat)

    conn = None
    try:
        # Opened inside the try: the lock and its heartbeat are already taken,
        # and a connection that failed before the try skipped the finally that
        # lets them go, so the lock sat held for its TTL (#128, osprey's G1).
        conn = connect(db_path)
        result = _pulse_once(conn, workspace, firm_id, dry_run=dry_run,
                             only=only)
    except Exception as exc:
        result = {"ok": False, "reason": "error", "message": str(exc)}
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

    # THE EXIT IS OUTSIDE THE TRY, AND THAT IS LOAD-BEARING (#128 D3).
    #
    # Measured on this branch: at the old exit point the pulse's own connection
    # was still open AND still in a transaction, so the ledger's close -- which
    # runs on its own connection, in `_exit_with` -- got `database is locked`
    # and every successful pulse recorded `not closed`. The finally above is
    # what releases it, and the finally runs on the way OUT of the try, so any
    # exit inside the try is an exit with the database still held.
    #
    # Closing the row on the pulse's own connection instead would have meant
    # committing whatever that transaction is carrying, which is the pulse's
    # business and not the ledger's. Exiting after the connection is closed
    # costs nothing and is also strictly better for the lock: a reader that
    # starts another pulse the moment it sees this line can no longer bounce
    # off a pulse_lock row that this process has not let go of yet.
    return _exit_with(result, ledger)


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


def _drain_queue(workspace: Path, db_path: Path, firm_id: str,
                 ledger: "_Ledger | None" = None) -> int:
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
    # ONE ROW FOR THE DRAIN PROCESS, not one per request: the ledger counts
    # pulse processes, and per-request detail already lands in pulse_request.
    # The row was opened with source `queue` by _run_resolved, which is the
    # only place that knows the entry point.
    #
    # THE COUNTS ARE SUMMED ACROSS THE REQUESTS, and they are ABSENT when no
    # request reached a cycle at all (avocet's finding 1). `_count` writes NULL
    # for a count the result does not carry, and that NULL is reserved for "no
    # cycle ran" -- so a drain that ran three cycles and recorded a failure was
    # making exactly the claim a pulse that died at its preflight makes, and a
    # reader could not tell them apart. Summing to a plain 0 instead would make
    # the opposite wrong claim on a drain where every request timed out waiting
    # for the lock: "a cycle ran and found nothing". Absent, zero and a number
    # are three answers (law 7), so the keys appear only when a cycle really
    # ran.
    output: dict[str, Any] = {
        "ok": all(r.get("ok", False) for r in results) if results else True,
        "drained": len(results),
        "results": results,
    }
    ran_a_cycle = [r for r in results if isinstance(r.get("ran"), int)]
    if ran_a_cycle:
        for key in ("ran", "errors", "skipped"):
            output[key] = sum(int(r.get(key) or 0) for r in ran_a_cycle)
    return _exit_with(output, ledger)


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

    AND IT CHECKS BEFORE IT CLAIMS (#148). Before signalling, abort records the
    holder's process tree as generations -- pid and creation time -- re-reads
    them after the grace window, and reports ``descendants_before`` and
    ``alive_after``; ``ok`` is true only when no process of that run is still
    alive, and when one is, ``ok`` is false with exit 1 and the lock's own
    words unchanged. ``lock: signalled`` is false on its own, whatever the
    reading says: it means the holder was signalled and the lock was LEFT
    HELD, and abort never reports success over a lock it could not release.
    The reading can be honestly empty there -- the lock's own liveness is the
    cleanup's pid-only probe, so a reused pid can hold the branch open while
    no process of that run is alive -- and naming a stranger in
    ``alive_after`` would be worse than saying nothing, so it says nothing and
    the ``ok`` carries the fact. Liveness there is presence in the process table, never
    permission to signal, so a zombie reads dead and another user's descendant
    still reads alive (:mod:`firm.pulse.descendants`).
    """
    # THE CONTAINMENT RECORD IS NOT ABORT'S, AND IT IS CLEARED HERE (G1-2).
    # `_CONTAINMENT` is a module global that `_exit_with` merges onto every
    # result, and `run_pulse` clears it on entry -- but abort is the one way
    # out that returns BEFORE the record is taken, so a pulse run earlier in
    # the same process would leave `contained` and `containment_supported` on
    # an abort result that never asked for them. Measured by osprey against
    # `tests/test_pulse_cleanup.py`, which calls this function directly at
    # :361, :394, :496 and :597.
    global _CONTAINMENT
    _CONTAINMENT = {}

    # WHAT THIS PROMISES NOW (#148). Abort writes down the holder's tree
    # before it signals anything, looks again after the grace window, and
    # reports `descendants_before` and `alive_after` as `[[pid, created], ...]`
    # -- one shape for both. `ok: true` means no process belonging to that run
    # is alive; if any is, `ok` is false and the exit code is 1, with the lock
    # vocabulary and its message unchanged. Liveness there is presence in the
    # process table with the same creation time, never permission to signal, so
    # it has no EPERM branch and a zombie reads dead (`pulse/descendants.py`).
    # AND IT NEVER CLAIMS SUCCESS OVER A READING IT DID NOT TAKE (R5d): each of
    # the two readings proves it can see by finding abort's own pid in it, and
    # a blind one gives `ok: false` with `alive_after` ABSENT and `tree_read`
    # naming which read failed and why.
    # The limitation, said here rather than found later: a process the holder
    # starts after the snapshot is not in it.
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
    holder_pid: int | None = None
    holder_before: list[tuple[int, str]] = []
    before: list[tuple[int, str]] = []
    blind_before: str | None = None
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
                holder_pid = int(pid_str)
                # THE SNAPSHOT IS TAKEN BEFORE THE SIGNAL, and that ordering is
                # the whole reading (#148 R5). On POSIX, taken afterwards it
                # walks a tree whose root has gone -- the kernel reparents the
                # orphans -- finds nothing, and reports that nothing survived:
                # green for exactly the reason it should be red. On Windows a
                # late snapshot still finds the tree, because the children keep
                # naming the dead holder's pid and a missing parent keeps its
                # edge, so there the order is proven by the call-order leg in
                # `tests/test_pulse_containment.py`, not by what the walk finds.
                # The walk is transitive: a Member arrives under a launcher, so
                # the holder's direct children are launchers and the process
                # doing the work is a generation below them.
                # ONE READING, USED FOR BOTH (R5d). The snapshot and the
                # holder's own generation come from the same table, so they
                # cannot disagree with each other, and one blindness check
                # covers both.
                table_before = descendants.process_table()
                blind_before = descendants.blind_reason(table_before)
                before = descendants.descendants_of(holder_pid, table_before)
                # THE HOLDER IS A GENERATION TOO, taken here rather than after
                # the act (G1-3). Read afterwards by pid alone, a pid handed to
                # a new process between the signal and the re-read reads as the
                # holder still being alive, and abort then flips `ok` false
                # over a stranger. This module's own first rule is generation,
                # not pid, and the holder is not an exception to it.
                holder_gen = descendants.generation_of(holder_pid, table_before)
                holder_before = [holder_gen] if holder_gen else []
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
    if was_alive:
        # WHAT WAS SEEN, AND WHAT IS LEFT. Both keys carry the same shape,
        # `[[pid, created], ...]`, so a reader that can parse one can parse the
        # other. They appear only when a holder was actually signalled: after
        # `none`, `stale-cleared` or `remote-holder` nothing was signalled and
        # abort has no standing to report a reading it never took. Absent and
        # empty are different claims -- absent says abort did not look, empty
        # says it looked and found nothing -- and only the second can carry the
        # promise `ok` now makes.
        table_after = descendants.process_table()
        blind_after = descendants.blind_reason(table_after)
        blind = (("before", blind_before) if blind_before
                 else ("after", blind_after) if blind_after else None)

        if blind_before is None:
            # A blind AFTER read does not unmake the BEFORE one: that reading
            # was taken and it stands.
            result["descendants_before"] = descendants.as_result(before)

        if blind is not None:
            # ABORT NEVER CLAIMS SUCCESS OVER A READING IT DID NOT TAKE (R5d).
            # `alive_after` stays ABSENT rather than empty, because absent says
            # abort did not look and empty says it looked and found nothing --
            # and only one of those is true here. `tree_read` says which read
            # failed and why, so an operator has somewhere to go.
            #
            # NO `return` HERE, and that is deliberate. This function's ways
            # out are pinned by the exit contract, because each one is a place
            # the result line can be got wrong. This branch needs no exit of
            # its own: it sets what it must and falls through to the single
            # one at the bottom, where every other ending already goes.
            which, why = blind
            result["tree_read"] = {"failed": which, "reason": why}
            result["ok"] = False
        else:
            survivors = descendants.still_alive(before, table_after)
            # The same reader for the holder as for everything else: matched
            # on pid AND creation time, so a reused pid is a different process
            # and reads as gone, which it is.
            alive = sorted(
                descendants.still_alive(holder_before, table_after)
                + survivors)
            result["alive_after"] = descendants.as_result(alive)
            if alive or result.get("lock") == "signalled":
                # THE ACCEPTANCE LINE, and the one behaviour this PR
                # changes: after abort returns ok: true, no process belonging
                # to that run is alive -- and if any is, abort does not report
                # ok: true. Every reader of this command takes the last stdout
                # line's `ok` as the answer (#128), so an ok: true beside a
                # live Member is the report #148 was filed about, whatever the
                # message next to it says. The words are unchanged; only the
                # success bit moves.
                result["ok"] = False

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
