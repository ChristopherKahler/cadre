"""`heartbeat enable` over a running heartbeat ends the pulse in flight (#147).

On Windows `install_timer` re-created the task with ``schtasks /Create ... /F``
and did not end the instance that was running, so an operator who asked for a new
interval got the new task and the OLD pulse, still running with the old settings,
while ``heartbeat status`` described the new one.

THREE TIERS, AND NO LEG MAY CLAIM A TIER ABOVE THE ONE IT RUNS IN. This file is
tiers A and A' only:

* **tier A** -- ``run_cmd`` faked. Proves OUR CODE ASKS: that ``/End`` is issued
  for the right task and before ``/Create``. **It proves nothing about the
  pulse.** A leg here that stopped at "the command was issued" would go green
  while the pulse it was meant to end kept running.
* **tier A'** -- a child process on the one JSON object, with the backend faked.
  Proves THE REPORT FOLLOWS THE LOCK READING, never what schtasks said.
* **tier B/C** -- a real scheduled task, and a real task running a job-contained
  launcher. Those live in ``tests/test_winsched_live.py`` under its existing
  gate, and CI's Windows job is where they run (``tests.yml:111`` sets
  ``CADRE_WINSCHED_LIVE=1``). **Nothing in this file measures them.**

WHY `/End` IS SOUND HERE AND WAS NOT AT #119. `/End` alone left three survivors
then (``winlaunch.py:49-51``). #141 then put the launcher in a job with
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` and neither breakaway flag, holding the
handle for its own life, so the launcher's exit -- however it is ended -- closes
the handle and ends the tree. #147's own table measured a scratch sleeping task
rather than a contained launcher, so it does not measure this case.

THE REPORT IS DERIVED FROM THE LOCK, NEVER FROM `/End`. A task that is absent or
idle makes `/End` fail or say SUCCESS, and neither is a reading of a pulse.
``previous_pulse`` is computed from ``cleanup["lock"]`` alone.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import firm.cli.heartbeat as hb
import firm.pulse.cleanup as cleanup_mod
import firm.sched.launchd as launchd_mod
import firm.sched.systemd as systemd_mod
import firm.sched.winsched as winsched_mod
from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.sched.winsched import WindowsScheduler

FIRM = "overco"
STEM = f"{hb._UNIT_PREFIX}{FIRM}"

#: Every word `previous_pulse` may be. Four, because the EIGHT lock values
#: collapse: the four that mean "the lock was not read" -- remote-holder, no-db,
#: firm-id-unresolved, unreadable -- and not-attempted, which means it was never
#: tried, all answer unknown.
WORDS = {"none", "ended", "survived", "unknown"}


# ---------------------------------------------------------------------------
# the fence (PR C's, copied rather than imported), and its control
# ---------------------------------------------------------------------------

def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".config" / "systemd" / "user").mkdir(parents=True)
    return home


def _child_env_for(home: Path) -> dict[str, str]:
    """One producer, so the fence the control proves is the fence the legs use.

    BOTH variables: Windows reads USERPROFILE (then HOMEDRIVE+HOMEPATH) and
    IGNORES HOME; POSIX reads HOME. `default_unit_dir()` is
    `Path.home()/.config/systemd/user`.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(hb.__file__).resolve().parents[2])
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env.pop("HOMEDRIVE", None)
    env.pop("HOMEPATH", None)
    env["CADRE_SCHEDULER"] = "systemd"
    env["CADRE_CLAUDE_BIN"] = sys.executable
    env.pop("CADRE_DB_URL", None)
    env.pop("FIRM_ID", None)
    return env


def test_S0_control_the_child_really_lives_in_the_temp_home(tmp_path):
    """The fence, proved here rather than cited from PR C.

    A file that inherits a fence inherits the obligation to prove it. Setting
    HOME alone did nothing on Windows and four of PR C's legs failed in CI for a
    reason no Linux run could show.
    """
    home = _home(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-c", "from pathlib import Path; print(Path.home())"],
        capture_output=True, env=_child_env_for(home), timeout=120,
        stdin=subprocess.DEVNULL)
    answered = proc.stdout.decode("utf-8", "replace").strip()
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert Path(answered) == home, (answered, str(home))


# ---------------------------------------------------------------------------
# TIER A -- our code ASKS. Nothing here is a reading of a pulse.
# ---------------------------------------------------------------------------

