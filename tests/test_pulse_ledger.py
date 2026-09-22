"""The pulse ledger: one row per pulse PROCESS, in migration 016 (#128 D3).

A firm's own record that it pulsed. ``.firm/last-pulse.json`` is written by
exactly one launcher -- the hub's ``_fire_pulse`` (``dashboard/server.py``) --
so a firm whose pulses come from its timer has no record at all, and
``heartbeat status`` shows either nothing or a stale Board pulse. The ledger is
that record, and it is the surface the doctor's gap card and ``last_pulse``
read.

What the verdict ruled, and what these legs are written against (osprey,
2026-09-14, items 3, 4 and 5 of the G0 verdict):

* the source label is a ``--source`` FLAG, never an environment variable: a
  variable is inherited by every Member run and by every Board pulse the hub
  dispatches, so a label there describes whoever set it, not the pulse reading
  it. A pulse with no flag records ``unset``, and ``unset`` is not an accepted
  value, so absence cannot be faked.
* a missing table is a JSON NOTE, never a migration. The write is best effort;
  the pulse runs, exits on its own outcome, and its JSON carries
  ``"pulse_run": "not recorded: <reason>"``. Migrations are applied by ``init``
  and ``doctor --fix`` and by nothing else.
* the interval comes from ``firm.pulse_interval`` (#134, migration 015), never
  from ``firm.schedule``, which is business hours.

THE CHANNEL. Every leg that drives a pulse runs ``python -m firm pulse`` as a
CHILD PROCESS and reads the database afterwards, because that is the channel a
scheduler and the hub read (law 42): a call to ``run_pulse`` in this process
would skip argparse, ``main()`` and ``sys.exit``. The child harness is
``tests/test_pulse_exit_contract.py``'s, imported rather than copied -- it
carries the ``PYTHONPATH`` rule that keeps the child importing this tree, and a
second copy of that is a second rule (#141).

TWO OBSERVERS, AND THE SECOND IS NOT THE PROCESS. Every leg below asserts on
the DATABASE, which no amount of correct-looking JSON can fake, and the legs
that have a JSON claim assert that too. The row and the printed line come from
the same exit function, so a change that makes one true and the other false
reddens exactly one of the two (law 42's channel clause).

WHERE THE PLATFORM MARKS COME FROM. A live pulse runs its Member-runtime
preflight, and off Linux ``spawn._is_execable`` rejects this platform's own
interpreter, so no stand-in resolves (``tests/platform_marks.py``). Legs that
need a pulse to get PAST the preflight carry that mark. Legs that only need a
pulse to START -- the ledger row is opened at the top of ``_run_resolved``,
before the preflight -- drive the unresolvable claude conftest installs and run
everywhere. Five of the twelve run on every host.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

import firm.cli.pulse as pulse_cli
from firm.core.db import connect
from firm.services import pulse_ledger, pulse_queue
from tests.platform_marks import spawn_layer_rejects_this_platforms_binaries
from tests.test_pulse_exit_contract import (
    FIRM,
    REFUSING_MEMBER,
    _assert_exit,
    _child_env,
    _firm,
    _hold_lock,
    _pulse,
)

#: The migration that carries the table. 015 is ``015_pulse_interval.sql``
#: (#134, landed as PR #140), measured on main ``2f8545bf``; the G0's own
#: ``015_pulse_run.sql`` is superseded and must not be built under that number.
LEDGER_MIGRATION = "016_pulse_run"

#: Every label a launch site may pass. ``unset`` is deliberately NOT here: it
#: is what a pulse with no flag records, and accepting it as a value would let
#: a caller fake the absence.
SOURCES = ("heartbeat", "board", "cli", "queue")

#: A pulse row that has not been closed yet.
OPEN_ROW = "ended_at IS NULL"


# ---------------------------------------------------------------------------
# Reading the ledger
# ---------------------------------------------------------------------------

def _ledger(ws: Path) -> list[dict]:
    """Every ``pulse_run`` row at *ws*, oldest first, as dicts."""
    conn = connect(ws / ".firm" / "firm.db")
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM pulse_run ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _one_row(ws: Path) -> dict:
    rows = _ledger(ws)
    assert len(rows) == 1, f"expected exactly one pulse_run row, got {rows}"
    return rows[0]


def _drop_the_ledger_table(ws: Path) -> None:
    """A firm upgraded without ``init`` or ``doctor --fix``: migration 016 is
    not applied and the table is not there. Produced by removing both rather
    than by migrating to 015 only, so the ``_migrations`` bookkeeping and the
    schema say the same thing -- a database where one said 016 was applied and
    the other had no table would be a state no upgrade path can reach."""
    conn = connect(ws / ".firm" / "firm.db")
    try:
        conn.execute("DROP TABLE IF EXISTS pulse_run")
        conn.execute("DELETE FROM _migrations WHERE name = ?",
                     (LEDGER_MIGRATION,))
        conn.commit()
    finally:
        conn.close()
    assert not _table_exists(ws, "pulse_run"), "precondition: the table is gone"


def _table_exists(ws: Path, name: str) -> bool:
    conn = connect(ws / ".firm" / "firm.db")
    try:
        return bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,)).fetchone())
    finally:
        conn.close()


def _applied(ws: Path) -> list[str]:
    conn = connect(ws / ".firm" / "firm.db")
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM _migrations ORDER BY name").fetchall()]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# L1 · the row's outcome is the pulse's own verdict, from the same exit
# ═══════════════════════════════════════════════════════════════════════════

@spawn_layer_rejects_this_platforms_binaries
def test_L1a_a_pulse_that_worked_leaves_one_closed_row_saying_ok(tmp_path):
    """The counts on the row are the counts in the JSON, and the row is closed.

    `outcome` and the exit code come from ``_exit_with`` or they can disagree:
    that is the whole reason the close-out lives in the exit function rather
    than beside the pulse's own return.
    """
    ws = _firm(tmp_path / "ws")

    run = _pulse(ws, claude=REFUSING_MEMBER)

    result = _assert_exit(run, rc=0, ok=True)
    row = _one_row(ws)
    assert row["outcome"] == "ok", run.output
    assert row["ok"] == 1, run.output
    assert row["ended_at"] is not None, "a finished pulse leaves a closed row"
    assert row["started_at"] is not None
    assert row["firm_id"] == FIRM
    assert (row["ran"], row["errors"], row["skipped"]) == (
        result["ran"], result["errors"], result["skipped"]), run.output
    assert result["pulse_run"] == row["id"], (
        "the JSON names the row it wrote", run.output)


def test_L1b_a_pulse_that_ended_on_a_branch_records_that_branchs_reason(tmp_path):
    """Not every pulse reaches the cycle. A pulse whose Member runtime is not
    wired ends at its preflight, and its row must carry THAT verdict -- which
    is the reason ``outcome`` is written where the exit code is written, and
    not from the result of a cycle that never ran."""
    ws = _firm(tmp_path / "ws")      # conftest's claude does not resolve

    run = _pulse(ws)

    result = _assert_exit(run, rc=1, ok=False, reason="runtime-not-wired")
    row = _one_row(ws)
    assert row["outcome"] == "runtime-not-wired", run.output
    assert row["outcome"] == result["reason"], (
        "the row says what the printed line says", run.output)
    assert row["ok"] == 0, run.output
    assert row["ended_at"] is not None, run.output
    assert result["pulse_run"] == row["id"], run.output


# ═══════════════════════════════════════════════════════════════════════════
# L2 · a killed pulse leaves its row open, and the next pulse closes it
# ═══════════════════════════════════════════════════════════════════════════

def _wedge_a_pulse(ws: Path, timeout: float = 60.0) -> subprocess.Popen:
    """A real pulse process, started and then stuck, with its row open.

    The wedge is the pulse lock held from ANOTHER host with a fresh heartbeat:
    ``--drain-queue`` claims its request and then waits for that lock rather
    than failing on it (``cli/pulse.py::_drain_queue``), so the process sits
    in a sleep with its ledger row open and nothing of its own to finish. No
    Member is ever spawned, so nothing here depends on a stand-in completing.
    """
    conn = connect(ws / ".firm" / "firm.db")
    try:
        pulse_queue.request_pulse(conn, FIRM, requested_by="board")
        conn.commit()
    finally:
        conn.close()
    _hold_lock(ws, "other-host-t128b:1:wedge")

    child = subprocess.Popen(
        [sys.executable, "-m", "firm", "pulse", "--workspace", str(ws),
         "--drain-queue"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, env=_child_env(REFUSING_MEMBER))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            out = child.communicate()
            raise AssertionError(
                f"the pulse exited instead of wedging: {out!r}")
        rows = [r for r in _ledger(ws) if r["ended_at"] is None]
        if rows and rows[0]["holder"].split(":")[1] == str(child.pid):
            return child
        time.sleep(0.2)
    child.kill()
    raise AssertionError("no open pulse_run row appeared for the wedged pulse")


@spawn_layer_rejects_this_platforms_binaries
def test_L2_a_killed_pulse_leaves_its_row_open_and_the_next_one_closes_it(
        tmp_path):
    """`unclosed`, not `died`.

    The D3 design said `died` (brief lines 300, 316 and 379) and osprey's
    verdict overruled it in those words at line 563: a pulse whose CLOSING
    WRITE failed leaves the identical open row with the identical dead pid, so
    `died` would name a crash nobody read. `unclosed` says exactly what was
    measured -- nobody closed this row -- and says nothing about why.
    """
    ws = _firm(tmp_path / "ws")
    child = _wedge_a_pulse(ws)
    child.kill()             # no handler runs: nothing of the pulse's closes
    child.wait(timeout=60)   # reaped, so the pid is dead rather than a zombie
    assert not pulse_cli._pid_alive(child.pid), (
        "precondition: the killed pulse's pid must be dead")

    killed = _one_row(ws)
    assert killed["ended_at"] is None, (
        "a pulse that never reached its exit cannot have closed its own row")
    assert killed["outcome"] is None

    # The lock its wedge used is released, so the next pulse can run.
    conn = connect(ws / ".firm" / "firm.db")
    try:
        conn.execute("DELETE FROM pulse_lock WHERE firm_id = ?", (FIRM,))
        conn.commit()
    finally:
        conn.close()

    run = _pulse(ws, claude=REFUSING_MEMBER)

    _assert_exit(run, rc=0, ok=True)
    rows = {r["id"]: r for r in _ledger(ws)}
    closed = rows[killed["id"]]
    assert closed["outcome"] == "unclosed", run.output
    assert closed["ended_at"] is not None, (
        "a row closed out still has to be closed", run.output)
    assert len(rows) == 2, ("the closing pulse writes its own row too",
                            run.output)


# ═══════════════════════════════════════════════════════════════════════════
# L3 · the close-out leaves another machine's row alone
# ═══════════════════════════════════════════════════════════════════════════

def _open_row_held_by(ws: Path, holder: str, *, source: str = "cli") -> int:
    conn = connect(ws / ".firm" / "firm.db")
    try:
        cur = conn.execute(
            "INSERT INTO pulse_run (firm_id, started_at, source, holder)"
            " VALUES (?, ?, ?, ?)",
            (FIRM, "2026-09-21T00:00:00+00:00", source, holder))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def test_L3_a_row_held_from_another_host_is_left_alone(tmp_path):
    """The holder is ``host:pid:nonce`` BECAUSE the database can be shared
    across machines (``pulse/dblock.py``, ``core/db.py``). A close-out that
    reads the pid and not the host declares a pulse on another machine dead on
    the strength of a pid in its OWN process table -- which is a different
    machine's process, or nothing at all."""
    ws = _firm(tmp_path / "ws")
    # pid 1 is alive on every host, so a close-out that ignores the host and
    # reads the pid would ALSO leave this row open and the leg would pass while
    # proving nothing. The pid is one that cannot be alive here.
    elsewhere = _open_row_held_by(ws, "other-host-t128b:2147480000:remote")

    run = _pulse(ws)

    row = {r["id"]: r for r in _ledger(ws)}[elsewhere]
    assert row["ended_at"] is None, (
        "a row held from another machine is that machine's to close", run.output)
    assert row["outcome"] is None, run.output


