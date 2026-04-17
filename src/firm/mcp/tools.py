"""Firm MCP tools — entity CRUD wrapping firm.services.

Each tool connects to the SQLite DB, calls the corresponding service
function, and returns JSON-serializable results. Errors return
{"error": str} instead of raising.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations
from firm.services import comment as comment_svc
from firm.services import contract as contract_svc
from firm.services import document as document_svc
from firm.services import firm_svc as firm_svc_mod
from firm.services import gate as gate_svc
from firm.services import goal as goal_svc
from firm.services import member as member_svc
from firm.services import operation as operation_svc
from firm.services import project as project_svc
from firm.services import unit as unit_svc
from firm.heuristics import gaps as gaps_heuristics

mcp = FastMCP("firm")

# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

# Allow tests to inject a connection factory
_conn_factory: Any = None


def _get_conn() -> sqlite3.Connection:
    """Get a connection to the firm database."""
    if _conn_factory is not None:
        return _conn_factory()
    cwd = os.environ.get("FIRM_CWD", os.getcwd())
    return connect(get_db_path(Path(cwd)))


def _safe(fn, *args, **kwargs) -> dict | list:
    """Call fn, return result or {"error": str} on ValueError."""
    conn = _get_conn()
    try:
        result = fn(conn, *args, **kwargs)
        if isinstance(result, list):
            return [dict(r) if hasattr(r, "keys") else r for r in result]
        if hasattr(result, "keys"):
            return dict(result)
        return result
    except (ValueError, sqlite3.IntegrityError) as exc:
        return {"error": str(exc)}
    finally:
        if _conn_factory is None:
            conn.close()


# ---------------------------------------------------------------------------
# Member tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_members(firm_id: str = "chrisai", status: str = "", reports_to: str = "") -> str:
    """List all members in the firm. Filter by status or reports_to member ID."""
    kwargs: dict[str, Any] = {}
    if status:
        kwargs["status"] = status
    if reports_to:
        kwargs["reports_to"] = reports_to
    result = _safe(member_svc.list_members, firm_id, **kwargs)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_view_member(member_id: str) -> str:
    """View a single member by ID (e.g. MEM-001)."""
    result = _safe(member_svc.view_member, member_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_create_member(name: str, role: str, firm_id: str = "chrisai", description: str = "", reports_to_member_id: str = "", contract_id: str = "") -> str:
    """Create a new firm member with a name and role."""
    data: dict[str, Any] = {"name": name, "role": role}
    if description:
        data["description"] = description
    if reports_to_member_id:
        data["reports_to_member_id"] = reports_to_member_id
    if contract_id:
        data["contract_id"] = contract_id
    result = _safe(member_svc.create_member, firm_id, data)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_update_member(member_id: str, status: str = "", role: str = "", description: str = "", reports_to_member_id: str = "") -> str:
    """Update a member's fields (status, role, description, reports_to)."""
    data: dict[str, Any] = {}
    if status:
        data["status"] = status
    if role:
        data["role"] = role
    if description:
        data["description"] = description
    if reports_to_member_id:
        data["reports_to_member_id"] = reports_to_member_id
    if not data:
        return json.dumps({"error": "No fields to update"})
    result = _safe(member_svc.update_member, member_id, data)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_get_direct_reports(member_id: str, firm_id: str = "chrisai") -> str:
    """Get members who report directly to the given member."""
    result = _safe(member_svc.get_direct_reports, firm_id, member_id)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Unit tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_units(firm_id: str = "chrisai", project_id: str = "", claimed_by: str = "", status: str = "") -> str:
    """List units in the firm. Filter by project, claimed_by member, or status."""
    from firm.core import repo
    def _query(conn, **kw):
        kwargs: dict[str, Any] = {"firm_id": firm_id}
        if project_id:
            kwargs["project_id"] = project_id
        if claimed_by:
            kwargs["claimed_by"] = claimed_by
        if status:
            kwargs["status"] = status
        return repo.find(conn, "unit", **kwargs)
    result = _safe(_query)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_view_unit(unit_id: str) -> str:
    """View a single unit by ID (e.g. UNIT-001)."""
    result = _safe(unit_svc.view_unit, unit_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_create_unit(name: str, project_id: str, firm_id: str = "chrisai", priority: str = "medium", acceptance_criteria: str = "[]", depends_on: str = "[]") -> str:
    """Create a new unit (work item) in a project. acceptance_criteria and depends_on are JSON arrays."""
    data: dict[str, Any] = {
        "name": name,
        "project_id": project_id,
        "priority": priority,
    }
    # Parse JSON strings to lists for the service layer
    try:
        data["acceptance_criteria"] = json.loads(acceptance_criteria) if isinstance(acceptance_criteria, str) else acceptance_criteria
    except json.JSONDecodeError:
        data["acceptance_criteria"] = []
    try:
        data["depends_on"] = json.loads(depends_on) if isinstance(depends_on, str) else depends_on
    except json.JSONDecodeError:
        data["depends_on"] = []
    result = _safe(unit_svc.create_unit, firm_id, data)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_checkout_unit(unit_id: str, member_id: str) -> str:
    """Assign a unit to a member (atomic checkout). Fails if already claimed."""
    result = _safe(unit_svc.checkout_unit, unit_id, member_id)
    if result is None:
        return json.dumps({"error": f"Unit {unit_id} is already claimed or does not exist"})
    return json.dumps(result, default=str)


@mcp.tool()
def firm_release_unit(unit_id: str) -> str:
    """Release a unit back to unclaimed status."""
    result = _safe(unit_svc.release_unit, unit_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_complete_unit(unit_id: str) -> str:
    """Mark a unit as done and trigger completion handler."""
    result = _safe(unit_svc.complete_unit, unit_id)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Gate tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_gates(firm_id: str = "chrisai", status: str = "", requesting_member_id: str = "") -> str:
    """List gates (board decision checkpoints). Filter by status or requesting member."""
    kwargs: dict[str, Any] = {}
    if status:
        kwargs["status"] = status
    if requesting_member_id:
        kwargs["requesting_member_id"] = requesting_member_id
    result = _safe(gate_svc.list_gates, firm_id, **kwargs)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_view_gate(gate_id: str) -> str:
    """View a single gate by ID."""
    result = _safe(gate_svc.view_gate, gate_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_request_gate(requesting_member_id: str, action: str, target_entity_type: str, target_entity_id: str, firm_id: str = "chrisai", context: str = "") -> str:
    """Request board approval for an action. The board must approve or reject."""
    data: dict[str, Any] = {
        "requesting_member_id": requesting_member_id,
        "action": action,
        "target_entity_type": target_entity_type,
        "target_entity_id": target_entity_id,
    }
    if context:
        data["context"] = context
    result = _safe(gate_svc.request_gate, firm_id, data)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_approve_gate(gate_id: str, approver_comment: str = "") -> str:
    """Approve a pending gate request."""
    data = {"approver_comment": approver_comment} if approver_comment else None
    result = _safe(gate_svc.approve_gate, gate_id, data)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_reject_gate(gate_id: str, approver_comment: str = "") -> str:
    """Reject a pending gate request."""
    data = {"approver_comment": approver_comment} if approver_comment else None
    result = _safe(gate_svc.reject_gate, gate_id, data)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Goal tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_goals(firm_id: str = "chrisai") -> str:
    """List all goals in the firm."""
    result = _safe(goal_svc.list_goals, firm_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_view_goal(goal_id: str) -> str:
    """View a single goal by ID."""
    result = _safe(goal_svc.view_goal, goal_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_create_goal(target: str, parent_entity_type: str, parent_entity_id: str, firm_id: str = "chrisai", metric: str = "") -> str:
    """Create a goal attached to a parent entity (member, operation, project)."""
    data: dict[str, Any] = {
        "target": target,
        "parent_entity_type": parent_entity_type,
        "parent_entity_id": parent_entity_id,
    }
    if metric:
        data["metric"] = metric
    result = _safe(goal_svc.create_goal, firm_id, data)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_update_goal(goal_id: str, status: str = "", metric: str = "") -> str:
    """Update a goal's status or metric."""
    data: dict[str, Any] = {}
    if status:
        data["status"] = status
    if metric:
        data["metric"] = metric
    if not data:
        return json.dumps({"error": "No fields to update"})
    result = _safe(goal_svc.update_goal, goal_id, data)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Operation tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_operations(firm_id: str = "chrisai") -> str:
    """List all operations in the firm."""
    result = _safe(operation_svc.list_operations, firm_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_create_operation(name: str, firm_id: str = "chrisai", status: str = "active") -> str:
    """Create a new operation (business function)."""
    data: dict[str, Any] = {"name": name, "status": status}
    result = _safe(operation_svc.create_operation, firm_id, data)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Project tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_projects(firm_id: str = "chrisai", operation_id: str = "") -> str:
    """List projects in the firm. Filter by operation."""
    from firm.core import repo
    def _query(conn, **kw):
        kwargs: dict[str, Any] = {"firm_id": firm_id}
        if operation_id:
            kwargs["operation_id"] = operation_id
        return repo.find(conn, "project", **kwargs)
    result = _safe(_query)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_create_project(name: str, operation_id: str, due_date: str, firm_id: str = "chrisai", status: str = "in_progress") -> str:
    """Create a new project within an operation. due_date is required (YYYY-MM-DD)."""
    data: dict[str, Any] = {"name": name, "operation_id": operation_id, "status": status, "due_date": due_date}
    result = _safe(project_svc.create_project, firm_id, data)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Comment tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_comments(firm_id: str = "chrisai", parent_entity_type: str = "", parent_entity_id: str = "") -> str:
    """List comments. Filter by parent entity."""
    kwargs: dict[str, Any] = {}
    if parent_entity_type:
        kwargs["parent_entity_type"] = parent_entity_type
    if parent_entity_id:
        kwargs["parent_entity_id"] = parent_entity_id
    result = _safe(comment_svc.list_comments, firm_id, **kwargs)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_create_comment(body: str, parent_entity_type: str, parent_entity_id: str, author_type: str, author_id: str, firm_id: str = "chrisai") -> str:
    """Create an immutable comment on an entity. author_type: 'member' or 'board'."""
    data: dict[str, Any] = {
        "body": body,
        "parent_entity_type": parent_entity_type,
        "parent_entity_id": parent_entity_id,
        "author_type": author_type,
        "author_id": author_id,
    }
    result = _safe(comment_svc.create_comment, firm_id, data)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Document tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_documents(firm_id: str = "chrisai") -> str:
    """List all documents in the firm."""
    result = _safe(document_svc.list_documents, firm_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_create_document(name: str, doc_type: str, content_path: str, parent_entity_type: str, parent_entity_id: str, firm_id: str = "chrisai") -> str:
    """Create a versioned document attached to a parent entity. doc_type: 'instructions', 'brief', 'report', etc."""
    data: dict[str, Any] = {
        "name": name,
        "type": doc_type,
        "content_path": content_path,
        "parent_entity_type": parent_entity_type,
        "parent_entity_id": parent_entity_id,
    }
    result = _safe(document_svc.create_document, firm_id, data)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Contract tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_contracts(firm_id: str = "chrisai") -> str:
    """List all contracts in the firm."""
    from firm.core import repo
    def _query(conn, **kw):
        return repo.find(conn, "contract", firm_id=firm_id)
    result = _safe(_query)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_view_contract(contract_id: str) -> str:
    """View a single contract by ID (e.g. CON-001)."""
    result = _safe(contract_svc.view_contract, contract_id)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Firm aggregate tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_status(firm_id: str = "chrisai") -> str:
    """Get firm-wide status: member count, unit stats, pending gates, goal health."""
    result = _safe(firm_svc_mod.firm_status, firm_id)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Gap detection + hire proposals
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_detect_gaps(firm_id: str = "chrisai", stale_days: int = 7, overload_threshold: int = 3) -> str:
    """Surface staffing/coverage/workload gaps: unclaimed units, overloaded members, stale goals, coverage gaps."""
    result = _safe(
        gaps_heuristics.detect_gaps,
        firm_id,
        stale_days=stale_days,
        overload_threshold=overload_threshold,
    )
    return json.dumps(result, default=str)


@mcp.tool()
def firm_propose_hire(proposer_id: str, proposed_role: str, justification: str, proposed_description: str = "", firm_id: str = "chrisai") -> str:
    """Sterling (or any active member) proposes a new hire. Creates a hire_member Gate for Board approval."""
    result = _safe(
        gaps_heuristics.propose_hire,
        firm_id,
        proposer_id,
        proposed_role,
        proposed_description,
        justification,
    )
    return json.dumps(result, default=str)
