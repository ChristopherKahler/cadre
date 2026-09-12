"""End-to-end CLI tests for ``firm goal propose`` and the Member refusal on
``firm goal create`` (issue #17).

The charter tells every Member to propose its goal on its first run. The only
route was the firm MCP tool ``firm_propose_goal``, and hub-founded firms load no
firm MCP server, so the proposal failed in every one of them. Meanwhile
``firm goal create`` had no Member check, so a Member that found it wrote its
own goal and the records named the Board as the author.

Invoked via ``subprocess.run([sys.executable, "-m", "firm", ...])`` so argparse
wiring, ``CADRE_MEMBER_ID`` resolution and exit codes are exercised exactly as a
Member's Bash call sees them. Every must-fail case asserts the table it guards
is unchanged, not only that a message printed.
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
from firm.services.authority import grant_authority

REPO_ROOT = Path(__file__).resolve().parents[2]

TARGET = "Publish 10 posts a week"
METRIC = '{"value": 10, "unit": "posts/week"}'
REASONING = "published volume is the outcome I own"


def _connect(workspace: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(workspace))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _seed_workspace(workspace: Path) -> None:
    db_path = get_db_path(workspace)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(workspace)
    try:
        apply_migrations(conn)
        create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
        create(conn, "member", {"id": "MEM-001", "firm_id": "chrisai",
                                "name": "Cooper", "role": "Writer"})
        create(conn, "member", {"id": "MEM-002", "firm_id": "chrisai",
                                "name": "Dalton", "role": "GM"})
    finally:
        conn.close()


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    _seed_workspace(tmp_path)
    return tmp_path


def _run(*args: str, member: str | None = None) -> subprocess.CompletedProcess[str]:
    """Explicit child environment: never ambient inheritance."""
    env = dict(os.environ)
    env.pop("CADRE_MEMBER_ID", None)
    env.pop("FIRM_ID", None)
    if member is not None:
        env["CADRE_MEMBER_ID"] = member
    return subprocess.run(
        [sys.executable, "-m", "firm", *args],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=120,
    )


def _propose(workspace: Path, member: str | None = "MEM-001") -> subprocess.CompletedProcess[str]:
    return _run(
        "goal", "propose", TARGET,
        "--parent-type", "member", "--parent-id", "MEM-001",
        "--metric", METRIC, "--reasoning", REASONING,
        "--workspace", str(workspace),
        member=member,
    )


def _rows(workspace: Path, sql: str) -> list[sqlite3.Row]:
    conn = _connect(workspace)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _count(workspace: Path, table: str) -> int:
    return _rows(workspace, f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]


# ═══════════════════════════════════════════════════════════════════════════
# Must work
# ═══════════════════════════════════════════════════════════════════════════

def test_a_keyless_member_proposes_and_a_pending_gate_exists(workspace: Path) -> None:
    result = _propose(workspace)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["status"] == "pending"

    gates = _rows(workspace, "SELECT * FROM gate")
    assert len(gates) == 1
    gate = gates[0]
    assert gate["status"] == "pending"
    assert gate["action"] == "create-goal"
    assert gate["requesting_member_id"] == "MEM-001"
    assert (gate["target_entity_type"], gate["target_entity_id"]) == ("member", "MEM-001")
    assert json.loads(gate["context"]) == {
        "target": TARGET, "parent_entity_type": "member",
        "parent_entity_id": "MEM-001", "metric": METRIC, "reasoning": REASONING,
    }
    assert _count(workspace, "goal") == 0, "a proposal bound a goal before the Board decided"


def test_the_cli_and_the_mcp_tool_raise_the_same_gate(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from firm.mcp import tools as mcp_tools

    assert _propose(workspace).returncode == 0

    monkeypatch.setattr(mcp_tools, "_conn_factory", None)
    monkeypatch.setenv("FIRM_CWD", str(workspace))
    monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-001")
    via_mcp = json.loads(mcp_tools.firm_propose_goal(
        target=TARGET, parent_entity_type="member", parent_entity_id="MEM-001",
        reasoning=REASONING, metric=METRIC,
    ))
    assert "error" not in via_mcp, via_mcp

    columns = ("firm_id", "requesting_member_id", "action", "target_entity_type",
               "target_entity_id", "context", "status")
    cli_gate, mcp_gate = [
        tuple(row[c] for c in columns)
        for row in _rows(workspace, "SELECT * FROM gate ORDER BY created_at, id")
    ]
    assert cli_gate == mcp_gate


def test_approving_the_proposal_binds_the_goal(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from firm.services.gate import approve_gate

    result = _propose(workspace)
    assert result.returncode == 0, result.stderr
    gate_id = json.loads(result.stdout)["id"]

    monkeypatch.delenv("CADRE_MEMBER_ID", raising=False)  # the Board approves
    conn = _connect(workspace)
    try:
        approved = approve_gate(conn, gate_id)
    finally:
        conn.close()

    assert approved["status"] == "approved"
    goals = _rows(workspace, "SELECT * FROM goal")
    assert len(goals) == 1
    goal = goals[0]
    assert goal["id"] == approved["goal"]["id"]
    assert goal["target"] == TARGET
    assert (goal["parent_entity_type"], goal["parent_entity_id"]) == ("member", "MEM-001")
    assert json.loads(goal["metric"]) == {"value": 10, "unit": "posts/week"}


def test_the_board_can_still_create_a_goal(workspace: Path) -> None:
    result = _run(
        "goal", "create", "Ship the site",
        "--parent-type", "member", "--parent-id", "MEM-001",
        "--workspace", str(workspace),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True
    assert _count(workspace, "goal") == 1


# ═══════════════════════════════════════════════════════════════════════════
# Must fail, and must not write
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("holds_authority_key", [False, True])
def test_a_member_running_goal_create_is_refused_with_the_pointer(
    workspace: Path, holds_authority_key: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if holds_authority_key:
        monkeypatch.delenv("CADRE_MEMBER_ID", raising=False)  # the Board grants
        conn = _connect(workspace)
        try:
            grant_authority(conn, "MEM-002")
        finally:
            conn.close()

    result = _run(
        "goal", "create", "My own easy goal",
        "--parent-type", "member", "--parent-id", "MEM-002",
        "--workspace", str(workspace),
        member="MEM-002",
    )

    assert result.returncode == 1, result.stdout
    refusal = json.loads(result.stderr)
    assert refusal["ok"] is False
    assert "firm goal propose" in refusal["hint"]
    assert "\n" not in refusal["hint"]
    assert _count(workspace, "goal") == 0


def test_no_member_identity_is_refused_and_writes_nothing(workspace: Path) -> None:
    result = _propose(workspace, member=None)

    assert result.returncode == 1
    assert json.loads(result.stderr)["ok"] is False
    assert "no member identity" in result.stderr
    assert _count(workspace, "gate") == 0
