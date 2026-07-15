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

from firm.core.db import connect, get_db_path, resolve_firm_id
from firm.services import comment as comment_svc
from firm.services import contract as contract_svc
from firm.services import document as document_svc
from firm.services import escalation as escalation_svc
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


# Board-only member fields that must never reach the member MCP surface
# (Invariant #5 — structural blindness). member.autonomy carries the sovereign
# Calibration-Ladder override: Board config a member must not see, because a
# member that learns its autonomy grants (or its tier) games them. Stripped at
# the read chokepoint below so no member-returning tool — present or future —
# can leak it. No other entity has this column, so the pop is a harmless no-op
# everywhere else. (run_score needs no strip: it lives on member_run, which no
# member tool returns.)
_BOARD_ONLY_FIELDS = ("autonomy",)


def _scrub(row: dict) -> dict:
    """Drop Board-only fields from an outbound MCP dict (Invariant #5)."""
    for field in _BOARD_ONLY_FIELDS:
        row.pop(field, None)
    return row


def _serialize(result):
    """Normalize a service return into JSON-safe, member-blind data."""
    if isinstance(result, list):
        return [_scrub(dict(r)) if hasattr(r, "keys") else r for r in result]
    if hasattr(result, "keys"):
        return _scrub(dict(result))
    return result


def _safe(fn, *args, **kwargs) -> dict | list:
    """Call fn, return result or {"error": str} on ValueError."""
    conn = _get_conn()
    try:
        return _serialize(fn(conn, *args, **kwargs))
    except (ValueError, TypeError, sqlite3.IntegrityError) as exc:
        return {"error": str(exc)}
    finally:
        if _conn_factory is None:
            conn.close()


def _safe_firm(fn, firm_id: str, *args, **kwargs) -> dict | list:
    """_safe for firm-scoped services. An empty firm_id resolves to the firm
    this workspace's database actually holds — never a hardcoded name, which
    tagged rows with a foreign firm_id invisible to the firm's own queries
    (field failure 2026-07-12). Resolution happens inside the error boundary,
    so an ambiguous scope comes back as {"error": ...} like any other."""
    conn = _get_conn()
    try:
        fid = resolve_firm_id(conn, firm_id or None)
        return _serialize(fn(conn, fid, *args, **kwargs))
    except (ValueError, TypeError, sqlite3.IntegrityError) as exc:
        return {"error": str(exc)}
    finally:
        if _conn_factory is None:
            conn.close()


