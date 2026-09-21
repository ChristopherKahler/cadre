r"""What a Cadre scheduled task on Windows actually runs (#119, design D1a).

A task whose command is a console program gets a console, and a console gets a
window: every pulse drew one on the operator's desktop, and closing it killed
the pulse. ``conhost.exe --headless`` hid the window, but on Windows 10 19045 it
was measured returning exit code 0 for a client that exited 5 or 7, and 0 again
for a flag it did not know. Task Scheduler would have recorded every failed
pulse as a success.

So a task runs the firm's own ``pythonw.exe``, which has no console at all, on a
small generated stub::

    "<venv>\Scripts\pythonw.exe" "<launcher dir>\<stem>.pyw"

and the stub hands over to :func:`main`, which starts the task's command through
:mod:`firm.core.proc`. pythonw has no console, so that module gives the command
``CREATE_NO_WINDOW``: a console of its own that has no window, which everything
the command starts inherits. Measured on a private desktop (fork doc, "M-W1 ·
PRIVATE DESKTOP · RESULTS"): pythonw plus ``firm.core.proc`` opened no window and
brought exit code 7 back; pythonw plus a raw ``subprocess.call`` opened one.

THE STUB (``<stem>.pyw``) uses the standard library only until it has somewhere
to write. pythonw has no stdout and no stderr, so anything raised before the log
is open would vanish without a trace. The stub opens ``<stem>.log`` as UTF-8,
points ``sys.stdout`` and ``sys.stderr`` at it, and only then imports this
module. From there on, anything raised is written to the log as a traceback and
the process exits 3. A log it cannot open exits 2, before any Cadre code is
imported. Running this module by path, or with ``-m``, would import Cadre
before that redirect, and that is the whole reason a stub exists.

THE SPEC (``<stem>.json``) carries what the old ``.cmd`` launcher carried: the
command, the environment added over the user's own, the working directory,
whether to supervise, and a timer's interval. :func:`main` reads it at start.

OUTPUT. The command's stdout and stderr both go to the log's own file handle.
Nothing is piped, so nothing can fill up: a command writing 200 KB to each
stream never waits on a reader. Each run of the command starts the log afresh,
as the ``> log`` redirect in the ``.cmd`` launcher did, so a service that
restarts all day does not grow its log without end.

EXIT CODE. The command's exit code is the launcher's, so Task Scheduler's Last
Result is the pulse's own. A Windows status code such as ``0xC000013A`` is
returned in its signed form, because ``sys.exit`` converts to a C long: measured
on Windows with Python 3.12.6, ``sys.exit(3221225786)`` raised OverflowError and
exited ``0xFFFFFFFF``, while ``sys.exit(-1073741510)`` exited ``0xC000013A``.

SUPERVISION. Task Scheduler cannot restart an interactive user's task when it
fails, so a service runs its command again 5 seconds after every exit, as the
``.cmd`` loop did, for as long as this launcher runs. Ending the launcher does
not end a command it has already started: on Windows ``schtasks /End`` left the
command and its console running (#119 fork doc, M-W2 arm A4b-1).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import time
import traceback
from pathlib import Path
from typing import IO, Any, Callable

from firm.core.proc import run_utf8
from firm.sched import winjob

#: Seconds a supervised command waits before it runs again.
BACKOFF_SECONDS = 5

# The stub a task runs. Standard library only until the log is open, and the
# redirect happens before the first Cadre import: see the module docstring.
_STUB = """\
# Cadre's launcher for one Windows scheduled task (issue #119). Task Scheduler
# runs this file with pythonw.exe. Written by firm.sched.winlaunch; do not edit.
import sys

try:
    log = open({log}, "w", encoding="utf-8", errors="replace")
except BaseException:
    sys.exit(2)
sys.stdout = sys.stderr = log
try:
    from firm.sched.winlaunch import main
    code = main({spec}, log)
except BaseException:
    import traceback
    traceback.print_exc(file=log)
    log.flush()
    sys.exit(3)