# ═══════════════════════════════════════════════════════════════════════════
# L4 · a dry run leaves no trace
# ═══════════════════════════════════════════════════════════════════════════

def test_L4_a_dry_run_writes_no_row_and_claims_none(tmp_path):
    """``cli/pulse.py`` already holds this rule for the exports and for the
    policy ingest: a dry run is read-only by contract. A ledger row is a
    write."""
    ws = _firm(tmp_path / "ws")

    run = _pulse(ws, "--dry-run")

    result = _assert_exit(run, rc=0, ok=True, dry_run=True)
    assert _ledger(ws) == [], run.output
    assert "pulse_run" not in result, (
        "absent, not 'not recorded': a dry run did not fail to record, it "
        "recorded nothing on purpose", run.output)


# ═══════════════════════════════════════════════════════════════════════════
# L5 · --source records the label; no flag records `unset`
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("source", SOURCES)
def test_L5a_the_flag_is_what_the_row_records(tmp_path, source):
    ws = _firm(tmp_path / "ws")

    run = _pulse(ws, "--source", source)

    assert _one_row(ws)["source"] == source, run.output


def test_L5b_a_pulse_with_no_flag_records_unset(tmp_path):
    """Every timer installed before this change has no flag in its stored
    argv, and a pulse that cannot say where it came from must say exactly
    that. ``cli`` would be a guess, and a wrong one for every one of them
    (law 7)."""
    ws = _firm(tmp_path / "ws")

    run = _pulse(ws)

    assert _one_row(ws)["source"] == "unset", run.output


