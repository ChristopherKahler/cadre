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

    def test_approve_gate(self):
        gate = json.loads(mcp_tools.firm_request_gate(
            requesting_member_id="MEM-001",
            action="test_action",
            target_entity_type="firm",
            target_entity_id="chrisai",
        ))
        result = json.loads(mcp_tools.firm_approve_gate(gate["id"], "Looks good"))
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

    def test_create_goal(self):
        result = json.loads(mcp_tools.firm_create_goal(
            target="Publish 10 blog posts",
            parent_entity_type="operation",
            parent_entity_id="OP-001",
        ))
        assert "error" not in result
        assert result["id"].startswith("GOAL-")

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

    def test_shapes_metric_json(self):
        ops = json.loads(mcp_tools.firm_list_operations())
        goal = json.loads(mcp_tools.firm_create_goal(
            ">= 5 approved-ready assets", "operation", ops[0]["id"],
            metric="publish_ready_queue_depth",
        ))

        result = json.loads(mcp_tools.firm_update_goal_metric(
            goal["id"], current="6", value="5", unit="assets",
        ))
        assert "error" not in result

        metric = result["metric"]
        assert metric["current"] == 6
        assert metric["value"] == 5
        assert metric["unit"] == "assets"
        assert metric["type"] == "publish_ready_queue_depth"

    def test_no_fields_returns_error(self):
        ops = json.loads(mcp_tools.firm_list_operations())
        goal = json.loads(mcp_tools.firm_create_goal(
            "target", "operation", ops[0]["id"],
        ))
        result = json.loads(mcp_tools.firm_update_goal_metric(goal["id"]))
        assert "error" in result