@pytest.fixture
def captured(monkeypatch, tmp_path):
    """Every `run_cmd` the Windows backend issues, in order.

    TIER A. The sequence is all this proves. `schtasks` is never run.
    """
    calls: list[list[str]] = []

    def fake(argv, timeout=30):
        calls.append(list(argv))
        return 0, "SUCCESS: fake"

    monkeypatch.setattr(winsched_mod, "run_cmd", fake)
    monkeypatch.setattr(winsched_mod, "_resolve_pythonw",
                        lambda: Path(sys.executable))
    monkeypatch.setattr(winsched_mod, "_pythonw_self_test",
                        lambda p: (True, ""))
    return calls


def _win(tmp_path: Path) -> WindowsScheduler:
    return WindowsScheduler(launcher_dir=tmp_path / "launchers")


def _verbs(calls: list[list[str]]) -> list[str]:
    return [c[1] for c in calls if c and c[0] == "schtasks"]


def test_E1_end_is_issued_for_this_task_and_before_the_create(tmp_path, captured):
    """TIER A: proves our code ASKS. It proves nothing about the pulse.

    `/Create /F` re-creates the task definition and leaves a running instance
    alone, which is the whole defect. Ending first is what makes the heartbeat
    the operator gets the one they just asked for.
    """
    _win(tmp_path).install_timer(
        STEM, description="d", workdir=tmp_path, env={},
        argv=[sys.executable, "-m", "firm", "pulse"], interval="15m")

    verbs = _verbs(captured)
    assert "/End" in verbs, (captured,)
    assert verbs.index("/End") < verbs.index("/Create"), (
        "ending AFTER the re-create ends the new task, not the old pulse", verbs)
    ended = next(c for c in captured if len(c) > 1 and c[1] == "/End")
    created = next(c for c in captured if len(c) > 1 and c[1] == "/Create")
    assert ended[ended.index("/TN") + 1] == created[created.index("/TN") + 1], (
        "the two commands must name the SAME task", ended, created)


def test_E2_a_failed_end_still_reaches_the_create(tmp_path, monkeypatch):
    """TIER A. A task that is absent or idle makes `/End` fail, and that is not
    a reason to refuse an install the operator asked for."""
    calls: list[list[str]] = []

    def fake(argv, timeout=30):
        calls.append(list(argv))
        if len(argv) > 1 and argv[1] == "/End":
            return 1, "ERROR: The system cannot find the file specified."
        return 0, "SUCCESS: fake"

    monkeypatch.setattr(winsched_mod, "run_cmd", fake)
    monkeypatch.setattr(winsched_mod, "_resolve_pythonw",
                        lambda: Path(sys.executable))
    monkeypatch.setattr(winsched_mod, "_pythonw_self_test", lambda p: (True, ""))

    out = _win(tmp_path).install_timer(
        STEM, description="d", workdir=tmp_path, env={},
        argv=[sys.executable], interval="15m")

    assert "/Create" in _verbs(calls), ("a failed /End stopped the install", calls)
    assert out["ended"]["rc"] == 1, out


def test_E3_the_end_is_recorded_verbatim_and_nothing_is_inferred(
        tmp_path, captured):
    """TIER A. The rc and the words go in the dict as they came back.

    NOTHING IS INFERRED FROM THEM. `/End` says SUCCESS for a task that was idle
    and fails for one that is absent, and neither is a reading of a pulse. The
    report an operator acts on comes from the LOCK, one layer up.
    """
    out = _win(tmp_path).install_timer(
        STEM, description="d", workdir=tmp_path, env={},
        argv=[sys.executable], interval="15m")

    assert "ended" in out, out
    assert out["ended"]["rc"] == 0, out
    assert out["ended"]["said"] == "SUCCESS: fake", (
        "verbatim, not summarised", out)