# ═══════════════════════════════════════════════════════════════════════════
# L6 · `unset` cannot be passed
# ═══════════════════════════════════════════════════════════════════════════

def test_L6_unset_is_not_an_accepted_source(tmp_path):
    """Absence cannot be faked. If ``unset`` were in ``choices`` a caller
    could write it deliberately and the column would no longer separate "this
    pulse did not say" from "this pulse said it was not any of them"."""
    ws = _firm(tmp_path / "ws")

    proc = subprocess.run(
        [sys.executable, "-m", "firm", "pulse", "--workspace", str(ws),
         "--source", "unset"],
        capture_output=True, env=_child_env(), timeout=120,
        stdin=subprocess.DEVNULL)

    detail = (f"rc {proc.returncode}\n{proc.stdout.decode('utf-8', 'replace')}"
              f"\n{proc.stderr.decode('utf-8', 'replace')}")
    assert proc.returncode == 2, ("argparse rejects an invalid choice with a "
                                  "usage error", detail)
    # "invalid choice", never merely "unset" in the message: before the flag
    # existed at all, argparse said `unrecognized arguments: --source unset`,
    # which is rc 2 with `unset` in it and proves nothing about choices.
    assert b"invalid choice" in proc.stderr, detail
    assert b"unset" in proc.stderr, detail
    assert "unset" not in _choices(), _choices()
    assert _ledger(ws) == [], ("a pulse the parser refused never started",
                               detail)


