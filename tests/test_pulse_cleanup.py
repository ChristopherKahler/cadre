"""Ending a pulse is not clearing up after it. Issue #141, condition C1.

The job ends the processes. Two database rows outlive them, and the reaper
cannot be relied on to close either:

* `pulse_lock`. `dblock.acquire` (`pulse/dblock.py:30-51`) replaces a holder
  only when its heartbeat is older than `LOCK_TTL_SEC` = 600 (`:18`), and it
  never checks whether the holder's process is alive -- the only `pid` in that
  file is `os.getpid()` at `:27`, building the holder's identity. So a killed
  pulse wedges a restart for up to ten minutes.
* `member_run`. The row stays `running`. `pulse/orchestrator.py:88-121` does
  close such rows, as `failed` with `error.type='orphaned'` (`:119-121`), but
  only past twice the contract timeout plus a grace (`:111`) AND only when a
  pulse runs. After `disable` no pulse comes, so neither condition is ever met.

THE GUARD THAT OUTRANKS THE FEATURE (osprey, 17:41): the lock is cleared ONLY
when the recorded holder is verifiably dead. A lock whose holder is alive is
left alone and reported, never cleared -- because clearing a live pulse's lock
lets a second pulse start beside it, which is worse than the orphan #141 exists
to remove. The arm for that case is here and it is not an afterthought: it is
the case a host where containment FAILED actually lands in.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create

FIRM = "chrisai"


@pytest.fixture
def cleanup():
    """Imported per test, so a tree without the module fails each arm alone."""
    from firm.pulse import cleanup as module
    return module


def _seed(conn, *, running: list[str] = ("RUN-001",)) -> None:
    """A firm with a Member and one or more runs still marked running."""
    create(conn, "firm", {"id": FIRM, "name": "ChrisAI",
                          "operator": {"name": "Chris Kahler", "role": "Board"}})
    create(conn, "member", {"id": "MEM-001", "firm_id": FIRM,
                            "name": "Quill", "role": "Blog Author"})
    for index, run_id in enumerate(running):
        create(conn, "member_run", {
            "id": run_id, "firm_id": FIRM, "member_id": "MEM-001",
            "unit_id": None, "status": "running",
            "started_at": f"2026-04-15 1{index}:00:00"})


def _workspace(tmp_path: Path):
    """A firm workspace with a migrated database, seeded and ready."""
    from firm.core.db import connect, get_db_path

    db = get_db_path(tmp_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    apply_migrations(conn)
    _seed(conn)
    conn.commit()
    conn.close()
    return tmp_path


def _open(workspace: Path):
    from firm.core.db import connect, get_db_path
    return connect(get_db_path(workspace))


def _status_of(workspace: Path, run_id: str) -> str:
    conn = _open(workspace)
    try:
        row = conn.execute("SELECT status FROM member_run WHERE id = ?",
                           (run_id,)).fetchone()
        return row["status"] if hasattr(row, "keys") else row[0]
    finally:
        conn.close()


def _hold(workspace: Path, holder: str) -> None:
    from firm.pulse import dblock
    conn = _open(workspace)
    try:
        assert dblock.acquire(conn, FIRM, holder), "the arm could not take the lock"
    finally:
        conn.close()


def _holder(workspace: Path):
    from firm.pulse import dblock
    conn = _open(workspace)
    try:
        return dblock.current_holder(conn, FIRM)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# L1 -- a dead holder: clear the lock, close the runs
# ---------------------------------------------------------------------------

def test_l1_a_dead_holder_has_its_lock_cleared_and_its_runs_closed(
        cleanup, tmp_path, monkeypatch):
    """The whole point. After the task is gone, the firm is not left mid-pulse."""
    import socket

    ws = _workspace(tmp_path)
    _hold(ws, f"{socket.gethostname()}:424242:deadbeef")
    monkeypatch.setattr(cleanup, "_pid_alive", lambda pid: False)

    out = cleanup.release_and_finalize(ws, FIRM, by="heartbeat disable")

    assert out["lock"] == "cleared", out
    assert _holder(ws) is None, (
        "the lock row is still there, so a restart inside the next ten minutes "
        "is refused with pulse-already-running over a pulse that is dead")
    assert out["runs_finalized"] == ["RUN-001"], out
    assert _status_of(ws, "RUN-001") == "failed", (
        "the Member run is still marked running, so every 'is this firm busy' "
        "reader believes a run that no longer exists")


def test_l1_the_closed_run_says_what_ended_it(cleanup, tmp_path, monkeypatch):
    """A run closed with no reason looks like an unexplained failure.

    The Board reads Records. "failed" on its own invites someone to go looking
    for a crash that never happened, so the verb that ended it is written down.
    """
    import socket

    ws = _workspace(tmp_path)
    _hold(ws, f"{socket.gethostname()}:424242:deadbeef")
    monkeypatch.setattr(cleanup, "_pid_alive", lambda pid: False)

    cleanup.release_and_finalize(ws, FIRM, by="heartbeat disable")

    conn = _open(ws)
    try:
        row = conn.execute("SELECT * FROM member_run WHERE id = 'RUN-001'"
                           ).fetchone()
        blob = json.dumps({k: row[k] for k in row.keys()}, default=str)
    finally:
        conn.close()
    assert "heartbeat disable" in blob, (
        "the closed run does not say which action ended it: %s" % blob)


# ---------------------------------------------------------------------------
# L2 -- a LIVE holder: touch nothing. The guard that outranks the feature.
# ---------------------------------------------------------------------------

def test_l2_a_live_holder_keeps_its_lock_and_its_run(cleanup, tmp_path,
                                                     monkeypatch):
    """Clearing a live pulse's lock would let a second pulse start beside it.

    This is the case a host where containment FAILED lands in: the task is gone,
    the tree is not, and the pulse is still working. Reported, never cleared.
    """
    import socket

    ws = _workspace(tmp_path)
    holder = f"{socket.gethostname()}:424242:deadbeef"
    _hold(ws, holder)
    monkeypatch.setattr(cleanup, "_pid_alive", lambda pid: True)

    out = cleanup.release_and_finalize(ws, FIRM, by="heartbeat disable")

    assert out["lock"] == "held-by-a-live-pulse", out
    assert out.get("reason"), (
        "the cleanup left the lock alone and said nothing about why, so an "
        "operator sees a lock that survived a disable and no explanation")
    assert _holder(ws) == holder, (
        "the lock of a LIVE pulse was cleared; a second pulse can now start "
        "beside the first, which is worse than the orphan this closes")
    assert _status_of(ws, "RUN-001") == "running", (
        "a live pulse's run was finalized under it")
    assert out["runs_finalized"] == []


# ---------------------------------------------------------------------------
# L3 -- another machine's lock is never touched
# ---------------------------------------------------------------------------

def test_l3_a_lock_held_from_another_machine_is_left_to_that_machine(
        cleanup, tmp_path):
    """The lock is shared across every machine that pulses this firm."""
    ws = _workspace(tmp_path)
    _hold(ws, "some-other-box:99:cafebabe")

    out = cleanup.release_and_finalize(ws, FIRM, by="heartbeat disable")

    assert out["lock"] == "remote-holder", out
    assert _holder(ws) == "some-other-box:99:cafebabe", (
        "a lock held from another machine was cleared from this one")
    assert _status_of(ws, "RUN-001") == "running", (
        "runs belonging to another machine's pulse were finalized from here")


# ---------------------------------------------------------------------------
# L4 -- no lock row at all, and orphans still get closed
# ---------------------------------------------------------------------------

def test_l4_with_no_lock_row_the_orphaned_runs_are_still_closed(
        cleanup, tmp_path):
    """A pulse killed before it wrote a lock still leaves its run behind.

    "No lock" is not "nothing to do". Treating the lock as the gate on the
    finalize would leave exactly the rows a hard kill produces.
    """
    ws = _workspace(tmp_path)
    assert _holder(ws) is None, "the arm's own precondition failed"

    out = cleanup.release_and_finalize(ws, FIRM, by="heartbeat disable")

    assert out["lock"] == "none", out
    assert out["runs_finalized"] == ["RUN-001"], out
    assert _status_of(ws, "RUN-001") == "failed"


def test_l4_a_workspace_with_no_database_is_reported_not_raised(cleanup,
                                                                tmp_path):
    """Never raises. This runs at the end of a verb that already did its work.

    A cleanup that raised would turn a successful disable into a failed command
    while leaving the task removed anyway -- the operator would be told the
    disable failed, and it did not.
    """
    out = cleanup.release_and_finalize(tmp_path / "not-a-firm", FIRM,
                                       by="heartbeat disable")

    assert out["lock"] == "no-db", out
    assert out.get("reason")


# ---------------------------------------------------------------------------
# L5 -- the verb calls it, and says what it did
# ---------------------------------------------------------------------------

def test_l5_heartbeat_disable_cleans_up_after_the_task_it_removed(
        tmp_path, monkeypatch):
    """`heartbeat disable` ends the task AND clears up, and reports both.

    Without the call, everything above is a module nobody reaches. The leg
    watches the verb's own output rather than the module, so moving the call
    somewhere it never runs fails here.
    """
    import socket

    from firm.cli import heartbeat as hb

    ws = _workspace(tmp_path)
    _hold(ws, f"{socket.gethostname()}:424242:deadbeef")

    from firm.pulse import cleanup as cleanup_module
    monkeypatch.setattr(cleanup_module, "_pid_alive", lambda pid: False)

    class _Sched:
        """A scheduler that has the task installed and removes it on request."""

        def __init__(self) -> None:
            self.removed: list[str] = []

        def status(self, stem):
            # D3 asks the scheduler AGAIN after remove(), so a fake that
            # answers "installed" forever reports a removal that did not
            # happen. This models the half it always claimed to.
            return {"installed": stem not in self.removed,
                    "state": "ready", "workdir": str(ws)}

        def remove(self, stem):
            self.removed.append(stem)
            return {"removed": [stem]}

    sched = _Sched()
    monkeypatch.setattr(hb, "_sched", lambda unit_dir=None: sched)
    emitted: list[dict] = []
    monkeypatch.setattr(hb, "_emit", emitted.append)

    rc = hb.run_disable(FIRM)

    assert rc == 0, emitted
    assert sched.removed, "the task was never removed, so this leg measured nothing"
    assert emitted, "the verb emitted nothing"
    payload = emitted[-1]
    assert payload.get("cleanup"), (
        "`heartbeat disable` says nothing about the lock or the runs it left "
        "behind: %r. The task is gone and the firm still reads as mid-pulse."
        % payload)
    assert payload["cleanup"]["lock"] == "cleared", payload
    assert payload["cleanup"]["runs_finalized"] == ["RUN-001"], payload
    assert _status_of(ws, "RUN-001") == "failed"
    assert _holder(ws) is None


# ---------------------------------------------------------------------------
# L6 -- `firm pulse --abort` uses THIS module instead of its own copy
# ---------------------------------------------------------------------------
#
# This module's own docstring recorded the duplication rather than hiding it:
# "`cli/pulse.py`'s `_handle_abort` does the same two things with its own copy
# of the code, and should end up calling this function. It is not changed here
# because PR 142 is open on that file." PR 142 landed on 2026-09-21 at 18:18:12
# (main `5b8781042d15`), so the reason has expired and the copy comes out.
#
# TWO COPIES OF A RULE ARE TWO RULES. The guard that decides whether a lock may
# be cleared lives in one of them; a fix applied to one copy leaves the other
# deciding the old way, and nothing in the suite would say so.
#
# WHAT ABORT KEEPS, because it is abort's and not the cleanup's: signalling the
# holder, counting what it signalled, and telling "cleared" (a holder it killed)
# apart from "stale-cleared" (a holder already dead when it looked). The cleanup
# never signals anything.

def test_l6_cli_pulse_keeps_no_second_orphan_finalizer():
    """The copy is gone, not merely unused.

    Asserted on the module rather than on behaviour because an unused copy is
    exactly what the next person edits by mistake: it still imports, still
    reads as live code, and passes every test that goes through the CLI.
    """
    from firm.cli import pulse as cli_pulse

    assert not hasattr(cli_pulse, "_finalize_orphans"), (
        "cli/pulse.py still carries its own orphan finalizer beside "
        "firm.pulse.cleanup's, so the two can drift apart silently")


def test_l6_abort_calls_the_shared_cleanup_and_names_itself(
        cleanup, tmp_path, monkeypatch, capsys):
    """Abort delegates the lock and the runs, and says which verb it was.

    `by` reaches the Board: it is written into the finalized run's notes, so a
    run closed this way says an abort ended it instead of looking like an
    unexplained failure.
    """
    import socket

    from firm.cli import pulse as cli_pulse

    ws = _workspace(tmp_path)
    _hold(ws, f"{socket.gethostname()}:424242:deadbeef")
    monkeypatch.setattr(cli_pulse, "_pid_alive", lambda pid: False)

    seen: dict = {}

    def _spy(workspace, firm_id=None, **kwargs):
        seen["workspace"] = workspace
        seen["firm_id"] = firm_id
        seen.update(kwargs)
        return {"lock": "cleared", "runs_finalized": ["RUN-001"]}

    monkeypatch.setattr(cleanup, "release_and_finalize", _spy)

    rc = cli_pulse._handle_abort(ws, FIRM)
    capsys.readouterr()

    assert seen, (
        "abort never called firm.pulse.cleanup.release_and_finalize, so it is "
        "still clearing the lock and closing the runs with its own copy")
    assert seen["by"] == "firm pulse --abort", seen
    assert rc == 0


def test_l6_abort_reports_a_holder_it_could_not_kill_as_signalled(
        cleanup, tmp_path, monkeypatch, capsys):
    """The word `signalled` is abort's, and delegation must not lose it.

    `test_pulse_exit_contract.py` pins it: a holder that ignores SIGTERM reports
    `lock: signalled`, `aborted: 1`, exit 0. The shared cleanup calls that same
    state `held-by-a-live-pulse`, which is the right word for `heartbeat
    disable` and the wrong one for a caller that has just signalled the holder.
    """
    import socket

    from firm.cli import pulse as cli_pulse

    ws = _workspace(tmp_path)
    _hold(ws, f"{socket.gethostname()}:424242:deadbeef")
    monkeypatch.setattr(cli_pulse, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(cli_pulse.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(cli_pulse.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        cleanup, "release_and_finalize",
        lambda *a, **k: {"lock": "held-by-a-live-pulse",
                         "runs_finalized": ["RUN-001"]})

    rc = cli_pulse._handle_abort(ws, FIRM)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert out["lock"] == "signalled", out
    assert out["aborted"] == 1, out
    assert rc == 0


def test_l6_a_caller_that_owns_a_live_holders_runs_can_close_them(
        cleanup, tmp_path, monkeypatch):
    """The one real difference between the two callers, named as a parameter.

    `heartbeat disable` finds a live holder on a host where containment failed:
    that pulse is still working and its runs are not disable's to close.
    `firm pulse --abort` has just told the holder to die and OWNS closing them
    -- its own docstring says so, and without it the row stays `running`
    forever.

    The lock is still left alone in both cases. That guard outranks this
    parameter and this arm proves the two are independent.

    #148 is open on the fact that abort finalizes a run whose Member is still
    alive. That defect is PRESERVED here on purpose: this leg removes a
    duplicate, it does not change what abort does, and changing both at once
    would leave neither measured.
    """
    import socket

    ws = _workspace(tmp_path)
    holder = f"{socket.gethostname()}:424242:deadbeef"
    _hold(ws, holder)
    monkeypatch.setattr(cleanup, "_pid_alive", lambda pid: True)

    out = cleanup.release_and_finalize(ws, FIRM, by="firm pulse --abort",
                                       finalize_live_runs=True)

    assert out["lock"] == "held-by-a-live-pulse", out
    assert _holder(ws) == holder, (
        "a LIVE pulse's lock was cleared; the caller owning the runs must not "
        "also take the lock")
    assert out["runs_finalized"] == ["RUN-001"], out
    assert _status_of(ws, "RUN-001") == "failed", out


def test_l6_a_row_that_cannot_be_closed_is_reported_rather_than_swallowed(
        cleanup, tmp_path, monkeypatch):
    """One bad row never stops the rest, and never disappears either.

    `cli/pulse.py` printed a `warn` line for a row it could not close. The
    shared copy caught the exception and moved on with nothing said, so
    delegating without this would make a silent failure out of a loud one.
    """
    import socket

    ws = _workspace(tmp_path)
    _hold(ws, f"{socket.gethostname()}:424242:deadbeef")
    monkeypatch.setattr(cleanup, "_pid_alive", lambda pid: False)

    import firm.hooks.run_record as run_record

    def _boom(*args, **kwargs):
        raise RuntimeError("records are unwritable")

    monkeypatch.setattr(run_record, "on_run_end", _boom)

    out = cleanup.release_and_finalize(ws, FIRM, by="firm pulse --abort")

    assert out["runs_finalized"] == [], out
    assert out.get("runs_not_finalized"), (
        "a member_run that could not be closed left no trace in the result, so "
        "the caller reports a clean cleanup over a row still marked running")
    assert "RUN-001" in json.dumps(out["runs_not_finalized"])


def test_l6_abort_carries_an_unclosed_row_in_its_result_not_on_its_own_line(
        cleanup, tmp_path, monkeypatch, capsys):
    """A row that could not be closed reaches the reader, and does not displace
    the reader's answer.

    The private finalizer printed a `warn` line of its own. Moving the same
    information into `_handle_abort` as a print made
    `test_every_way_out_of_the_pulse_goes_through_the_exit_function` fail, which
    is that guard doing its job: every reader of `firm pulse` takes the LAST
    stdout line as the result (#128), so a second line printed beside it is a
    reader taking a warning for an outcome.

    So the fact travels inside the result. This arm pins both halves: it is
    there, and there is still exactly one line.
    """
    import socket

    from firm.cli import pulse as cli_pulse

    ws = _workspace(tmp_path)
    _hold(ws, f"{socket.gethostname()}:424242:deadbeef")
    monkeypatch.setattr(cli_pulse, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(
        cleanup, "release_and_finalize",
        lambda *a, **k: {"lock": "cleared", "runs_finalized": [],
                         "runs_not_finalized": [
                             {"run_id": "RUN-001", "error": "unwritable"}]})

    rc = cli_pulse._handle_abort(ws, FIRM)
    lines = [ln for ln in capsys.readouterr().out.strip().splitlines() if ln]

    assert len(lines) == 1, (
        f"abort printed {len(lines)} lines; a reader taking the last one no "
        f"longer gets the result: {lines}")
    out = json.loads(lines[0])
    assert out["runs_not_finalized"] == [
        {"run_id": "RUN-001", "error": "unwritable"}], out
    assert out["lock"] == "stale-cleared", out
    assert rc == 0


def test_l7_a_remote_database_is_not_a_missing_one(cleanup, tmp_path,
                                                   monkeypatch):
    """avocet's FINDING 5, and my abort refactor is what would ship it.

    This module decides there is no database from the local .firm/firm.db file
    alone. Four sites in src already pair that file check with
    `db_is_remote()` before concluding anything -- including `cli/pulse.py`
    inside `_handle_abort` itself. avocet measured the gap: the same empty
    workspace with CADRE_DB_URL unset and then set returns the IDENTICAL
    answer, `lock: no-db`, so the flag changes nothing.

    On its own that was a gap in `heartbeat disable`. The moment `--abort`
    delegates here it becomes a REGRESSION IN ABORT: abort passes its own
    two-part guard on a shared database, hands over, and is told there is no
    database at all -- so a firm on a shared database gets its lock left
    wedged and is told it has no firm.

    The assertion is `is not no-db` rather than a specific outcome on purpose.
    What this leg owns is that a REMOTE database and a MISSING one stop being
    the same answer; what happens next belongs to whatever the connection
    does, and a test that pinned a fabricated remote result would be measuring
    its own stub.
    """
    monkeypatch.setenv("CADRE_DB_URL", "libsql://example.invalid")

    from firm.core.db import db_is_remote

    assert db_is_remote(), (
        "precondition: the flag did not move, so this arm proves nothing")

    out = cleanup.release_and_finalize(tmp_path / "no-local-file", FIRM,
                                       by="firm pulse --abort")

    assert out["lock"] != "no-db", (
        "a firm on a shared remote database was told it has no database, so "
        "its pulse lock is left wedged for the full TTL")


def test_l6_both_verbs_finalize_through_the_same_function(
        cleanup, tmp_path, monkeypatch, capsys):
    """One function, two callers, counted on one object (osprey, 18:51).

    The two legs above prove each verb reaches `release_and_finalize`. Neither
    proves it is the SAME one: two callers each reaching a function of their
    own would pass both and still be two rules, which is the whole thing this
    refactor exists to stop.

    So a single spy is installed on the module attribute and BOTH CLI verbs
    are driven through their own entry points. `cli/heartbeat.py` imports the
    name inside the function body, at call time, which is why one patch
    catches both.
    """
    import socket

    from firm.cli import heartbeat as cli_heartbeat
    from firm.cli import pulse as cli_pulse

    ws = _workspace(tmp_path)
    _hold(ws, f"{socket.gethostname()}:424242:deadbeef")
    monkeypatch.setattr(cli_pulse, "_pid_alive", lambda pid: False)

    callers: list[str] = []

    def _spy(workspace, firm_id=None, *, by, **kwargs):
        callers.append(by)
        return {"lock": "cleared", "runs_finalized": []}

    monkeypatch.setattr(cleanup, "release_and_finalize", _spy)

    class _Sched:
        name = "systemd"

        def __init__(self) -> None:
            self.gone: set[str] = set()

        def status(self, stem):
            # D3 asks the scheduler AGAIN after remove(), so a fake that
            # answers "installed" forever reports a removal that did not
            # happen. This models the half it always claimed to.
            return {"installed": stem not in self.gone,
                    "state": "active", "workdir": str(ws)}

        def remove(self, stem):
            self.gone.add(stem)
            return {"removed": [stem]}

    monkeypatch.setattr(cli_heartbeat, "_sched", lambda unit_dir=None: _Sched())

    cli_pulse._handle_abort(ws, FIRM)
    cli_heartbeat.run_disable(FIRM, unit_dir=tmp_path / "units")
    capsys.readouterr()

    assert sorted(callers) == ["firm pulse --abort", "heartbeat disable"], (
        f"the two verbs did not both land on this one function: {callers}")


def _workspace_with_two_runs(tmp_path):
    """A firm with RUN-001 and RUN-002 both still marked running.

    avocet's pair needs two: one row that closes and one that will not. A
    single-row arm can prove the failure is REPORTED but not that the rest of
    the work still happened, and "one bad row never stops the rest" is half
    the rule.
    """
    from firm.core.db import connect, get_db_path

    db = get_db_path(tmp_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    apply_migrations(conn)
    _seed(conn, running=("RUN-001", "RUN-002"))
    conn.commit()
    conn.close()
    return tmp_path


def test_l7_one_bad_row_is_named_and_the_rest_still_close(cleanup, tmp_path,
                                                          monkeypatch):
    """avocet's FINDING 7 pair, both halves on one arm.

    Measured on the graded head: the control closed RUN-001 and RUN-002 and
    both read `failed`; the arm where the second row raises closed RUN-001,
    left RUN-002 reading `running`, and said NOTHING about it. The payload of
    a firm with one stuck run looked exactly like the payload of a firm that
    only ever had one orphan.

    Two properties, and neither is worth having alone: the good row still
    closes, AND the bad one is named with its reason.
    """
    import socket

    ws = _workspace_with_two_runs(tmp_path)
    _hold(ws, f"{socket.gethostname()}:424242:deadbeef")
    monkeypatch.setattr(cleanup, "_pid_alive", lambda pid: False)

    import firm.hooks.run_record as run_record

    real = run_record.on_run_end

    def _one_bad(conn, *, firm_id, run_id, **kwargs):
        if run_id == "RUN-002":
            raise RuntimeError("records are unwritable for this row")
        return real(conn, firm_id=firm_id, run_id=run_id, **kwargs)

    monkeypatch.setattr(run_record, "on_run_end", _one_bad)

    out = cleanup.release_and_finalize(ws, FIRM, by="heartbeat disable")

    assert out["runs_finalized"] == ["RUN-001"], out
    assert _status_of(ws, "RUN-001") == "failed"
    assert _status_of(ws, "RUN-002") == "running", (
        "precondition: the arm did not actually leave a row stuck")
    named = json.dumps(out.get("runs_not_finalized") or [])
    assert "RUN-002" in named, (
        "a run left stuck at `running` is invisible to whoever ran the verb; "
        f"the payload reads exactly like a firm with one orphan: {out}")
    assert "unwritable" in named, (
        "the row is named with no reason, so the operator knows something "
        "failed and nothing about what to do")


def test_l7_heartbeat_disable_carries_the_unclosed_row_to_the_operator(
        cleanup, tmp_path, monkeypatch, capsys):
    """The surface. `heartbeat disable` prints the whole cleanup dict.

    It therefore carries `runs_not_finalized` for free -- and "for free" is
    exactly why this leg exists. The key reached disable's payload as a side
    effect of the abort refactor rather than as a property anybody pinned, and
    a property nothing holds is a property that leaves on the next tidy-up
    (avocet, 19:00).
    """
    from firm.cli import heartbeat as cli_heartbeat

    ws = _workspace_with_two_runs(tmp_path)

    monkeypatch.setattr(
        cleanup, "release_and_finalize",
        lambda *a, **k: {"lock": "cleared", "runs_finalized": ["RUN-001"],
                         "runs_not_finalized": [
                             {"run_id": "RUN-002", "error": "unwritable"}]})

    class _Sched:
        name = "systemd"

        def __init__(self) -> None:
            self.gone: set[str] = set()

        def status(self, stem):
            # D3 asks the scheduler AGAIN after remove(), so a fake that
            # answers "installed" forever reports a removal that did not
            # happen. This models the half it always claimed to.
            return {"installed": stem not in self.gone,
                    "state": "active", "workdir": str(ws)}

        def remove(self, stem):
            self.gone.add(stem)
            return {"removed": [stem]}

    monkeypatch.setattr(cli_heartbeat, "_sched", lambda unit_dir=None: _Sched())

    rc = cli_heartbeat.run_disable(FIRM, unit_dir=tmp_path / "units")
    # heartbeat._emit pretty-prints with indent=2, so the payload spans
    # several lines and the LAST line is a closing brace. Parsing the
    # whole of stdout is the only reading that matches what it writes.
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["cleanup"]["runs_not_finalized"] == [
        {"run_id": "RUN-002", "error": "unwritable"}], payload