def test_E4_a_refusal_ends_nothing(tmp_path, monkeypatch):
    """TIER A, and it is the ORDER that matters.

    `_prepare_task` raises before any side effect -- the file's own rule. If
    `/End` ran first, a self-test refusal would have ended a live pulse and then
    installed nothing: the worst of both.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(winsched_mod, "run_cmd",
                        lambda a, timeout=30: (calls.append(list(a)), (0, ""))[1])
    monkeypatch.setattr(winsched_mod, "_resolve_pythonw",
                        lambda: Path(sys.executable))
    monkeypatch.setattr(winsched_mod, "_pythonw_self_test",
                        lambda p: (False, "the launcher failed its self-test"))

    with pytest.raises(winsched_mod.SchedulerError):
        _win(tmp_path).install_timer(
            STEM, description="d", workdir=tmp_path, env={},
            argv=[sys.executable], interval="15m")

    assert "/End" not in _verbs(calls), (
        "a refusal ended a pulse and installed nothing", calls)

    # THE POSITIVE SIBLING, IN THIS LEG ON PURPOSE. Asserting only that a
    # refusal issues no `/End` is GREEN against a backend that never issues one
    # at all -- it was green at the base sha for exactly that reason. The
    # passing self-test has to issue it in the same run, or this leg measures
    # nothing and says "order is correct" while there is no order.
    calls.clear()
    monkeypatch.setattr(winsched_mod, "_pythonw_self_test", lambda p: (True, ""))
    _win(tmp_path).install_timer(
        STEM, description="d", workdir=tmp_path, env={},
        argv=[sys.executable], interval="15m")
    assert "/End" in _verbs(calls), (
        "the same backend must issue /End when the self-test PASSES, or the "
        "assertion above is about a command that does not exist", calls)


# ---------------------------------------------------------------------------
# condition 5 -- the other two backends are unchanged. A SEQUENCE CONTROL ONLY.
# ---------------------------------------------------------------------------

def test_X1_systemd_gains_no_end_shaped_call(tmp_path, monkeypatch):
    """A regression control on the captured sequence, and NOTHING MORE.

    What systemd does to a running oneshot on re-enable is UNTESTED by this and
    is registered outside this PR. This leg says only that this PR did not leak
    a teardown verb into a backend that never had one.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(systemd_mod, "run_cmd",
                        lambda a, timeout=30: (calls.append(list(a)), (0, ""))[1])
    systemd_mod.SystemdScheduler(unit_dir=tmp_path / "u").install_timer(
        STEM, description="d", workdir=tmp_path, env={},
        argv=[sys.executable], interval="15m")
    verbs = [v for c in calls for v in c]
    assert not any(v in ("stop", "kill", "/End") for v in verbs), (calls,)


def test_X2_launchd_gains_no_end_shaped_call(tmp_path, monkeypatch):
    """The same control for launchd. It already calls `self.remove(stem)` first,
    for its own mechanical reason (bootstrap rejects doubles), and this leg pins
    that nothing NEW was added beside it."""
    calls: list[list[str]] = []
    monkeypatch.setattr(launchd_mod, "run_cmd",
                        lambda a, timeout=30: (calls.append(list(a)), (0, ""))[1])
    launchd_mod.LaunchdScheduler(agent_dir=tmp_path / "a").install_timer(
        STEM, description="d", workdir=tmp_path, env={},
        argv=[sys.executable], interval="15m")
    verbs = [v for c in calls for v in c]
    assert not any(v == "/End" for v in verbs), (calls,)


# ---------------------------------------------------------------------------
# TIER A' -- the report follows the LOCK, never `/End`
# ---------------------------------------------------------------------------

def _lock_vocabulary() -> set[str]:
    """Every value `release_and_finalize` may put in `lock`, READ FROM SOURCE.

    Law 31: the guard's own list cannot supply the set it is meant to cover,
    because it would be written from the same assumption the code was. `NO_LOCK`
    is the only exported constant anywhere in the two; the rest are literals,
    so they are read out of the assignments and dict bodies themselves.

    THE POINT IS AN EIGHTH VALUE. When one is added, this reddens the totality
    leg instead of the mapping silently defaulting or raising KeyError deep
    inside a verb that has already installed the timer.

    BOTH MODULES, and the second one is not optional. `heartbeat.py` builds its
    OWN cleanup dicts -- `run_disable`'s `not-removed` and `remove-raised`
    branches each hand back `{"lock": "not-attempted", ...}` -- so the vocabulary
    was never `cleanup.py`'s alone. A walk over one module would read seven of
    eight and report a total mapping, which is the exact failure this leg exists
    to prevent, committed inside the leg.
    """
    found: set[str] = set()
    for mod in (cleanup_mod, hb):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        found |= _lock_literals(tree)
    found.add(cleanup_mod.NO_LOCK)
    return found