# ═══════════════════════════════════════════════════════════════════════════
# L7 · the drain path records `queue` with no flag passed
# ═══════════════════════════════════════════════════════════════════════════

@spawn_layer_rejects_this_platforms_binaries
def test_L7_draining_the_queue_records_queue(tmp_path):
    """``--drain-queue`` is its own entry point and nobody passes it a source:
    the flag would have to be added to the hub's argv and to the systemd
    wrapper to say what the option itself already says."""
    ws = _firm(tmp_path / "ws")
    conn = connect(ws / ".firm" / "firm.db")
    try:
        pulse_queue.request_pulse(conn, FIRM, requested_by="board")
        conn.commit()
    finally:
        conn.close()

    run = _pulse(ws, "--drain-queue", claude=REFUSING_MEMBER)

    _assert_exit(run, rc=0, ok=True, drained=1)
    row = _one_row(ws)
    assert row["source"] == "queue", (
        "one row for the drain PROCESS, source queue", run.output)
    assert row["ended_at"] is not None, run.output


# ═══════════════════════════════════════════════════════════════════════════
# L8 · a missing table is a note, and the pulse's own verdict is untouched
# ═══════════════════════════════════════════════════════════════════════════

@spawn_layer_rejects_this_platforms_binaries
def test_L8_a_missing_table_never_fails_the_pulse(tmp_path):
    """A side record that fails must not turn a pulse that ran into one that
    errored -- the rule the exports already follow (``cli/pulse.py``). The
    pulse exits on its OWN outcome and says in its JSON that nothing was
    recorded, with the reason."""
    ws = _firm(tmp_path / "ws")
    _drop_the_ledger_table(ws)

    run = _pulse(ws, "--source", "cli", claude=REFUSING_MEMBER)

    result = _assert_exit(run, rc=0, ok=True)
    note = result.get("pulse_run")
    assert isinstance(note, str) and note.startswith("not recorded: "), (
        "three states, three shapes: a row id, a note, or absent on a dry "
        "run (law 7)", run.output)
    assert "pulse_run" in note or "016" in note, (
        "the note names what is missing, so a reader can act on it", run.output)
    assert "Traceback" not in run.output, run.output


