"""#148: a pulse contains its own tree, and `--abort` checks before it claims.

Two properties, from the G0 verdict (lane doc 6159-6284) and its addendum 1
(6483-6542):

* **R2/R3, containment at start.** The pulse puts itself in a kill-on-close job
  before it spawns its first Member, through `winjob.contain_this_process()`,
  the way `winlaunch` already does. It runs anyway when the job cannot be made,
  never raises into its caller, and reports the outcome in its own result.

* **R1/R5, abort verifies.** Before signalling the holder, `--abort` writes down
  every process the holder has started, by pid AND creation time. After the
  grace window it looks again and reports `alive_after`. It says `ok: true`
  only when the holder is dead and that list is empty.

WHAT EACH TIER PROVES, so no leg claims a rank above the one it runs in:

* **Tier A** (here, every platform): OUR CODE ASKS. The primitive is called, in
  the right order, and its answer reaches the result. A green tier-A leg says
  nothing about whether any process died.
* **Tier A'** (here, every platform): THE REPORT FOLLOWS THE READING, including
  when the reading is "no", when it is a raise, and when the holder is still
  alive (R1a).
* **Tier B/C** (NOT here -- `tests/test_winsched_live.py`, CI's Windows job
  only): WINDOWS AGREES and THE TREE ACTUALLY DIES. Nothing in this file can
  prove containment: the suite runs on Linux, and `winjob` is Windows-only by
  `sys.platform`. Every containment assertion here is about the CALL.

THE ABORT LEGS ARE DIFFERENT, AND THIS IS THE POINT. Abort's snapshot and
re-read need no job at all -- they are a READING. So on this Linux host a
holder that exits and leaves work running is exactly the state #148 was filed
about, and it can be produced and asserted here for real.

THE TREE IS THREE DEEP BY CONSTRUCTION, AND THAT IS ADDENDUM 2's POINT. A real
Member arrives under a venv launcher, which inserts a generation: holder ->
launcher -> the thing actually doing the work. A walk that only looks at the
holder's direct children finds the launcher and stops, so it can report "the
tree is gone" while the leaf keeps running and keeps spending. Every leg below
asserts the LEAF, never the middle, so a one-level walk reddens everywhere.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

import firm.cli.pulse as pulse_cli
from firm.core.db import connect
from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.pulse import dblock, descendants
from firm.sched import winjob

FIRM = "containco"

#: The real-signal arms need a holder that exits on SIGTERM leaving children,
#: and a process tree readable through `/proc`. On Windows `os.kill(pid,
#: SIGTERM)` is `TerminateProcess`, so those arms are the live leg's, on CI
#: (R1b). The R1a leg below deliberately does NOT carry this mark: it fakes the
#: signal and therefore runs everywhere.
posix_only = pytest.mark.skipif(
    os.name != "posix",
    reason=("needs a POSIX-only shape: a zombie, or a `/proc` walk. The tree "
            "itself is a plain Popen chain and runs everywhere, so only the "
            "legs that genuinely need POSIX carry this mark"))


# ---------------------------------------------------------------------------
# A firm
# ---------------------------------------------------------------------------

def _firm(ws: Path, *, members=(("MEM-001", "Lead", "active"),)) -> Path:
    conn = connect(ws / ".firm" / "firm.db")
    try:
        apply_migrations(conn)
        create(conn, "firm", {"id": FIRM, "name": "Contain Co"})
        for member_id, name, status in members:
            create(conn, "member", {"id": member_id, "firm_id": FIRM,
                                    "name": name, "role": "worker",
                                    "status": status})
        create(conn, "operation", {"id": "OPS-001", "firm_id": FIRM,
                                   "name": "Ops"})
        create(conn, "project", {"id": "PROJ-001", "firm_id": FIRM,
                                 "operation_id": "OPS-001", "name": "Work",
                                 "status": "in_progress",
                                 "due_date": "2099-12-31"})
        conn.commit()
    finally:
        conn.close()
    return ws


def _units(ws: Path, *rows: dict) -> None:
    conn = connect(ws / ".firm" / "firm.db")
    try:
        for row in rows:
            create(conn, "unit", {"firm_id": FIRM, "project_id": "PROJ-001",
                                  "name": f"Unit {row['id']}", **row})
        conn.commit()
    finally:
        conn.close()


def _hold_lock(ws: Path, holder: str) -> None:
    conn = connect(ws / ".firm" / "firm.db")
    try:
        assert dblock.acquire(conn, FIRM, holder)
    finally:
        conn.close()


def _lock_holder(ws: Path) -> str | None:
    conn = connect(ws / ".firm" / "firm.db")
    try:
        return dblock.current_holder(conn, FIRM)
    finally:
        conn.close()


def _stand_in(tmp_path: Path) -> str:
    """A Member binary that is not claude and completes at once.

    The conftest points `CADRE_CLAUDE_BIN` at a path that does not exist, so a
    pulse with no stand-in stops at `runtime-not-wired` before it does anything
    else. A leg that asserted containment on THAT result would be asserting it
    on an error path and would say nothing about a normal pulse.
    """
    line = ('{"type":"result","subtype":"success","is_error":false,'
            '"usage":{"input_tokens":1,"output_tokens":1}}')
    if os.name != "posix":
        # WINDOWS DOES NOT EXECUTE `#!` SCRIPTS (WinError 193;
        # `platform_marks.py:146-154` carries the same fact for the other
        # suites). A shebang stand-in fails the three real-spawn legs for a
        # harness reason, and stops the order leg at `runtime-not-wired` before
        # it ever reaches a spawn. A `.cmd` runs, and the spawn layer accepts
        # it: `_is_execable` is True for any file on win32
        # (`spawn.py:211-212`), and `resolve_claude_bin` needs only isfile plus
        # X_OK (`:256`). It ignores its arguments, as a stand-in should.
        path = tmp_path / "stand-in-member.cmd"
        path.write_text("@echo off\r\necho " + line + "\r\n",
                        encoding="utf-8")
        return str(path)
    path = tmp_path / "stand-in-member"
    path.write_text("#!/bin/sh\nprintf '%s\\n' '" + line + "'\n",
                    encoding="utf-8")
    path.chmod(0o755)
    return str(path)


class _StopAtFirstSpawn(Exception):
    """Ends the pulse at its first Member spawn.

    A bare `AssertionError` here would be indistinguishable from a real failed
    assertion inside the pulse, so a leg could report "order proven" over a
    product error it actually caused.
    """


# ---------------------------------------------------------------------------
# The process tree: holder -> middle -> leaf, three deep ON PURPOSE
# ---------------------------------------------------------------------------

#: Each generation writes one line to a shared file and flushes it to disk, the
#: way the #147 live leg's recorder does: a pipe can be read before the writer
#: has produced everything, and a test that guesses which pid is which is a
#: test whose failures cannot be read.
#: NO `fork`, ON PURPOSE, AND IT IS A CORRECTNESS POINT RATHER THAN PORTABILITY
#: HOUSEKEEPING. The Windows descendant walk (`Win32_Process` by
#: `ParentProcessId`) is different code from the POSIX one. A fork-built tree
#: skips every tree leg on Windows, which would leave that walk exercised only
#: by the live leg -- and the live leg asserts `alive_after` is EMPTY, which an
#: walk that returns nothing passes falsely. A `Popen` chain runs everywhere,
#: so the same legs grade both walks.
#:
#: Each stage reports ITS OWN pid and creation time. Self-reporting avoids
#: reading another process's creation time from the test, which on Windows
#: would mean `OpenProcess` -- the very call R5b forbids the product's read to
#: use, and not a call this file should normalise either.
_STAGE = textwrap.dedent("""\
    import os, subprocess, sys, time

    label, out, nxt, script = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]


    def creation():
        # POSIX self-reports its start time; Windows reports "" and the TEST
        # fills the stamp from the one process table it already reads. ONE
        # REPRESENTATION, and that is F9: this used to return a FILETIME pair
        # from GetProcessTimes while the table stored CreationDate ticks. Both
        # name the same instant and they never compare equal, so every Windows
        # presence check would have read the process as gone -- and would have
        # read exactly like the product losing it.
        if os.name == "posix":
            raw = open("/proc/%d/stat" % os.getpid()).read()
            return raw[raw.rindex(")") + 1:].split()[19]
        return "-"


    if nxt != "none":
        subprocess.Popen([sys.executable, script, nxt, out,
                          "leaf" if nxt == "middle" else "none", script])

    with open(out, "a") as fh:
        fh.write("%s %d %s\\n" % (label, os.getpid(), creation()))
        fh.flush()
        os.fsync(fh.fileno())

    time.sleep(300)
    """)


def _win_table() -> dict[int, tuple[int, str]]:
    """The Windows process table: {pid: (ppid, CreationDate ticks)}. (F3)

    ONE `Get-CimInstance` call, not one per pid. Windows has no `/proc`, so
    every Windows branch below needs this table, and a per-pid query would turn
    a handful of assertions into a handful of PowerShell starts. `CreationDate`
    is the field `test_winsched_live.py` already reads at :121 and :167, so the
    generation identity here is the one the rest of this lane uses.

    NO `OpenProcess` ANYWHERE IN HERE. That is R5b's rule for the product's
    read, and a harness that took a handle to answer the same question would be
    demonstrating the opposite of what these legs assert.
    """
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-CimInstance Win32_Process | "
         "Select-Object ProcessId,ParentProcessId,"
         "@{n='Created';e={$_.CreationDate.ToUniversalTime().Ticks}} | ConvertTo-Json -Compress"],
        capture_output=True, text=True, timeout=120).stdout.strip()
    if not out:
        return {}
    rows = json.loads(out)
    if isinstance(rows, dict):
        rows = [rows]
    table: dict[int, tuple[int, str]] = {}
    for row in rows:
        try:
            table[int(row["ProcessId"])] = (int(row["ParentProcessId"]),
                                            str(row["Created"]))
        except (KeyError, TypeError, ValueError):
            continue
    return table


def _generation(pid: int | None) -> tuple[int, str] | None:
    """A process identity a recycled pid cannot forge: the pid AND when it
    started. None when the process is gone.

    Read from `/proc/<pid>/stat` field 22 (`starttime`, clock ticks since
    boot). The field is taken from the LAST `)` rather than by splitting the
    whole line, because field 2 is the executable name in parentheses and it
    may contain spaces -- splitting naively shifts every field after it and the
    reader silently returns another process's number.
    """
    if not pid:
        return None
    if os.name != "posix":
        row = _win_table().get(pid)
        return None if row is None else (pid, row[1])
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except (OSError, ValueError):
        return None
    try:
        after = raw[raw.rindex(")") + 1:].split()
        return pid, after[19]
    except (ValueError, IndexError):
        return None


def _alive(pid: int | None) -> bool:
    """Is this pid a live process? A harness reader, for preconditions.

    TWO REFUSALS AND A PLATFORM SPLIT, each for a measured reason:

    * 0 and None are refused outright. `os.kill(0, 0)` asks about the caller's
      whole PROCESS GROUP, not about a process, so it answers True for a pid
      that does not exist -- a liveness reader that cannot say "no" is worse
      than none.
    * ON WINDOWS THIS MUST NOT USE `os.kill` AT ALL. Anything but the `CTRL_*`
      events routes to `TerminateProcess`, so `os.kill(pid, 0)` KILLS the
      process it was asked about. The product says so in its own comment at
      `cli/pulse.py:543` and probes with `OpenProcess` instead. A test helper
      that got this wrong would quietly kill the holder it was checking on and
      then measure the corpse.
    """
    if not pid:
        return False
    if os.name != "posix":
        return _present(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _ppid(pid: int) -> int | None:
    if os.name != "posix":
        row = _win_table().get(pid)
        return None if row is None else row[0]
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return int(raw[raw.rindex(")") + 1:].split()[1])


def _proc_state(pid: int) -> str | None:
    """The third field of `/proc/<pid>/stat`: `Z` for a zombie, `S` sleeping.

    POSIX only, and not a gap: Windows has no zombies, so presence in the table
    IS the answer there and `_present` never consults this.
    """
    if os.name != "posix":
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return raw[raw.rindex(")") + 1:].split()[0]


def _depth_below(ancestor: int, pid: int, limit: int = 12) -> int | None:
    """How many generations *pid* sits below *ancestor*, or None if unrelated.

    Asserted rather than assumed, because the depth is the whole point of the
    three-deep tree: a venv launcher inserts a generation of its own, so the
    number this returns is the only thing that says the tree really is deeper
    than a one-level walk can reach.

    THE TABLE IS READ ONCE FOR THE WHOLE WALK (F10). On Windows `_ppid` starts
    a PowerShell, and a twelve-step walk that called it each time would start
    twelve. One read also makes the walk self-consistent: every step reads the
    same instant rather than a table that moved under it.
    """
    table = _win_table() if os.name != "posix" else None
    steps = 0
    cur = pid
    while steps < limit:
        parent = table.get(cur, (None, ""))[0] if table is not None \
            else _ppid(cur)
        if parent is None or parent <= 1:
            return None
        steps += 1
        if parent == ancestor:
            return steps
        cur = parent
    return None


def _present(pid: int | None, started: str | None = None) -> bool:
    """Is this generation still in the process table? (R5b)

    A DESCENDANT'S LIVENESS IS NOT `_alive`'s QUESTION AND MUST NOT USE ITS
    ANSWER. `_alive` asks the kernel "may I signal this?", so it inherits
    whatever `os.kill` says about permission -- and the two `_pid_alive` copies
    in the product already disagree on EPERM (`cli/pulse.py:563` True,
    `cleanup.py:104` False). A descendant is decided instead by PRESENCE IN THE
    PROCESS TABLE WITH THE SAME CREATION TIME, which no permission can change
    and no recycled pid can forge, with state `Z` reading dead (R5a).

    Two names rather than one, because one name serving both questions is how a
    reader ends up answering the question it was not asked.
    """
    if not pid:
        return False
    if os.name != "posix":
        # The same table every other Windows branch reads, so presence and
        # identity cannot disagree with each other. Windows has no zombies, so
        # presence with a matching creation time IS the answer.
        row = _win_table().get(pid)
        if row is None:
            return False
        return started is None or row[1] == started
    gen = _generation(pid)
    if gen is None:
        return False
    if started is not None and gen[1] != started:
        return False                       # same pid, different process
    return _proc_state(pid) != "Z"


def _pids_in(entries) -> list[int]:
    """The pids out of a reported list, whatever shape the report settles on.

    A generation may land as `[pid, started]` or as `{"pid": ..., ...}`; this
    reads either rather than pinning a shape the verdict left open.
    """
    out = []
    for entry in entries or []:
        if isinstance(entry, (list, tuple)) and entry:
            out.append(int(entry[0]))
        elif isinstance(entry, dict) and entry.get("pid") is not None:
            out.append(int(entry["pid"]))
        elif isinstance(entry, int):
            out.append(entry)
    return out


class _Tree:
    """A holder, a middle generation and a leaf -- or a lone holder.

    The middle generation is the point: a real Member arrives under a venv
    launcher, so a walk that stops at the holder's direct children finds the
    launcher and misses the work.
    """

    def __init__(self, tmp_path: Path, *, depth: int = 3) -> None:
        self.record = tmp_path / "tree.txt"
        self.record.write_text("", encoding="utf-8")
        script = tmp_path / "stage.py"
        script.write_text(_STAGE, encoding="utf-8")
        nxt = "middle" if depth >= 3 else "none"
        self.proc = subprocess.Popen(
            [sys.executable, str(script), "holder", str(self.record), nxt,
             str(script)])
        # THE HOLDER IS REAPED THE MOMENT IT DIES. It is this pytest process's
        # own child now that nothing forks, so an exited holder would sit as a
        # zombie until someone waited on it -- and abort's `_pid_alive` reads a
        # zombie as alive (R5a), which would send every leg down the
        # `signalled` branch instead of `cleared`. The exit contract's
        # `_live_holder` reaps on a thread for exactly this reason.
        threading.Thread(target=self.proc.wait, daemon=True).start()
        self.stages = self._await(depth)
        self.holder = self.stages["holder"][0]
        self.middle = self.stages.get("middle", (None, None))[0]
        self.leaf = self.stages.get("leaf", (None, None))[0]

    def _await(self, depth: int) -> dict[str, tuple[int, str]]:
        deadline = time.time() + 30
        stages: dict[str, tuple[int, str]] | None = None
        while time.time() < deadline:
            lines = [ln.split() for ln in
                     self.record.read_text(encoding="utf-8").splitlines() if ln]
            if len(lines) >= depth:
                stages = {p[0]: (int(p[1]), p[2]) for p in lines}
                break
            time.sleep(0.05)
        if stages is None:
            raise AssertionError(
                f"the tree never reached {depth} generations; recorded: "
                f"{self.record.read_text(encoding='utf-8')!r}")
        if os.name == "posix":
            return stages
        # WINDOWS STAMPS COME FROM THE TABLE, not from the stage (F9). Every
        # generation has reported and each is sleeping 300 s, so each one is
        # certainly present; a missing row is a real failure and says so here
        # rather than becoming a blank stamp that quietly matches nothing.
        table = _win_table()
        filled: dict[str, tuple[int, str]] = {}
        for label, (pid, _placeholder) in stages.items():
            row = table.get(pid)
            assert row is not None, (
                f"the {label} generation (pid {pid}) reported itself and is "
                f"sleeping, but is not in the process table")
            filled[label] = (pid, row[1])
        return filled

    def close(self) -> None:
        for pid in (self.leaf, self.middle, self.holder):
            if not pid:                 # 0/None would mean the process GROUP
                continue
            if os.name != "posix":
                # `signal.SIGKILL` DOES NOT EXIST ON WINDOWS. Referencing it is
                # an AttributeError, so a teardown written with it takes the
                # whole run down on the platform the product ships to.
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=60)
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass


@pytest.fixture
def tree(tmp_path):
    """holder -> middle -> leaf, three real processes."""
    base = tmp_path / "tree3"
    base.mkdir(parents=True, exist_ok=True)
    made = _Tree(base, depth=3)
    try:
        yield made
    finally:
        made.close()


@pytest.fixture
def lonely_tree(tmp_path):
    """A holder with nothing under it.

    The clean case cannot be made by killing the children of a live holder:
    that leaves zombies the holder never reaps, and `os.kill(pid, 0)` answers
    for a zombie. Not starting them is the only way to measure "nothing
    survived" without measuring the harness instead.
    """
    base = tmp_path / "tree1"
    base.mkdir(parents=True, exist_ok=True)
    made = _Tree(base, depth=1)
    try:
        yield made
    finally:
        made.close()


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(pulse_cli.__file__).resolve().parents[2])
    env.pop("FIRM_ID", None)
    env.pop("CADRE_DB_URL", None)
    return env


def _abort(ws: Path, timeout: int = 120) -> tuple[int, dict | None, str]:
    """`python -m firm pulse --abort` as a child, with its last line parsed."""
    proc = subprocess.run(
        [sys.executable, "-m", "firm", "pulse", "--workspace", str(ws),
         "--abort"],
        capture_output=True, env=_child_env(), timeout=timeout,
        stdin=subprocess.DEVNULL)
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    lines = out.strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else None
    except ValueError:
        result = None
    return (proc.returncode, result if isinstance(result, dict) else None,
            f"rc {proc.returncode}\nstdout:\n{out}\nstderr:\n{err}")


# ---------------------------------------------------------------------------
# CONTROLS. Green at the base and green after: they prove the harness can see,
# so a red leg below means the product and not the instrument.
# ---------------------------------------------------------------------------

def test_control_the_tree_is_three_generations_deep_and_all_are_alive(tree):
    """Without this, every leg below could pass by measuring a tree that was
    never built, and the leaf assertions would be vacuous."""
    assert tree.holder and tree.middle and tree.leaf
    assert len({tree.holder, tree.middle, tree.leaf}) == 3, tree.stages
    for name, pid in (("holder", tree.holder), ("middle", tree.middle),
                      ("leaf", tree.leaf)):
        assert _alive(pid), f"the {name} died before the test began"
    assert _generation(tree.leaf) is not None
    print(f"[148] tree depth 3: holder={tree.holder} middle={tree.middle} "
          f"leaf={tree.leaf}")


def test_control_the_leaf_sits_at_least_two_generations_below_the_holder(tree):
    """THE CONTROL ADDENDUM 2 ASKS FOR, stated as a property of the TREE rather
    than of the walk: if the leaf were a direct child, a one-level walk would
    find it and the leg that says "a one-level walk must redden" would prove
    nothing.

    The depth is guaranteed by construction here and NOT by the venv. avocet
    measured that a venv launcher inserts a generation of its own, so a tree
    that merely happened to be deep on this host would be two deep on another
    and these legs would quietly stop testing anything."""
    depth = _depth_below(tree.holder, tree.leaf)
    print(f"[148] walk depth from holder {tree.holder} to leaf {tree.leaf}: "
          f"{depth}")
    assert depth is not None, (
        f"the leaf {tree.leaf} is not a descendant of the holder "
        f"{tree.holder} at all")
    assert depth >= 2, (
        f"the leaf is only {depth} generation(s) below the holder; a one-level "
        f"walk would find it and every depth assertion here would be vacuous")
    assert _ppid(tree.leaf) != tree.holder


def test_control_the_generation_reader_returns_nothing_for_a_dead_pid(tree):
    """The must-fail half of the reader. A reader that answers for a process
    that does not exist would report every survivor as present and every
    absence as proven."""
    leaf = tree.leaf
    tree.close()
    # WAIT ON `_present`, NOT `_alive`. A killed leaf sits in state Z until
    # something reaps it, and `_alive` and `_generation` both still answer for
    # a zombie -- so a wait on `_alive` can time out and the assertion below
    # can fail on a slow reap rather than on a defect. `_present` reads Z as
    # dead (R5a), which is the question this control is actually asking.
    for _ in range(100):
        if not _present(leaf):
            break
        time.sleep(0.05)
    assert not _present(leaf), "the reader answered for a dead pid"


def test_control_the_containment_primitive_answers_on_this_host_without_raising():
    """`contain_this_process` promises it never raises. This EXECUTES it rather
    than reading the promise. On Linux the honest answer is `supported: False`,
    and that is an answer, not a failure."""
    held = winjob.contain_this_process()
    assert isinstance(held, winjob.Containment)
    assert held.contained is (sys.platform == "win32")
    if sys.platform != "win32":
        assert held.supported is False
        assert "Windows" in held.reason
    record = held.as_record(os.getpid(), "2026-09-22T00:00:00")
    assert set(record) >= {"contained", "reason", "limit_flags", "supported",
                           "pid", "at"}


# ---------------------------------------------------------------------------
# R2 / R3, condition 1: the pulse contains itself, before it spawns anything
# ---------------------------------------------------------------------------

def test_the_pulse_asks_for_containment_before_it_spawns_the_first_member(
        tmp_path, monkeypatch):
    """TIER A: our code ASKS, and asks in the order that makes it work.

    A child started before the job is assigned is outside it forever, so the
    ORDER is the property, not the presence of a call (`winlaunch.py`, measured
    at 198 < 235). Both halves are asserted in this one run: that a spawn was
    attempted at all, and that containment came first. Without the first half a
    pulse that spawned nothing would pass this leg while proving nothing."""
    ws = _firm(tmp_path / "ws")
    _units(ws, {"id": "UNIT-001", "assignee_member_id": "MEM-001"})
    monkeypatch.setenv("CADRE_CLAUDE_BIN", _stand_in(tmp_path))
    order: list[str] = []

    def fake_contain():
        order.append("contain")
        return winjob.Containment(True, "", 0x00002000)

    def fake_spawn(*args, **kwargs):
        order.append("spawn")
        raise _StopAtFirstSpawn

    monkeypatch.setattr(winjob, "contain_this_process", fake_contain)
    # PATCHED AT THE CALL SITES, NOT AT THE DEFINITION. Both callers do
    # `from firm.pulse.spawn import spawn_member_run`, which binds the function
    # into their own module at import time -- so patching `firm.pulse.spawn`
    # replaces a name nobody reads and leaves the real spawn running. The
    # `spawn in order` assertion below is what caught that; this comment is
    # here so the next reader does not have to be caught by it too.
    for site in ("firm.contracts.claude_code.spawn_member_run",
                 "firm.pulse.runner.spawn_member_run"):
        monkeypatch.setattr(site, fake_spawn)

    pulse_cli.run_pulse(ws, firm_id=FIRM)

    assert "spawn" in order, (
        "no Member spawn was attempted, so this leg measured nothing about "
        f"order; calls seen: {order}")
    assert "contain" in order, f"containment was never asked for; saw {order}"
    assert order.index("contain") < order.index("spawn"), (
        f"containment must come before the first spawn; saw {order}")


def test_the_pulse_reports_what_containment_answered(tmp_path, monkeypatch,
                                                     capsys):
    """TIER A': the answer reaches the operator, presence-keyed like #141's.

    The KEY is asserted, not only the value. A report that drops the key
    entirely and a report that says `false` are different claims, and a
    value-only assertion passes through a missing key on the default."""
    ws = _firm(tmp_path / "ws")
    monkeypatch.setenv("CADRE_CLAUDE_BIN", _stand_in(tmp_path))
    monkeypatch.setattr(winjob, "contain_this_process",
                        lambda: winjob.Containment(True, "", 0x00002000))

    pulse_cli.run_pulse(ws, firm_id=FIRM)

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "contained" in result, f"the key itself is missing from {result}"
    assert result["contained"] is True, result
    assert "containment_flags" in result, f"no flags key in {result}"


def test_a_containment_that_says_no_is_reported_and_the_pulse_still_runs(
        tmp_path, monkeypatch, capsys):
    """R3: a host that cannot make a job still gets its pulse.

    Refusing to run would turn a locked-down machine into a firm with no
    heartbeat, which is worse than the leak. What it must never do is run while
    REPORTING itself contained."""
    ws = _firm(tmp_path / "ws")
    monkeypatch.setenv("CADRE_CLAUDE_BIN", _stand_in(tmp_path))
    monkeypatch.setattr(
        winjob, "contain_this_process",
        lambda: winjob.Containment(False, "CreateJobObjectW failed, "
                                          "GetLastError=5 (Access is denied)"))

    rc = pulse_cli.run_pulse(ws, firm_id=FIRM)

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["contained"] is False, result
    assert "Access is denied" in result["containment_reason"], result
    assert rc == 0, "a refused job must not fail the pulse"
    assert result["ok"] is True, result


def test_a_containment_that_raises_is_caught_and_never_reaches_the_caller(
        tmp_path, monkeypatch, capsys):
    """R3, the half #146 FINDING 1 measured: one broken kernel call must not
    stop the heartbeat. The pulse must not depend on another module keeping its
    "never raises" promise through every future edit."""
    def exploding():
        raise OSError("QueryInformationJobObject failed, GetLastError=6")

    ws = _firm(tmp_path / "ws")
    monkeypatch.setenv("CADRE_CLAUDE_BIN", _stand_in(tmp_path))
    monkeypatch.setattr(winjob, "contain_this_process", exploding)

    rc = pulse_cli.run_pulse(ws, firm_id=FIRM)

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["contained"] is False, result
    assert "GetLastError=6" in result["containment_reason"], result
    assert rc == 0, "a raising containment must not fail the pulse"


@pytest.mark.skipif(sys.platform == "win32",
                    reason="the point of this leg is the non-Windows answer")
def test_on_a_host_without_job_objects_the_report_says_so_without_noise(
        tmp_path, monkeypatch, capsys):
    """The real primitive, on this real host. `supported: False` is not a
    failure an operator can act on, so it must not read like one: absent is not
    failed, and the two want different things from the reader."""
    ws = _firm(tmp_path / "ws")
    monkeypatch.setenv("CADRE_CLAUDE_BIN", _stand_in(tmp_path))

    rc = pulse_cli.run_pulse(ws, firm_id=FIRM)

    out = capsys.readouterr().out
    result = json.loads(out.strip().splitlines()[-1])
    assert result["contained"] is False, result
    assert result["containment_supported"] is False, result
    assert rc == 0
    assert "WARN" not in out and "warning" not in out.lower(), (
        "a host that simply has no job objects must not warn on every pulse")


# ---------------------------------------------------------------------------
# R1a: the `signalled` flip, driven in process, ON EVERY HOST
# ---------------------------------------------------------------------------

def test_abort_refuses_to_say_ok_when_the_holder_is_still_alive(tmp_path,
                                                                monkeypatch):
    """R1a, TIER A': the report follows the READING, never the signal.

    THE SIGNAL IS FAKED AND BOTH LIVENESS READS ARE REAL. On Windows the real
    `os.kill(pid, SIGTERM)` is `TerminateProcess`, which no process can ignore,
    so a holder that survives its signal cannot be produced there with a real
    signal -- that arm is the live leg's on CI (R1b). Faking the send and
    leaving the reads alone produces the branch on every host.

    The fake passes signal 0 THROUGH to the real `os.kill`. Abort's liveness
    read (`cli/pulse.py:541`) and the cleanup's own copy (`cleanup.py:69`) are
    two separate readers of the same question, and faking either one alone
    measures nothing: they would disagree and the `held-by-a-live-pulse` branch
    would never be entered at all.
    """
    ws = _firm(tmp_path / "ws")
    base = tmp_path / "holder"
    base.mkdir(parents=True, exist_ok=True)
    alive = _Tree(base, depth=1)
    real_kill = os.kill
    sent: list[tuple[int, int]] = []

    def fake_kill(pid, sig, *rest):
        if sig == 0:
            return real_kill(pid, sig, *rest)   # the liveness reads stay REAL
        sent.append((pid, sig))
        return None                             # record, and send nothing

    try:
        _hold_lock(ws, f"{socket.gethostname()}:{alive.holder}:t148r1a")
        monkeypatch.setattr(os, "kill", fake_kill)

        rc = pulse_cli.run_pulse(ws, abort=True, firm_id=FIRM)
        monkeypatch.undo()

        assert sent == [(alive.holder, signal.SIGTERM)], (
            f"abort must signal the holder exactly once with SIGTERM; "
            f"recorded {sent}")
        assert _alive(alive.holder), (
            "the holder died anyway, so this leg never produced the branch")
        assert rc == 1, "an abort that has not finished is not a success"
        assert _lock_holder(ws) is not None, (
            "a lock whose holder is still running must not be cleared")
    finally:
        monkeypatch.undo()
        alive.close()


def test_control_the_liveness_probe_is_still_real_under_the_kill_fake(
        tmp_path, monkeypatch):
    """THE CONTROL THAT MAKES THE LEG ABOVE MEAN ANYTHING.

    Abort's POSIX liveness probe is itself `os.kill(pid, 0)`
    (`cli/pulse.py:560`; the cleanup's copy probes the same way at
    `cleanup.py:103`). A fake that swallowed signal 0 as well would make EVERY
    pid read alive, and the `signalled` leg would pass with nothing behind it.

    So: under the same fake, a pid that is certainly dead must still read
    False. Windows hides this entirely, because `_pid_alive` takes the
    `OpenProcess` branch and never reaches `os.kill`."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=30)                     # reaped: the pid is truly gone
    real_kill = os.kill
    sent: list[tuple[int, int]] = []

    def fake_kill(pid, sig, *rest):
        if sig == 0:
            return real_kill(pid, sig, *rest)
        sent.append((pid, sig))
        return None

    monkeypatch.setattr(os, "kill", fake_kill)
    try:
        assert pulse_cli._pid_alive(dead.pid) is False, (
            "the liveness probe reported a reaped pid as alive under the "
            "fake, so every R1a assertion would be measuring the fake")
        assert pulse_cli._pid_alive(os.getpid()) is True, (
            "the probe cannot see a process that certainly exists")
    finally:
        monkeypatch.undo()
    assert sent == [], "the control must not have sent any real signal"


