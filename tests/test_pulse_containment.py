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
import time
from pathlib import Path

import pytest

import firm.cli.pulse as pulse_cli
from firm.core.db import connect
from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.pulse import dblock
from firm.sched import winjob

FIRM = "containco"

#: The real-signal arms need a holder that exits on SIGTERM leaving children,
#: and a process tree readable through `/proc`. On Windows `os.kill(pid,
#: SIGTERM)` is `TerminateProcess`, so those arms are the live leg's, on CI
#: (R1b). The R1a leg below deliberately does NOT carry this mark: it fakes the
#: signal and therefore runs everywhere.
posix_only = pytest.mark.skipif(
    os.name != "posix",
    reason=("drives a real holder tree and a SIGTERM the holder handles; "
            "Windows has neither shape here, and its arm is the live leg on "
            "CI's Windows job (R1b)"))


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
    path = tmp_path / "stand-in-member"
    path.write_text(
        '#!/bin/sh\n'
        'printf \'%s\\n\' \'{"type":"result","subtype":"success",'
        '"is_error":false,"usage":{"input_tokens":1,"output_tokens":1}}\'\n',
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
_STAGE = textwrap.dedent("""\
    import os, signal, subprocess, sys, time

    label, out, nxt = sys.argv[1], sys.argv[2], sys.argv[3]

    if label == "holder":
        if os.fork() != 0:
            os._exit(0)
        os.setsid()

    kid = None
    if nxt != "none":
        kid = subprocess.Popen([sys.executable, "-c", open(sys.argv[4]).read(),
                                nxt, out,
                                "leaf" if nxt == "middle" else "none",
                                sys.argv[4]])

    with open(out, "a") as fh:
        stat = open("/proc/%d/stat" % os.getpid()).read()
        started = stat[stat.rindex(")") + 1:].split()[19]
        fh.write("%s %d %s\\n" % (label, os.getpid(), started))
        fh.flush()
        os.fsync(fh.fileno())

    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    time.sleep(300)
    """)


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
    """Is this pid a live process?

    Refuses 0 and None outright. `os.kill(0, 0)` asks about the caller's whole
    PROCESS GROUP, not about a process, so it answers True for a pid that does
    not exist -- a liveness reader that cannot say "no" is worse than none.
    """
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _ppid(pid: int) -> int | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return int(raw[raw.rindex(")") + 1:].split()[1])


def _proc_state(pid: int) -> str | None:
    """The third field of `/proc/<pid>/stat`: `Z` for a zombie, `S` sleeping."""
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
    """
    steps = 0
    cur = pid
    while steps < limit:
        parent = _ppid(cur)
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
            [sys.executable, "-c", _STAGE, "holder", str(self.record), nxt,
             str(script)])
        self.proc.wait(timeout=20)     # the double-fork parent, already gone
        self.stages = self._await(depth)
        self.holder = self.stages["holder"][0]
        self.middle = self.stages.get("middle", (None, None))[0]
        self.leaf = self.stages.get("leaf", (None, None))[0]

    def _await(self, depth: int) -> dict[str, tuple[int, str]]:
        deadline = time.time() + 30
        while time.time() < deadline:
            lines = [ln.split() for ln in
                     self.record.read_text(encoding="utf-8").splitlines() if ln]
            if len(lines) >= depth:
                return {p[0]: (int(p[1]), p[2]) for p in lines}
            time.sleep(0.05)
        raise AssertionError(
            f"the tree never reached {depth} generations; recorded: "
            f"{self.record.read_text(encoding='utf-8')!r}")

    def close(self) -> None:
        for pid in (self.leaf, self.middle, self.holder):
            if not pid:                 # 0/None would mean the process GROUP
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

@posix_only
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


@posix_only
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


@posix_only
def test_control_the_generation_reader_returns_nothing_for_a_dead_pid(tree):
    """The must-fail half of the reader. A reader that answers for a process
    that does not exist would report every survivor as present and every
    absence as proven."""
    leaf = tree.leaf
    tree.close()
    for _ in range(60):
        if not _alive(leaf):
            break
        time.sleep(0.05)
    assert _generation(leaf) is None, "the reader answered for a dead pid"


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

@posix_only
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


@posix_only
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


@posix_only
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


@posix_only
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


@posix_only
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