# ---------------------------------------------------------------------------
# Member tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_members(firm_id: str = "", status: str = "", reports_to: str = "") -> str:
    """List all members in the firm. Filter by status or reports_to member ID."""
    kwargs: dict[str, Any] = {}
    if status:
        kwargs["status"] = status
    if reports_to:
        kwargs["reports_to"] = reports_to
    result = _safe_firm(member_svc.list_members, firm_id, **kwargs)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_view_member(member_id: str) -> str:
    """View a single member by ID (e.g. MEM-001)."""
    result = _safe(member_svc.view_member, member_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_create_member(name: str, role: str, firm_id: str = "", description: str = "", reports_to_member_id: str = "", contract_id: str = "") -> str:
    """Create a new firm member with a name and role."""
    data: dict[str, Any] = {"name": name, "role": role}
    if description:
        data["description"] = description
    if reports_to_member_id:
        data["reports_to_member_id"] = reports_to_member_id
    if contract_id:
        data["contract_id"] = contract_id
    result = _safe_firm(member_svc.create_member, firm_id, data)
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
def firm_get_direct_reports(member_id: str, firm_id: str = "") -> str:
    """Get members who report directly to the given member."""
    result = _safe_firm(member_svc.get_direct_reports, firm_id, member_id)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Unit tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_units(firm_id: str = "", project_id: str = "", claimed_by: str = "", status: str = "") -> str:
    """List units in the firm. Filter by project, claimed_by member, or status."""
    from firm.core import repo
    def _query(conn, **kw):
        kwargs: dict[str, Any] = {"firm_id": resolve_firm_id(conn, firm_id or None)}
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
def firm_create_unit(name: str, project_id: str, firm_id: str = "", priority: str = "medium", acceptance_criteria: str = "[]", depends_on: str = "[]", model: str = "") -> str:
    """Create a new unit (work item) in a project. acceptance_criteria and depends_on are JSON arrays.

    model: optional per-unit model override (opus/sonnet/haiku or a full id) —
    beats the assignee's contract model for THIS unit's run. Use it to stop
    paying judgment rates for mechanical work: triage units say sonnet even
    when the member's default is opus."""
    data: dict[str, Any] = {
        "name": name,
        "project_id": project_id,
        "priority": priority,
    }
    if model:
        data["model"] = model
    # Parse JSON strings to lists for the service layer
    try:
        data["acceptance_criteria"] = json.loads(acceptance_criteria) if isinstance(acceptance_criteria, str) else acceptance_criteria
    except json.JSONDecodeError:
        data["acceptance_criteria"] = []
    try:
        data["depends_on"] = json.loads(depends_on) if isinstance(depends_on, str) else depends_on
    except json.JSONDecodeError:
        data["depends_on"] = []
    result = _safe_firm(unit_svc.create_unit, firm_id, data)
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
def firm_complete_unit(unit_id: str, member_id: str, firm_id: str = "", run_id: str = "") -> str:
    """Mark a unit as done and trigger completion handler. member_id is the completing member (actor on the audit record)."""
    result = _safe_firm(unit_svc.complete_unit, firm_id, unit_id, member_id, run_id=run_id or None)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Gate tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_gates(firm_id: str = "", status: str = "", requesting_member_id: str = "") -> str:
    """List gates (board decision checkpoints). Filter by status or requesting member."""
    kwargs: dict[str, Any] = {}
    if status:
        kwargs["status"] = status
    if requesting_member_id:
        kwargs["requesting_member_id"] = requesting_member_id
    result = _safe_firm(gate_svc.list_gates, firm_id, **kwargs)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_view_gate(gate_id: str) -> str:
    """View a single gate by ID."""
    result = _safe(gate_svc.view_gate, gate_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_request_gate(requesting_member_id: str, action: str, target_entity_type: str, target_entity_id: str, firm_id: str = "", context: str = "") -> str:
    """Request board approval for an action. The board must approve or reject."""
    data: dict[str, Any] = {
        "requesting_member_id": requesting_member_id,
        "action": action,
        "target_entity_type": target_entity_type,
        "target_entity_id": target_entity_id,
    }
    if context:
        data["context"] = context
    result = _safe_firm(gate_svc.request_gate, firm_id, data)
    return json.dumps(result, default=str)


# Gate RESOLUTION is not a Member tool and never will be (fork 010). A Gate
# assumes the asker and the approver are different entities; a Member that can
# approve its own Gate turns every control in the system — the send locks, the
# spend gates, the goal proposals — into a speed bump with a self-service
# bypass. The Board resolves gates through the hub's audited action endpoint
# (`gate-approve`/`gate-reject`) or the CLI; the Board Proxy may never touch
# one. Members request; they do not resolve. Not an exclusion — a
# constitutional line, same class as _BOARD_ONLY_COMMANDS.


# ---------------------------------------------------------------------------
# Escalation tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_escalate(raised_by_member_id: str, title: str, body: str = "", severity: str = "normal", target_entity_type: str = "", target_entity_id: str = "", firm_id: str = "") -> str:
    """Escalate an item directly to the Board (DMs the Board immediately). Use for blockers, decisions, or anything needing Board attention that is NOT an approval request (use firm_request_gate for approvals). Re-raising the same open issue is deduped automatically — the Board is only re-notified after the reminder window, so escalate freely. severity: low|normal|high|critical."""
    data: dict[str, Any] = {
        "raised_by_member_id": raised_by_member_id,
        "title": title,
    }
    if body:
        data["body"] = body
    if severity:
        data["severity"] = severity
    if target_entity_type:
        data["target_entity_type"] = target_entity_type
    if target_entity_id:
        data["target_entity_id"] = target_entity_id
    result = _safe_firm(escalation_svc.raise_escalation, firm_id, data)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_list_escalations(firm_id: str = "", status: str = "", raised_by: str = "") -> str:
    """List escalations. Filter by status (open|acknowledged|resolved) or raising member."""
    kwargs: dict[str, Any] = {}
    if status:
        kwargs["status"] = status
    if raised_by:
        kwargs["raised_by"] = raised_by
    result = _safe_firm(escalation_svc.list_escalations, firm_id, **kwargs)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_view_escalation(escalation_id: str) -> str:
    """View a single escalation by ID (e.g. ESC-001)."""
    result = _safe(escalation_svc.view_escalation, escalation_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_resolve_escalation(escalation_id: str, status: str = "resolved", resolution: str = "") -> str:
    """Resolve or acknowledge an escalation (Board action). status: acknowledged|resolved."""
    result = _safe(
        escalation_svc.resolve_escalation,
        escalation_id,
        status=status,
        resolution=resolution or None,
    )
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Goal tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_goals(firm_id: str = "") -> str:
    """List all goals in the firm."""
    result = _safe_firm(goal_svc.list_goals, firm_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_view_goal(goal_id: str) -> str:
    """View a single goal by ID."""
    result = _safe(goal_svc.view_goal, goal_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_propose_goal(target: str, parent_entity_type: str, parent_entity_id: str, reasoning: str, firm_id: str = "", metric: str = "") -> str:
    """Propose a goal for Board approval. You may argue for the number you will
    be measured against; only the Board sets it. This raises a Gate carrying
    your reasoning — the goal exists only once the Board approves it.

    reasoning: why THIS metric proves your outcome. The Board reads this line
    before deciding; a proposal without a case is a rejection."""
    member_id = os.environ.get("CADRE_MEMBER_ID", "")
    if not member_id:
        return json.dumps({"error": (
            "no member identity in this session — goals are authored by the "
            "Board (dashboard goal-create action / `firm goal create`) and "
            "proposed by Members from inside their runs")})
    payload = {"target": target, "parent_entity_type": parent_entity_type,
               "parent_entity_id": parent_entity_id, "metric": metric,
               "reasoning": reasoning}
    result = _safe_firm(gate_svc.request_gate, firm_id, {
        "requesting_member_id": member_id,
        "action": "create-goal",
        "target_entity_type": parent_entity_type,
        "target_entity_id": parent_entity_id,
        "context": json.dumps(payload),
    })
    return json.dumps(result, default=str)


@mcp.tool()
def firm_update_goal_metric(goal_id: str, current: str = "", value: str = "", unit: str = "", metric_type: str = "", deadline: str = "", trend: str = "") -> str:
    """Refresh a goal's metric (the canonical way to update goal progress). Merges the provided fields into the metric JSON the goal-health banner parses — pass only what changed, e.g. current="6". Numbers may be passed as strings."""
    def _num(v: str) -> Any:
        try:
            f = float(v)
            return int(f) if f.is_integer() else f
        except ValueError:
            return v
    result = _safe(
        goal_svc.update_goal_metric,
        goal_id,
        current=_num(current) if current else None,
        value=_num(value) if value else None,
        unit=unit or None,
        metric_type=metric_type or None,
        deadline=deadline or None,
        trend=trend or None,
    )
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
def firm_list_operations(firm_id: str = "") -> str:
    """List all operations in the firm."""
    result = _safe_firm(operation_svc.list_operations, firm_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_create_operation(name: str, firm_id: str = "", status: str = "active") -> str:
    """Create a new operation (business function)."""
    data: dict[str, Any] = {"name": name, "status": status}
    result = _safe_firm(operation_svc.create_operation, firm_id, data)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Project tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_projects(firm_id: str = "", operation_id: str = "") -> str:
    """List projects in the firm. Filter by operation."""
    from firm.core import repo
    def _query(conn, **kw):
        kwargs: dict[str, Any] = {"firm_id": resolve_firm_id(conn, firm_id or None)}
        if operation_id:
            kwargs["operation_id"] = operation_id
        return repo.find(conn, "project", **kwargs)
    result = _safe(_query)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_create_project(name: str, operation_id: str, due_date: str, firm_id: str = "", status: str = "in_progress") -> str:
    """Create a new project within an operation. due_date is required (YYYY-MM-DD)."""
    data: dict[str, Any] = {"name": name, "operation_id": operation_id, "status": status, "due_date": due_date}
    result = _safe_firm(project_svc.create_project, firm_id, data)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Comment tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_comments(firm_id: str = "", parent_entity_type: str = "", parent_entity_id: str = "") -> str:
    """List comments. Filter by parent entity."""
    kwargs: dict[str, Any] = {}
    if parent_entity_type:
        kwargs["parent_entity_type"] = parent_entity_type
    if parent_entity_id:
        kwargs["parent_entity_id"] = parent_entity_id
    result = _safe_firm(comment_svc.list_comments, firm_id, **kwargs)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_create_comment(body: str, parent_entity_type: str, parent_entity_id: str, author_type: str, author_id: str, firm_id: str = "") -> str:
    """Create an immutable comment on an entity. author_type: 'member' or 'board'."""
    data: dict[str, Any] = {
        "body": body,
        "parent_entity_type": parent_entity_type,
        "parent_entity_id": parent_entity_id,
        "author_type": author_type,
        "author_id": author_id,
    }
    result = _safe_firm(comment_svc.create_comment, firm_id, data)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Document tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_documents(firm_id: str = "") -> str:
    """List all documents in the firm."""
    result = _safe_firm(document_svc.list_documents, firm_id)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_create_document(name: str, doc_type: str, content_path: str, parent_entity_type: str, parent_entity_id: str, firm_id: str = "") -> str:
    """Create a versioned document attached to a parent entity. doc_type: 'instructions', 'brief', 'report', etc."""
    data: dict[str, Any] = {
        "name": name,
        "type": doc_type,
        "content_path": content_path,
        "parent_entity_type": parent_entity_type,
        "parent_entity_id": parent_entity_id,
    }
    result = _safe_firm(document_svc.create_document, firm_id, data)
    return json.dumps(result, default=str)


@mcp.tool()
def firm_update_document(document_id: str, member_id: str, content_path: str = "", name: str = "", status: str = "") -> str:
    """Register a revised deliverable as a new version of an existing document (e.g. DOC-001).

    Pass content_path pointing at the NEW file — never the old one. The never-overwrite
    rule holds: write the revision to a new -vN path, leave the prior file untouched, then
    call this. The version field auto-increments and Records logs the bump under member_id.
    status: 'active', 'archived', or 'deprecated'.
    """
    data: dict[str, Any] = {}
    if content_path:
        data["content_path"] = content_path
    if name:
        data["name"] = name
    if status:
        data["status"] = status
    if not data:
        return json.dumps({"error": "nothing to update — pass content_path, name, or status"})
    result = _safe(
        document_svc.update_document,
        document_id,
        data,
        actor={"type": "member", "id": member_id},
    )
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Contract tools
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_list_contracts(firm_id: str = "") -> str:
    """List all contracts in the firm."""
    from firm.core import repo
    def _query(conn, **kw):
        return repo.find(conn, "contract", firm_id=resolve_firm_id(conn, firm_id or None))
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
def firm_status(firm_id: str = "") -> str:
    """Get firm-wide status: member count, unit stats, pending gates, goal health."""
    result = _safe_firm(firm_svc_mod.firm_status, firm_id)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Gap detection + hire proposals
# ---------------------------------------------------------------------------

@mcp.tool()
def firm_detect_gaps(firm_id: str = "", stale_days: int = 7, overload_threshold: int = 3) -> str:
    """Surface staffing/coverage/workload gaps: unclaimed units, overloaded members, stale goals, coverage gaps."""
    result = _safe_firm(
        gaps_heuristics.detect_gaps,
        firm_id,
        stale_days=stale_days,
        overload_threshold=overload_threshold,
    )
    return json.dumps(result, default=str)


@mcp.tool()
def firm_propose_hire(proposer_id: str, proposed_role: str, justification: str, proposed_description: str = "", firm_id: str = "") -> str:
    """Sterling (or any active member) proposes a new hire. Creates a hire_member Gate for Board approval."""
    result = _safe_firm(
        gaps_heuristics.propose_hire,
        firm_id,
        proposer_id,
        proposed_role,
        proposed_description,
        justification,
    )
    return json.dumps(result, default=str)
