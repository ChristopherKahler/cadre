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

def _task_looks_installed(monkeypatch):
    """schtasks answers as it does for a task that exists and has never run.

    Needed because `status()` stops at `installed: False` when schtasks does not
    know the task, and it is right to: containment is not a fact about a task
    that does not exist. The first draft of the K4 leg asked about a task
    nobody had installed, which is why it stayed red after the fix -- the
    leg's INPUT was wrong, not its assertion, and the assertion below is
    unchanged from the red commit.
    """
    def _fake(cmd, timeout=None):
        return 0, ("TaskName: \\Cadre\\%s\n"
                   "Status: Ready\n"
                   "Last Result: 267011\n" % STEM)

    monkeypatch.setattr("firm.sched.winsched.run_cmd", _fake)


def test_k4_status_reports_a_tree_that_is_not_contained_and_why(
        tmp_path, monkeypatch):
    """`status()` carries containment, so condition C3 has somewhere to land."""
    from firm.sched.winsched import WindowsScheduler

    _task_looks_installed(monkeypatch)
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
        tmp_path, monkeypatch):
    """Absent is not False. A task installed before this shipped has no record.

    Reporting "not contained" for a task whose launcher never wrote a record
    would be a reading taken from a file that does not exist, and an operator
    would chase a failure that never happened. Absent, empty and zero are three
    different states.
    """
    from firm.sched.winsched import WindowsScheduler

    _task_looks_installed(monkeypatch)
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


def test_k6_a_host_with_no_job_objects_is_absent_not_failed(winjob, winlaunch,
                                                            tmp_path):
    """NEW IN THE FIX COMMIT, and it is here because the fix needed it.

    The first version of this change printed "the pulse tree is NOT contained"
    into every launcher log on a host with no job objects, and two existing legs
    caught it: they assert a launcher's log holds exactly its command's bytes,
    and they were right to. Absent is not failed. A Windows machine whose job
    could not be made has a problem an operator can act on; a host whose kernel
    has no job objects has nothing to fix and is not running this scheduler in
    the first place.

    So `Containment.supported` is the third state, and the log line fires only
    where the mechanism exists. The RECORD still says not contained, with the
    reason, everywhere -- condition C3 is about the record, and it is unchanged.
    """
    spec = _spec(winlaunch, tmp_path)
    unsupported = winjob.Containment(
        contained=False, reason="no job objects on this host", supported=False)
    rec = _Recorder(unsupported)
    log = _Log()

    winlaunch.main(spec, log, run=rec.run, contain=rec.contain)

    assert log.text == "", (
        "the launcher wrote %r into the log on a host that has no job objects "
        "to begin with; a log an operator opens should hold the command's "
        "output, not a standing complaint about a kernel feature this host "
        "never had" % log.text)
    got = json.loads((tmp_path / "sched" / f"{STEM}.containment.json")
                     .read_text(encoding="utf-8"))
    assert got["contained"] is False, (
        "an unsupported host reported itself contained, which is a claim "
        "nothing backs")
    assert got["supported"] is False, (
        "the record does not distinguish a host without the mechanism from one "
        "where it failed, so an operator cannot tell which they have")
    assert got["reason"], "not contained, with no reason given"


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


def test_k7_a_record_that_cannot_be_written_does_not_kill_the_pulse(
        winlaunch, winjob, tmp_path, monkeypatch):
    """The command still runs when the containment record cannot be written.

    Found by reading, not by a failure, and it is the same rule the rest of
    this file already follows: a host that cannot contain still gets its pulse.
    A record whose whole job is to REPORT a degraded state must never be able
    to cause one -- and an unguarded write in the launcher does exactly that,
    because the stub turns any exception out of `main` into exit 3 with the
    command never started.

    The failure is still named in the log. Silence here would leave an operator
    with a pulse that runs and a `status()` that says nothing about
    containment, with no way to find out why.
    """
    spec = _spec(winlaunch, tmp_path)
    rec = _Recorder(winjob.Containment(contained=True, reason="", limit_flags=0x2000))
    log = _Log()

    def _explode(directory, stem, held):
        raise OSError(13, "Permission denied", str(directory))

    monkeypatch.setattr(winlaunch, "record_containment", _explode)

    code = winlaunch.main(spec, log, run=rec.run, contain=rec.contain)

    assert rec.events == ["contain", "run"], (
        "the command did not run (%r): a launcher that cannot write a note "
        "about itself killed the pulse it exists to run" % (rec.events,))
    assert code == 0
    assert "Permission denied" in log.text, (
        "the log does not say the containment record could not be written, so "
        "an operator sees `status()` silent about containment with no reason "
        "anywhere: %r" % log.text)


