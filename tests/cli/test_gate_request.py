"""End-to-end CLI tests for ``firm gate request``.

A Member's execution directive tells it that approval requests go to the Board.
Until this verb existed the only route was the firm MCP tool
``firm_request_gate``, and six of twelve firms load no firm MCP server — so in
half the estate the instruction named nothing a Member could run.

Invoked via ``subprocess.run([sys.executable, "-m", "firm", ...])`` so argparse
wiring, ``CADRE_MEMBER_ID`` resolution, and exit codes are exercised exactly as
a Member's Bash call sees them.

**Every must-fail case asserts the gate table is unchanged**, not merely that a
message printed. A refusal message proves the code reached a ``return``; only an
unchanged row count proves the guarded write did not happen.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

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


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    _seed_workspace(tmp_path)
    return tmp_path


def _run(*args: str, member: str | None = None, firm_env: str | None = None,
         cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Explicit child environment — never ambient inheritance."""
    env = dict(os.environ)
    env.pop("CADRE_MEMBER_ID", None)
    env.pop("FIRM_ID", None)
    if member is not None:
        env["CADRE_MEMBER_ID"] = member
    if firm_env is not None:
        env["FIRM_ID"] = firm_env
    return subprocess.run(
        [sys.executable, "-m", "firm", *args],
        capture_output=True, text=True, env=env,
        cwd=str(cwd) if cwd else str(REPO_ROOT), timeout=120,
    )


def _rows(workspace: Path, sql: str, *params: object) -> list[sqlite3.Row]:
    conn = sqlite3.connect(get_db_path(workspace))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _gate_count(workspace: Path) -> int:
    return _rows(workspace, "SELECT COUNT(*) AS n FROM gate")[0]["n"]


# ═══════════════════════════════════════════════════════════════════════════
# Must work
# ═══════════════════════════════════════════════════════════════════════════