# ═══════════════════════════════════════════════════════════════════════════
# L9 · the pulse applies no migration, ever
# ═══════════════════════════════════════════════════════════════════════════

def test_L9_the_pulse_never_applies_a_migration(tmp_path):
    """The pair L8 exists with. A ledger that helpfully created its own table
    would make L8 green by removing the condition L8 is about -- and it would
    change the schema under every other machine on a shared database, from an
    unattended timer tick, in a process a SIGTERM can stop half way. Migrations
    are ``init``'s and ``doctor --fix``'s (``core/migrate.py`` is called from
    those two and from nowhere else in the pulse path)."""
    ws = _firm(tmp_path / "ws")
    _drop_the_ledger_table(ws)
    before = _applied(ws)

    run = _pulse(ws)

    # The pulse still has to SAY it could not record, or this leg passes on a
    # tree where no ledger exists at all and proves nothing (law 45).
    assert run.result is not None, run.output
    note = run.result.get("pulse_run")
    assert isinstance(note, str) and note.startswith("not recorded: "), (
        "a pulse that could not record says so", run.output)
    assert not _table_exists(ws, "pulse_run"), (
        "the pulse created the ledger table it was told never to create",
        run.output)
    assert _applied(ws) == before, (
        "the pulse recorded a migration as applied", run.output)
    assert LEDGER_MIGRATION not in _applied(ws), run.output


# ═══════════════════════════════════════════════════════════════════════════
# L10 · `heartbeat status` reads last_pulse from the ledger
# ═══════════════════════════════════════════════════════════════════════════

