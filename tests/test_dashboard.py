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


# ---------------------------------------------------------------------------
# Doc comment → revision unit loop
# ---------------------------------------------------------------------------


def _doc_on_unit(conn):
    create(conn, "unit", {
        "id": "UNIT-050", "firm_id": "chrisai", "project_id": "PROJ-001",
        "name": "Produce the report", "status": "done",
        "assignee_member_id": "MEM-001",
    })
    return create(conn, "document", {
        "id": "DOC-010", "firm_id": "chrisai", "name": "Season report",
        "type": "report", "content_path": "docs/deliverables/report.md",
        "parent_entity_type": "unit", "parent_entity_id": "UNIT-050",
    })


def test_doc_revision_creates_comment_and_assigned_unit():
    conn = _fresh_conn()
    _doc_on_unit(conn)

    result = perform_action(conn, "doc-revision", "DOC-010", {
        "body": "Hook is weak — rewrite the first three lines.",
    })

    assert result["comment"]["author_type"] == "board"
    unit = result["unit"]
    assert unit["name"].startswith("Revise DOC-010")
    assert unit["project_id"] == "PROJ-001"
    assert unit["assignee_member_id"] == "MEM-001"
    assert unit["priority"] == "high"
    assert "Hook is weak" in unit["description"]
    assert "revision" in unit["tags"]

    # The revision direction reaches the member's briefing via the unit itself
    from firm.pulse.prompt import _render_unit_briefing
    briefing = _render_unit_briefing(conn, unit["id"])
    assert "Hook is weak" in briefing

    from firm.core.repo import find
    assert len(find(conn, "records", event_type="document.revision_requested")) == 1


def test_doc_revision_requires_comment_body():
    conn = _fresh_conn()
    _doc_on_unit(conn)
    with pytest.raises(ValueError, match="comment_body"):
        perform_action(conn, "doc-revision", "DOC-010", {"body": "  "})


def test_doc_revision_without_producing_unit_fails_cleanly():
    conn = _fresh_conn()
    create(conn, "document", {
        "id": "DOC-011", "firm_id": "chrisai", "name": "Firm-level doc",
        "type": "brief", "content_path": "docs/x.md",
        "parent_entity_type": "firm", "parent_entity_id": "chrisai",
    })
    with pytest.raises(ValueError, match="producing unit"):
        perform_action(conn, "doc-revision", "DOC-011", {"body": "fix it"})


# ---------------------------------------------------------------------------
# Member profile + command surface
# ---------------------------------------------------------------------------


def test_member_profile_shape(tmp_path):
    from firm.dashboard.server import member_profile
    conn = _fresh_conn()
    create(conn, "contract", {
        "id": "CON-001", "firm_id": "chrisai", "name": "Standard",
        "runtime_type": "claude_code",
    })
    from firm.services.member import update_member
    update_member(conn, "MEM-001", {"contract_id": "CON-001"})
    create(conn, "member_run", {
        "id": "RUN-001", "firm_id": "chrisai", "member_id": "MEM-001",
        "unit_id": "UNIT-001", "status": "completed",
        "started_at": "2026-07-05T10:00:00+00:00",
        "ended_at": "2026-07-05T10:04:00+00:00",
    })

    P = member_profile(conn, tmp_path, "MEM-001")

    assert P["member"]["id"] == "MEM-001"
    assert P["contract"]["name"] == "Standard"
    assert P["stats"]["runs_total"] == 1
    assert P["stats"]["success_rate"] == 100
    assert P["stats"]["avg_duration_sec"] == 240
    assert P["current_units"][0]["id"] == "UNIT-001"
    assert "Your Identity" in P["prompt_preview"]
    assert "Your Assignment" in P["prompt_preview"]  # has a claimed unit
    assert P["instructions"] == ""
    assert isinstance(P["contracts"], list) and len(P["contracts"]) == 1


