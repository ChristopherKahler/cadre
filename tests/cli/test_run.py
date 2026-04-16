"""End-to-end CLI tests for ``firm run end``.

Tests invoke the CLI via ``subprocess.run([sys.executable, "-m", "firm", ...])``
so argparse wiring, exit codes, and stdout/stderr are exercised as operators
see them.
"""

from __future__ import annotations

import json
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


def _seed_workspace(
    workspace: Path,
    *,
    unit_outputs: list[Any] | None = None,
) -> None:
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
        })
        create(conn, "unit", {
            "id": "UNIT-001", "firm_id": "chrisai",
            "project_id": "PRJ-010",
            "name": "Draft blog post #14",
            "status": "in_progress",
            "assignee_member_id": "MEM-001",
            "outputs": unit_outputs,
        })
        create(conn, "member_run", {
            "id": "RUN-001", "firm_id": "chrisai",
            "member_id": "MEM-001",
            "unit_id": "UNIT-001",
            "status": "running",
            "started_at": "2026-04-15 16:00:00",
        })
        create(conn, "member_run", {
            "id": "RUN-002", "firm_id": "chrisai",
            "member_id": "MEM-001",
            "unit_id": None,
            "status": "running",
            "started_at": "2026-04-15 17:00:00",
        })
    finally:
        conn.close()


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base_env = os.environ.copy()
    base_env["PYTHONPATH"] = str(REPO_ROOT / "src")
    if env:
        base_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "firm", *args],
        capture_output=True, text=True, env=base_env,
    )


def _count(workspace: Path, table: str) -> int:
    conn = sqlite3.connect(get_db_path(workspace))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _run_status(workspace: Path, run_id: str) -> str | None:
    conn = sqlite3.connect(get_db_path(workspace))
    try:
        row = conn.execute(
            "SELECT status FROM member_run WHERE id = ?", (run_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-5: happy path
# ---------------------------------------------------------------------------

def test_end_happy_path(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "run", "end", "RUN-001",
        "--status", "completed",
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    out = json.loads(result.stdout)
    assert out["ok"] is True
    assert out["run_id"] == "RUN-001"
    assert out["records_id"] == "LOG-001"
    assert _run_status(tmp_path, "RUN-001") == "completed"


def test_end_with_outputs_and_usage(tmp_path: Path) -> None:
    _seed_workspace(tmp_path, unit_outputs=[{"prior": "artifact"}])
    result = _run_cli(
        "run", "end", "RUN-001",
        "--status", "completed",
        "--outputs", '[{"path": "post.md"}]',
        "--usage", '{"plan": "api", "tokens_in": 1000}',
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert out["ok"] is True
    assert out["wrote"]["unit"] is True
    assert out["wrote"]["usage_event"] is True
    assert _count(tmp_path, "records") == 1
    assert _count(tmp_path, "usage_event") == 1


def test_end_without_unit_id(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "run", "end", "RUN-002",
        "--status", "completed",
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert out["ok"] is True
    assert out["wrote"]["unit"] is False


# ---------------------------------------------------------------------------
# AC-5: dry-run
# ---------------------------------------------------------------------------

def test_end_dry_run_reports_changes(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "run", "end", "RUN-001",
        "--status", "completed",
        "--outputs", '[{"path": "draft.md"}]',
        "--workspace", str(tmp_path),
        "--dry-run",
    )
    assert result.returncode == 0
    assert "[dry-run]" in result.stdout
    assert "would finalize RUN-001" in result.stdout
    assert "running -> completed" in result.stdout
    # No writes persisted.
    assert _count(tmp_path, "records") == 0
    assert _count(tmp_path, "usage_event") == 0
    assert _run_status(tmp_path, "RUN-001") == "running"


# ---------------------------------------------------------------------------
# AC-5: structured failures
# ---------------------------------------------------------------------------

def test_end_run_not_found(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "run", "end", "RUN-NONEXISTENT",
        "--status", "completed",
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert out["ok"] is False
    assert out["reason"] == "run-not-found"


def test_end_db_missing(tmp_path: Path) -> None:
    # No seed -- no .firm/firm.db at all.
    result = _run_cli(
        "run", "end", "RUN-001",
        "--status", "completed",
        "--workspace", str(tmp_path),
    )
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert out["ok"] is False
    assert out["reason"] == "db-not-found"


# ---------------------------------------------------------------------------
# Argparse surface
# ---------------------------------------------------------------------------

def test_end_argparse_surface(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "run", "end", "RUN-001",
        "--workspace", str(tmp_path),
    )
    # Missing --status should fail parsing.
    assert result.returncode != 0
    assert "--status" in result.stderr or "required" in result.stderr.lower()


def test_end_help_includes_all_flags() -> None:
    result = _run_cli("run", "end", "--help")
    assert result.returncode == 0
    for flag in ["--status", "--outputs", "--usage", "--error", "--notes",
                 "--dry-run", "--workspace", "--firm-id"]:
        assert flag in result.stdout, f"missing flag in help: {flag}"


# ---------------------------------------------------------------------------
# FIRM_ID env / flag
# ---------------------------------------------------------------------------

def test_end_firm_id_from_env(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "run", "end", "RUN-001",
        "--status", "completed",
        "--workspace", str(tmp_path),
        env={"FIRM_ID": "chrisai"},
    )
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert out["ok"] is True
    # Records row written with firm_id from env.
    conn = sqlite3.connect(get_db_path(tmp_path))
    try:
        row = conn.execute(
            "SELECT firm_id FROM records WHERE id = 'LOG-001'"
        ).fetchone()
        assert row[0] == "chrisai"
    finally:
        conn.close()


def test_end_firm_id_flag_precedence(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run_cli(
        "run", "end", "RUN-001",
        "--status", "completed",
        "--workspace", str(tmp_path),
        "--firm-id", "chrisai",
        env={"FIRM_ID": "should-be-ignored"},
    )
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert out["ok"] is True