def test_L10_last_pulse_comes_from_the_ledger_not_from_the_hubs_file(
        tmp_path, capsys, monkeypatch):
    """``.firm/last-pulse.json`` is written by the hub's ``_fire_pulse`` and by
    nothing else, so a firm that pulses only on its timer has no file and reads
    as a firm that has never pulsed. Its ledger says otherwise."""
    import firm.cli.heartbeat as hb
    import firm.sched.systemd as sysd

    ws = _firm(tmp_path / "ws")
    assert not (ws / ".firm" / "last-pulse.json").exists(), (
        "precondition: a timer-only firm has no hub file")
    _open_row_held_by(ws, "this-host:1:older", source="heartbeat")
    conn = connect(ws / ".firm" / "firm.db")
    try:
        conn.execute(
            "INSERT INTO pulse_run (firm_id, started_at, ended_at, source,"
            " holder, outcome, ok) VALUES (?, ?, ?, 'heartbeat', ?, 'ok', 1)",
            (FIRM, "2026-09-21T11:30:00+00:00", "2026-09-21T11:31:00+00:00",
             "this-host:2:newest"))
        conn.commit()
    finally:
        conn.close()

    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / f"cadre-heartbeat-{FIRM}.timer").write_text(
        "[Timer]\nOnUnitActiveSec=15m\n", encoding="utf-8")
    (unit_dir / f"cadre-heartbeat-{FIRM}.service").write_text(
        f"[Service]\nWorkingDirectory={ws}\n", encoding="utf-8")

    def fake(argv, timeout=30):
        if "is-active" in argv:
            return 0, "active"
        if "is-failed" in argv:
            return 1, "active"
        return 0, ""

    monkeypatch.setattr(sysd, "run_cmd", fake)

    rc = hb.run_status(unit_dir=unit_dir)

    assert rc == 0
    entry = json.loads(capsys.readouterr().out)["heartbeats"][0]
    assert entry["last_pulse"] == "2026-09-21T11:30:00+00:00", (
        "the NEWEST started_at, and the ledger's own value rather than a file "
        "mtime", entry)


def test_L10_control_an_unreadable_ledger_is_not_a_firm_that_never_pulsed(
        tmp_path, capsys, monkeypatch):
    """Law 48 at this surface. A zero is the one result that cannot tell "the
    thing is not there" from "I cannot see", so the two get different keys and
    a reader is never handed the wrong one."""
    import firm.cli.heartbeat as hb
    import firm.sched.systemd as sysd

    ws = _firm(tmp_path / "ws")
    _drop_the_ledger_table(ws)

    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / f"cadre-heartbeat-{FIRM}.timer").write_text(
        "[Timer]\nOnUnitActiveSec=15m\n", encoding="utf-8")
    (unit_dir / f"cadre-heartbeat-{FIRM}.service").write_text(
        f"[Service]\nWorkingDirectory={ws}\n", encoding="utf-8")
    monkeypatch.setattr(sysd, "run_cmd", lambda argv, timeout=30: (0, "active"))

    assert hb.run_status(unit_dir=unit_dir) == 0

    entry = json.loads(capsys.readouterr().out)["heartbeats"][0]
    assert "last_pulse" not in entry, entry
    assert LEDGER_MIGRATION in entry["last_pulse_unavailable"], entry


# ═══════════════════════════════════════════════════════════════════════════
# L11 · doctor names a gap as a gap, and never as a drop
# ═══════════════════════════════════════════════════════════════════════════

def _pulses_at(ws: Path, *starts: str) -> None:
    conn = connect(ws / ".firm" / "firm.db")
    try:
        for started in starts:
            conn.execute(
                "INSERT INTO pulse_run (firm_id, started_at, ended_at, source,"
                " holder, outcome, ok) VALUES (?, ?, ?, 'heartbeat', ?,"
                " 'ok', 1)",
                (FIRM, started, started, f"this-host:1:{started}"))
        conn.execute("UPDATE firm SET pulse_interval = '30m' WHERE id = ?",
                     (FIRM,))
        conn.commit()
    finally:
        conn.close()