def test_abort_names_the_live_holder_in_alive_after(tmp_path, monkeypatch,
                                                    capsys):
    """R1a's other half: the words stay honest and only the success bit moves.

    `lock: signalled` and its message are kept verbatim; `ok` flips and
    `alive_after` says which process is still there. Split from the leg above
    so a failure names which half broke."""
    ws = _firm(tmp_path / "ws")
    base = tmp_path / "holder"
    base.mkdir(parents=True, exist_ok=True)
    alive = _Tree(base, depth=1)
    real_kill = os.kill

    def fake_kill(pid, sig, *rest):
        if sig == 0:
            return real_kill(pid, sig, *rest)
        return None

    try:
        _hold_lock(ws, f"{socket.gethostname()}:{alive.holder}:t148r1b")
        monkeypatch.setattr(os, "kill", fake_kill)

        pulse_cli.run_pulse(ws, abort=True, firm_id=FIRM)
        monkeypatch.undo()

        result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert result["lock"] == "signalled", result
        assert result["message"] == ("holder signalled, still exiting; "
                                     "lock left for its own release"), result
        assert result["aborted"] == 1, result
        assert "alive_after" in result, (
            f"abort claimed an outcome without saying what is alive\n{result}")
        assert alive.holder in _pids_in(result["alive_after"]), result
        assert result["ok"] is False, result
    finally:
        monkeypatch.undo()
        alive.close()


