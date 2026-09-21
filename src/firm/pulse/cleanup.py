"""Ending a pulse is not the same as clearing up after it. Issue #141.

THE GAP THIS CLOSES. Once the launcher holds a kill-on-close job, ending a
Cadre task ends the pulse and every process under it -- which is what #141 is
for. It does not touch the firm's DATABASE, and two rows there outlive the
processes that wrote them:

* ``pulse_lock``. ``dblock.acquire`` (``pulse/dblock.py:30-51``) replaces a
  holder only when its heartbeat is older than ``LOCK_TTL_SEC``, 600 seconds
  (``:18``), and it never checks whether the holder's process is alive -- the
  only ``pid`` in that file is ``os.getpid()`` at ``:27``, building the
  holder's identity string. So a killed pulse leaves its row for up to ten
  minutes and a restart inside that window is refused.

* ``member_run``. The killed Member's row stays ``running``. The reaper at
  ``pulse/orchestrator.py:88-121`` does close such rows, as ``failed`` with
  ``error.type='orphaned'`` (``:119-121``), but only when they are older than
  twice the contract timeout plus a grace (``:111``) AND only when a pulse
  runs. After ``disable`` no pulse comes, so neither condition is ever met and
  the row stays ``running`` for good. **The reaper cannot be relied on when the
  only thing that calls it is the thing being disabled.**

So the cleanup is its own step, and this module is it.

WHY A MODULE OF ITS OWN, rather than more code in ``cli/heartbeat.py``. That
file is changed by two other open PRs, and a cleanup written into it would
collide with both. Here the verbs gain an import and one call line each, the
logic is testable without a CLI, and the next verb that needs it calls the same
function instead of growing a third copy.

A LIVE HOLDER IS LEFT ALONE. This mirrors ``firm pulse --abort``: a lock whose
holder is still running on this machine is NOT cleared, because the holder
releases it on its own way out and clearing it under a live pulse would let a
second one start beside it. That matters most exactly when it is least
expected -- a host where the job could not be created still has a live tree
after the task ends, and this function must not paper over it.

A REMOTE HOLDER IS NEVER TOUCHED. The lock is shared across every machine that
pulses this firm. A row held from another host belongs to that host's pulse,
and its TTL frees it if that pulse is dead.

PAID. That debt was recorded here rather than left to be found: ``cli/pulse.py``'s
``_handle_abort`` held its own copy of the same two steps, and was left alone
only because PR 142 was open on that file (osprey's ruling, 2026-09-21). PR 142
landed at 18:18 that day, so the copy came out and ``_handle_abort`` now calls
:func:`release_and_finalize`.

WHAT ABORT KEEPS, because it is abort's and not this module's: signalling the
holder, counting what it signalled, and telling ``cleared`` (a holder abort
killed) apart from ``stale-cleared`` (a holder already dead when it looked).
This module never signals anything. The one behavioural difference between the
two callers -- whether a LIVE holder's runs get closed -- is the
``finalize_live_runs`` parameter below, which is why there is one rule here
instead of two rules in two files that drift apart without a test noticing.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any

#: What the caller is told when there was no lock row at all. Not an error: a
#: firm whose pulse never started has nothing to clear.
NO_LOCK = "none"


def _pid_alive(pid: int) -> bool:
    """Is this pid running? Measured, never assumed from the row's age.

    ``os.kill(pid, 0)`` on POSIX and ``OpenProcess`` on Windows, which is what
    ``cli/pulse.py`` does for the same question. A pid that cannot be read is
    reported as NOT alive, because the alternative -- treating "I could not
    tell" as "it is running" -- leaves the lock wedged for its full TTL over a
    process that ended cleanly.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                           ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD(0)
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259                   # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _wait_for_exit(pid: int, seconds: float, sleep: Any = None) -> float:
    """Give a holder a bounded chance to die on its own, and report how long.

    The task has just been ended, so a contained tree is on its way out but may
    not be gone yet -- and reading "still alive" a millisecond too early would
    leave a lock that was about to free itself. BOUNDED, because the other
    outcome is a host where containment failed and the pulse is not going
    anywhere; a cleanup that waited for that would hang the verb.

    Returns the seconds actually spent, which the caller reports, so "the
    holder did not die" comes with how long it was given.
    """
    if seconds <= 0:
        return 0.0
    import time as _time

    naptime = sleep or _time.sleep
    step, spent = 0.25, 0.0
    while spent < seconds:
        if not _pid_alive(pid):
            return spent
        naptime(step)
        spent += step
    return spent


def _finalize_orphans(conn: Any, firm_id: str, *, notes: str,
                      error: dict) -> tuple[list[str], list[dict]]:
    """Close every ``member_run`` still marked running for this firm.

    Through ``on_run_end`` rather than an UPDATE, so the ``usage_event`` and
    records rows a normal finish writes are written here too: a run that ends
    with no trace on Records is invisible to the Board reviewing what happened.

    One bad row never stops the rest. A cleanup that gave up halfway would
    leave a firm in a state nobody asked for and no verb produces.

    Returns ``(closed, failures)``. THE FAILURES ARE RETURNED RATHER THAN
    SWALLOWED: ``cli/pulse.py`` printed a ``warn`` line for a row it could not
    close, and this function catching the same exception with nothing said
    would have turned a loud failure into a silent one the day abort started
    calling it. A caller that reports ``runs_finalized: []`` with no reason is
    reporting a clean cleanup over a row still marked running.
    """
    from firm.hooks.run_record import on_run_end

    rows = conn.execute(
        "SELECT id FROM member_run WHERE firm_id = ? AND status = 'running'",
        (firm_id,),
    ).fetchall()
    closed: list[str] = []
    failures: list[dict] = []
    for row in rows:
        run_id = row["id"] if hasattr(row, "keys") else row[0]
        try:
            on_run_end(conn, firm_id=firm_id, run_id=run_id,
                       final_status="failed", notes=notes, error=error)
            closed.append(run_id)
        except Exception as exc:                       # noqa: BLE001
            failures.append({"run_id": run_id, "error": str(exc)})
    return closed, failures