def test_L11_the_gap_card_says_no_pulse_started_and_never_dropped(tmp_path):
    """Under Chris's rule a logged-out Windows box does not pulse, and that
    leaves the SAME evidence a real drop leaves: no row. The card may report
    what it can see -- that nothing started between two times -- and may not
    claim to know which of the two happened."""
    from firm.cli import doctor

    ws = _firm(tmp_path / "ws")
    _pulses_at(ws, "2026-09-21T09:00:00+00:00", "2026-09-21T09:30:00+00:00",
               "2026-09-21T14:00:00+00:00")   # four and a half hours, at 30m

    checks = doctor.diagnose(ws, FIRM, unit_dir=tmp_path / "units")

    card = next((c for c in checks if c["key"] == "pulse-gap"), None)
    assert card is not None, [c["key"] for c in checks]
    assert card["ok"] is False, card
    assert "no pulse started between" in card["detail"], card
    assert "09:30" in card["detail"] and "14:00" in card["detail"], (
        "the card names the window it found", card)
    assert "drop" not in card["detail"].lower(), (
        "a gap is what was measured; a drop is a cause nobody read", card)


def test_L11_control_a_firm_pulsing_on_its_interval_has_no_gap(tmp_path):
    """The control that proves the card discriminates rather than always
    firing (law 24): the same code path, a firm whose pulses are on cadence."""
    from firm.cli import doctor

    ws = _firm(tmp_path / "ws")
    _pulses_at(ws, "2026-09-21T09:00:00+00:00", "2026-09-21T09:30:00+00:00",
               "2026-09-21T10:00:00+00:00")

    checks = doctor.diagnose(ws, FIRM, unit_dir=tmp_path / "units")

    card = next((c for c in checks if c["key"] == "pulse-gap"), None)
    assert card is not None, [c["key"] for c in checks]
    assert card["ok"] is True, card


def test_L11_control_a_firm_with_no_ledger_table_reads_not_available(tmp_path):
    """Law 48's half of item 5: "the table is missing" and "no pulses" are
    different claims and must not print the same card. A firm without
    migration 016 has not been measured at all."""
    from firm.cli import doctor

    ws = _firm(tmp_path / "ws")
    _drop_the_ledger_table(ws)

    checks = doctor.diagnose(ws, FIRM, unit_dir=tmp_path / "units")

    card = next((c for c in checks if c["key"] == "pulse-gap"), None)
    assert card is not None, [c["key"] for c in checks]
    assert card["state"] == "undeterminable", card
    assert "not available" in card["detail"], card
    assert LEDGER_MIGRATION.split("_")[0] in card["detail"], (
        "the card names the migration that is missing", card)


# ═══════════════════════════════════════════════════════════════════════════
# L12 · every label the code can pass is a label the parser accepts
# ═══════════════════════════════════════════════════════════════════════════

SRC = Path(pulse_cli.__file__).resolve().parents[1]


