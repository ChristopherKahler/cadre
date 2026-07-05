"""Tests for firm.dashboard.server — state assembly and Board actions."""

from __future__ import annotations

import sqlite3
from unittest import mock

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, get
from firm.dashboard.server import _INDEX_HTML, assemble_state, perform_action


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {
        "id": "chrisai", "name": "ChrisAI",
        "north_star": "Grow the audience",
    })
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai", "name": "Sterling",
        "role": "CMO", "status": "active",
    })
    op = create(conn, "operation", {
        "id": "OPS-001", "firm_id": "chrisai", "name": "Content", "status": "active",
    })
    create(conn, "project", {
        "id": "PROJ-001", "firm_id": "chrisai", "operation_id": op["id"],
        "name": "IG Engine", "status": "in_progress", "due_date": "2026-12-31",
    })
    create(conn, "unit", {
        "id": "UNIT-001", "firm_id": "chrisai", "project_id": "PROJ-001",
        "name": "Pillar map", "status": "pending", "claimed_by": "MEM-001",
    })
    return conn


def test_assemble_state_shape():
    conn = _fresh_conn()
    state = assemble_state(conn, "chrisai")

    assert state["firm"]["name"] == "ChrisAI"
    assert state["notify_configured"] is False
    assert [m["id"] for m in state["roster"]] == ["MEM-001"]
    assert state["roster"][0]["load"] == 1
    assert state["roster"][0]["current_units"][0]["id"] == "UNIT-001"
    assert [u["id"] for u in state["units"]] == ["UNIT-001"]
    assert state["projects"][0]["name"] == "IG Engine"
    assert state["gates"] == []
    assert state["escalations"] == []
    for key in ("goals", "documents", "runs", "records", "comments",
                "cost_by_member", "budget_periods", "generated_at"):
        assert key in state


def test_assemble_state_strips_prompt_snapshots():
    conn = _fresh_conn()
    create(conn, "member_run", {
        "id": "RUN-001", "firm_id": "chrisai", "member_id": "MEM-001",
        "unit_id": "UNIT-001", "status": "completed",
        "started_at": "2026-07-05T10:00:00+00:00",
        "ended_at": "2026-07-05T10:05:00+00:00",
        "prompt_snapshot": "SECRET-LONG-PROMPT",
    })
    state = assemble_state(conn, "chrisai")
    run = state["runs"][0]
    assert "prompt_snapshot" not in run
    assert run["duration_sec"] == 300


@mock.patch("firm.services.gate.notify.send_board_dm", return_value={"sent": False, "reason": "test"})
def test_gate_actions(mock_dm):
    conn = _fresh_conn()
    from firm.services.gate import request_gate
    gate = request_gate(conn, "chrisai", {
        "requesting_member_id": "MEM-001",
        "action": "Approve the thing",
        "target_entity_type": "unit",
        "target_entity_id": "UNIT-001",
    })

    result = perform_action(conn, "gate-approve", gate["id"], {"comment": "ship it"})
    assert result["status"] == "approved"
    assert get(conn, "gate", gate["id"])["approver_comment"] == "ship it"


@mock.patch("firm.services.escalation.notify.send_board_dm", return_value={"sent": True, "reason": "test"})
def test_escalation_actions(mock_dm):
    conn = _fresh_conn()
    from firm.services.escalation import raise_escalation
    esc = raise_escalation(conn, "chrisai", {
        "raised_by_member_id": "MEM-001", "title": "Need decision",
    })["escalation"]

    acked = perform_action(conn, "escalation-acknowledge", esc["id"], {})
    assert acked["status"] == "acknowledged"
    resolved = perform_action(conn, "escalation-resolve", esc["id"], {"resolution": "done"})
    assert resolved["status"] == "resolved"


def test_goal_metric_action():
    conn = _fresh_conn()
    from firm.services.goal import create_goal
    goal = create_goal(conn, "chrisai", {
        "target": ">= 5 assets",
        "parent_entity_type": "operation",
        "parent_entity_id": "OPS-001",
    })
    result = perform_action(conn, "goal-metric", goal["id"], {"current": 6, "value": 5})
    assert result["metric"]["current"] == 6


def test_unknown_action_raises():
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="Unknown action"):
        perform_action(conn, "gate-detonate", "GATE-001", {})


def test_index_html_ships_with_package():
    assert _INDEX_HTML.exists()
    content = _INDEX_HTML.read_text()
    assert "Cadre Boardroom" in content
    assert "/api/state" in content


# ---------------------------------------------------------------------------
# Document reading + Board communication actions
# ---------------------------------------------------------------------------


def test_read_document_returns_content_and_comments(tmp_path):
    from firm.dashboard.server import read_document
    conn = _fresh_conn()
    doc_file = tmp_path / "docs" / "report.md"
    doc_file.parent.mkdir()
    doc_file.write_text("# Report\n\nAll good.")
    create(conn, "document", {
        "id": "DOC-001", "firm_id": "chrisai", "name": "Report",
        "type": "report", "content_path": "docs/report.md",
        "parent_entity_type": "unit", "parent_entity_id": "UNIT-001",
    })
    from firm.services.comment import create_comment
    create_comment(conn, "chrisai", {
        "parent_entity_type": "document", "parent_entity_id": "DOC-001",
        "body": "Nice work", "author_type": "board",
    })

    out = read_document(conn, tmp_path, "DOC-001")
    assert out["content"] == "# Report\n\nAll good."
    assert out["document"]["name"] == "Report"
    assert [c["body"] for c in out["comments"]] == ["Nice work"]


def test_read_document_missing_doc_raises():
    from firm.dashboard.server import read_document
    from pathlib import Path
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="not found"):
        read_document(conn, Path("/tmp"), "DOC-404")


def test_comment_create_action_posts_as_board():
    conn = _fresh_conn()
    result = perform_action(conn, "comment-create", "unit", {
        "parent_entity_id": "UNIT-001", "body": "Board direction: focus on hooks",
    })
    assert result["author_type"] == "board"
    assert result["parent_entity_id"] == "UNIT-001"


def test_unit_create_action_assigns_member():
    conn = _fresh_conn()
    result = perform_action(conn, "unit-create", "new", {
        "name": "New task from the Board",
        "project_id": "PROJ-001",
        "assignee_member_id": "MEM-001",
        "description": "Do the thing",
        "priority": "high",
    })
    assert result["assignee_member_id"] == "MEM-001"
    assert result["priority"] == "high"
    row = get(conn, "unit", result["id"])
    assert row["status"] == "pending"


def test_unit_create_requires_name_and_project():
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="required"):
        perform_action(conn, "unit-create", "new", {"name": "no project"})


def test_board_comment_reaches_member_prompt():
    """The full loop: Board comments on a unit → member's next briefing shows it."""
    from firm.pulse.prompt import _render_unit_briefing
    conn = _fresh_conn()
    perform_action(conn, "comment-create", "unit", {
        "parent_entity_id": "UNIT-001", "body": "Ship the hard case first",
    })
    briefing = _render_unit_briefing(conn, "UNIT-001")
    assert "Comments on this Unit" in briefing
    assert "THE BOARD: Ship the hard case first" in briefing