# ---------------------------------------------------------------------------
# R5, condition 2: abort snapshots, re-reads, and only then claims
# ---------------------------------------------------------------------------

def test_abort_reports_the_leaf_that_outlived_the_holder_and_refuses_ok(
        tmp_path, tree):
    """THE LEG #148 EXISTS FOR, and the one that is red at `4218ab74`.

    The holder exits on SIGTERM and leaves its tree running -- an uncontained
    pulse, which is every hand-started and hub-started pulse on Windows today.
    Today abort reports `ok: true, lock: cleared` here and finalizes the run:
    the lock is gone, the row says finished, and a process of that run is still
    spending tokens. That is the harm, exactly as filed.

    IT ASSERTS THE LEAF, NOT THE MIDDLE. A walk one level deep finds only the
    middle generation, and a fix that shipped that walk would pass a leg that
    named the middle while the leaf kept running."""
    ws = _firm(tmp_path / "ws")
    _hold_lock(ws, f"{socket.gethostname()}:{tree.holder}:t148leaf")

    rc, result, output = _abort(ws)

    assert _present(tree.leaf, tree.stages["leaf"][1]), (
        "the leaf died on its own, so this run never produced the state the "
        f"leg is about\n{output}")
    assert result is not None, output
    assert "alive_after" in result, (
        f"abort claimed an outcome without reporting what survived\n{output}")
    assert tree.leaf in _pids_in(result["alive_after"]), (
        f"the leaf {tree.leaf} is alive and abort did not name it; a walk that "
        f"stops at the holder's direct children reports only {tree.middle}"
        f"\n{output}")
    assert result["ok"] is False, (
        f"abort said ok:true with a process of that run still alive\n{output}")
    assert rc == 1, output