def _resolve(node: ast.AST) -> str | None:
    """The string *node* denotes, or None when this scanner cannot read it.

    Two shapes reach a launch site in this codebase, and both are read here
    rather than assumed: a literal (``"--source", "cli"``) and an attribute of
    the ledger module (``"--source", pulse_ledger.BOARD``), which is the shape
    the two real sites use so that the parser, the column and the callers hold
    one list between them.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id.endswith("pulse_ledger")):
        value = getattr(pulse_ledger, node.attr, None)
        return value if isinstance(value, str) else None
    return None


def _labels_passed_in(tree: ast.AST) -> list[tuple[str | None, str]]:
    """Every ``--source`` label handed to a pulse, as (label, how it was written).

    Read from the SOURCE (law 31): the guard's own list cannot supply the set
    of shapes it is meant to cover, because it would be written from the same
    assumption the code was. It reaches a list literal
    ``[..., "--source", "board", ...]`` and an augmented one
    ``argv += ["--source", label]`` alike, since both are list nodes.

    A label this scanner CANNOT read comes back as ``(None, <the source>)``
    rather than being skipped. A skip would make a third shape invisible, and
    an invisible shape is a guard that reports clean because it went blind --
    the one failure direction a guard may not have (law 41).
    """
    found: list[tuple[str | None, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        items = node.elts
        for i, item in enumerate(items[:-1]):
            if isinstance(item, ast.Constant) and item.value == "--source":
                nxt = items[i + 1]
                found.append((_resolve(nxt), ast.unparse(nxt)))
    return found


def _choices() -> tuple[str, ...]:
    from firm.__main__ import _build_parser

    parser = _build_parser()
    pulse_parser = parser._subparsers._group_actions[0].choices["pulse"]
    action = next(a for a in pulse_parser._actions if "--source" in a.option_strings)
    return tuple(action.choices or ())


#: The launch sites this build put in the tree, and the label each passes.
#: One row per site, which is the map row law 31 asks for -- and the guard
#: below fails if the tree grows a site this table does not name.
LAUNCH_SITES = {
    "cli/heartbeat.py": ["heartbeat"],
    "dashboard/server.py": ["board"],
}


def _sites() -> dict[str, list[tuple[str | None, str]]]:
    found: dict[str, list[tuple[str | None, str]]] = {}
    for path in sorted(SRC.rglob("*.py")):
        labels = _labels_passed_in(ast.parse(path.read_text(encoding="utf-8")))
        if labels:
            found[path.relative_to(SRC).as_posix()] = labels
    return found


def test_L12_every_label_a_launch_site_passes_is_in_choices():
    """A site passing a label ``choices`` rejects is a usage error at run time,
    inside a timer, where nobody is reading stderr. The map rows in the build
    record are these sites, and this guard is what keeps the map honest."""
    sites = _sites()

    assert sites, ("scanned every file under src/firm and found no launch "
                   "site passing --source: this guard proved NOTHING (law 23)")
    choices = _choices()
    assert choices, "the pulse parser has no --source choices to check against"

    unreadable = {where: [src for label, src in labels if label is None]
                  for where, labels in sites.items()}
    assert not any(unreadable.values()), (
        "a --source argument this scanner cannot read is a shape the guard "
        "is blind to, and a blind guard reports clean", unreadable)

    bad = {where: [label for label, _src in labels if label not in choices]
           for where, labels in sites.items()}
    assert not any(bad.values()), (bad, choices)
    assert "unset" not in choices, choices

    measured = {where: [label for label, _src in labels]
                for where, labels in sites.items()}
    assert measured == LAUNCH_SITES, (
        "the launch sites in the tree are not the ones this map names; add "
        "the row and the map row in the build record together", measured)


def test_L12_control_the_scanner_reads_both_shapes_and_fails_a_bad_one():
    """The positive control and the must-fail canary, in one run (law 48). A
    scanner that finds nothing and a scanner that is blind print the same
    zero, so the zero is never admissible on its own -- and the control covers
    BOTH shapes the tree uses, because a control that covers one is the blind
    spot this guard already had once."""
    literal = ast.parse('argv = ["-m", "firm", "pulse", "--source", "cli"]')
    attribute = ast.parse('argv += ["--source", pulse_ledger.BOARD]')
    canary = ast.parse('argv = ["--source", "not-a-real-label"]')
    unreadable = ast.parse('argv = ["--source", pick(a, b)]')

    assert _labels_passed_in(literal) == [("cli", "'cli'")]
    assert _labels_passed_in(attribute) == [("board", "pulse_ledger.BOARD")]
    assert _labels_passed_in(unreadable) == [(None, "pick(a, b)")]

    found = _labels_passed_in(canary)
    assert found == [("not-a-real-label", "'not-a-real-label'")]
    assert [l for l, _s in found if l not in _choices()] == ["not-a-real-label"], (
        "the guard must FAIL on a label the parser would reject; a guard "
        "never seen red is decoration that happens to print PASS")
