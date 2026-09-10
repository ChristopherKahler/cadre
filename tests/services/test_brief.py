"""The Member briefing — the thing the manifest could not render.

The landed design put this in `hooks.session_start.queries` plus an `inject`
template. Measured on base 0.15.0: `inject` does not put query results into its
text, so that briefing renders a template with holes in it. Issue #6. This is
the command code that replaces it.

Both directions are held on every claim. A briefing that can only be checked for
"does it print the Units" passes while printing somebody else's Units, printing
closed ones, or printing a wall of duplicated context on every single run.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from firm.core import repo
from firm.core.db import get_db_path
from firm.core.migrate import apply_migrations
from firm.services import brief

FIRM = "zqfirm"
OTHER = "zqother"


@pytest.fixture
def conn(tmp_path):
    db = get_db_path(tmp_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    apply_migrations(c)
    repo.create(c, "firm", {"id": FIRM, "name": "Test Firm"})
    repo.create(c, "member", {"id": "MEM-001", "firm_id": FIRM,
                              "name": "Vantage", "role": "Chief of Staff"})
    repo.create(c, "member", {"id": "MEM-002", "firm_id": FIRM,
                              "name": "Ledger", "role": "Controller"})
    # unit.project_id is NOT NULL, so a Unit cannot exist without one.
    repo.create(c, "operation", {"id": "OPS-1", "firm_id": FIRM, "name": "Ops"})
    repo.create(c, "project", {"id": "PRJ-1", "firm_id": FIRM, "operation_id": "OPS-1",
                               "name": "Alpha", "status": "in_progress",
                               "due_date": "2026-12-31"})
    yield c
    c.close()


def _unit(c, unit_id, *, assignee, status="pending", project_id="PRJ-1", **extra):
    repo.create(c, "unit", {"id": unit_id, "firm_id": FIRM, "name": f"Work {unit_id}",
                            "project_id": project_id, "assignee_member_id": assignee,
                            "status": status, **extra})


# ---------------------------------------------------------------------------
# Whose Units, and which
# ---------------------------------------------------------------------------

def test_it_prints_this_members_open_units(conn):
    _unit(conn, "UNIT-001", assignee="MEM-001")
    out = brief.render(conn, FIRM, "MEM-001")
    assert "UNIT-001" in out
    assert "Open Units (1)" in out


def test_it_does_not_print_another_members_units(conn):
    """The control that matters most. A briefing showing a colleague's queue
    invites a Member to do their work, which is the failure the whole assignee
    column exists to prevent."""
    _unit(conn, "UNIT-001", assignee="MEM-001")
    _unit(conn, "UNIT-002", assignee="MEM-002")
    out = brief.render(conn, FIRM, "MEM-001")
    assert "UNIT-001" in out
    assert "UNIT-002" not in out


def _schema_statuses(conn) -> list[str]:
    """The statuses the database actually allows, read off the CHECK constraint.

    Restating them here from memory is how the first version of this file
    invented two that do not exist and missed one that does. A migration adding
    a status must not be able to leave this test quietly wrong.
    """
    import re
    row = conn.execute(
        "select sql from sqlite_master where type='table' and name='unit'").fetchone()
    ddl = row[0]
    match = re.search(r"status\s+TEXT[^,]*?CHECK\s*\(\s*status\s+IN\s*\(([^)]*)\)",
                      ddl, re.IGNORECASE | re.DOTALL)
    assert match, f"no status CHECK constraint found in the unit DDL: {ddl[:300]}"
    found = re.findall(r"'([^']+)'", match.group(1))
    assert found, "the CHECK constraint parsed to zero statuses"
    return found


def test_the_schema_statuses_are_what_this_file_thinks_they_are(conn):
    """Anchors every case below on the database rather than on my memory."""
    statuses = _schema_statuses(conn)
    assert set(brief.FINISHED_STATUSES) <= set(statuses), (
        f"brief treats {brief.FINISHED_STATUSES} as finished, but the schema "
        f"only allows {statuses}")
    assert len(statuses) >= 4, statuses


def test_it_does_not_print_finished_units(conn):
    checked = 0
    for status in brief.FINISHED_STATUSES:
        conn.execute("delete from unit")
        _unit(conn, "UNIT-001", assignee="MEM-001", status=status)
        out = brief.render(conn, FIRM, "MEM-001")
        assert "UNIT-001" not in out, f"a {status} Unit was printed"
        assert "none assigned to you" in out
        checked += 1
    assert checked == len(brief.FINISHED_STATUSES) == 2, f"visited {checked}"


def test_every_unfinished_status_counts_as_open(conn):
    """The control for the test above. Without it, a briefing that showed
    nothing at all would pass every 'does not print' case.

    Driven off the schema, so a status added by a migration is covered the day
    it lands rather than the day somebody remembers this file.
    """
    working = [s for s in _schema_statuses(conn) if s not in brief.FINISHED_STATUSES]
    assert working, "no working statuses to check — this test would prove nothing"
    for status in working:
        conn.execute("delete from unit")
        _unit(conn, "UNIT-001", assignee="MEM-001", status=status)
        out = brief.render(conn, FIRM, "MEM-001")
        assert "UNIT-001" in out, f"a {status} Unit was dropped from its own briefing"
        assert status in out
    assert len(working) >= 3, f"visited {len(working)} working statuses: {working}"


def test_an_unassigned_unit_belongs_to_nobody(conn):
    _unit(conn, "UNIT-001", assignee=None)
    out = brief.render(conn, FIRM, "MEM-001")
    assert "UNIT-001" not in out


def test_it_counts_what_it_prints(conn):
    for i in range(1, 4):
        _unit(conn, f"UNIT-00{i}", assignee="MEM-001")
    out = brief.render(conn, FIRM, "MEM-001")
    assert "Open Units (3)" in out
    printed = sum(1 for line in out.splitlines() if line.strip().startswith("UNIT-"))
    assert printed == 3, f"header says 3, body printed {printed}"


# ---------------------------------------------------------------------------
# Absent is not empty
# ---------------------------------------------------------------------------

def test_no_units_says_so_plainly(conn):
    out = brief.render(conn, FIRM, "MEM-001")
    assert "none assigned to you" in out
    assert "Do not start work nobody asked for" in out


def test_an_unknown_member_is_a_wiring_fault_not_an_empty_queue(conn):
    """"You have no work" and "I could not find you" need different actions
    from whoever reads them, so they must not print the same."""
    out = brief.render(conn, FIRM, "MEM-404")
    assert "not a Member" in out
    assert "CADRE_MEMBER_ID" in out
    assert "none assigned to you" not in out


def test_no_member_id_briefs_nobody(conn):
    """A Board session has no CADRE_MEMBER_ID. Inventing a Member for it would
    put somebody else's work on the Board's screen."""
    assert brief.render(conn, FIRM, None) == ""


# ---------------------------------------------------------------------------
# What it deliberately leaves out
# ---------------------------------------------------------------------------

def test_it_does_not_repeat_the_rules_the_domain_layer_already_injects(conn):
    """A Member's own prompt is about 6 KB and hooks add 12 to 14 KB before any
    work. A second copy of the firm's rules is a cost paid on every run."""
    _unit(conn, "UNIT-001", assignee="MEM-001")
    out = brief.render(conn, FIRM, "MEM-001")
    assert "are not repeated here" in out
    assert f"[DOMAIN: {FIRM}]" in out, "it must still say where they come from"


