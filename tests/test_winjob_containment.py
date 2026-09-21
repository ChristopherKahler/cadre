"""Is the pulse tree CONTAINED, and does anyone find out when it is not? Issue #141.

`schtasks /End` ends the launcher only. The command it started, that command's
console and its python were all measured still running five seconds later
(#119 fork doc, M-W2 arm A4b-1, three survivors). So "stop the heartbeat" stopped
the supervisor and left the pulse running, which is the defect #141 closes.

THE DESIGN, as approved with conditions: before the command starts,
`winlaunch.main` puts the launcher's own process into a job carrying
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and holds the handle for the launcher's
life. Every child starts inside the job and inherits it, so the launcher's exit
ends the tree.

WHAT THESE LEGS CAN AND CANNOT SEE. A unit leg cannot see Task Scheduler, and it
must not put the test runner itself into a kill-on-close job -- that would end
pytest's own children when the handle closed. So the legs here pin the CONTRACT:
that containment is asked for before the command runs, that its outcome is
recorded where an operator can read it, and that a failure to contain is
reported rather than swallowed. The mechanism itself is measured elsewhere, by
arms that use real processes:

  J3 synthetic  PASSED. A background child survives a launcher exit without the
                job and is gone with it, control first, sentinel 0, sweep clean.
  J2            a real task, `/Run` then `/End`, survivors counted. Owed.

CONDITION C3 IS WHY THE RECORD EXISTS. "The log line is the record" is not
enough on its own: a machine where assignment fails silently goes back to
today's behaviour with nothing to show for it. So the outcome is written beside
the launcher and `status()` carries it, and an operator asking about the
heartbeat is told the tree is not contained and why.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

STEM = "cadre-heartbeat-lab"


@pytest.fixture
def winlaunch():
    """Imported per test, so a tree without the module fails each arm alone."""
    from firm.sched import winlaunch as module
    return module


@pytest.fixture
def winjob():
    from firm.sched import winjob as module
    return module


class _Log:
    """A log that records what was written to it, standing in for the stub's file."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, text: str) -> int:
        self.lines.append(text)
        return len(text)

    def flush(self) -> None:
        return None

    def seek(self, *_a) -> int:
        return 0

    def truncate(self, *_a) -> int:
        return 0

    @property
    def text(self) -> str:
        return "".join(self.lines)


def _spec(winlaunch, tmp_path, **overrides):
    fields = dict(argv=["pulse", "--firm-id", "lab"], env={"FIRM_ID": "lab"},
                  cwd=tmp_path, supervise=False, interval="30m")
    fields.update(overrides)
    winlaunch.write_launcher(tmp_path / "sched", STEM, **fields)
    return tmp_path / "sched" / f"{STEM}.json"


class _Recorder:
    """Records the ORDER of what happened, which is the whole of leg K1."""

    def __init__(self, containment):
        self.events: list[str] = []
        self._containment = containment

    def contain(self):
        self.events.append("contain")
        return self._containment

    def run(self, argv, **kwargs):
        self.events.append("run")

        class _Done:
            returncode = 0
        return _Done()


# ---------------------------------------------------------------------------
# K1 -- containment is asked for BEFORE the command starts
# ---------------------------------------------------------------------------

def test_k1_the_launcher_is_contained_before_its_command_starts(
        winlaunch, winjob, tmp_path):
    """Order, not presence. A job entered after the command is not containment.

    A child started before the assignment is NOT in the job, and closing the
    handle later does not reach it. So "the launcher creates a job" is not the
    property -- "the launcher is already in the job when the command starts" is,
    and only the order can tell those two apart.
    """
    spec = _spec(winlaunch, tmp_path)
    rec = _Recorder(winjob.Containment(contained=True, reason="", limit_flags=0x2000))

    winlaunch.main(spec, _Log(), run=rec.run, contain=rec.contain)

    assert rec.events == ["contain", "run"], (
        "the launcher did %r. A command started before the job exists is "
        "outside it, and the launcher's exit would not reach it -- which is "
        "today's behaviour with extra machinery." % (rec.events,))


# ---------------------------------------------------------------------------
# K2, K3 -- the outcome is recorded, either way (condition C3)
# ---------------------------------------------------------------------------

def test_k2_a_contained_launcher_records_that_it_is_contained(
        winlaunch, winjob, tmp_path):
    """The record carries the limit flags, which C2 requires the build to state."""
    spec = _spec(winlaunch, tmp_path)
    rec = _Recorder(winjob.Containment(contained=True, reason="", limit_flags=0x2000))

    winlaunch.main(spec, _Log(), run=rec.run, contain=rec.contain)

    written = tmp_path / "sched" / f"{STEM}.containment.json"
    assert written.is_file(), (
        "nothing recorded whether the pulse tree is contained, so `heartbeat "
        "status` has nothing to report and a silent failure to contain is "
        "indistinguishable from success (condition C3)")
    got = json.loads(written.read_text(encoding="utf-8"))
    assert got["contained"] is True, got
    assert got["limit_flags"] == "0x00002000", (
        "the record does not carry the job's limit flags: %r. C2 requires the "
        "build record to state them, and a record taken from the job itself is "
        "the only one that cannot drift from it." % (got.get("limit_flags"),))
    assert got.get("pid"), "the record does not say which process it is about"


