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
``.cmd`` loop did, until the task ends with the user's logon session.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import traceback
from pathlib import Path
from typing import IO, Any, Callable

from firm.core.proc import run_utf8

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


def main(spec_path: str | os.PathLike[str], log: IO[str], *,
         run: Callable[..., Any] = run_utf8,
         sleep: Callable[[float], Any] = time.sleep) -> int:
    """Run the command *spec_path* describes, with its output on *log*.

    A timer runs it once and returns its exit code; a command that cannot start
    raises, so the stub writes the traceback and exits non-zero. A service runs
    it again :data:`BACKOFF_SECONDS` after every exit, a failure to start
    included, and never returns.
    """
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    argv = list(spec["argv"])
    env = dict(os.environ)
    env.update(spec.get("env") or {})
    supervise = bool(spec.get("supervise"))
    while True:
        _fresh(log)
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