def test_member_update_switches_contract_and_manager():
    conn = _fresh_conn()
    create(conn, "contract", {
        "id": "CON-002", "firm_id": "chrisai", "name": "Heavy",
        "runtime_type": "claude_code",
    })
    create(conn, "member", {
        "id": "MEM-002", "firm_id": "chrisai", "name": "Quill",
        "role": "Writer", "status": "active", "reports_to_member_id": "MEM-001",
    })

    updated = perform_action(conn, "member-update", "MEM-002", {
        "role": "Senior Writer", "contract_id": "CON-002",
        "reports_to_member_id": None,
    })
    assert updated["role"] == "Senior Writer"
    assert updated["contract_id"] == "CON-002"
    assert updated["reports_to_member_id"] is None  # back to the Board


def test_write_instructions_persists_and_reaches_prompt(tmp_path):
    from firm.dashboard.server import write_instructions
    from firm.pulse.prompt import _render_member_identity
    conn = _fresh_conn()

    write_instructions(conn, tmp_path, "MEM-001", "Always ship the hard case first.")

    path = tmp_path / ".firm" / "instructions" / "MEM-001.md"
    assert path.read_text() == "Always ship the hard case first."
    identity = _render_member_identity(conn, "MEM-001", str(tmp_path))
    assert "Always ship the hard case first." in identity
    from firm.core.repo import find
    assert len(find(conn, "records", event_type="member.instructions_updated")) == 1


def test_standing_notes_reach_member_identity():
    from firm.pulse.prompt import _render_member_identity
    conn = _fresh_conn()
    perform_action(conn, "comment-create", "member", {
        "parent_entity_id": "MEM-001", "body": "Prefer faceless formats this month.",
    })
    identity = _render_member_identity(conn, "MEM-001", "/tmp")
    assert "Standing Notes" in identity
    assert "THE BOARD: Prefer faceless formats this month." in identity


# ---------------------------------------------------------------------------
# Evidence resolution + member artifacts
# ---------------------------------------------------------------------------


@mock.patch("firm.services.gate.notify.send_board_dm", return_value={"sent": False, "reason": "test"})
def test_pending_gate_carries_related_docs(mock_dm):
    conn = _fresh_conn()
    create(conn, "document", {
        "id": "DOC-001", "firm_id": "chrisai", "name": "The report",
        "type": "report", "content_path": "docs/r.md",
        "parent_entity_type": "unit", "parent_entity_id": "UNIT-001",
    })
    create(conn, "document", {
        "id": "DOC-002", "firm_id": "chrisai", "name": "Other doc",
        "type": "brief", "content_path": "docs/o.md",
        "parent_entity_type": "firm", "parent_entity_id": "chrisai",
    })
    from firm.services.gate import request_gate
    request_gate(conn, "chrisai", {
        "requesting_member_id": "MEM-001",
        "action": "Approve the report — see also DOC-002 for context",
        "target_entity_type": "unit", "target_entity_id": "UNIT-001",
    })

    state = assemble_state(conn, "chrisai")
    gate = state["gates"][0]
    ids = {d["id"] for d in gate["related_docs"]}
    assert ids == {"DOC-001", "DOC-002"}  # target-attached + text-referenced


def test_member_profile_artifacts_grouped_by_producer(tmp_path):
    from firm.dashboard.server import member_profile
    conn = _fresh_conn()
    create(conn, "document", {
        "id": "DOC-001", "firm_id": "chrisai", "name": "Produced by unit",
        "type": "report", "content_path": "docs/a.md",
        "parent_entity_type": "unit", "parent_entity_id": "UNIT-001",
    })
    create(conn, "document", {
        "id": "DOC-002", "firm_id": "chrisai", "name": "Someone else's",
        "type": "report", "content_path": "docs/b.md",
        "parent_entity_type": "firm", "parent_entity_id": "chrisai",
    })

    P = member_profile(conn, tmp_path, "MEM-001")
    assert [a["id"] for a in P["artifacts"]] == ["DOC-001"]
    assert P["artifacts"][0]["type"] == "report"


