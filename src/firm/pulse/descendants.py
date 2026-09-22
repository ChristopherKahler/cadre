"""Who belongs to this pulse's tree, and is any of it still running? (#148)

``firm pulse --abort`` used to signal the lock's holder and then report
``ok: true`` without looking at anything. On every start path that no job
contains -- a pulse run by hand, and the hub's pulse button -- the Member the
holder started outlived the signal, while the database recorded the run as
finished. A leaked process the database still calls active is a mess someone
can see; one it calls finished is invisible, and it keeps spending.

So abort writes down the tree BEFORE it signals, and looks again afterwards.
This module is the looking.

THREE RULES, EACH ONE MEASURED RATHER THAN CHOSEN.

**A generation, not a pid.** Every process here is identified by its pid AND
its creation time. Pids are reused, and a reused pid is the one reading that
turns "the Member is still running" into "something is running" -- which is the
same sentence with none of the meaning.

**Liveness is presence in the process table, never permission to signal**
(R5b). ``os.kill(pid, 0)`` and ``OpenProcess`` both answer "may I touch this?",
which is a different question from "is this running?" and it has an EPERM
branch. The product already shows the cost: its two ``_pid_alive`` copies
disagree on EPERM (``cli/pulse.py:563`` returns True, ``pulse/cleanup.py:104``
returns False), so one process could read alive to abort and dead to the
cleanup. Reading the table has no such branch, needs no handle, and answers the
same for a descendant owned by another user. On Windows ``os.kill(pid, 0)``
would be worse than wrong: anything but the ``CTRL_*`` events routes to
``TerminateProcess``, so the probe would KILL what it was asked about.

**A zombie is not alive** (R5a). A process in state ``Z`` on POSIX is an exit
status nobody has collected: it runs no code, holds no memory and spends
nothing. Naming one as a survivor would make ``ok: false`` permanent for any
holder that has not reaped a child -- an abort that can never succeed on a
perfectly healthy firm. Windows has no zombies, so presence in the table is the
whole answer there.

A STAMP COMPARES ONLY WITH ITSELF, ON THE SAME HOST. The POSIX string is a
count of clock ticks since boot; the Windows string is .NET ticks since
0001-01-01 UTC. They are never comparable to each other, and neither is
meaningful on another machine. The Windows one is taken through
``ToUniversalTime()`` rather than as local ticks (R5c): ``CreationDate``
converts with ``Kind`` Local, and a local count is not stable across a daylight
saving change -- two reads either side of one give different strings for the
same process, which reads as that process being GONE. Empty over a live
survivor is the dangerous direction.

TWO LIMITATIONS, BOTH STATED AT THE CODE RATHER THAN DISCOVERED LATER.

1. A process the holder starts AFTER the snapshot is not in it, and will not be
   reported. The snapshot is taken while the holder is alive and about to be
   signalled, so the window is small, but it is not zero.
2. A descendant whose INTERMEDIATE parent exited before the snapshot is not
   reachable by the walk at all: on POSIX it has been reparented to init, and
   on Windows the dead parent's row is simply gone, so no chain of
   ``ParentProcessId`` leads to it from the holder.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from firm.core.proc import run_utf8

#: One generation: the pid and the creation time that tells two uses of that
#: pid apart. THIS IS THE ONE SHAPE, and both ``descendants_before`` and
#: ``alive_after`` in abort's result carry it: a two-element list,
#: ``[pid, created]``, with *created* a string whose only contract is that it
#: compares equal for the same process and unequal across a pid reuse.
Generation = tuple[int, str]

_WINDOWS = sys.platform.startswith("win")


def _posix_table() -> dict[int, tuple[int, str, str]]:
    """``{pid: (ppid, starttime, state)}`` from one pass over ``/proc``.

    ``starttime`` is field 22 and ``state`` is field 3, both counted from the
    LAST ``)`` rather than by splitting the whole line: field 2 is the
    executable name in parentheses and may contain spaces, which shifts every
    field after it. A reader that splits naively returns another process's
    number and looks entirely healthy doing it.
    """
    table: dict[int, tuple[int, str, str]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return table
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            raw = Path("/proc", entry, "stat").read_text()
            after = raw[raw.rindex(")") + 1:].split()
            table[int(entry)] = (int(after[1]), after[19], after[0])
        except (OSError, ValueError, IndexError):
            continue        # it exited while we walked; that is an answer too
    return table


def _windows_table() -> dict[int, tuple[int, str, str]]:
    """``{pid: (ppid, CreationDate ticks, "R")}`` from one CIM query.

    One call for the whole table, not one per pid: a tree walk asks about every
    process it can reach, and a query apiece would be a PowerShell start apiece.
    ``CreationDate`` is the same field the Windows live leg reads.
    """
    table: dict[int, tuple[int, str, str]] = {}
    # THROUGH `run_utf8`, NEVER `subprocess` DIRECTLY, for two reasons that
    # both matter here. It decodes the child as UTF-8 instead of the operator's
    # locale codec, and it carries the no-window flags: a pulse runs detached
    # with no console, so a bare PowerShell start would put a console window on
    # the operator's screen every time abort looked at a tree (#119). The
    # sweep in `tests/test_no_window_flags.py` holds both rules and caught this
    # module starting a child outside `firm.core.proc`.
    try:
        out = run_utf8(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process | "
             "Select-Object ProcessId,ParentProcessId,"
             "@{n='Created';e={$_.CreationDate.ToUniversalTime()"
             ".Ticks}} | "
             "ConvertTo-Json -Compress"],
            capture_output=True, timeout=120).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return table
    if not out:
        return table
    try:
        rows = json.loads(out)
    except ValueError:
        return table
    if isinstance(rows, dict):
        rows = [rows]
    for row in rows:
        try:
            table[int(row["ProcessId"])] = (int(row["ParentProcessId"]),
                                            str(row["Created"]), "R")
        except (KeyError, TypeError, ValueError):
            continue
    return table


def process_table() -> dict[int, tuple[int, str, str]]:
    """``{pid: (ppid, created, state)}`` for every process this host can see.

    Never raises: a table that could not be read comes back empty, and every
    caller below treats an empty table as "nothing found" rather than as an
    error. Abort must not fail because a reading failed -- it reports what it
    saw, and an empty reading is a claim it is entitled to make wrongly far
    less often than it is entitled to crash.
    """
    return _windows_table() if _WINDOWS else _posix_table()


def _is_alive(entry: tuple[int, str, str]) -> bool:
    """A zombie is not alive (R5a); anything else in the table is."""
    return entry[2] != "Z"


def generation_of(pid: int,
                  table: dict[int, tuple[int, str, str]] | None = None
                  ) -> Generation | None:
    """This pid's generation, or None when it is not a live process."""
    table = process_table() if table is None else table
    entry = table.get(pid)
    if entry is None or not _is_alive(entry):
        return None
    return (pid, entry[1])