def test_the_briefing_stays_small(conn):
    """Ten Units with long text must not produce a wall. The budget is the
    reason this command exists rather than a bigger injection."""
    for i in range(1, 11):
        _unit(conn, f"UNIT-{i:03d}", assignee="MEM-001",
              name="x" * 400, acceptance_criteria="y" * 400)
    out = brief.render(conn, FIRM, "MEM-001")
    assert len(out) < 4000, f"briefing is {len(out)} bytes for 10 Units"
    assert "…" in out, "long text must be clipped, not dropped"


def test_it_names_the_route_for_closing_a_unit(conn):
    """The route moved, and the reason is a deadlock rather than a preference.

    This asserted `firm unit complete` and `base learn`. Both are real commands
    and both still work — but neither writes the write-back marker, and the
    Stop gate now blocks a session that closed a Unit without one. A Member
    following the old briefing to the letter would close its Unit, write back
    exactly as instructed, succeed at both, and still be blocked with nothing
    on screen naming the command that releases it.

    So the briefing names the two verbs that feed the gate. They are read from
    the same constants the gate's own message is rendered from, and
    tests/services/test_writeback.py compares the two RENDERED surfaces so they
    cannot drift apart again.
    """
    from firm.services.writeback import CLOSE_VERB, WRITE_BACK_VERB

    _unit(conn, "UNIT-001", assignee="MEM-001")
    out = brief.render(conn, FIRM, "MEM-001")
    assert CLOSE_VERB in out
    assert WRITE_BACK_VERB in out
    assert "firm unit create" in out, "queueing follow-up work is unchanged"
    assert "base learn --domain" not in out, (
        "the old write-back route records a note and writes no marker, so it "
        "reads as success and still deadlocks the session")