# ---------------------------------------------------------------------------
# Custom views seam (.firm/dashboard/views.json)
# ---------------------------------------------------------------------------

def _write_manifest(tmp_path, views):
    import json as _json
    d = tmp_path / ".firm" / "dashboard"
    d.mkdir(parents=True, exist_ok=True)
    (d / "views.json").write_text(_json.dumps({"views": views}), encoding="utf-8")


def test_load_custom_views_absent_manifest(tmp_path):
    from firm.dashboard.server import load_custom_views
    assert load_custom_views(tmp_path) == []


def test_load_custom_views_parses_and_skips_bad_entries(tmp_path):
    from firm.dashboard.server import load_custom_views
    _write_manifest(tmp_path, [
        {"id": "table", "title": "The Table", "fragment": "dashboard/views/table.html",
         "files": {"game_state": "game/game_state.json"}},
        {"id": "BAD ID!", "fragment": "x.html"},          # invalid id
        {"id": "no-fragment", "title": "nope"},            # missing fragment
        "not-a-dict",
    ])
    views = load_custom_views(tmp_path)
    assert [v["id"] for v in views] == ["table"]
    assert views[0]["title"] == "The Table"
    assert views[0]["files"] == {"game_state": "game/game_state.json"}


def test_load_custom_views_malformed_json_degrades(tmp_path):
    from firm.dashboard.server import load_custom_views
    d = tmp_path / ".firm" / "dashboard"
    d.mkdir(parents=True)
    (d / "views.json").write_text("{nope", encoding="utf-8")
    assert load_custom_views(tmp_path) == []


def test_read_view_fragment_and_file(tmp_path):
    from firm.dashboard.server import load_custom_views, read_view_file, read_view_fragment
    _write_manifest(tmp_path, [
        {"id": "table", "fragment": "dashboard/views/table.html",
         "files": {"game_state": "game/game_state.json", "log": "game/log.jsonl"}},
    ])
    vd = tmp_path / ".firm" / "dashboard" / "views"
    vd.mkdir(parents=True)
    (vd / "table.html").write_text("<h1>hi</h1>", encoding="utf-8")
    gd = tmp_path / ".firm" / "game"
    gd.mkdir(parents=True)
    (gd / "game_state.json").write_text('{"mode": "campaign"}', encoding="utf-8")
    (gd / "log.jsonl").write_text('{"kind": "narration"}\n', encoding="utf-8")

    view = load_custom_views(tmp_path)[0]
    assert read_view_fragment(tmp_path, view) == b"<h1>hi</h1>"
    content, ctype = read_view_file(tmp_path, view, "game_state")
    assert ctype == "application/json"
    assert b"campaign" in content
    content, ctype = read_view_file(tmp_path, view, "log")
    assert ctype.startswith("text/plain")


def test_read_view_file_undeclared_key_rejected(tmp_path):
    from firm.dashboard.server import load_custom_views, read_view_file
    _write_manifest(tmp_path, [
        {"id": "table", "fragment": "dashboard/views/table.html", "files": {}},
    ])
    view = load_custom_views(tmp_path)[0]
    with pytest.raises(ValueError, match="not declared"):
        read_view_file(tmp_path, view, "secrets")


def test_view_paths_cannot_escape_firm_dir(tmp_path):
    from firm.dashboard.server import load_custom_views, read_view_file, read_view_fragment
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    _write_manifest(tmp_path, [
        {"id": "evil", "fragment": "../outside.txt",
         "files": {"leak": "../../outside.txt"}},
    ])
    view = load_custom_views(tmp_path)[0]
    with pytest.raises(ValueError, match="escapes"):
        read_view_fragment(tmp_path, view)
    with pytest.raises(ValueError, match="escapes"):
        read_view_file(tmp_path, view, "leak")
