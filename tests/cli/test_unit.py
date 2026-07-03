"""End-to-end CLI tests for ``firm unit complete``.

Tests invoke the CLI via ``subprocess.run([sys.executable, "-m", "firm", ...])``
so argparse wiring, exit codes, and stdout/stderr are exercised as operators
see them.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from firm.core.db import get_db_path
from firm.core.migrate import apply_migrations
from firm.core.repo import create


REPO_ROOT = Path(__file__).resolve().parents[2]


def _seed_workspace(workspace: Path, *, project_ac: list[dict[str, Any]] | None) -> None:
    db_path = get_db_path(workspace)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        apply_migrations(conn)
        create(conn, "firm", {
            "id": "chrisai", "name": "ChrisAI",
            "operator": {"name": "Chris Kahler", "role": "Board / Founder"},
        })
        create(conn, "member", {
            "id": "MEM-001", "firm_id": "chrisai",
            "name": "Quill", "role": "Blog Author",
        })
        create(conn, "operation", {
            "id": "OPS-001", "firm_id": "chrisai",
            "name": "Content Publishing",
        })
        create(conn, "project", {
            "id": "PRJ-010", "firm_id": "chrisai",
            "operation_id": "OPS-001",
            "name": "Q2 Blog Push",
            "status": "in_progress",
            "due_date": "2026-06-30",
            "acceptance_criteria": project_ac,
        })
        create(conn, "unit", {
            "id": "UNIT-100", "firm_id": "chrisai",
            "project_id": "PRJ-010",
            "name": "Draft blog post #14",
            "status": "in_progress",
            "assignee_member_id": "MEM-001",
        })
    finally:
        conn.close()


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run ``python -m firm <args>`` in a subprocess with PYTHONPATH=src."""
    base_env = os.environ.copy()
    base_env["PYTHONPATH"] = str(REPO_ROOT / "src")
    if env:
        base_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "firm", *args],
        capture_output=True, text=True, env=base_env,
    )


def _records_count(workspace: Path) -> int:
    conn = sqlite3.connect(get_db_path(workspace))
    try:
        return conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    finally:
        conn.close()


def _project_ac_raw(workspace: Path, project_id: str) -> Any:
    conn = sqlite3.connect(get_db_path(workspace))
    try:
        row = conn.execute(
            "SELECT acceptance_criteria FROM project WHERE id = ?", (project_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-5: happy path via CLI
# ---------------------------------------------------------------------------

def test_complete_happy_path_exits_zero(tmp_path: Path) -> None:
    _seed_workspace(tmp_path, project_ac=[
        {"id": "AC-1", "text": "Schema compiles",
         "resolved": False, "resolved_by": "UNIT-100"},
    ])
    result = _run_cli(
        "unit", "complete", "UNIT-100",
        "--member", "MEM-001",
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "completed UNIT-100" in result.stdout
    assert "AC-1" in result.stdout
    assert "LOG-001" in result.stdout


def test_complete_writes_records_row(tmp_path: Path) -> None:
    _seed_workspace(tmp_path, project_ac=[])
    assert _records_count(tmp_path) == 0
    result = _run_cli(
        "unit", "complete", "UNIT-100",
        "--member", "MEM-001",
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 0
    assert _records_count(tmp_path) == 1


def test_complete_prints_none_when_no_ac_flipped(tmp_path: Path) -> None:
    _seed_workspace(tmp_path, project_ac=[])
    result = _run_cli(
        "unit", "complete", "UNIT-100",
        "--member", "MEM-001",
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 0
    assert "(none)" in result.stdout


# ---------------------------------------------------------------------------
# AC-6: dry-run
# ---------------------------------------------------------------------------

def test_dry_run_prints_plan_without_writing(tmp_path: Path) -> None:
    _seed_workspace(tmp_path, project_ac=[
        {"id": "AC-1", "text": "Schema compiles",
         "resolved": False, "resolved_by": "UNIT-100"},
    ])
    ac_before = _project_ac_raw(tmp_path, "PRJ-010")

    result = _run_cli(
        "unit", "complete", "UNIT-100",
        "--member", "MEM-001",
        "--workspace", str(tmp_path),
        "--dry-run",
    )

    assert result.returncode == 0
    assert "[dry-run]" in result.stdout
    assert "would complete UNIT-100" in result.stdout
    assert "AC-1" in result.stdout
    # No writes.
    assert _records_count(tmp_path) == 0
    assert _project_ac_raw(tmp_path, "PRJ-010") == ac_before


# ---------------------------------------------------------------------------
# AC-3 surfacing via CLI
# ---------------------------------------------------------------------------

def test_unit_not_found_exits_nonzero(tmp_path: Path) -> None:
    _seed_workspace(tmp_path, project_ac=[])
    result = _run_cli(
        "unit", "complete", "UNIT-404",
        "--member", "MEM-001",
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 1
    assert "unit-not-found" in result.stderr


def test_db_missing_exits_nonzero(tmp_path: Path) -> None:
    # No seed — no .firm/firm.db at all.
    result = _run_cli(
        "unit", "complete", "UNIT-100",
        "--member", "MEM-001",
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 1
    assert ".firm/firm.db not found" in result.stderr


# ---------------------------------------------------------------------------
# Argparse surface
# ---------------------------------------------------------------------------

def test_missing_member_flag_fails_parsing(tmp_path: Path) -> None:
    _seed_workspace(tmp_path, project_ac=[])
    result = _run_cli(
        "unit", "complete", "UNIT-100",
        "--workspace", str(tmp_path),
    )
    assert result.returncode != 0
    assert "required" in result.stderr.lower() or "--member" in result.stderr


def test_complete_help_includes_all_flags() -> None:
    result = _run_cli("unit", "complete", "--help")
    assert result.returncode == 0
    for flag in ["--member", "--run-id", "--workspace", "--firm-id", "--dry-run"]:
        assert flag in result.stdout, f"missing flag in help: {flag}"


# ---------------------------------------------------------------------------
# FIRM_ID env override
# ---------------------------------------------------------------------------

def test_firm_id_env_override(tmp_path: Path) -> None:
    _seed_workspace(tmp_path, project_ac=[])
    result = _run_cli(
        "unit", "complete", "UNIT-100",
        "--member", "MEM-001",
        "--workspace", str(tmp_path),
        env={"FIRM_ID": "chrisai"},
    )
    assert result.returncode == 0
    # Records row written with firm_id=chrisai (env-resolved).
    conn = sqlite3.connect(get_db_path(tmp_path))
    try:
        row = conn.execute(
            "SELECT firm_id FROM records WHERE id = 'LOG-001'"
        ).fetchone()
        assert row[0] == "chrisai"
    finally:
        conn.close()


def test_firm_id_explicit_flag_wins_over_env(tmp_path: Path) -> None:
    _seed_workspace(tmp_path, project_ac=[])
    result = _run_cli(
        "unit", "complete", "UNIT-100",
        "--member", "MEM-001",
        "--workspace", str(tmp_path),
        "--firm-id", "chrisai",
        env={"FIRM_ID": "should-be-ignored"},
    )
    assert result.returncode == 0