def _lock_literals(tree: ast.AST) -> set[str]:
    """Every string literal assigned to a `lock` key in one module."""
    found: set[str] = set()
    for node in ast.walk(tree):
        # `{"lock": "not-attempted", ...}` -- a dict built in place
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == "lock"
                        and isinstance(v, ast.Constant)
                        and isinstance(v.value, str)):
                    found.add(v.value)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        for tgt in node.targets:
            # `result["lock"] = "..."`
            if (isinstance(tgt, ast.Subscript)
                    and isinstance(tgt.slice, ast.Constant)
                    and tgt.slice.value == "lock"
                    and isinstance(node.value.value, str)):
                found.add(node.value.value)
            # NOT every uppercase constant. An earlier draft of this reader
            # took any `NAME = "literal"`, which in `heartbeat.py` swept up
            # `_UNIT_PREFIX` and reported "cadre-heartbeat-" as a lock value.
            # A reader that OVER-reads is as broken as one that under-reads:
            # it would have demanded a mapping entry for a string that is not
            # a lock reading at all. `NO_LOCK` is added by name at the call
            # site instead, because it is the one constant that really is one.
    return found


def test_C5_the_mapping_is_TOTAL_against_the_lock_s_own_vocabulary():
    """Every value the lock can hold has a word, and an EIGHTH would redden this.

    The verdict named four lock values. The lock has seven: the three that mean
    "not read" (`no-db`, `firm-id-unresolved`, `unreadable`) join `remote-holder`
    as `unknown`, because in all four the lock was not read and what became of
    the pulse is therefore not known -- NOT that it survived.

    Keyed to `cleanup.py`'s own vocabulary rather than to a list written here, so
    a value added there reddens this leg rather than falling through.
    """
    vocab = _lock_vocabulary()
    assert len(vocab) >= 8, (
        "the reader found fewer values than the two modules hold; a reader "
        "that cannot see must not report a total mapping", sorted(vocab))

    # BOTH HALVES, and the second is the one that makes this a test.
    # Asserting only that each word is valid would leave an EIGHTH value
    # falling to `unknown` at run time AND this leg green -- the silent default
    # wearing a test. Asserting the vocabulary is a SUBSET OF THE KEYS means a
    # value added to either module reddens here and somebody chooses its word.
    assert vocab <= set(hb._PREVIOUS_PULSE), (
        "a lock value exists that the mapping does not name",
        sorted(vocab - set(hb._PREVIOUS_PULSE)))
    for value in sorted(vocab):
        assert hb._PREVIOUS_PULSE[value] in WORDS, value

    # And at RUN time the direction is the other way, on purpose: a verb that
    # has already installed the timer must not raise over a word.
    assert hb._previous_pulse("a-value-nobody-has-written-yet") == "unknown", (
        "an unrecognised reading is UNKNOWN, never survived")


@pytest.mark.parametrize("lock,word", [
    ("none", "none"),
    ("cleared", "ended"),
    ("held-by-a-live-pulse", "survived"),
    ("remote-holder", "unknown"),
    ("no-db", "unknown"),
    ("firm-id-unresolved", "unknown"),
    ("unreadable", "unknown"),
    ("not-attempted", "unknown"),
])
def test_C4_each_lock_reading_has_its_word(lock, word):
    """`unknown` means the lock was UNREAD, not that a pulse survived.

    Collapsing the two would tell an operator a pulse is still running when the
    truth is that nobody could look -- the same mistake PR D's `undeterminable`
    exists to stop one layer over.
    """
    assert hb._previous_pulse(lock) == word