def test_abort_takes_its_snapshot_while_the_holder_is_still_alive(tmp_path,
                                                                 tree):
    """R5's order property: the snapshot is taken BEFORE the signal.

    Taken afterwards it reads an empty tree, reports nothing survived, and is
    green for the reason it should be red -- the same shape as a verdict
    written before the thing it describes. The leaf is only reachable by
    walking from a live holder, so a snapshot naming it cannot have been taken
    after the holder exited."""
    ws = _firm(tmp_path / "ws")
    _hold_lock(ws, f"{socket.gethostname()}:{tree.holder}:t148order")

    _rc, result, output = _abort(ws)

    assert result is not None, output
    assert "descendants_before" in result, (
        f"abort does not say what it saw before it signalled\n{output}")
    seen = _pids_in(result["descendants_before"])
    assert tree.leaf in seen, (
        f"the snapshot missed the leaf {tree.leaf}; it was either taken after "
        f"the holder died or walked only one level (saw {seen})\n{output}")
    assert tree.middle in seen, (
        f"the snapshot missed the middle generation {tree.middle}\n{output}")


def test_abort_says_ok_only_when_the_holder_is_dead_and_nothing_survived(
        tmp_path, lonely_tree):
    """The other half of the same rule, in the same file: when the tree really
    is gone, abort still says yes. Without this leg, a fix that answered
    `ok: false` unconditionally would pass every leg above.

    It uses a holder with nothing under it rather than one whose children the
    test kills: killing the children of a LIVE holder leaves zombies nobody
    reaps, and `os.kill(pid, 0)` answers for a zombie -- so that version of
    this leg could never have gone green, for a reason with nothing to do with
    the product. Measured: the first draft failed on exactly that.
    """
    ws = _firm(tmp_path / "ws")
    assert _alive(lonely_tree.holder), "the holder died before the test began"
    _hold_lock(ws, f"{socket.gethostname()}:{lonely_tree.holder}:t148clean")

    rc, result, output = _abort(ws)

    assert result is not None, output
    # PRESENT AND EMPTY, not merely empty. Absent is the BEFORE state: the key
    # does not exist at `4218ab74` at all, so a leg asserting only emptiness
    # would have to be written as `result.get("alive_after", []) == []` and
    # would pass at the base. The two claims are different: empty says abort
    # looked and found nothing; absent says abort did not look.
    assert "alive_after" in result, (
        f"the key is missing, so abort never looked\n{output}")
    assert result["alive_after"] == [], output
    assert result["ok"] is True, output
    assert rc == 0, output


