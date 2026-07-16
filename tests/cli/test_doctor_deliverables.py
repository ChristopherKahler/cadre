"""Tests for ``firm doctor``'s deliverables check.

The check that would have caught ESC-026 on day one: chief-of-staff closed 26
Units and registered 3 Documents while every other check reported green.

Both directions are held. A check with only must-flag cases passes at 100% while
flagging a healthy firm, and a check that cannot fire is worse than no check —
it reports green over the defect it was written for (ESC-021).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from firm.cli.doctor import diagnose
from firm.core.db import get_db_path
from firm.core.migrate import apply_migrations
from firm.core.repo import create


def _seed(workspace: Path) -> sqlite3.Connection:
    db_path = get_db_path(workspace)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chief-of-staff", "name": "Chief of Staff"})
    create(conn, "member", {"id": "MEM-001", "firm_id": "chief-of-staff",
                            "name": "Cooper", "role": "Ops"})
    create(conn, "operation", {"id": "OPS-001", "firm_id": "chief-of-staff",
                               "name": "Ops"})
    create(conn, "project", {"id": "PRJ-001", "firm_id": "chief-of-staff",
                             "operation_id": "OPS-001", "name": "Inbox",
                             "status": "in_progress", "due_date": "2026-12-31"})
    return conn


def _unit(conn: sqlite3.Connection, unit_id: str, status: str, **extra) -> None:
    create(conn, "unit", {"id": unit_id, "firm_id": "chief-of-staff",
                          "project_id": "PRJ-001", "name": f"Work {unit_id}",
                          "status": status, **extra})


def _card(workspace: Path) -> dict:
    checks = diagnose(workspace, "chief-of-staff")
    return next(c for c in checks if c["key"] == "deliverables")


def test_flags_a_done_unit_with_no_document(tmp_path: Path) -> None:
    conn = _seed(tmp_path)
    _unit(conn, "UNIT-001", "done")
    conn.commit()
    conn.close()

    card = _card(tmp_path)
    assert card["ok"] is False
    assert "UNIT-001" in card["detail"], "the doctor must NAME the offenders"
    assert card["route"] == "train"


def test_names_every_offender_it_can_show(tmp_path: Path) -> None:
    conn = _seed(tmp_path)
    for n in range(1, 4):
        _unit(conn, f"UNIT-00{n}", "done")
    conn.commit()
    conn.close()

    card = _card(tmp_path)
    assert card["ok"] is False
    for n in range(1, 4):
        assert f"UNIT-00{n}" in card["detail"]
    assert "3 done Unit(s)" in card["detail"]


def test_truncates_a_long_offender_list_but_reports_the_count(tmp_path: Path) -> None:
    conn = _seed(tmp_path)
    for n in range(1, 13):
        _unit(conn, f"UNIT-{n:03d}", "done")
    conn.commit()
    conn.close()

    card = _card(tmp_path)
    assert card["ok"] is False
    assert "12 done Unit(s)" in card["detail"]
    assert "+4 more" in card["detail"], "a silent cap reads as full coverage"


def test_passes_when_the_done_unit_has_a_document(tmp_path: Path) -> None:
    conn = _seed(tmp_path)
    _unit(conn, "UNIT-001", "done")
    create(conn, "document", {
        "id": "DOC-001", "firm_id": "chief-of-staff", "name": "report.md",
        "type": "draft", "content_path": "deliverables/report.md",
        "parent_entity_type": "unit", "parent_entity_id": "UNIT-001",
    })
    conn.commit()
    conn.close()

    assert _card(tmp_path)["ok"] is True


def test_passes_when_the_done_unit_records_outputs(tmp_path: Path) -> None:
    """A Unit that says what it produced is not the ESC-026 harm."""
    conn = _seed(tmp_path)
    _unit(conn, "UNIT-001", "done", outputs=["deliverables/report.md"])
    conn.commit()
    conn.close()

    assert _card(tmp_path)["ok"] is True


def test_ignores_units_that_are_not_done(tmp_path: Path) -> None:
    """Must-allow: an open Unit has not reached the register-before-close rule.
    Flagging it would fire on the whole queue and train the Board to ignore the
    card."""
    conn = _seed(tmp_path)
    for n, status in enumerate(
        ("pending", "in_progress", "blocked", "in_review", "cancelled"), start=1
    ):
        _unit(conn, f"UNIT-{n:03d}", status)
    conn.commit()
    conn.close()

    assert _card(tmp_path)["ok"] is True


def test_passes_on_a_firm_with_no_units(tmp_path: Path) -> None:
    conn = _seed(tmp_path)
    conn.commit()
    conn.close()

    assert _card(tmp_path)["ok"] is True


def test_another_units_document_does_not_cover_this_one(tmp_path: Path) -> None:
    """The Document must be parented to the Unit in question — a firm-wide
    document count is how 3-of-26 looked survivable."""
    conn = _seed(tmp_path)
    _unit(conn, "UNIT-001", "done")
    _unit(conn, "UNIT-002", "done")
    create(conn, "document", {
        "id": "DOC-001", "firm_id": "chief-of-staff", "name": "report.md",
        "type": "draft", "content_path": "deliverables/report.md",
        "parent_entity_type": "unit", "parent_entity_id": "UNIT-001",
    })
    conn.commit()
    conn.close()

    card = _card(tmp_path)
    assert card["ok"] is False
    assert "UNIT-002" in card["detail"]
    assert "UNIT-001" not in card["detail"]


def test_fix_never_auto_registers(tmp_path: Path) -> None:
    """--fix must not guess the file mapping: it routes 'train' and names the
    offenders instead. A wrong auto-registration is a false deliverable on
    Records, which is worse than a missing one."""
    from firm.cli.doctor import fix

    conn = _seed(tmp_path)
    _unit(conn, "UNIT-001", "done")
    conn.commit()
    conn.close()

    checks = diagnose(tmp_path, "chief-of-staff")
    did = fix(tmp_path, "chief-of-staff", checks)
    assert not any("deliverable" in d.lower() for d in did)

    conn = sqlite3.connect(get_db_path(tmp_path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM document").fetchone()[0] == 0
    finally:
        conn.close()
