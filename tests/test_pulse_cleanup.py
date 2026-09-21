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
            return {"installed": True, "state": "ready", "workdir": str(ws)}

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