#: A holder that starts a child, lets it exit at once, and never waits on it.
#: The child becomes a ZOMBIE under a live holder: it holds no memory, runs no
#: code and spends nothing, but `os.kill(pid, 0)` still answers for it.
_ZOMBIE_HOLDER = textwrap.dedent("""\
    import os, signal, subprocess, sys, time
    if os.fork() != 0:
        os._exit(0)
    os.setsid()
    kid = subprocess.Popen([sys.executable, "-c", "pass"])
    time.sleep(1.0)                      # let it die; never wait() on it
    with open(sys.argv[1], "a") as fh:
        fh.write("holder %d\\nzombie %d\\n" % (os.getpid(), kid.pid))
        fh.flush()
        os.fsync(fh.fileno())
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    time.sleep(300)
    """)


@posix_only
def test_a_zombie_child_is_not_a_survivor(tmp_path, monkeypatch, capsys):
    """R5a: a process in state `Z` is dead, and abort must not name it.

    A zombie is an exit status nobody has collected. It holds no tokens and
    cannot be a running Member, so naming it in `alive_after` would make
    `ok: false` permanent for any holder that has not reaped a child -- an
    abort that can never succeed on a perfectly healthy firm.

    This is the leg that made the rule: abort's `_pid_alive` probes with
    `os.kill(pid, 0)`, which succeeds for a zombie (measured on this host,
    state `Z`). The holder is REAL and the signal is faked, R1a's shape, so the
    `signalled` branch is entered on every host that has zombies at all.
    """
    record = tmp_path / "zomb.txt"
    record.write_text("", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-c", _ZOMBIE_HOLDER,
                             str(record)])
    proc.wait(timeout=20)
    holder = zombie = None
    deadline = time.time() + 30
    while time.time() < deadline:
        rows = [ln.split() for ln in
                record.read_text(encoding="utf-8").splitlines() if ln]
        if len(rows) >= 2:
            holder, zombie = int(rows[0][1]), int(rows[1][1])
            break
        time.sleep(0.05)
    assert holder and zombie, f"the zombie holder never reported: {rows!r}"

    ws = _firm(tmp_path / "ws")
    real_kill = os.kill

    def fake_kill(pid, sig, *rest):
        if sig == 0:
            return real_kill(pid, sig, *rest)
        return None

    try:
        assert _proc_state(zombie) == "Z", (
            f"the child is in state {_proc_state(zombie)!r}, not Z, so this "
            f"leg is not measuring a zombie at all")
        assert _alive(zombie), (
            "os.kill(pid, 0) no longer answers for a zombie on this host, so "
            "the rule this leg pins has no subject")
        _hold_lock(ws, f"{socket.gethostname()}:{holder}:t148zomb")
        monkeypatch.setattr(os, "kill", fake_kill)

        pulse_cli.run_pulse(ws, abort=True, firm_id=FIRM)
        monkeypatch.undo()

        result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert "alive_after" in result, result
        named = _pids_in(result["alive_after"])
        assert zombie not in named, (
            f"abort named the zombie {zombie} as a survivor; it holds nothing "
            f"and an abort could never succeed under it\n{result}")
        assert named == [holder], (
            f"alive_after must name the holder and nothing else; got {named}"
            f"\n{result}")
    finally:
        monkeypatch.undo()
        for pid in (zombie, holder):
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass




def test_the_descendant_walker_names_no_permission_call_in_its_source():
    """R5b's STATIC half, and it runs on every platform (F11).

    The dynamic leg below fakes `os.kill` and `OpenProcess` and watches whether
    the read touches them. A mutant that took a FRESH `ctypes.WinDLL` handle
    would walk straight past both fakes and pass it -- the object it called
    would not be the object under the fake. So this reads the walker's source
    instead, law-12 shape: the forbidden names ABSENT, each paired with a
    positive sibling that must be PRESENT in the same run, so a reader that has
    gone blind cannot report an absence it never measured.
    """
    source = Path(descendants.__file__).read_text(encoding="utf-8")
    body = source[source.index("def _posix_table"):]

    # The positive siblings first: if these are not here, the absences below
    # are measuring an empty string.
    assert "/proc" in body, "the POSIX branch is gone; absence proves nothing"
    assert "Win32_Process" in body, (
        "the Windows branch is gone; absence proves nothing")
    assert "ToUniversalTime" in body, (
        "the Windows stamp has drifted back to a LOCAL tick count (R5c); two "
        "reads either side of a daylight saving change would disagree for the "
        "same process, and a live survivor would read as gone")

    assert "OpenProcess" not in body, (
        "the walker opens a handle to decide liveness; R5b forbids it, and it "
        "would inherit the EPERM branch the two _pid_alive copies disagree on")
    assert "os.kill" not in body, (
        "the walker signals to decide liveness; on Windows os.kill is "
        "TerminateProcess and the read would kill what it asked about")