def _firm_at(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    conn = connect(get_db_path(root))
    try:
        apply_migrations(conn)
        create(conn, "firm", {"id": FIRM, "name": "Over Co"})
    finally:
        conn.close()
    return root


@pytest.fixture
def quiet_systemctl(monkeypatch):
    """`systemctl` answers 0 and says nothing, the way `test_sched.py` drives it.

    IN PROCESS, and that is a ruling rather than a shortcut. There is no seam to
    fake `run_cmd` inside a child, so a child-driven leg here runs a REAL
    `systemctl --user enable --now`, which fails off a user manager and sends
    every one of these legs down the `SchedulerError` path instead of reaching
    the cleanup. The shipping channel is proved ONCE, by the child leg below;
    these three prove what the report says.
    """
    monkeypatch.setattr(systemd_mod, "run_cmd", lambda a, timeout=30: (0, ""))
    # conftest points CADRE_CLAUDE_BIN at a path that does not exist, so nothing
    # resolves a Member runtime by accident. `run_enable` REFUSES on that before
    # it installs anything -- a THIRD early exit beside `db-not-found` and the
    # `SchedulerError` path, and one that emits neither new key, correctly: it
    # refused before any install, so there is no cleanup to report and nothing
    # was ended. These legs are about the VERBS, not the runtime, so they point
    # it at this interpreter: it resolves, and nothing here ever runs a pulse.
    monkeypatch.setenv("CADRE_CLAUDE_BIN", sys.executable)
    return None


def _enable(ws: Path, units: Path, capsys) -> dict:
    """`run_enable` in process, on EXACTLY ONE JSON object.

    `unit_dir` is the tests' own knob and pins the systemd backend, so this
    never reaches winsched or launchd. `json.loads` over the whole buffer is
    what makes "exactly one object" an assertion rather than a hope.
    """
    rc = hb.run_enable(ws, FIRM, "15m", unit_dir=units)
    out = capsys.readouterr().out
    result = json.loads(out)
    result["_rc"] = rc
    return result


def test_C1_no_lock_reports_none_and_still_exits_ok(tmp_path, capsys, quiet_systemctl):
    """TIER A': the report follows the lock reading.

    Nothing was holding the lock, so nothing was running to end.
    """
    ws = _firm_at(tmp_path / "ws")

    result = _enable(ws, tmp_path / "units", capsys)

    assert result["ok"] is True, result
    assert result["_rc"] == 0, result
    assert "cleanup" in result, ("the cleanup dict is emitted whole", result)
    assert result["previous_pulse"] == "none", result


def test_C2_a_dead_holder_reports_ended_and_closes_its_run(tmp_path, capsys, quiet_systemctl):
    """A holder that is verifiably dead means no pulse from before this change
    remains -- one was ended, or it had already exited.

    The run row is closed with "heartbeat enable" named in its notes, the way
    `disable` names itself, so a run that ends this way says which action ended
    it rather than looking like an unexplained failure.
    """
    ws = _firm_at(tmp_path / "ws")
    _seed_lock(ws, alive=False)

    result = _enable(ws, tmp_path / "units", capsys)

    assert result["previous_pulse"] == "ended", result
    assert result["ok"] is True, result
    notes = _run_notes(ws)
    assert any("heartbeat enable" in (n or "") for n in notes), notes


def test_C3_a_live_holder_reports_survived_and_is_left_alone(tmp_path, capsys, quiet_systemctl):
    """The honest answer on a host whose launcher could not be contained.

    The guard is asserted BOTH WAYS: the lock is still held and the running row
    is still running. Reporting "survived" while quietly clearing the lock would
    pass a one-sided check and let a second pulse start beside the first.
    """
    ws = _firm_at(tmp_path / "ws")
    child = _seed_lock(ws, alive=True)
    try:
        result = _enable(ws, tmp_path / "units", capsys)

        assert result["previous_pulse"] == "survived", result
        assert result["ok"] is True, result
        assert _lock_held(ws), "a live pulse's lock was cleared"
        assert _running_rows(ws), "a live pulse's run was finalized"
    finally:
        child.kill()
        child.wait(timeout=30)


# ---------------------------------------------------------------------------
# seeding the lock, and reading back what the verb did to it
# ---------------------------------------------------------------------------

def _seed_lock(ws: Path, *, alive: bool) -> subprocess.Popen | None:
    """Put a holder in this firm's pulse lock, dead or alive.

    Identity is `host:pid:nonce` (`dblock.make_holder_id`), and the cleanup
    decides "alive" from the pid. A DEAD holder is this host with a pid that is
    not running; a LIVE one is this host with a real sleeping child, which the
    caller ends in its own `finally`.

    A live holder is a real process on purpose. Faking one by patching the
    liveness check would test the patch.
    """
    from firm.pulse import dblock

    child = None
    if alive:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                                 stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        pid = child.pid
    else:
        # A pid that has certainly exited: spawn one and reap it.
        gone = subprocess.Popen([sys.executable, "-c", ""],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        gone.wait(timeout=30)
        pid = gone.pid

    import socket
    holder = f"{socket.gethostname()}:{pid}:seeded00"
    conn = connect(get_db_path(ws))
    try:
        dblock.acquire(conn, FIRM, holder)
        # A RUN THE PULSE LEFT OPEN, because a lock with no run behind it is
        # only half the state this verb acts on. The cleanup clears the lock AND
        # closes the runs a dead pulse left running, and a leg that seeded only
        # the lock would assert against a table that was always empty -- passing
        # its "nothing was finalized" half for the wrong reason and unable to
        # prove its "was finalized" half at all.
        # `member_run.member_id` is a foreign key, so the member exists first.
        # Seeded rather than founded: this leg is about the cleanup, and a real
        # founding would drag the whole firm-creation path into a test of one
        # verb's report.
        create(conn, "member", {"id": "seeded-member", "firm_id": FIRM,
                                "name": "Seeded Member", "role": "seeded"})
        conn.execute(
            "INSERT INTO member_run (id, firm_id, member_id, status, started_at)"
            " VALUES (?, ?, ?, 'running', datetime('now'))",
            (f"run-seeded-{pid}", FIRM, "seeded-member"))
        conn.commit()
    finally:
        conn.close()
    return child


def _lock_held(ws: Path) -> bool:
    from firm.pulse import dblock
    conn = connect(get_db_path(ws))
    try:
        return dblock.current_holder(conn, FIRM) is not None
    finally:
        conn.close()


def _run_notes(ws: Path) -> list[str]:
    conn = connect(get_db_path(ws))
    try:
        rows = conn.execute("SELECT notes FROM member_run").fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def _running_rows(ws: Path) -> list:
    conn = connect(get_db_path(ws))
    try:
        return conn.execute(
            "SELECT id FROM member_run WHERE status = 'running'").fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# the failure path (addendum 3)
# ---------------------------------------------------------------------------

def test_C6_an_install_that_raised_reports_unknown_and_says_why(
        tmp_path, monkeypatch, capsys, quiet_systemctl):
    """THE WORDS ARE THE ASSERTION, NOT THE FACT, and that is the point.

    `install_timer` raises from TWO places and they differ on exactly the thing
    an operator wants to know: `_prepare_task`, which is BEFORE `/End`, so
    nothing was ended; and `/Create`, which is AFTER `/End` was issued, so a
    running pulse may well have been ended and the old task definition still
    stands.

    `run_enable` cannot tell those apart. So a message saying "nothing was
    ended" would be a claim the code cannot support, and it would read as
    reassurance in the one case where the operator most needs to look.

    This leg forces the refusal at `_prepare_task`, so nothing is installed --
    but it asserts the WORDING, because the wording is what has to hold on the
    path this leg cannot reach.
    """
    ws = _firm_at(tmp_path / "ws")

    class _Refusing:
        name = "refusing"

        def status(self, stem):
            return {"installed": False}

        def install_timer(self, *a, **k):
            raise winsched_mod.SchedulerError("refusing to install: self-test")

    monkeypatch.setattr(hb, "_sched", lambda unit_dir=None: _Refusing())

    rc = hb.run_enable(ws, FIRM, "15m")

    result = json.loads(capsys.readouterr().out)
    assert rc == 1, result
    assert result["ok"] is False, result
    assert result["cleanup"]["lock"] == "not-attempted", result
    assert result["previous_pulse"] == "unknown", result
    assert "whether a running pulse was ended is unknown" in \
        result["cleanup"]["reason"], (
        "the reason must not claim nothing was ended: install_timer raises "
        "both before and after /End, and this verb cannot tell which", result)
    assert "previous task definition stands" in result["cleanup"]["reason"], result


def test_C7_a_refusal_BEFORE_the_install_carries_neither_key(
        tmp_path, monkeypatch, capsys, quiet_systemctl):
    """THE THIRD STATE OF THE KEY, and it is asserted by PRESENCE.

    `run_enable` has three shapes of exit and they differ in what they may
    honestly say about a pulse:

    * refused BEFORE any install -- nothing was installed, nothing was ended,
      and there is no cleanup to report, so BOTH KEYS ARE ABSENT;
    * the install RAISED -- `not-attempted` / `unknown`, because whether a
      running pulse was ended cannot be known here;
    * installed -- the lock reading, whatever it was.

    Absent and `unknown` are different answers. Emitting `previous_pulse:
    unknown` on a refusal that never reached the scheduler would tell an
    operator the fate of a pulse was UNREADABLE when in truth nothing was ever
    attempted -- the same collapse `#158` fought one verb over, and the reason
    this leg asserts the KEY and not a value.

    Driven through the claude-runtime refusal because it is a real
    pre-install refusal that conftest arms on every test in this repo.
    """
    ws = _firm_at(tmp_path / "ws")
    monkeypatch.setenv("CADRE_CLAUDE_BIN", str(tmp_path / "no-such-runtime"))

    rc = hb.run_enable(ws, FIRM, "15m", unit_dir=tmp_path / "units")

    result = json.loads(capsys.readouterr().out)
    assert rc == 1, result
    assert result["ok"] is False, result
    assert "cleanup" not in result, (
        "a refusal before the install has no cleanup to report", result)
    assert "previous_pulse" not in result, (
        "and no pulse whose fate it may name -- absent is not unknown", result)

    # THE POSITIVE SIBLING, IN THIS LEG. Asserting only that the keys are
    # ABSENT is green against a build that never emits them at all -- which is
    # exactly what the base sha does, so this leg would have passed there and
    # measured nothing. The successful enable has to carry BOTH in the same run,
    # or "absent on a refusal" is a statement about keys that do not exist.
    monkeypatch.setenv("CADRE_CLAUDE_BIN", sys.executable)
    rc_ok = hb.run_enable(ws, FIRM, "15m", unit_dir=tmp_path / "units")
    ok = json.loads(capsys.readouterr().out)
    assert rc_ok == 0, ok
    assert "cleanup" in ok and "previous_pulse" in ok, (
        "the same verb must carry both keys when it DOES install, or the "
        "assertion above is about keys nothing ever emits", ok)


def test_C8_the_backends_answer_is_reported_and_absent_where_there_is_none(
        tmp_path, capsys, quiet_systemctl, monkeypatch):
    """`scheduler_ended` is carried where a backend answered, and ABSENT where
    none did -- `disable`'s `scheduler_removed` beside `removed` is the same
    idea, for the record and never what the exit code rests on.

    BOTH DIRECTIONS IN ONE LEG. The Windows backend answers, so the key is
    there; systemd never re-creates over a live task, so it never answers, and
    inventing a null for it would be a claim about a mechanism that host does
    not have.

    An earlier draft of `run_enable` CLAIMED in a comment that this was
    reported and never put it in the payload. A report claimed and not printed
    is not a report, which is why this leg asserts the KEY.
    """
    ws = _firm_at(tmp_path / "ws")

    # systemd: no such answer, so no such key.
    plain = _enable(ws, tmp_path / "units", capsys)
    assert "scheduler_ended" not in plain, (
        "systemd does not end a task to re-create it and must claim nothing",
        plain)

    # winsched, faked at run_cmd: it answers, so the key is carried.
    monkeypatch.setattr(winsched_mod, "run_cmd",
                        lambda a, timeout=30: (0, "SUCCESS: fake"))
    monkeypatch.setattr(winsched_mod, "_resolve_pythonw",
                        lambda: Path(sys.executable))
    monkeypatch.setattr(winsched_mod, "_pythonw_self_test", lambda p: (True, ""))
    monkeypatch.setattr(
        hb, "_sched",
        lambda unit_dir=None: WindowsScheduler(launcher_dir=tmp_path / "wl"))

    rc = hb.run_enable(ws, FIRM, "15m")
    win = json.loads(capsys.readouterr().out)

    assert rc == 0, win
    assert "scheduler_ended" in win, (
        "the backend answered and the payload dropped it", win)
    assert win["scheduler_ended"]["said"] == "SUCCESS: fake", (
        "verbatim, the way the backend said it", win)


def test_C9_the_behaviour_is_written_where_someone_reads_it(tmp_path):
    """Condition 6: said where an operator will meet it, not only in a PR.

    Two surfaces, because they are read by different people at different
    moments: the function's own docstring, for whoever opens the code, and
    `cadre heartbeat enable --help`, for whoever is about to run it on a firm
    that is pulsing right now. The second is the one that matters: this verb
    now ENDS something, and a person about to run it deserves to learn that
    before it happens rather than from `previous_pulse` afterwards.
    """
    assert hb.run_enable.__doc__, "run_enable carries no docstring at all"
    doc = hb.run_enable.__doc__
    assert "ENDS THE PULSE IN FLIGHT" in doc, doc
    assert "previous_pulse" in doc, doc

    proc = subprocess.run(
        [sys.executable, "-m", "firm", "heartbeat", "enable", "--help"],
        capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    helptext = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0, helptext
    assert "ENDS THE" in helptext and "PULSE IN FLIGHT" in helptext, (
        "the operator about to run it is not told that it ends one", helptext)
    assert "previous_pulse" in helptext, helptext


# ---------------------------------------------------------------------------
# the SHIPPING CHANNEL, proved once (addendum 4). Everything above drives
# `run_enable` directly; these two drive the real CLI a caller runs.
# ---------------------------------------------------------------------------

def _fake_systemctl(bin_dir: Path, exit_code: int) -> dict[str, str]:
    """A `systemctl` FIRST on PATH that exits *exit_code* and says nothing.

    POSIX ONLY, and the reason is not squeamishness: `CreateProcess` searches
    System32 before PATH, so on Windows no shim can shadow `schtasks.exe`, and
    a real `/Create` is not a test's to make.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "systemctl"
    shim.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    shim.chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}" + os.environ.get("PATH", "")}


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a shim cannot shadow schtasks.exe: CreateProcess searches System32 "
           "before PATH, and a real /Create is not a test's to make. The "
           "Windows shipping channel is proved by the live leg in "
           "tests/test_winsched_live.py, on CI.")
def test_C10_the_real_cli_reports_the_ended_pulse(tmp_path):
    """THE SHIPPING CHANNEL, once. Everything else drives `run_enable` directly.

    argparse, `main()` and `sys.exit` are not exercised by an in-process call,
    and they are what a caller and the hub actually meet. One leg through them
    is what makes the rest of this file a statement about the product rather
    than about a function.
    """
    home = _home(tmp_path)
    ws = _firm_at(tmp_path / "ws")
    _seed_lock(ws, alive=False)

    env = _child_env_for(home)
    env.update(_fake_systemctl(tmp_path / "bin", 0))
    env["CADRE_SCHEDULER"] = "systemd"

    proc = subprocess.run(
        [sys.executable, "-m", "firm", "heartbeat", "enable",
         "--workspace", str(ws), "--firm-id", FIRM, "--interval", "15m"],
        capture_output=True, env=env, timeout=180, stdin=subprocess.DEVNULL)

    result = json.loads(proc.stdout.decode("utf-8", "replace"))
    assert proc.returncode == 0, (result, proc.stderr.decode("utf-8", "replace"))
    assert result["ok"] is True, result
    assert result["cleanup"]["lock"] == "cleared", result
    assert result["previous_pulse"] == "ended", result


def test_C11_the_real_cli_reports_an_install_that_raised(tmp_path):
    """The same channel on the failure path, and it runs EVERYWHERE.

    `CADRE_SCHEDULER=systemd` is forced so the child never reaches winsched or
    launchd whatever host this is, and `systemctl` fails: a shim exiting 1 on
    POSIX, and simply absent on Windows, which fails the same way for a
    different reason. Either way the install raises and the object must carry
    the wording that does not overclaim.
    """
    home = _home(tmp_path)
    ws = _firm_at(tmp_path / "ws")

    env = _child_env_for(home)
    env["CADRE_SCHEDULER"] = "systemd"
    if sys.platform != "win32":
        env.update(_fake_systemctl(tmp_path / "bin", 1))

    proc = subprocess.run(
        [sys.executable, "-m", "firm", "heartbeat", "enable",
         "--workspace", str(ws), "--firm-id", FIRM, "--interval", "15m"],
        capture_output=True, env=env, timeout=180, stdin=subprocess.DEVNULL)

    result = json.loads(proc.stdout.decode("utf-8", "replace"))
    assert proc.returncode == 1, result
    assert result["ok"] is False, result
    assert result["cleanup"]["lock"] == "not-attempted", result
    assert result["previous_pulse"] == "unknown", result
    assert "whether a running pulse was ended is unknown" in \
        result["cleanup"]["reason"], result