def test_k3_a_launcher_that_cannot_be_contained_says_so_and_still_runs(
        winlaunch, winjob, tmp_path):
    """A job that cannot be created is reported, and the command runs anyway.

    Both halves matter and they pull in opposite directions. Refusing to run
    would turn a Windows build that cannot make a job into a firm with no
    pulse at all, which is worse than today. Running WITHOUT SAYING SO would be
    today's behaviour dressed up as a fix, which is law 7.
    """
    spec = _spec(winlaunch, tmp_path)
    why = "CreateJobObjectW failed, GetLastError=5 (Access is denied.)"
    rec = _Recorder(winjob.Containment(contained=False, reason=why, limit_flags=None))
    log = _Log()

    code = winlaunch.main(spec, log, run=rec.run, contain=rec.contain)

    assert rec.events == ["contain", "run"], (
        "the command did not run after containment failed (%r); a host that "
        "cannot make a job must still get its pulse" % (rec.events,))
    assert code == 0
    got = json.loads((tmp_path / "sched" / f"{STEM}.containment.json")
                     .read_text(encoding="utf-8"))
    assert got["contained"] is False
    assert got["reason"] == why, (
        "the recorded reason is %r, not the one containment gave. An operator "
        "reading 'not contained' with no reason cannot act on it." % got["reason"])
    assert why in log.text, (
        "the launcher log does not name the containment failure, so the one "
        "place a Windows task's own output lands says nothing about it")


# ---------------------------------------------------------------------------
# K4 -- an operator asking about the heartbeat is told
# ---------------------------------------------------------------------------

def test_k4_status_reports_a_tree_that_is_not_contained_and_why(tmp_path):
    """`status()` carries containment, so condition C3 has somewhere to land."""
    from firm.sched.winsched import WindowsScheduler

    sched = WindowsScheduler(launcher_dir=tmp_path / "sched")
    (tmp_path / "sched").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sched" / f"{STEM}.containment.json").write_text(json.dumps({
        "contained": False,
        "reason": "AssignProcessToJobObject failed, GetLastError=5",
        "limit_flags": None, "pid": 4242, "at": "2026-09-21T17:00:00",
    }), encoding="utf-8")

    out = sched.status(STEM)

    assert out.get("contained") is False, (
        "status() says nothing about containment (%r), so an operator whose "
        "pulses are not contained has no way to find out" % out.get("contained"))
    assert "GetLastError=5" in (out.get("containment_reason") or ""), (
        "status() reports the tree is not contained but not why: %r"
        % out.get("containment_reason"))


def test_k4_status_without_a_containment_record_says_unknown_not_contained(
        tmp_path):
    """Absent is not False. A task installed before this shipped has no record.

    Reporting "not contained" for a task whose launcher never wrote a record
    would be a reading taken from a file that does not exist, and an operator
    would chase a failure that never happened. Absent, empty and zero are three
    different states.
    """
    from firm.sched.winsched import WindowsScheduler

    sched = WindowsScheduler(launcher_dir=tmp_path / "sched")
    (tmp_path / "sched").mkdir(parents=True, exist_ok=True)

    out = sched.status(STEM)

    assert out.get("contained") is None, (
        "status() reported containment %r for a task that has never written a "
        "containment record" % out.get("contained"))


# ---------------------------------------------------------------------------
# K5 -- the job the product makes carries the flags C2 names. Windows only.
# ---------------------------------------------------------------------------

def test_k5_the_product_job_is_kill_on_close_with_neither_breakaway_flag(winjob):
    """Read the flags back off the job itself, never from the constant passed in.

    Condition C2: the job must not set `JOB_OBJECT_LIMIT_BREAKAWAY_OK` or
    `JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK`, because a child asking to break out
    of a job that forbids it FAILS TO START -- and inside a Member run that is a
    tool that stopped working. The build record states all three, and this is
    where the statement comes from.

    NOTHING IS ASSIGNED HERE. Putting the pytest process into a kill-on-close
    job would end its own children when the handle closed, on a CI runner as
    well as on the operator's machine. Assignment is measured by J3, which uses
    a real child and passed.
    """
    if sys.platform != "win32":
        pytest.skip(
            "job objects are a Windows kernel mechanism; on this host there is "
            "nothing to create and the arm would assert against a stub")

    handle = winjob.create_kill_on_close_job()
    try:
        flags = winjob.job_limit_flags(handle)
    finally:
        winjob.close(handle)

    assert flags & winjob.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, (
        "the job's LimitFlags read back 0x%08x with KILL_ON_JOB_CLOSE unset, so "
        "closing the handle would end nothing" % flags)
    assert not flags & winjob.JOB_OBJECT_LIMIT_BREAKAWAY_OK, (
        "BREAKAWAY_OK is set (0x%08x); a child could leave the job and survive "
        "the launcher, which is the defect" % flags)
    assert not flags & winjob.JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK, (
        "SILENT_BREAKAWAY_OK is set (0x%08x); a child would leave the job "
        "without even failing, so nothing would notice" % flags)


def test_k5_containment_off_windows_is_reported_not_pretended(winjob):
    """On a host with no job objects, containment is False with a reason.

    Never True. A launcher that reported itself contained on a platform where
    nothing contains it would put the claim in `status()` and the operator would
    believe it. The pulse still runs there -- this is a Windows scheduler and
    the POSIX one has its own mechanism -- but the answer is honest.
    """
    if sys.platform == "win32":
        pytest.skip("this host HAS job objects; the arm measures the other case")

    got = winjob.contain_this_process()

    assert got.contained is False
    assert got.reason, "containment failed with no reason given"
    assert got.limit_flags is None