def test_the_descendant_read_never_asks_permission_to_signal(tmp_path, tree,
                                                             monkeypatch,
                                                             capsys):
    """R5b, TIER A: the descendant read decides by the process table, never by
    `os.kill` or `OpenProcess`.

    Asking "may I signal this?" answers a different question from "is this
    running?", and the product already shows the cost: its two `_pid_alive`
    copies disagree on EPERM (`cli/pulse.py:563` True, `cleanup.py:104` False),
    so a descendant owned by another user would read alive to one and dead to
    the other. Presence in the process table with the same creation time has no
    such branch.

    HOW THE FAKE DISCRIMINATES, and why it has to. The holder's OWN liveness is
    legitimately `os.kill(pid, 0)` and #148 does not change it, so the fake
    lets the holder's pid through and RAISES for every other pid. Any call the
    descendant read makes therefore fails loudly and names the pid, instead of
    passing quietly and leaving the leg green for the wrong reason.

    The Windows arm of this leg belongs to the live leg on CI, where a Windows
    process tree exists; this file's tree is built with `fork`.
    """
    ws = _firm(tmp_path / "ws")
    _hold_lock(ws, f"{socket.gethostname()}:{tree.holder}:t148r5b")
    real_kill = os.kill
    holder = tree.holder
    trespass: list[int] = []

    def fake_kill(pid, sig, *rest):
        if pid == holder:
            if sig == 0:
                return real_kill(pid, sig, *rest)   # the holder's own read
            return None                             # signal recorded, not sent
        trespass.append(pid)
        raise AssertionError(
            f"the descendant read called os.kill on {pid}; R5b forbids it")

    monkeypatch.setattr(os, "kill", fake_kill)
    if os.name != "posix":
        # ON WINDOWS `os.kill` IS NOT THE PROBE (F11). `_pid_alive` takes the
        # `OpenProcess` branch there, off the CACHED `ctypes.windll.kernel32`
        # object -- so that is the object the fake has to replace. It raises
        # for any pid but the holder, exactly as the `os.kill` fake does.
        #
        # NOT COVERED, and said here rather than left to be found: the
        # cleanup's copy builds a fresh `WinDLL`, which this fake cannot reach.
        # It probes the holder only, so it cannot reach a descendant anyway;
        # the static leg above is what covers a mutant that takes a fresh
        # handle to walk the tree.
        import ctypes

        real_open = ctypes.windll.kernel32.OpenProcess

        def fake_open(access, inherit, pid):
            if pid == holder:
                return real_open(access, inherit, pid)
            trespass.append(pid)
            raise AssertionError(
                f"the descendant read called OpenProcess on {pid}; "
                f"R5b forbids it")

        monkeypatch.setattr(ctypes.windll.kernel32, "OpenProcess", fake_open)
    try:
        pulse_cli.run_pulse(ws, abort=True, firm_id=FIRM)
    finally:
        monkeypatch.undo()

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert not trespass, (
        f"the read asked os.kill about {trespass}, which is the reader R5b "
        f"forbids")
    seen = _pids_in(result.get("descendants_before"))
    assert tree.middle in seen and tree.leaf in seen, (
        f"the read must still list every generation without os.kill; "
        f"saw {seen}, expected middle {tree.middle} and leaf {tree.leaf}"
        f"\n{result}")


def test_alive_after_is_present_whenever_a_holder_was_signalled(tmp_path, tree):
    """The KEY, asserted by presence. `alive_after: []` and no key at all are
    different claims: the first says abort looked and found nothing, the second
    says abort did not look. Only the first can carry R5."""
    ws = _firm(tmp_path / "ws")
    _hold_lock(ws, f"{socket.gethostname()}:{tree.holder}:t148key")

    _rc, result, output = _abort(ws)

    assert result is not None, output
    assert result.get("aborted") == 1, f"no holder was signalled\n{output}"
    assert "alive_after" in result, (
        f"a holder was signalled and abort did not say what survived\n{output}")


def test_an_abort_never_carries_a_containment_record_it_did_not_take(
        tmp_path, monkeypatch, capsys):
    """G1-2: the containment answer belongs to a pulse, not to an abort.

    `_CONTAINMENT` is a module global that `_exit_with` merges onto every
    result, and `run_pulse` clears it on entry -- but abort returns BEFORE the
    record is taken, so without a clear of its own an abort inherits whatever
    the last pulse in this process left behind. It is not hypothetical:
    `tests/test_pulse_cleanup.py` calls `_handle_abort` directly at :361,
    :394, :496 and :597, in processes where other legs have run pulses.

    So this drives exactly that order in one process: a real pulse first, then
    a direct `_handle_abort`, and none of the four keys may survive the trip.
    """
    ws = _firm(tmp_path / "ws")
    monkeypatch.setenv("CADRE_CLAUDE_BIN", _stand_in(tmp_path))
    monkeypatch.setattr(winjob, "contain_this_process",
                        lambda: winjob.Containment(True, "", 0x00002000))

    pulse_cli.run_pulse(ws, firm_id=FIRM)
    pulsed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    # The positive sibling, in the same run: the keys ARE there for a pulse,
    # so their absence below is a measurement and not a blind reader.
    assert pulsed["contained"] is True, pulsed

    pulse_cli._handle_abort(ws, FIRM)
    aborted = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    for key in ("contained", "containment_reason", "containment_flags",
                "containment_supported"):
        assert key not in aborted, (
            f"the abort result carries {key!r}, which it never asked for: "
            f"{aborted}")