def release_and_finalize(workspace: Path | str, firm_id: str | None = None, *,
                         by: str, wait_seconds: float = 0.0,
                         sleep: Any = None,
                         finalize_live_runs: bool = False) -> dict[str, Any]:
    """Clear this firm's pulse lock and close the runs its dead pulse left open.

    *by* names the verb doing it (``"heartbeat disable"``), and it reaches the
    Board: it is written into the finalized run's notes, so a run that ends this
    way says which action ended it rather than looking like an unexplained
    failure.

    Never raises. This runs at the end of a verb that has already done its real
    work -- the task is gone by the time it is called -- and a cleanup that
    raised would turn a successful disable into a failed command while leaving
    the task removed anyway.

    Returns what it found and what it did:

    ``lock``      ``"none"`` (no row), ``"cleared"``, ``"held-by-a-live-pulse"``
                  or ``"remote-holder"``.
    ``holder``    the holder string, when there was one.
    ``runs_finalized``  the ids closed, which is ``[]`` when there was nothing
                  to close -- a different fact from the key being absent.
    ``reason``    why nothing was done, when nothing was.
    ``waited_seconds``  how long a live holder was given to exit, when one was.
    ``runs_not_finalized``  present only when a row could not be closed, with
                  the id and the error for each -- so a caller never reports an
                  empty ``runs_finalized`` that means "nothing to do" and one
                  that means "nothing worked" in the same words.

    *finalize_live_runs* NAMES THE ONE DIFFERENCE BETWEEN THIS FUNCTION'S TWO
    CALLERS, so that difference lives in a parameter instead of in a second
    copy of the code. ``heartbeat disable`` leaves a live holder's runs alone:
    that pulse is still working and its runs are not disable's to close.
    ``firm pulse --abort`` has just signalled the holder and OWNS closing them
    -- without it the row stays ``running`` for good. THE LOCK GUARD IS NOT
    AFFECTED EITHER WAY: a live holder's lock is never cleared, whatever this
    is set to, because clearing it admits a second pulse beside the first.

    ONE LIMIT, STATED RATHER THAN LEFT TO BE FOUND. The guard is "clear only
    when the holder is verifiably dead", and the strongest identity available
    here is the PID ALONE: ``dblock.make_holder_id`` builds ``host:pid:nonce``
    (``pulse/dblock.py:24-27``) and records no start time, so a pid Windows has
    handed to an unrelated process reads as alive. That fails toward LEAVING
    THE LOCK ALONE, which is the safe direction -- the lock then frees on its
    own TTL rather than being cleared under a pulse that might be live. Adding
    a start time to the holder identity would close it properly and belongs to
    ``dblock``, not here.
    """
    from firm.core.db import connect, get_db_path

    result: dict[str, Any] = {"lock": NO_LOCK, "runs_finalized": []}
    try:
        db_path = get_db_path(Path(workspace))
        if not db_path.exists():
            result["lock"] = "no-db"
            result["reason"] = f"no firm database at {db_path}"
            return result

        from firm.core.db import resolve_firm_id
        from firm.pulse import dblock

        conn = connect(db_path)
        try:
            try:
                firm_id = resolve_firm_id(conn, firm_id)
            except ValueError as exc:
                result["lock"] = "firm-id-unresolved"
                result["reason"] = str(exc)
                return result

            holder = dblock.current_holder(conn, firm_id)
            if holder is None:
                result["lock"] = NO_LOCK
            else:
                result["holder"] = holder
                host, pid_str, _nonce = holder.split(":", 2)
                if host != socket.gethostname():
                    # Another machine's pulse. Its TTL frees it if it is dead,
                    # and clearing it from here would let two pulses run.
                    result["lock"] = "remote-holder"
                    result["reason"] = (
                        "the lock is held from another machine, so its own "
                        "host owns it; the TTL frees it if that pulse is dead")
                    return result
                waited = _wait_for_exit(int(pid_str), wait_seconds, sleep)
                if waited:
                    result["waited_seconds"] = waited
                if _pid_alive(int(pid_str)):
                    # THE CASE THE JOB WAS SUPPOSED TO PREVENT. Reached on a
                    # host where containment failed, and the honest answer is
                    # to leave the lock alone: the live holder releases it on
                    # its way out, and clearing it now would admit a second
                    # pulse beside the first.
                    result["lock"] = "held-by-a-live-pulse"
                    result["reason"] = (
                        f"process {pid_str} still holds the lock after waiting "
                        f"{waited:.1f}s, so the pulse tree was not contained; "
                        "its own exit releases it")
                    if not finalize_live_runs:
                        return result
                else:
                    dblock.release(conn, firm_id, holder)
                    result["lock"] = "cleared"

            closed, failures = _finalize_orphans(
                conn, firm_id,
                notes=f"the pulse was ended by {by}",
                error={"reason": "pulse-ended", "by": by})
            result["runs_finalized"] = closed
            if failures:
                result["runs_not_finalized"] = failures
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:                           # noqa: BLE001
        result["lock"] = "unreadable"
        result["reason"] = f"the cleanup could not complete: {exc}"
    return result
