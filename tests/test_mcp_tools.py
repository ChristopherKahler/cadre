"""MCP tool tests — verify tools wrap services correctly and handle errors.

Tests call tool functions directly (not via MCP transport) with a
patched _conn_factory pointing to an in-memory seeded DB.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import get
from firm.mcp import tools as mcp_tools
from firm.seed import seed_chrisai


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_test_conn: sqlite3.Connection | None = None


def _make_test_conn() -> sqlite3.Connection:
    """Return the shared test connection (seeded once)."""
    assert _test_conn is not None, "Test connection not initialized"
    return _test_conn


@pytest.fixture(autouse=True)
def _patch_conn(monkeypatch):
    """Seed a fresh in-memory DB and patch the tool connection factory."""
    global _test_conn
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    seed_chrisai(conn)
    _test_conn = conn
    monkeypatch.setattr(mcp_tools, "_conn_factory", _make_test_conn)
    yield
    # Don't close — _safe() calls conn.close() which is a no-op on shared :memory:
    # But we need to prevent actual close from killing the shared conn
    _test_conn = None


    # _safe() skips close when _conn_factory is set (test mode)


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

class TestReadTools:

    def test_list_members_returns_three(self):
        result = json.loads(mcp_tools.firm_list_members())
        assert len(result) == 3

    def test_view_member_returns_sterling(self):
        result = json.loads(mcp_tools.firm_view_member("MEM-002"))
        assert result["name"] == "Sterling"
        assert result["role"] == "Chief Marketing Officer"

    def test_list_units_returns_seeded(self):
        result = json.loads(mcp_tools.firm_list_units())
        assert len(result) >= 1
        assert result[0]["id"] == "UNT-001"

    def test_list_gates_returns_empty(self):
        result = json.loads(mcp_tools.firm_list_gates())
        assert result == []

    def test_list_operations(self):
        result = json.loads(mcp_tools.firm_list_operations())
        assert len(result) == 1
        assert result[0]["name"] == "Content Pipeline"

    def test_list_projects(self):
        result = json.loads(mcp_tools.firm_list_projects())
        assert len(result) == 1
        assert result[0]["name"] == "Blog v1"

    def test_list_contracts(self):
        result = json.loads(mcp_tools.firm_list_contracts())
        assert len(result) == 3

    def test_view_contract(self):
        result = json.loads(mcp_tools.firm_view_contract("CON-001"))
        assert result["name"] == "Quill Blog Author Contract"

    def test_get_direct_reports(self):
        result = json.loads(mcp_tools.firm_get_direct_reports("MEM-002"))
        ids = {r["id"] for r in result}
        assert ids == {"MEM-001", "MEM-003"}

    def test_firm_status(self):
        result = json.loads(mcp_tools.firm_status())
        assert "error" not in result


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------

class TestWriteTools:

    def test_create_unit(self):
        result = json.loads(mcp_tools.firm_create_unit(
            name="Test post",
            project_id="PRJ-001",
        ))
        assert "error" not in result
        assert result["name"] == "Test post"
        assert result["id"].startswith("UNIT-")

    def test_checkout_unit(self):
        # Create a fresh unit first
        created = json.loads(mcp_tools.firm_create_unit(
            name="Checkout test",
            project_id="PRJ-001",
        ))
        result = json.loads(mcp_tools.firm_checkout_unit(created["id"], "MEM-001"))
        assert "error" not in result

    def test_request_gate(self):
        result = json.loads(mcp_tools.firm_request_gate(
            requesting_member_id="MEM-002",
            action="hire_member",
            target_entity_type="firm",
            target_entity_id="chrisai",
            context="Need a designer",
        ))
        assert "error" not in result
        assert result["id"].startswith("GATE-")
        assert result["status"] == "pending"

    def test_gate_resolution_is_not_a_member_tool(self):
        # Fork 010: a Member that can approve its own Gate makes every other
        # control theatre. Members request; the Board resolves (hub action
        # endpoint / CLI). The tools must be ABSENT, not merely discouraged.
        assert not hasattr(mcp_tools, "firm_approve_gate")
        assert not hasattr(mcp_tools, "firm_reject_gate")

    def test_gate_resolves_through_the_board_surface(self):
        from firm.services import gate as gate_svc
        gate = json.loads(mcp_tools.firm_request_gate(
            requesting_member_id="MEM-001",
            action="test_action",
            target_entity_type="firm",
            target_entity_id="chrisai",
        ))
        result = gate_svc.approve_gate(
            mcp_tools._get_conn(), gate["id"], {"approver_comment": "Looks good"})
        assert result["status"] == "approved"

    def test_create_comment(self):
        result = json.loads(mcp_tools.firm_create_comment(
            body="Great work on this unit",
            parent_entity_type="unit",
            parent_entity_id="UNT-001",
            author_type="member",
            author_id="MEM-002",
        ))
        assert "error" not in result

    def test_propose_goal_raises_gate_and_approval_materializes(self, monkeypatch):
        # Fork 008: a Member may argue for the number; only the Board sets it.
        monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-001")
        gate = json.loads(mcp_tools.firm_propose_goal(
            target="Publish 10 blog posts",
            parent_entity_type="operation",
            parent_entity_id="OP-001",
            reasoning="output volume is the outcome I own",
        ))
        assert "error" not in gate
        assert gate["id"].startswith("GATE-")
        from firm.services import gate as gate_svc
        approved = gate_svc.approve_gate(mcp_tools._get_conn(), gate["id"])
        assert approved["goal"]["id"].startswith("GOAL-")
        assert approved["goal"]["target"] == "Publish 10 blog posts"

    def test_propose_goal_without_member_identity_is_refused(self, monkeypatch):
        monkeypatch.delenv("CADRE_MEMBER_ID", raising=False)
        result = json.loads(mcp_tools.firm_propose_goal(
            target="x", parent_entity_type="operation",
            parent_entity_id="OP-001", reasoning="r",
        ))
        assert "error" in result

    def test_rejected_goal_proposal_creates_nothing(self, monkeypatch):
        monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-001")
        before = len(json.loads(mcp_tools.firm_list_goals()))
        gate = json.loads(mcp_tools.firm_propose_goal(
            target="An easy goal I can definitely hit",
            parent_entity_type="operation",
            parent_entity_id="OP-001",
            reasoning="sandbagging",
        ))
        from firm.services import gate as gate_svc
        gate_svc.reject_gate(mcp_tools._get_conn(), gate["id"],
                             {"approver_comment": "set a real bar"})
        assert len(json.loads(mcp_tools.firm_list_goals())) == before

    def test_create_operation(self):
        result = json.loads(mcp_tools.firm_create_operation(name="Social Pipeline"))
        assert "error" not in result
        assert result["name"] == "Social Pipeline"

    def test_create_project(self):
        result = json.loads(mcp_tools.firm_create_project(
            name="YouTube v1",
            operation_id="OP-001",
            due_date="2026-12-31",
        ))
        assert "error" not in result

    def test_create_document(self):
        result = json.loads(mcp_tools.firm_create_document(
            name="Brand Guide",
            doc_type="brief",
            content_path="/docs/brand-guide.md",
            parent_entity_type="operation",
            parent_entity_id="OP-001",
        ))
        assert "error" not in result


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:

    def test_view_nonexistent_member(self):
        result = json.loads(mcp_tools.firm_view_member("MEM-999"))
        assert "error" in result

    def test_create_unit_bad_project(self):
        result = json.loads(mcp_tools.firm_create_unit(
            name="Bad unit",
            project_id="PRJ-999",
        ))
        assert "error" in result

    def test_request_gate_bad_member(self):
        result = json.loads(mcp_tools.firm_request_gate(
            requesting_member_id="MEM-999",
            action="test",
            target_entity_type="firm",
            target_entity_id="chrisai",
        ))
        assert "error" in result

    def test_checkout_already_claimed(self):
        # UNT-001 is already claimed by MEM-001 in seed
        result = json.loads(mcp_tools.firm_checkout_unit("UNT-001", "MEM-002"))
        assert "error" in result

    def test_update_member_no_fields(self):
        result = json.loads(mcp_tools.firm_update_member("MEM-001"))
        assert "error" in result
        assert "No fields" in result["error"]


# ---------------------------------------------------------------------------
# firm_complete_unit — correct service arity (COM-005)
# ---------------------------------------------------------------------------


class TestCompleteUnit:

    def test_complete_unit_flips_status_to_done(self):
        result = json.loads(mcp_tools.firm_complete_unit("UNT-001", "MEM-001"))
        assert "error" not in result

        unit = json.loads(mcp_tools.firm_view_unit("UNT-001"))
        assert unit["status"] == "done"


# ---------------------------------------------------------------------------
# firm_update_goal_metric — canonical metric refresh (COM-010)
# ---------------------------------------------------------------------------


class TestUpdateGoalMetric:

    @staticmethod
    def _board_approved_goal(monkeypatch, target, metric=""):
        """Goals only exist Board-approved now — go through the front door.
        Approval is a Board surface (fork 010), so it goes via the service."""
        from firm.services import gate as gate_svc
        monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-001")
        ops = json.loads(mcp_tools.firm_list_operations())
        gate = json.loads(mcp_tools.firm_propose_goal(
            target, "operation", ops[0]["id"],
            reasoning="test", metric=metric,
        ))
        return gate_svc.approve_gate(mcp_tools._get_conn(), gate["id"])["goal"]

    def test_shapes_metric_json(self, monkeypatch):
        goal = self._board_approved_goal(
            monkeypatch, ">= 5 approved-ready assets",
            metric="publish_ready_queue_depth")

        result = json.loads(mcp_tools.firm_update_goal_metric(
            goal["id"], current="6", value="5", unit="assets",
        ))
        assert "error" not in result

        metric = result["metric"]
        assert metric["current"] == 6
        assert metric["value"] == 5
        assert metric["unit"] == "assets"
        assert metric["type"] == "publish_ready_queue_depth"

    def test_no_fields_returns_error(self, monkeypatch):
        goal = self._board_approved_goal(monkeypatch, "target")
        result = json.loads(mcp_tools.firm_update_goal_metric(goal["id"]))
        assert "error" in result


# ---------------------------------------------------------------------------
# firm_escalate — Board escalation with dedup (arity guard)
# ---------------------------------------------------------------------------


class TestEscalationTools:

    def test_escalate_and_resolve_roundtrip(self):
        result = json.loads(mcp_tools.firm_escalate(
            "MEM-001", "Need a Board decision on X", body="details",
            severity="high",
        ))
        assert "error" not in result
        assert result["deduped"] is False
        esc_id = result["escalation"]["id"]

        # duplicate raise → deduped, no second row
        dup = json.loads(mcp_tools.firm_escalate(
            "MEM-001", "Need a Board decision on X",
        ))
        assert dup["deduped"] is True
        assert len(json.loads(mcp_tools.firm_list_escalations(status="open"))) == 1

        resolved = json.loads(mcp_tools.firm_resolve_escalation(
            esc_id, resolution="decided",
        ))
        assert resolved["status"] == "resolved"


# ---------------------------------------------------------------------------
# Tool surface — the registry is a public contract
# ---------------------------------------------------------------------------


class TestToolSurface:

    def test_registered_tool_names_are_exactly_the_public_surface(self):
        """Pin the exact tool-name list, not just a count.

        The e2e smoke test's bare count drifted stale (asserted 33 while five
        commits moved the surface to 37) because nothing in pytest pinned the
        registry. External consumers integrate against these names, so a tool
        appearing, vanishing, or renaming must fail loudly here — and the diff
        must say WHICH name moved. Deliberate surface changes update this list
        and scripts/e2e-test.sh step 8 together.
        """
        expected = [
            "firm_checkout_unit",
            "firm_complete_unit",
            "firm_create_comment",
            "firm_create_document",
            "firm_create_member",
            "firm_create_operation",
            "firm_create_project",
            "firm_create_unit",
            "firm_detect_gaps",
            "firm_escalate",
            "firm_get_direct_reports",
            "firm_list_comments",
            "firm_list_contracts",
            "firm_list_documents",
            "firm_list_escalations",
            "firm_list_gates",
            "firm_list_goals",
            "firm_list_members",
            "firm_list_operations",
            "firm_list_projects",
            "firm_list_units",
            "firm_propose_goal",
            "firm_propose_hire",
            "firm_release_unit",
            "firm_request_gate",
            "firm_resolve_escalation",
            "firm_status",
            "firm_update_document",
            "firm_update_goal",
            "firm_update_goal_metric",
            "firm_update_member",
            "firm_view_contract",
            "firm_view_escalation",
            "firm_view_gate",
            "firm_view_goal",
            "firm_view_member",
            "firm_view_unit",
        ]
        actual = sorted(mcp_tools.mcp._tool_manager._tools.keys())
        assert actual == expected

    def test_gate_resolution_is_not_on_the_member_surface(self):
        """Members request Gates; they never resolve them (fork 010).

        The approve/reject tools were removed from the MCP surface in
        25e7420 — a constitutional line, not an oversight. This pins it."""
        names = set(mcp_tools.mcp._tool_manager._tools.keys())
        assert not {n for n in names if "gate" in n and (
            "approve" in n or "reject" in n or "resolve" in n)}
