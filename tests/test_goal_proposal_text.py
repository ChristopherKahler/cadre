"""The charter and the per-run prompt teach ``firm goal propose`` (issue #17).

The charter the wire step writes told every Member to propose its goal with
``firm_propose_goal``, a firm MCP tool that hub-founded firms never load. The
charter now names the CLI verb. Charters already written into firms still name
the MCP tool, so the per-run prompt names the verb too, and names the MCP tool
only when the firm's ``.mcp.json`` loads the firm server.

The prompt tells a Member to propose only while it has nothing to show: no
active goal attached to it, no approved proposal whose goal is still active, and
no proposal already waiting on the Board. Otherwise every run would raise
another Gate.

These tests seed Gates through ``request_gate`` and ``approve_gate`` rather than
the new service, so each one fails on main by its own assertion.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.dashboard import discovery
from firm.dashboard.wiring import _render_charter
from firm.pulse.prompt import assemble_prompt
from firm.services.gate import approve_gate, request_gate
from firm.services.goal import create_goal

CLI_VERB = "firm goal propose"
MCP_TOOL = "firm_propose_goal"


@pytest.fixture(autouse=True)
def _board_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seeding goals and approving Gates is the Board acting."""
    monkeypatch.delenv("CADRE_MEMBER_ID", raising=False)


def _seed(workspace: Path, members: tuple[str, ...] = ("MEM-001",)) -> sqlite3.Connection:
    db = workspace / ".firm" / "firm.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    for member_id in members:
        create(conn, "member", {"id": member_id, "firm_id": "chrisai",
                                "name": f"Member {member_id}", "role": "Writer",
                                "status": "active"})
    create(conn, "operation", {"id": "OPS-001", "firm_id": "chrisai",
                               "name": "Content", "status": "active"})
    create(conn, "project", {"id": "PRJ-010", "firm_id": "chrisai",
                             "operation_id": "OPS-001", "name": "Posts",
                             "status": "in_progress", "due_date": "2026-12-31"})
    create(conn, "unit", {"id": "UNIT-100", "firm_id": "chrisai",
                          "project_id": "PRJ-010", "name": "Write a post",
                          "status": "pending"})
    return conn


def _raise_proposal(conn: sqlite3.Connection, member_id: str, parent_type: str,
                    parent_id: str, target: str) -> dict:
    """A create-goal Gate shaped exactly as firm_propose_goal raised it on main."""
    return request_gate(conn, "chrisai", {
        "requesting_member_id": member_id,
        "action": "create-goal",
        "target_entity_type": parent_type,
        "target_entity_id": parent_id,
        "context": json.dumps({
            "target": target, "parent_entity_type": parent_type,
            "parent_entity_id": parent_id, "metric": "", "reasoning": "because",
        }),
    })


def _prompt(conn: sqlite3.Connection, workspace: Path, member_id: str = "MEM-001") -> str:
    return assemble_prompt(conn, "chrisai", member_id, "UNIT-100", cwd=str(workspace))


def _command_lines(prompt: str) -> list[str]:
    return [line.strip() for line in prompt.splitlines()
            if line.strip().startswith(CLI_VERB)]


def test_the_charter_teaches_the_cli_verb_and_names_no_mcp_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery, "cli_survey", lambda: [])  # no live host probes
    charter = _render_charter(
        firm={"name": "ChrisAI", "description": "A firm.",
              "north_star": "Ship one thing a week."},
        members=[{"name": "Cooper", "role": "Writer"}],
        plan={"firm_id": "chrisai", "members": [{"name": "Cooper"}], "mcp": []},
        base={"present": False, "extensions": []},
        today="2026-09-12",
    )
    teaching = " ".join(charter.split())
    assert f"`{CLI_VERB} " in teaching
    assert "--parent-id $CADRE_MEMBER_ID" in teaching
    assert "--reasoning" in teaching
    assert MCP_TOOL not in charter


def test_the_prompt_for_a_member_with_no_approved_goal_names_the_cli_verb(
    tmp_path: Path,
) -> None:
    conn = _seed(tmp_path)
    try:
        prompt = _prompt(conn, tmp_path)
    finally:
        conn.close()

    commands = _command_lines(prompt)
    assert len(commands) == 1, prompt
    for part in ("--parent-type member", "--parent-id $CADRE_MEMBER_ID",
                 "--metric", "--reasoning"):
        assert part in commands[0]
    assert not (tmp_path / ".mcp.json").exists()
    assert MCP_TOOL not in prompt


def test_the_prompt_names_the_mcp_tool_only_when_the_firm_loads_it(
    tmp_path: Path,
) -> None:
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"firm": {"command": "python"}}}', encoding="utf-8",
    )
    conn = _seed(tmp_path)
    try:
        prompt = _prompt(conn, tmp_path)
    finally:
        conn.close()

    assert len(_command_lines(prompt)) == 1
    assert f"mcp__firm__{MCP_TOOL}" in prompt


def test_only_a_member_with_nothing_to_show_is_told_to_propose(tmp_path: Path) -> None:
    conn = _seed(tmp_path, members=("MEM-001", "MEM-002", "MEM-003", "MEM-004"))
    try:
        # MEM-002: the Board attached a goal to it directly.
        create_goal(conn, "chrisai", {
            "target": "Answer every lead within a day",
            "parent_entity_type": "member", "parent_entity_id": "MEM-002",
        })
        # MEM-003: its proposal is still waiting on the Board.
        pending = _raise_proposal(conn, "MEM-003", "member", "MEM-003",
                                  "Ten posts a week")
        # MEM-004: the Board approved its proposal for the operation it runs.
        approved = _raise_proposal(conn, "MEM-004", "operation", "OPS-001",
                                   "Grow the list to 5000")
        approve_gate(conn, approved["id"])

        prompts = {m: _prompt(conn, tmp_path, m)
                   for m in ("MEM-001", "MEM-002", "MEM-003", "MEM-004")}
    finally:
        conn.close()

    assert len(_command_lines(prompts["MEM-001"])) == 1
    assert _command_lines(prompts["MEM-002"]) == []
    assert _command_lines(prompts["MEM-003"]) == []
    assert f"Your goal proposal {pending['id']} is waiting for the Board" in prompts["MEM-003"]
    assert _command_lines(prompts["MEM-004"]) == []
