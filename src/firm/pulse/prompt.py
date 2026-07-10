"""One-shot prompt assembler for PULSE Member runs.

Builds a 5-section prompt from DB state and instruction files. The assembled
string is the entire context for a ``claude --print`` invocation — quality of
prompt = quality of output.

Specification: BRIEF.md Section 2 (Prompt Assembly Specification).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from firm.core import repo
from firm.hooks.session_pulse import (
    render_active_roster,
    render_goal_health,
    render_pending_gates,
)


# ---------------------------------------------------------------------------
# Section 1: System Context
# ---------------------------------------------------------------------------

def _render_system_context(
    conn: sqlite3.Connection,
    firm_id: str,
) -> str:
    """Render firm identity, operator, and schedule."""
    firm = repo.get(conn, "firm", firm_id)
    if not firm:
        return "## System Context\n\nFirm not found."

    name = firm.get("name", "Unknown Firm")
    iso_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    operator = firm.get("operator")
    if isinstance(operator, str):
        try:
            operator = json.loads(operator)
        except (json.JSONDecodeError, TypeError):
            operator = None

    op_name = operator.get("name", "the Board") if isinstance(operator, dict) else "the Board"

    schedule = firm.get("schedule")
    if isinstance(schedule, str):
        try:
            schedule = json.loads(schedule)
        except (json.JSONDecodeError, TypeError):
            schedule = None

    hours_line = ""
    if isinstance(schedule, dict):
        bh = schedule.get("business_hours", {})
        tz_name = schedule.get("timezone", "UTC")
        start = bh.get("start", "")
        end = bh.get("end", "")
        if start and end:
            hours_line = f"\nBusiness hours: {start} - {end} {tz_name}"

    return (
        f"## System Context\n\n"
        f"You are a Member of {name}. "
        f"You operate under the direction of the Board ({op_name}).\n"
        f"Current date: {iso_date}"
        f"{hours_line}"
    )


# ---------------------------------------------------------------------------
# Section 2: Member Identity
# ---------------------------------------------------------------------------

def _read_instructions(member_id: str, cwd: str) -> str | None:
    """Read .firm/instructions/{member_id}.md from the workspace. None if missing."""
    path = os.path.join(cwd, ".firm", "instructions", f"{member_id}.md")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except (FileNotFoundError, OSError):
        return None


def _render_protocols(cwd: str) -> str | None:
    """Concatenate .firm/protocols/*.md into one standing-directive section.

    Firm-wide protocol fragments installed by extensions (e.g. squad) reach
    EVERY Member's prompt automatically — a Member cannot be unaware of an
    installed capability. Sorted by filename so installers can prefix-order
    (10-squad.md before 20-other.md). No directory or no fragments → None.
    """
    proto_dir = os.path.join(cwd, ".firm", "protocols")
    try:
        names = sorted(os.listdir(proto_dir))
    except (FileNotFoundError, OSError):
        return None
    parts = []
    for name in names:
        if not name.endswith(".md"):
            continue
        try:
            with open(os.path.join(proto_dir, name), encoding="utf-8") as f:
                text = f.read().strip()
        except (FileNotFoundError, OSError):
            continue
        if text:
            parts.append(text)
    if not parts:
        return None
    return "## Firm Protocols\n\n" + "\n\n---\n\n".join(parts)


def _render_member_identity(
    conn: sqlite3.Connection,
    member_id: str,
    cwd: str,
) -> str:
    """Render member name, role, description, manager, and instruction file."""
    member = repo.get(conn, "member", member_id)
    if not member:
        return "## Your Identity\n\nMember not found."

    name = member.get("name", "Unknown")
    role = member.get("role", "Unknown")
    mid = member.get("id", member_id)
    description = member.get("description", "")

    manager_name = "Board (direct report)"
    reports_to = member.get("reports_to_member_id")
    if reports_to:
        mgr = repo.get(conn, "member", reports_to)
        if mgr:
            manager_name = mgr.get("name", reports_to)

    lines = [
        "## Your Identity\n",
        f"- Name: {name}",
        f"- Role: {role}",
        f"- ID: {mid}",
        f"- Reports to: {manager_name}",
    ]
    if description:
        lines.append(f"- Description: {description}")

    instructions = _read_instructions(member_id, cwd)
    if instructions:
        lines.append(f"\n{instructions}")

    # Standing notes — Board comments on the MEMBER (not a unit) are
    # persistent direction that shapes every future run, the Board's way
    # of tuning a Member without editing framework files.
    notes = [
        c for c in repo.find(conn, "comment", parent_entity_id=member_id)
        if c.get("parent_entity_type") == "member" and not c.get("archived")
    ]
    if notes:
        notes.sort(key=lambda c: c.get("created_at") or "")
        rendered = []
        for c in notes[-5:]:
            author = c.get("author_id") or c.get("author_type") or "?"
            if c.get("author_type") == "board" and not c.get("author_id"):
                author = "THE BOARD"
            rendered.append(f"- [{c.get('created_at')}] {author}: {c.get('body')}")
        lines.append(
            "\n### Standing Notes\n"
            "Persistent direction attached to you as a Member — apply these "
            "to every run until they are archived.\n" + "\n".join(rendered)
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section 2b: Contract — sanctioned loadout + binding policies
# ---------------------------------------------------------------------------

def _render_contract(conn: sqlite3.Connection, member_id: str) -> str | None:
    """Render the Member's Contract: sanctioned commands, tools, and policies.

    Without this section a Member never learns which commands its Contract
    sanctions — it improvises tooling instead of using the operator's
    frameworks, and contract policies (voice gates, CTA rules) never bind.
    """
    member = repo.get(conn, "member", member_id)
    if not member or not member.get("contract_id"):
        return None
    contract = repo.get(conn, "contract", member["contract_id"])
    if not contract:
        return None

    lines = [
        "## Your Contract\n",
        f"- Contract: {contract.get('name') or contract.get('id')}",
    ]

    raw = contract.get("skill_loadout")
    loadout: dict[str, Any] = {}
    if raw:
        try:
            loadout = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            loadout = {}
    if not isinstance(loadout, dict):
        loadout = {}

    stages = loadout.get("stages")
    if isinstance(stages, dict) and stages:
        lines.append("\n### Sanctioned commands — use these, do not improvise tooling")
        lines.extend(f"- {stage}: {cmd}" for stage, cmd in stages.items())

    tools = loadout.get("tools")
    if isinstance(tools, list) and tools:
        lines.append("\n### Tools")
        lines.extend(f"- {t}" for t in tools)

    duties = loadout.get("duties")
    if isinstance(duties, list) and duties:
        lines.append("\n### Duties")
        lines.extend(f"- {d}" for d in duties)

    policies = loadout.get("policies")
    if isinstance(policies, list) and policies:
        lines.append("\n### Binding policies — non-negotiable")
        lines.extend(f"- {p}" for p in policies)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section 3: Operational Context
# ---------------------------------------------------------------------------

def _render_operational_context(
    conn: sqlite3.Connection,
    firm_id: str,
) -> str:
    """Render operational context by reusing session_pulse.py renders."""
    parts: list[str] = []

    roster = render_active_roster(conn, firm_id)
    if roster:
        parts.append(roster)
    gates = render_pending_gates(conn, firm_id)
    if gates:
        parts.append(gates)
    goals = render_goal_health(conn, firm_id)
    if goals:
        parts.append(goals)

    if not parts:
        return "## Operational Context\n\nNo operational context."

    return "## Operational Context\n\n" + "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Section 4: Unit Briefing
# ---------------------------------------------------------------------------

def _format_acceptance_criteria(ac: Any) -> str:
    """Format acceptance_criteria JSON into readable list."""
    if not ac:
        return "None specified"
    if isinstance(ac, str):
        try:
            ac = json.loads(ac)
        except (json.JSONDecodeError, TypeError):
            return ac
    if isinstance(ac, list):
        return "\n".join(f"- {item}" for item in ac)
    return str(ac)


def _resolve_deps(conn: sqlite3.Connection, depends_on: Any) -> str:
    """Resolve depends_on unit IDs to status lines."""
    if not depends_on:
        return "None"
    if isinstance(depends_on, str):
        try:
            depends_on = json.loads(depends_on)
        except (json.JSONDecodeError, TypeError):
            return depends_on
    if not isinstance(depends_on, list):
        return str(depends_on)

    lines: list[str] = []
    for dep_id in depends_on:
        dep = repo.get(conn, "unit", str(dep_id))
        if dep:
            lines.append(f"- [{dep_id}] {dep.get('name', '?')} ({dep.get('status', '?')})")
        else:
            lines.append(f"- [{dep_id}] (not found)")
    return "\n".join(lines) if lines else "None"


def _render_unit_briefing(
    conn: sqlite3.Connection,
    unit_id: str,
) -> str:
    """Render unit assignment with AC, deps, and project context."""
    unit = repo.get(conn, "unit", unit_id)
    if not unit:
        return "## Your Assignment\n\nUnit not found."

    name = unit.get("name", "Unknown")
    priority = unit.get("priority", "medium")
    status = unit.get("status", "pending")
    project_id = unit.get("project_id")

    project_line = ""
    if project_id:
        project = repo.get(conn, "project", project_id)
        if project:
            project_line = f"Project: [{project_id}] {project.get('name', '?')}"

    # Use unit AC, fall back to project AC if empty
    ac = unit.get("acceptance_criteria")
    if not ac and project_id:
        project = repo.get(conn, "project", project_id)
        if project:
            ac = project.get("acceptance_criteria")

    deps = _resolve_deps(conn, unit.get("depends_on"))

    outputs = unit.get("outputs")
    outputs_str = ""
    if outputs:
        if isinstance(outputs, str):
            try:
                outputs = json.loads(outputs)
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(outputs, list):
            outputs_str = "\n".join(f"- {o}" for o in outputs)
        else:
            outputs_str = str(outputs)

    lines = [
        "## Your Assignment\n",
        f"Unit: [{unit_id}] {name}",
    ]
    if project_line:
        lines.append(project_line)
    lines.extend([
        f"Priority: {priority}",
        f"Status: {status}",
    ])
    # Description carries the assigner's direction (Board or colleague) —
    # omitting it made 'Assign work' descriptions invisible to the Member.
    if unit.get("description"):
        lines.append(f"\n### Briefing\n{unit['description']}")
    lines.extend([
        f"\n### Acceptance Criteria\n{_format_acceptance_criteria(ac)}",
        f"\n### Dependencies\n{deps}",
    ])
    if outputs_str:
        lines.append(f"\n### Outputs Expected\n{outputs_str}")

    # Comment thread — the Board (and colleagues) leave direction on the
    # unit; without this section those messages never reach the Member.
    comments = [
        c for c in repo.find(conn, "comment", parent_entity_id=unit_id)
        if c.get("parent_entity_type") == "unit" and not c.get("archived")
    ]
    if comments:
        comments.sort(key=lambda c: c.get("created_at") or "")
        rendered = []
        for c in comments[-8:]:  # newest 8 — enough thread, bounded prompt
            author = c.get("author_id") or c.get("author_type") or "?"
            if c.get("author_type") == "board" and not c.get("author_id"):
                author = "THE BOARD"
            rendered.append(f"- [{c.get('created_at')}] {author}: {c.get('body')}")
        lines.append(
            "\n### Comments on this Unit\n"
            "Read these before starting — comments from THE BOARD are direction, "
            "not suggestions.\n" + "\n".join(rendered)
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section 5: Execution Directive
# ---------------------------------------------------------------------------

def _render_execution_directive(
    conn: sqlite3.Connection,
    member_id: str,
    cwd: str,
) -> str:
    """Render execution rules from contract config."""
    member = repo.get(conn, "member", member_id)
    work_dir = cwd

    if member and member.get("contract_id"):
        contract = repo.get(conn, "contract", member["contract_id"])
        if contract:
            rc = contract.get("runtime_config")
            if isinstance(rc, str):
                try:
                    rc = json.loads(rc)
                except (json.JSONDecodeError, TypeError):
                    rc = None
            if isinstance(rc, dict) and rc.get("cwd"):
                work_dir = rc["cwd"]

    return (
        "## Execution Rules\n\n"
        "- Complete the assigned Unit according to its acceptance criteria\n"
        f"- Work in: {work_dir}\n"
        "- When done: Report completion status and list outputs produced\n"
        "- Do NOT modify files outside your assigned scope\n"
        "- If blocked: Report the blocker clearly instead of guessing\n"
        "- If something needs the BOARD (a decision, a blocker only they can "
        "clear, anything above your authority): raise it with the firm MCP "
        "tool firm_escalate — it DMs the Board directly the moment you call it. "
        "Approval requests still go through firm_request_gate (also notifies "
        "the Board). Duplicate raises of the same open issue are deduped "
        "automatically, so escalate without fear of spamming\n"
        "- If your work surfaces follow-up tasks, create Units for them AS YOU GO "
        "via the firm MCP tools (unit_create — within your Project's scope, "
        "assigned to the right colleague) instead of doing their work yourself. "
        "A later pulse activates them in parallel; queued work is throughput, "
        "hoarded work is a bottleneck"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def assemble_prompt(
    conn: sqlite3.Connection,
    firm_id: str,
    member_id: str,
    unit_id: str,
    *,
    cwd: str | None = None,
) -> str:
    """Assemble the complete one-shot prompt for a Member run.

    Args:
        conn: SQLite connection with migrations applied.
        firm_id: Firm scope.
        member_id: The Member being activated.
        unit_id: The Unit assigned for this run.
        cwd: Working directory override (defaults to os.getcwd()).

    Returns:
        A single prompt string with all 5 sections joined.
    """
    workspace = cwd or os.getcwd()

    contract_section = _render_contract(conn, member_id)
    protocols_section = _render_protocols(workspace)

    sections = [
        _render_system_context(conn, firm_id),
        _render_member_identity(conn, member_id, workspace),
        *((contract_section,) if contract_section else ()),
        _render_operational_context(conn, firm_id),
        _render_unit_briefing(conn, unit_id),
        _render_execution_directive(conn, member_id, workspace),
        *((protocols_section,) if protocols_section else ()),
    ]

    return "\n\n".join(sections)