def test_it_teaches_no_tool_that_does_not_exist(conn):
    """Six of twelve firms load no firm MCP server, so these resolve to nothing
    for half the fleet. Teaching them is issue #2."""
    _unit(conn, "UNIT-001", assignee="MEM-001")
    out = brief.render(conn, FIRM, "MEM-001")
    for absent in ("unit_create", "firm_escalate", "firm_request_gate"):
        assert absent not in out


def test_it_shows_acceptance_criteria_when_there_are_some(conn):
    _unit(conn, "UNIT-001", assignee="MEM-001",
          acceptance_criteria="the manifest validates and installs")
    out = brief.render(conn, FIRM, "MEM-001")
    assert "done when: the manifest validates and installs" in out


def test_it_lists_the_projects_the_units_belong_to(conn):
    _unit(conn, "UNIT-001", assignee="MEM-001", project_id="PRJ-1")
    out = brief.render(conn, FIRM, "MEM-001")
    assert "Projects: PRJ-1" in out


def test_it_lists_each_project_once_however_many_units(conn):
    repo.create(conn, "project", {"id": "PRJ-2", "firm_id": FIRM, "operation_id": "OPS-1",
                                  "name": "Beta", "status": "in_progress",
                                  "due_date": "2026-12-31"})
    for i in (1, 2, 3):
        _unit(conn, f"UNIT-00{i}", assignee="MEM-001", project_id="PRJ-1")
    _unit(conn, "UNIT-004", assignee="MEM-001", project_id="PRJ-2")
    out = brief.render(conn, FIRM, "MEM-001")
    line = [l for l in out.splitlines() if l.startswith("Projects:")][0]
    assert line.count("PRJ-1") == 1, line
    assert line.count("PRJ-2") == 1, line


# ---------------------------------------------------------------------------
# resolve_member_id
# ---------------------------------------------------------------------------

def test_an_explicit_member_beats_the_environment(conn, monkeypatch):
    monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-002")
    assert brief.resolve_member_id(conn, "MEM-001") == "MEM-001"


def test_the_environment_is_used_when_nothing_is_passed(conn, monkeypatch):
    monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-002")
    assert brief.resolve_member_id(conn) == "MEM-002"


def test_an_empty_environment_variable_is_not_a_member(conn, monkeypatch):
    """An empty string is 'set'. Treating it as a member id would look up ''."""
    monkeypatch.setenv("CADRE_MEMBER_ID", "   ")
    assert brief.resolve_member_id(conn) is None


def test_no_environment_and_no_argument_is_nobody(conn, monkeypatch):
    monkeypatch.delenv("CADRE_MEMBER_ID", raising=False)
    assert brief.resolve_member_id(conn) is None


# ---------------------------------------------------------------------------
# run_brief — the surface a hook calls
# ---------------------------------------------------------------------------

def test_run_brief_prints_and_returns_zero(tmp_path, conn, capsys, monkeypatch):
    _unit(conn, "UNIT-001", assignee="MEM-001")
    conn.commit()
    monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-001")
    rc = brief.run_brief(tmp_path)
    assert rc == 0
    assert "UNIT-001" in capsys.readouterr().out


def test_run_brief_is_silent_and_zero_outside_a_firm(tmp_path, capsys):
    """A SessionStart hook that fails a session start over a missing briefing is
    worse than one that says nothing."""
    rc = brief.run_brief(tmp_path / "not-a-firm")
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_run_brief_survives_a_broken_database(tmp_path, capsys):
    db = get_db_path(tmp_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"this is not a database")
    assert brief.run_brief(tmp_path) == 0
    assert capsys.readouterr().out == ""