log.flush()
sys.exit(code)
"""


def stub_text(spec: Path, log: Path) -> str:
    """The ``.pyw`` a task runs for the launcher described by *spec*."""
    return _STUB.format(log=repr(str(log)), spec=repr(str(spec)))


def write_launcher(directory: Path, stem: str, *, argv: list[str],
                   env: dict[str, str], cwd: Path | str, supervise: bool,
                   interval: str | None = None) -> Path:
    """Write ``<stem>.json`` and ``<stem>.pyw`` into *directory*.

    Returns the stub's path, which is what the task command names. The log,
    ``<stem>.log``, is created by the stub when the task runs.
    """
    directory.mkdir(parents=True, exist_ok=True)
    spec: dict[str, Any] = {"stem": stem, "argv": [str(a) for a in argv],
                            "env": dict(env), "cwd": str(cwd),
                            "supervise": bool(supervise)}
    if interval is not None:
        spec["interval"] = interval
    spec_path = directory / f"{stem}.json"
    spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    stub = directory / f"{stem}.pyw"
    stub.write_text(stub_text(spec_path, directory / f"{stem}.log"),
                    encoding="utf-8")
    # A REINSTALL DROPS THE OLD CONTAINMENT ANSWER (#141). It was written by a
    # launcher that no longer exists, and carrying it forward would let
    # `status()` report a freshly installed task as contained before any
    # launcher of THIS install has run -- a true-looking reading of a process
    # that is gone. Absent until answered is the honest state.
    containment_path(directory, stem).unlink(missing_ok=True)
    return stub


def _exit_code(returncode: int) -> int:
    """*returncode* in the form ``sys.exit`` hands to the process whole."""
    code = returncode & 0xFFFFFFFF
    return code - (1 << 32) if code & 0x80000000 else code


def _fresh(log: IO[str]) -> None:
    """Empty the log before a run. Seek first: truncating at the old position
    would leave the next run writing after a gap."""
    log.flush()
    log.seek(0)
    log.truncate()


def containment_path(directory: Path, stem: str) -> Path:
    """Where a launcher records whether its tree is contained (#141, C3)."""
    return directory / f"{stem}.containment.json"


def record_containment(directory: Path, stem: str,
                       held: winjob.Containment) -> Path:
    """Write the containment answer where `status()` can read it.

    A LOG LINE IS NOT ENOUGH ON ITS OWN, which is condition C3: a machine where
    the job cannot be made goes back to today's behaviour, and without a record
    there is nothing to show for it. The log is also rewritten on every run of a
    supervised command, so an answer that lives only there disappears the next
    time the command restarts.
    """
    written = containment_path(directory, stem)
    written.parent.mkdir(parents=True, exist_ok=True)
    record = held.as_record(os.getpid(),
                            dt.datetime.now().isoformat(timespec="seconds"))
    written.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return written


def main(spec_path: str | os.PathLike[str], log: IO[str], *,
         run: Callable[..., Any] = run_utf8,
         sleep: Callable[[float], Any] = time.sleep,
         contain: Callable[[], winjob.Containment] = winjob.contain_this_process
         ) -> int:
    """Run the command *spec_path* describes, with its output on *log*.

    A timer runs it once and returns its exit code; a command that cannot start
    raises, so the stub writes the traceback and exits non-zero. A service runs
    it again :data:`BACKOFF_SECONDS` after every exit, a failure to start
    included, and never returns.

    CONTAINMENT HAPPENS FIRST, before the command exists (#141). A child started
    before the job is assigned is OUTSIDE it, and closing the handle later never
    reaches that child -- so the order is the property, not the presence of a
    job. ``schtasks /End`` ends this process, its handle closes with it, and
    everything it started ends too; without that, `/End` was measured leaving
    three processes of the command running five seconds later (M-W2, arm A4b-1).

    A HOST THAT CANNOT CONTAIN STILL GETS ITS PULSE. Refusing to run would turn
    a locked-down machine into a firm with no heartbeat, which is worse than
    today's leak. What it never does is run quietly: the answer goes into the
    log and into the record `status()` reads.
    """
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    argv = list(spec["argv"])
    env = dict(os.environ)
    env.update(spec.get("env") or {})
    supervise = bool(spec.get("supervise"))

    held = contain()
    try:
        record_containment(Path(spec_path).parent, spec["stem"], held)
        record_failure = ""
    except Exception as exc:                    # noqa: BLE001
        # THE RECORD MUST NOT BE ABLE TO KILL THE PULSE. Anything raised out of
        # `main` is caught by the stub, written to the log as a traceback, and
        # exited 3 -- with the command never started. So an unguarded write
        # here would mean a read-only launcher directory or a full disk stops
        # the heartbeat, and it would stop it in order to fail to write a note
        # SAYING the heartbeat is fine.
        #
        # It is the same rule the containment itself follows: a host that
        # cannot make a job still gets its pulse. The record reports a degraded
        # state; it is never allowed to cause one. What it does instead is say
        # so in the log, because a `status()` silent about containment with no
        # reason anywhere is the one outcome an operator cannot act on.
        record_failure = (
            f"winlaunch: the containment record could not be written, so "
            f"`heartbeat status` will not say whether this tree is contained: "
            f"{exc}")

    while True:
        _fresh(log)
        if record_failure:
            print(record_failure, file=log, flush=True)
        if held.supported and not held.contained:
            # Inside the loop, AFTER `_fresh`: a supervised command truncates
            # this log on every restart, so a line printed once before the loop
            # would be gone from the log an operator actually opens.
            #
            # ONLY WHERE THE MECHANISM EXISTS. On a host with no job objects
            # there is nothing an operator could do about it and this launcher
            # is not that host's scheduler, so the line would be noise in every
            # log forever. The RECORD still says `contained: false` with the
            # reason on every platform, which is what condition C3 asks for --
            # the difference is between telling someone about a failure they
            # can fix and shouting about a mechanism their kernel never had.
            print(f"winlaunch: the pulse tree is NOT contained, so ending this "
                  f"task will not end what it started: {held.reason}",
                  file=log, flush=True)
        try:
            done = run(argv, cwd=spec["cwd"], env=env, stdin=subprocess.DEVNULL,
                       stdout=log, stderr=log)
        except Exception:
            if not supervise:
                raise
            traceback.print_exc(file=log)
            print(f"winlaunch: {argv[0]} could not start; trying again in "
                  f"{BACKOFF_SECONDS} seconds", file=log, flush=True)
        else:
            code = _exit_code(done.returncode)
            if not supervise:
                return code
            print(f"winlaunch: {argv[0]} exited {code}; starting it again in "
                  f"{BACKOFF_SECONDS} seconds", file=log, flush=True)
        sleep(BACKOFF_SECONDS)