def descendants_of(pid: int,
                   table: dict[int, tuple[int, str, str]] | None = None
                   ) -> list[Generation]:
    """Every live process under *pid*, TRANSITIVELY, as generations.

    Transitively is the whole point and it is not defensive programming: a real
    Member arrives under a launcher (a venv ``python.exe`` on Windows starts
    the real interpreter as its child), so the holder's DIRECT children are
    launchers and the process actually doing the work sits a generation below
    them. A walk one level deep reports the launcher, misses the worker, and
    then says the tree is gone.

    Ordered by pid so two readings of the same tree compare equal.
    """
    table = process_table() if table is None else table
    children: dict[int, list[int]] = {}
    for child, (parent, _created, _state) in table.items():
        children.setdefault(parent, []).append(child)

    found: list[Generation] = []
    seen: set[int] = {pid}
    queue = list(children.get(pid, ()))
    while queue:
        current = queue.pop()
        if current in seen:
            continue        # a cycle cannot happen, but a wrong table can
        seen.add(current)
        entry = table.get(current)
        if entry is not None and _is_alive(entry):
            found.append((current, entry[1]))
        queue.extend(children.get(current, ()))
    return sorted(found)


def still_alive(generations: list[Generation]) -> list[Generation]:
    """The subset of *generations* still running, read fresh.

    Matching on the creation time as well as the pid is what makes this a
    re-read of the SAME processes rather than a question about whoever holds
    those numbers now.
    """
    table = process_table()
    alive: list[Generation] = []
    for pid, created in generations:
        entry = table.get(pid)
        if entry is not None and _is_alive(entry) and entry[1] == created:
            alive.append((pid, created))
    return sorted(alive)


def as_result(generations: list[Generation]) -> list[list]:
    """The wire shape abort reports: ``[[pid, created], ...]``.

    One shape for both keys, so a reader that can parse ``descendants_before``
    can parse ``alive_after`` without being told twice.
    """
    return [[pid, created] for pid, created in generations]