def test_creates_a_pending_gate_attributed_to_the_member(workspace: Path) -> None:
    result = _run(
        "gate", "request", "--member", "MEM-001",
        "--action", "publish the January post",
        "--target-type", "unit", "--target-id", "UNIT-100",
        "--workspace", str(workspace),
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["requesting_member_id"] == "MEM-001"
    assert payload["action"] == "publish the January post"
    assert payload["status"] == "pending"

    rows = _rows(workspace, "SELECT * FROM gate")
    assert len(rows) == 1
    assert rows[0]["requesting_member_id"] == "MEM-001"
    assert rows[0]["target_entity_type"] == "unit"
    assert rows[0]["target_entity_id"] == "UNIT-100"
    assert rows[0]["status"] == "pending"


def test_member_identity_comes_from_the_environment_when_no_flag(workspace: Path) -> None:
    """A Member's Bash subshell inherits CADRE_MEMBER_ID, so the Member never
    has to know its own id to ask for approval."""
    result = _run(
        "gate", "request",
        "--action", "delete the draft",
        "--target-type", "unit", "--target-id", "UNIT-100",
        "--workspace", str(workspace),
        member="MEM-002",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["requesting_member_id"] == "MEM-002"
    assert _rows(workspace, "SELECT * FROM gate")[0]["requesting_member_id"] == "MEM-002"


def test_explicit_member_flag_beats_the_environment(workspace: Path) -> None:
    result = _run(
        "gate", "request", "--member", "MEM-001",
        "--action", "ship it", "--target-type", "unit", "--target-id", "UNIT-100",
        "--workspace", str(workspace),
        member="MEM-002",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["requesting_member_id"] == "MEM-001"


def test_context_lands_on_the_row(workspace: Path) -> None:
    result = _run(
        "gate", "request", "--member", "MEM-001",
        "--action", "spend on ads", "--target-type", "unit", "--target-id", "UNIT-100",
        "--context", "the campaign needs a decision before Friday",
        "--workspace", str(workspace),
    )
    assert result.returncode == 0, result.stderr
    assert _rows(workspace, "SELECT * FROM gate")[0]["context"] == (
        "the campaign needs a decision before Friday"
    )


def test_the_request_is_written_to_records(workspace: Path) -> None:
    _run(
        "gate", "request", "--member", "MEM-001",
        "--action", "publish", "--target-type", "unit", "--target-id", "UNIT-100",
        "--workspace", str(workspace),
    )
    events = _rows(
        workspace,
        "SELECT * FROM records WHERE event_type = ?", "gate.requested",
    )
    assert len(events) == 1


def test_firm_id_resolves_from_the_environment(workspace: Path) -> None:
    result = _run(
        "gate", "request", "--member", "MEM-001",
        "--action", "publish", "--target-type", "unit", "--target-id", "UNIT-100",
        "--workspace", str(workspace),
        firm_env="chrisai",
    )
    assert result.returncode == 0, result.stderr
    assert _rows(workspace, "SELECT * FROM gate")[0]["firm_id"] == "chrisai"


def test_explicit_firm_id_flag_is_honoured(workspace: Path) -> None:
    result = _run(
        "gate", "request", "--member", "MEM-001", "--firm-id", "chrisai",
        "--action", "publish", "--target-type", "unit", "--target-id", "UNIT-100",
        "--workspace", str(workspace),
    )
    assert result.returncode == 0, result.stderr
    assert _rows(workspace, "SELECT * FROM gate")[0]["firm_id"] == "chrisai"


def test_workspace_defaults_to_the_current_directory(workspace: Path) -> None:
    result = _run(
        "gate", "request", "--member", "MEM-001",
        "--action", "publish", "--target-type", "unit", "--target-id", "UNIT-100",
        cwd=workspace,
    )
    assert result.returncode == 0, result.stderr
    assert _gate_count(workspace) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Must fail — and must not write
# ═══════════════════════════════════════════════════════════════════════════

def test_no_member_anywhere_is_refused_and_writes_nothing(workspace: Path) -> None:
    before = _gate_count(workspace)
    result = _run(
        "gate", "request",
        "--action", "publish", "--target-type", "unit", "--target-id", "UNIT-100",
        "--workspace", str(workspace),
    )
    assert result.returncode == 1
    assert json.loads(result.stderr)["ok"] is False
    assert "no member identity" in result.stderr
    assert _gate_count(workspace) == before == 0


def test_unknown_member_is_refused_and_writes_nothing(workspace: Path) -> None:
    before = _gate_count(workspace)
    result = _run(
        "gate", "request", "--member", "MEM-404",
        "--action", "publish", "--target-type", "unit", "--target-id", "UNIT-100",
        "--workspace", str(workspace),
    )
    assert result.returncode == 1
    assert json.loads(result.stderr)["ok"] is False
    assert _gate_count(workspace) == before == 0


def test_an_invalid_target_type_is_refused_and_writes_nothing(workspace: Path) -> None:
    before = _gate_count(workspace)
    result = _run(
        "gate", "request", "--member", "MEM-001",
        "--action", "publish", "--target-type", "banana", "--target-id", "UNIT-100",
        "--workspace", str(workspace),
    )
    assert result.returncode == 1
    error = json.loads(result.stderr)["error"]
    assert "banana" in error
    assert _gate_count(workspace) == before == 0


def test_a_missing_target_is_refused_and_writes_nothing(workspace: Path) -> None:
    before = _gate_count(workspace)
    result = _run(
        "gate", "request", "--member", "MEM-001",
        "--action", "publish", "--target-type", "unit", "--target-id", "UNIT-999",
        "--workspace", str(workspace),
    )
    assert result.returncode == 1
    assert json.loads(result.stderr)["ok"] is False
    assert _gate_count(workspace) == before == 0


@pytest.mark.parametrize("omitted", ["--action", "--target-type", "--target-id"])
def test_a_missing_required_flag_is_an_argparse_error(workspace: Path, omitted: str) -> None:
    args = ["gate", "request", "--member", "MEM-001",
            "--action", "publish", "--target-type", "unit", "--target-id", "UNIT-100",
            "--workspace", str(workspace)]
    index = args.index(omitted)
    del args[index:index + 2]
    result = _run(*args)
    assert result.returncode == 2, result.stdout
    assert omitted in result.stderr
    assert _gate_count(workspace) == 0


def test_a_workspace_with_no_firm_db_is_refused(tmp_path: Path) -> None:
    result = _run(
        "gate", "request", "--member", "MEM-001",
        "--action", "publish", "--target-type", "unit", "--target-id", "UNIT-100",
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 1
    assert result.stdout.strip() == ""
    assert not get_db_path(tmp_path).exists(), (
        "a refused request created a database as a side effect"
    )


def test_a_bare_gate_group_prints_help_and_succeeds() -> None:
    """The bare-group fallback every other subcommand group has."""
    result = _run("gate")
    assert result.returncode == 0
    assert "request" in result.stdout