def test_the_holder_is_re_read_as_a_generation_not_as_a_pid(tmp_path,
                                                            monkeypatch,
                                                            capsys):
    """G1-3: a pid reused between the signal and the re-read is a stranger.

    The walker's own first rule is generation, not pid, and the holder is not
    an exception to it. Read back by pid alone, a pid handed to a new process
    in that window reads as the holder still being alive and abort flips `ok`
    false over something that has nothing to do with this run.

    TIER A, driven by moving the table under the re-read: the same pid comes
    back with a DIFFERENT creation time, which is exactly what a reuse looks
    like. The holder is real and the signal is faked, R1a's shape, so the
    branch is entered on every host.
    """
    ws = _firm(tmp_path / "ws")
    base = tmp_path / "holder"
    base.mkdir(parents=True, exist_ok=True)
    alive = _Tree(base, depth=1)
    real_kill = os.kill
    real_table = descendants.process_table

    signalled = {"yet": False}
    calls = {"n": 0}

    def fake_kill(pid, sig, *rest):
        if sig == 0:
            return real_kill(pid, sig, *rest)
        signalled["yet"] = True
        return None

    def drifting_table():
        """Before the signal, the truth; after it, a REUSED pid.

        THE DRIFT IS TIED TO THE SIGNAL, NOT TO A CALL COUNT, and that is a
        fix rather than a preference. The first draft drifted after the first
        read and measured nothing: abort reads the table TWICE before it
        signals -- once for the descendants, once for the holder's own
        generation -- so the pre-signal generation was already the drifted
        one, matched itself on the re-read, and the leg failed while the
        product was right. A pid is reused after a process dies, which is
        after the signal; modelling it any other way models something that
        cannot happen.
        """
        table = dict(real_table())
        calls["n"] += 1
        if signalled["yet"] and alive.holder in table:
            ppid, created, state = table[alive.holder]
            table[alive.holder] = (ppid, created + "9999", state)
        return table

    try:
        _hold_lock(ws, f"{socket.gethostname()}:{alive.holder}:t148gen")
        monkeypatch.setattr(os, "kill", fake_kill)
        monkeypatch.setattr(descendants, "process_table", drifting_table)

        pulse_cli.run_pulse(ws, abort=True, firm_id=FIRM)
        monkeypatch.undo()

        result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert calls["n"] > 1, (
            "the table was read once, so the re-read this leg is about never "
            "happened and nothing was measured")
        assert _pids_in(result.get("alive_after")) == [], (
            f"the holder was re-read by pid alone: its creation time changed, "
            f"so it is a different process and must read as gone\n{result}")
        # AND THE LOCK WAS LEFT HELD, SO THIS IS NOT A SUCCESS (G2-1). The
        # reading is honestly empty -- the stranger holding that pid is not
        # this run's process and naming it would be worse than saying nothing
        # -- but `lock: signalled` means abort could not release the lock, and
        # an earlier draft of this leg asserted `ok True` here, which pinned
        # exactly the contradiction #148 was filed about: a success printed
        # beside "still exiting", with the lock wedged behind it.
        assert result["lock"] == "signalled", result
        assert result["ok"] is False, result
    finally:
        monkeypatch.undo()
        alive.close()


#: .NET ticks at the Windows FILETIME epoch (1601-01-01 UTC), i.e. the number
#: of 100 ns units from 0001-01-01 to 1601-01-01. Adding it converts a
#: FILETIME to the same scale `CreationDate.Ticks` uses.
_FILETIME_EPOCH_IN_DOTNET_TICKS = 504911232000000000


@pytest.mark.skipif(os.name == "posix",
                    reason="reads a Windows FILETIME through GetProcessTimes; "
                           "the POSIX stamp is a boot-relative tick count and "
                           "has no second channel to check it against")
def test_the_windows_created_stamp_is_a_utc_count(capsys):
    """R5c, TIER B and DRIVABLE: the walker's stamp against another channel.

    `CreationDate` converts with `Kind` Local, so `.Ticks` would be a LOCAL
    count -- not stable across a daylight saving change, and two reads either
    side of one give different strings for the same process. `still_alive`
    reads that as the process being gone, so `alive_after` comes back empty
    over a live survivor: the dangerous direction.

    The independent channel is `GetProcessTimes`, whose creation time is a
    FILETIME in 100 ns units since 1601-01-01 UTC. Adding
    `_FILETIME_EPOCH_IN_DOTNET_TICKS` puts it on the same scale as
    `CreationDate.Ticks`. The two must agree within **[-9, 0] ticks**: CIM
    truncates to microseconds and always downward, so the walker's value is
    never larger and never more than 9 ticks smaller (avocet measured -9, -5
    and -1 on three processes).

    A drift back to local ticks reddens this by about five hours of ticks. The
    static leg's `ToUniversalTime` presence is the second channel, so the
    regression is caught by two different kinds of evidence.
    """
    import ctypes
    from ctypes import wintypes

    me = os.getpid()
    walked = descendants.generation_of(me)
    assert walked is not None, "the walker cannot see this very process"

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.GetCurrentProcess.argtypes = []
    k32.GetProcessTimes.restype = wintypes.BOOL
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
        ctypes.POINTER(wintypes.FILETIME)] * 4
    made, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
    assert k32.GetProcessTimes(k32.GetCurrentProcess(), ctypes.byref(made),
                               ctypes.byref(exited), ctypes.byref(kernel),
                               ctypes.byref(user)), ctypes.get_last_error()
    filetime = (made.dwHighDateTime << 32) | made.dwLowDateTime
    independent = filetime + _FILETIME_EPOCH_IN_DOTNET_TICKS
    difference = int(walked[1]) - independent

    with capsys.disabled():
        print(f"[148] walker stamp {walked[1]} vs GetProcessTimes "
              f"{independent}, difference {difference} ticks")

    assert -9 <= difference <= 0, (
        f"the walker's stamp is {difference} ticks from an independent UTC "
        f"reading of the same process. A local count differs by whole hours; "
        f"CIM's microsecond truncation differs by at most 9 ticks, downward.")
