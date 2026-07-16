"""End-to-end CLI tests for ``firm unit create`` and ``firm doc register``.

The firm's execution rules tell every Member to queue follow-up work as they go
and to register a deliverable before its Unit closes. Both instructions were
unexecutable: ``firm unit`` exposed only ``complete``, and no verb registered a
Document (chief-of-staff ESC-041 / ESC-026). These tests hold the CLI surface
those rules assume.

Invoked via ``subprocess.run([sys.executable, "-m", "firm", ...])`` so argparse
wiring, ``CADRE_MEMBER_ID`` resolution, and exit codes are exercised exactly as
a Member's Bash call sees them.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from firm.core.db import get_db_path
from firm.core.migrate import apply_migrations
from firm.core.repo import create

REPO_ROOT = Path(__file__).resolve().parents[2]


def _seed_workspace(workspace: Path) -> None:
    db_path = get_db_path(workspace)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        apply_migrations(conn)
        create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
        create(conn, "member", {"id": "MEM-001", "firm_id": "chrisai",
                                "name": "Cooper", "role": "Ops Engineer"})
        create(conn, "member", {"id": "MEM-002", "firm_id": "chrisai",
                                "name": "Dalton", "role": "Correspondence"})
        create(conn, "operation", {"id": "OPS-001", "firm_id": "chrisai",
                                   "name": "Ops"})
        create(conn, "project", {"id": "PRJ-010", "firm_id": "chrisai",
                                 "operation_id": "OPS-001", "name": "Inbox",
                                 "status": "in_progress",
                                 "due_date": "2026-12-31"})
        create(conn, "unit", {"id": "UNIT-100", "firm_id": "chrisai",
                              "project_id": "PRJ-010", "name": "Existing work",
                              "status": "in_progress",
                              "assignee_member_id": "MEM-001"})
    finally:
        conn.close()


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base_env = os.environ.copy()
    base_env["PYTHONPATH"] = str(REPO_ROOT / "src")
    base_env.pop("CADRE_MEMBER_ID", None)
    if env:
        base_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "firm", *args],
        capture_output=True, text=True, env=base_env,
    )


def _rows(workspace: Path, sql: str, *params: object) -> list[sqlite3.Row]:
    conn = sqlite3.connect(get_db_path(workspace))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# firm unit create — ESC-041: a Member can queue its own follow-up work
# ---------------------------------------------------------------------------


def test_create_makes_a_unit_and_prints_its_id(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "unit", "create", "--name", "Chase the Caddy open loops",
        "--project", "PRJ-010", "--workspace", str(tmp_path),
        env={"CADRE_MEMBER_ID": "MEM-001"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["unit"]["id"] == "UNIT-101"
    assert "UNIT-101" in result.stdout

    rows = _rows(tmp_path, "SELECT * FROM unit WHERE id = 'UNIT-101'")
    assert rows[0]["name"] == "Chase the Caddy open loops"
    assert rows[0]["status"] == "pending", "queued work starts pending so a pulse can activate it"
    assert rows[0]["priority"] == "medium"


def test_create_defaults_assignee_to_the_calling_member(tmp_path: Path) -> None:
    """The caller is resolved from CADRE_MEMBER_ID — a Member queueing follow-up
    work for itself should not have to name itself."""
    _seed_workspace(tmp_path)
    result = _run_cli(
        "unit", "create", "--name", "Follow-up", "--project", "PRJ-010",
        "--workspace", str(tmp_path),
        env={"CADRE_MEMBER_ID": "MEM-001"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    rows = _rows(tmp_path, "SELECT * FROM unit WHERE id = 'UNIT-101'")
    assert rows[0]["assignee_member_id"] == "MEM-001"


def test_create_explicit_assignee_wins_over_the_caller(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "unit", "create", "--name", "Hand-off", "--project", "PRJ-010",
        "--assignee", "MEM-002", "--workspace", str(tmp_path),
        env={"CADRE_MEMBER_ID": "MEM-001"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    rows = _rows(tmp_path, "SELECT * FROM unit WHERE id = 'UNIT-101'")
    assert rows[0]["assignee_member_id"] == "MEM-002"


def test_create_records_actor_is_the_member_not_the_board(tmp_path: Path) -> None:
    """Hard requirement: a Member-created Unit must carry the Member on Records.
    ``create_unit`` hardcoded the Board actor, which would have credited every
    Member's queued work to the Board."""
    _seed_workspace(tmp_path)
    result = _run_cli(
        "unit", "create", "--name", "Follow-up", "--project", "PRJ-010",
        "--workspace", str(tmp_path),
        env={"CADRE_MEMBER_ID": "MEM-001"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    rows = _rows(
        tmp_path,
        "SELECT * FROM records WHERE event_type = 'unit.created'",
    )
    assert len(rows) == 1
    assert rows[0]["actor_type"] == "member"
    assert rows[0]["actor_id"] == "MEM-001"


def test_create_from_the_board_still_records_the_board(tmp_path: Path) -> None:
    """No CADRE_MEMBER_ID and no --assignee = a Board/operator call. It must stay
    a Board-actored event, not acquire a phantom member."""
    _seed_workspace(tmp_path)
    result = _run_cli(
        "unit", "create", "--name", "Board work", "--project", "PRJ-010",
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    rows = _rows(tmp_path, "SELECT * FROM records WHERE event_type = 'unit.created'")
    assert rows[0]["actor_type"] == "board"
    assert rows[0]["actor_id"] is None
    units = _rows(tmp_path, "SELECT * FROM unit WHERE id = 'UNIT-101'")
    assert units[0]["assignee_member_id"] is None


def test_create_accepts_repeatable_depends_on(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    first = _run_cli(
        "unit", "create", "--name", "Second step", "--project", "PRJ-010",
        "--depends-on", "UNIT-100", "--workspace", str(tmp_path),
        env={"CADRE_MEMBER_ID": "MEM-001"},
    )
    assert first.returncode == 0, f"stderr: {first.stderr}"
    second = _run_cli(
        "unit", "create", "--name", "Third step", "--project", "PRJ-010",
        "--depends-on", "UNIT-100", "--depends-on", "UNIT-101",
        "--workspace", str(tmp_path),
        env={"CADRE_MEMBER_ID": "MEM-001"},
    )
    assert second.returncode == 0, f"stderr: {second.stderr}"
    rows = _rows(tmp_path, "SELECT depends_on FROM unit WHERE id = 'UNIT-102'")
    assert json.loads(rows[0]["depends_on"]) == ["UNIT-100", "UNIT-101"]


def test_create_accepts_description_and_priority(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "unit", "create", "--name", "Urgent thing", "--project", "PRJ-010",
        "--description", "The why behind it", "--priority", "urgent",
        "--workspace", str(tmp_path),
        env={"CADRE_MEMBER_ID": "MEM-001"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    rows = _rows(tmp_path, "SELECT * FROM unit WHERE id = 'UNIT-101'")
    assert rows[0]["description"] == "The why behind it"
    assert rows[0]["priority"] == "urgent"


def test_create_accepts_repeatable_acceptance_criteria(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "unit", "create", "--name", "Thing", "--project", "PRJ-010",
        "--ac", "It works", "--ac", "It is registered",
        "--workspace", str(tmp_path),
        env={"CADRE_MEMBER_ID": "MEM-001"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    rows = _rows(tmp_path, "SELECT acceptance_criteria FROM unit WHERE id = 'UNIT-101'")
    assert json.loads(rows[0]["acceptance_criteria"]) == ["It works", "It is registered"]


def test_create_rejects_an_unknown_project(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "unit", "create", "--name", "Orphan", "--project", "PRJ-404",
        "--workspace", str(tmp_path),
        env={"CADRE_MEMBER_ID": "MEM-001"},
    )
    assert result.returncode == 1
    assert "PRJ-404" in result.stderr


def test_create_rejects_an_unknown_assignee(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "unit", "create", "--name", "Ghost work", "--project", "PRJ-010",
        "--assignee", "MEM-404", "--workspace", str(tmp_path),
    )
    assert result.returncode == 1
    assert "MEM-404" in result.stderr


def test_create_rejects_a_dependency_cycle(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "unit", "create", "--name", "Cyclic", "--project", "PRJ-010",
        "--depends-on", "UNIT-101", "--workspace", str(tmp_path),
        env={"CADRE_MEMBER_ID": "MEM-001"},
    )
    # UNIT-101 is this unit's own id-to-be — an unknown FK at create time.
    assert result.returncode == 1


def test_create_requires_a_name(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "unit", "create", "--project", "PRJ-010", "--workspace", str(tmp_path),
    )
    assert result.returncode != 0
    assert "--name" in result.stderr or "required" in result.stderr.lower()


def test_create_dry_run_writes_nothing(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "unit", "create", "--name", "Planned", "--project", "PRJ-010",
        "--workspace", str(tmp_path), "--dry-run",
        env={"CADRE_MEMBER_ID": "MEM-001"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "[dry-run]" in result.stdout
    assert _rows(tmp_path, "SELECT * FROM unit WHERE id = 'UNIT-101'") == []
    assert _rows(tmp_path, "SELECT * FROM records") == []


# ---------------------------------------------------------------------------
# Discoverability — a Member reading help must find the verbs
# ---------------------------------------------------------------------------


def test_unit_help_surfaces_create(tmp_path: Path) -> None:
    result = _run_cli("unit", "--help")
    assert result.returncode == 0
    assert "create" in result.stdout
    assert "complete" in result.stdout


def test_firm_help_surfaces_unit_and_doc(tmp_path: Path) -> None:
    result = _run_cli("--help")
    assert result.returncode == 0
    assert "unit" in result.stdout
    assert "doc" in result.stdout


def test_create_help_includes_all_flags() -> None:
    result = _run_cli("unit", "create", "--help")
    assert result.returncode == 0
    for flag in ["--name", "--description", "--assignee", "--project",
                 "--priority", "--depends-on", "--ac", "--workspace",
                 "--firm-id", "--dry-run"]:
        assert flag in result.stdout, f"missing flag in help: {flag}"


def test_doc_register_help_includes_all_flags() -> None:
    result = _run_cli("doc", "register", "--help")
    assert result.returncode == 0
    for flag in ["--unit", "--path", "--name", "--type", "--member",
                 "--workspace", "--firm-id"]:
        assert flag in result.stdout, f"missing flag in help: {flag}"


# ---------------------------------------------------------------------------
# firm doc register — ESC-026: the audited path to satisfy "registered before
# the Unit closes"
# ---------------------------------------------------------------------------


def test_doc_register_creates_a_document_and_sets_outputs(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    f = tmp_path / "report.md"
    f.write_text("the deliverable")

    result = _run_cli(
        "doc", "register", "--unit", "UNIT-100", "--path", str(f),
        "--workspace", str(tmp_path),
        env={"CADRE_MEMBER_ID": "MEM-001"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "created"
    assert payload["document"]["id"] == "DOC-001"

    docs = _rows(tmp_path, "SELECT * FROM document WHERE id = 'DOC-001'")
    assert docs[0]["parent_entity_id"] == "UNIT-100"
    assert docs[0]["content_path"] == "report.md"
    assert docs[0]["author_id"] == "MEM-001"

    units = _rows(tmp_path, "SELECT outputs FROM unit WHERE id = 'UNIT-100'")
    assert units[0]["outputs"] is not None, "ESC-026: outputs must be non-null"
    assert json.loads(units[0]["outputs"]) == ["report.md"]


def test_doc_register_defaults_member_to_the_caller(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    f = tmp_path / "report.md"
    f.write_text("x")
    result = _run_cli(
        "doc", "register", "--unit", "UNIT-100", "--path", str(f),
        "--workspace", str(tmp_path),
        env={"CADRE_MEMBER_ID": "MEM-002"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    rows = _rows(tmp_path, "SELECT * FROM records WHERE event_type = 'document.created'")
    assert rows[0]["actor_type"] == "member"
    assert rows[0]["actor_id"] == "MEM-002"


def test_doc_register_needs_a_member_when_there_is_no_caller(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    f = tmp_path / "report.md"
    f.write_text("x")
    result = _run_cli(
        "doc", "register", "--unit", "UNIT-100", "--path", str(f),
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 1
    assert "--member" in result.stderr or "CADRE_MEMBER_ID" in result.stderr


def test_doc_register_is_idempotent(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    f = tmp_path / "report.md"
    f.write_text("x")
    for _ in range(2):
        result = _run_cli(
            "doc", "register", "--unit", "UNIT-100", "--path", str(f),
            "--workspace", str(tmp_path),
            env={"CADRE_MEMBER_ID": "MEM-001"},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
    assert json.loads(result.stdout)["action"] == "existing"
    assert len(_rows(tmp_path, "SELECT * FROM document")) == 1


def test_doc_register_refuses_a_missing_file(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "doc", "register", "--unit", "UNIT-100",
        "--path", str(tmp_path / "ghost.md"),
        "--workspace", str(tmp_path),
        env={"CADRE_MEMBER_ID": "MEM-001"},
    )
    assert result.returncode == 1
    assert "not found" in result.stderr


def test_doc_register_refuses_to_clobber_another_units_document(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    _rows(tmp_path, "SELECT 1")
    conn = sqlite3.connect(get_db_path(tmp_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    create(conn, "unit", {"id": "UNIT-200", "firm_id": "chrisai",
                          "project_id": "PRJ-010", "name": "Other work",
                          "status": "in_progress", "assignee_member_id": "MEM-002"})
    conn.commit()
    conn.close()

    f = tmp_path / "report.md"
    f.write_text("x")
    first = _run_cli(
        "doc", "register", "--unit", "UNIT-100", "--path", str(f),
        "--workspace", str(tmp_path), env={"CADRE_MEMBER_ID": "MEM-001"},
    )
    assert first.returncode == 0, f"stderr: {first.stderr}"

    second = _run_cli(
        "doc", "register", "--unit", "UNIT-200", "--path", str(f),
        "--workspace", str(tmp_path), env={"CADRE_MEMBER_ID": "MEM-002"},
    )
    assert second.returncode == 1, "registering another unit's file must fail loudly"
    assert "UNIT-100" in second.stderr, "the error names the owning unit"
    docs = _rows(tmp_path, "SELECT * FROM document")
    assert len(docs) == 1
    assert docs[0]["parent_entity_id"] == "UNIT-100", "the owner keeps its row"


# ---------------------------------------------------------------------------
# firm unit complete --outputs — closing a Unit registers its deliverable
# ---------------------------------------------------------------------------


def test_complete_with_outputs_registers_a_document(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    f = tmp_path / "report.md"
    f.write_text("the deliverable")

    result = _run_cli(
        "unit", "complete", "UNIT-100", "--member", "MEM-001",
        "--outputs", str(f), "--workspace", str(tmp_path),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "DOC-001" in result.stdout

    docs = _rows(tmp_path, "SELECT * FROM document")
    assert len(docs) == 1
    assert docs[0]["parent_entity_id"] == "UNIT-100"
    assert docs[0]["author_id"] == "MEM-001"
    units = _rows(tmp_path, "SELECT status, outputs FROM unit WHERE id = 'UNIT-100'")
    assert units[0]["status"] == "done"
    assert json.loads(units[0]["outputs"]) == ["report.md"]


def test_complete_with_repeatable_outputs(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    for nm in ("spec.md", "run-report.md"):
        (tmp_path / nm).write_text("x")
    result = _run_cli(
        "unit", "complete", "UNIT-100", "--member", "MEM-001",
        "--outputs", str(tmp_path / "spec.md"),
        "--outputs", str(tmp_path / "run-report.md"),
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert len(_rows(tmp_path, "SELECT * FROM document")) == 2
    units = _rows(tmp_path, "SELECT outputs FROM unit WHERE id = 'UNIT-100'")
    assert json.loads(units[0]["outputs"]) == ["spec.md", "run-report.md"]


def test_complete_outputs_missing_file_aborts_before_completing(tmp_path: Path) -> None:
    """A deliverable that isn't on disk must not close the Unit — that is the
    exact "done with nothing to show" state §2 exists to prevent."""
    _seed_workspace(tmp_path)
    result = _run_cli(
        "unit", "complete", "UNIT-100", "--member", "MEM-001",
        "--outputs", str(tmp_path / "ghost.md"), "--workspace", str(tmp_path),
    )
    assert result.returncode == 1
    assert "not found" in result.stderr
    units = _rows(tmp_path, "SELECT status FROM unit WHERE id = 'UNIT-100'")
    assert units[0]["status"] == "in_progress", "the unit stays open"
    assert _rows(tmp_path, "SELECT * FROM document") == []


def test_complete_without_outputs_still_works(tmp_path: Path) -> None:
    """The flag is additive — the existing surface must not change."""
    _seed_workspace(tmp_path)
    result = _run_cli(
        "unit", "complete", "UNIT-100", "--member", "MEM-001",
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    units = _rows(tmp_path, "SELECT status FROM unit WHERE id = 'UNIT-100'")
    assert units[0]["status"] == "done"


def test_complete_dry_run_reports_planned_registration(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    f = tmp_path / "report.md"
    f.write_text("x")
    result = _run_cli(
        "unit", "complete", "UNIT-100", "--member", "MEM-001",
        "--outputs", str(f), "--workspace", str(tmp_path), "--dry-run",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "[dry-run]" in result.stdout
    assert "report.md" in result.stdout
    assert _rows(tmp_path, "SELECT * FROM document") == []


def test_complete_outputs_help_lists_the_flag() -> None:
    result = _run_cli("unit", "complete", "--help")
    assert result.returncode == 0
    assert "--outputs" in result.stdout