# ---------------------------------------------------------------------------
# K8 -- reading the job's flags can never stop the pulse (avocet's FINDING 1)
# ---------------------------------------------------------------------------
#
# K7 guarded the containment RECORD so a launcher directory that cannot be
# written does not stop the heartbeat. The call ONE LINE ABOVE it was left
# bare, and `contain_this_process` promises "Never raises" while calling
# `job_limit_flags` unguarded twice -- once for a job it already holds, once
# for the job it has just made. `job_limit_flags` raises `OSError` the moment
# `QueryInformationJobObject` fails.
#
# avocet measured the consequence on the real stub: with that query made to
# fail, the launcher exits 3 with THE COMMAND NEVER STARTED, traceback at
# `winjob.py:198`; the unmutated control exits 0. So the mechanism that exists
# to stop a pulse outliving its task would instead stop the pulse from ever
# running -- and it would do it while reporting nothing, because the reason
# lives in a traceback the stub writes to a log that the next supervised run
# truncates.
#
# THE RULE, and it is the same one K7 follows: a host that cannot contain still
# gets its pulse. Containment is a property the launcher reports, never a
# precondition it enforces. These arms hold that rule at all three sites.
#
# NOTHING HERE ASSIGNS THE TEST RUNNER TO A JOB. That is this file's standing
# refusal -- pytest in a kill-on-close job ends its own children when the
# handle closes -- so the flag read is exercised through the one-line helper
# that both call sites go through, which needs no job and no platform.


def test_k8_a_flag_read_that_fails_answers_with_none_and_a_reason(winjob,
                                                                  monkeypatch):
    """The helper both call sites go through. It cannot raise.

    Asserted on the helper rather than only through `contain_this_process`
    because the two call sites are what the defect was: one of them raising is
    enough, and a test that only drives the easy path would have passed over
    the one avocet measured.
    """
    def _boom(handle):
        raise OSError("QueryInformationJobObject failed, GetLastError=5")

    monkeypatch.setattr(winjob, "job_limit_flags", _boom)

    flags, reason = winjob._flags_or_none(object())

    assert flags is None, flags
    assert "QueryInformationJobObject" in reason, (
        "the flags could not be read and the reason does not name the call "
        "that failed, so an operator reading the record learns nothing")


def test_k8_containment_survives_a_flag_read_that_fails_on_a_held_job(
        winjob, monkeypatch):
    """The idempotent path: a second call for a job this process already holds.

    The process IS in the job -- that was settled on the first call -- so it is
    contained, and the only thing missing is the number. `contained: true` with
    `limit_flags: null` and a reason is the honest record; an exception here is
    the launcher killing its own pulse over a failed read of a field it only
    prints.
    """
    def _boom(handle):
        raise OSError("QueryInformationJobObject failed, GetLastError=5")

    monkeypatch.setattr(winjob, "_WINDOWS", True)
    monkeypatch.setattr(winjob, "_held", object())
    monkeypatch.setattr(winjob, "job_limit_flags", _boom)

    held = winjob.contain_this_process()

    assert held.contained is True, (
        "a process already inside its job was reported UNCONTAINED because a "
        "read of the job's flags failed")
    assert held.limit_flags is None
    assert held.reason, "the degraded read left no reason anywhere"


def test_k8_the_launcher_runs_its_command_when_contain_itself_raises(
        winlaunch, tmp_path):
    """The outermost guard, and the one avocet's mutation actually tripped.

    `winlaunch.main` called `contain()` bare. `contain_this_process` says it
    never raises, and the two unguarded flag reads made that untrue -- but the
    launcher must not depend on that promise being kept, because the cost of it
    being broken is every scheduled pulse on the machine. A raise from
    containment is recorded and the command runs.
    """
    spec = tmp_path / "k8.json"
    ran = tmp_path / "k8.ran"
    spec.write_text(json.dumps({
        "stem": "k8", "argv": ["unused"], "env": {}, "cwd": str(tmp_path),
        "supervise": False}), encoding="utf-8")

    def _boom():
        raise OSError("QueryInformationJobObject failed, GetLastError=5")

    class _Done:
        returncode = 0

    def _run(argv, **kwargs):
        ran.write_text("yes", encoding="utf-8")
        return _Done()

    with open(tmp_path / "k8.log", "w", encoding="utf-8") as log:
        code = winlaunch.main(spec, log, run=_run, contain=_boom)

    assert ran.exists(), (
        "containment raised and the command never started, so the mechanism "
        "that stops a pulse outliving its task stopped the pulse instead")
    assert code == 0
    log_text = (tmp_path / "k8.log").read_text(encoding="utf-8")
    assert "QueryInformationJobObject" in log_text, (
        "the containment failure is nowhere in the log, so the launcher ran "
        "the pulse and told nobody the tree is unprotected")
    record = json.loads(
        winlaunch.containment_path(tmp_path, "k8").read_text(encoding="utf-8"))
    assert record["contained"] is False, record
    assert "QueryInformationJobObject" in record["reason"], record
