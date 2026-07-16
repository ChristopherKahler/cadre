"""End-to-end CLI tests for ``firm member grant|revoke authority``.

Invokes the CLI via ``subprocess.run([sys.executable, "-m", "firm", ...])`` so
argparse wiring, exit codes, and stdout/stderr are exercised as operators see
them. This verb is the canonical grant surface — coboard drives it.
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
from firm.core.repo import create, find

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
        create(conn, "member", {
            "id": "MEM-001", "firm_id": "chrisai", "name": "Sterling", "role": "GM",
        })
    finally:
        conn.close()


def _run(workspace: Path, *args: str, as_member: str | None = None):
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }
    env.pop("CADRE_MEMBER_ID", None)
    if as_member:
        env["CADRE_MEMBER_ID"] = as_member
    return subprocess.run(
        [sys.executable, "-m", "firm", "member", *args,
         "--workspace", str(workspace)],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )


def _conn(workspace: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(workspace))
    conn.row_factory = sqlite3.Row
    return conn


def _autonomy(workspace: Path, member_id: str = "MEM-001"):
    conn = _conn(workspace)
    try:
        raw = conn.execute(
            "SELECT autonomy FROM member WHERE id = ?", (member_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    return json.loads(raw) if raw else None


def test_grant_authority_updates_autonomy(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run(tmp_path, "grant", "authority", "MEM-001", "--comment", "runs the floor")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "ok": True, "member_id": "MEM-001", "authority": True,
    }
    assert _autonomy(tmp_path) == {"sovereign": ["authority"]}


def test_revoke_authority_clears_it(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    _run(tmp_path, "grant", "authority", "MEM-001")
    result = _run(tmp_path, "revoke", "authority", "MEM-001", "--comment", "rotated off")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["authority"] is False
    # Cleared entirely rather than left as an empty husk.
    assert _autonomy(tmp_path) is None


def test_grant_is_idempotent(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    first = _run(tmp_path, "grant", "authority", "MEM-001")
    second = _run(tmp_path, "grant", "authority", "MEM-001")

    assert first.returncode == 0 and second.returncode == 0
    assert json.loads(second.stdout)["authority"] is True
    assert _autonomy(tmp_path) == {"sovereign": ["authority"]}

    conn = _conn(workspace=tmp_path)
    try:
        rows = find(conn, "records", event_type="member.authority_granted")
    finally:
        conn.close()
    assert len(rows) == 1, "a no-op grant is not a governance event"


def test_grant_writes_audit_row_with_comment(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    _run(tmp_path, "grant", "authority", "MEM-001", "--comment", "promoted")

    conn = _conn(tmp_path)
    try:
        rows = find(conn, "records", event_type="member.authority_granted")
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["target_entity_id"] == "MEM-001"
    assert rows[0]["details"]["comment"] == "promoted"


def test_cli_refuses_an_identified_member_caller(tmp_path: Path) -> None:
    # A Member shelling out to the CLI carries CADRE_MEMBER_ID into the
    # subprocess. Granting must refuse it exactly like the MCP path would —
    # otherwise the CLI is the hole the missing MCP tool was meant to close.
    _seed_workspace(tmp_path)
    result = _run(tmp_path, "grant", "authority", "MEM-001", as_member="MEM-001")

    assert result.returncode == 1
    payload = json.loads(result.stderr)
    assert payload["ok"] is False
    assert payload["error"] == "board_only"
    assert _autonomy(tmp_path) is None


def test_unknown_member_exits_nonzero(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run(tmp_path, "grant", "authority", "MEM-404")
    assert result.returncode == 1
    assert json.loads(result.stderr)["ok"] is False


def test_unknown_capability_rejected_by_argparse(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = _run(tmp_path, "grant", "spend", "MEM-001")
    assert result.returncode == 2  # argparse choices
    assert "invalid choice" in result.stderr


def test_missing_db_exits_nonzero(tmp_path: Path) -> None:
    result = _run(tmp_path, "grant", "authority", "MEM-001")
    assert result.returncode == 1
    assert json.loads(result.stderr)["reason"] == "db-not-found"
