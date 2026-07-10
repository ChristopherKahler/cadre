"""Tests for firm.pulse.prompt — one-shot prompt assembly."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, get
from firm.pulse.prompt import (
    _format_acceptance_criteria,
    _render_execution_directive,
    _render_member_identity,
    _render_operational_context,
    _render_system_context,
    _render_unit_briefing,
    assemble_prompt,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fresh_conn(*, schedule: dict | None = None, operator: dict | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    firm_data: dict = {"id": "chrisai", "name": "ChrisAI"}
    if operator:
        firm_data["operator"] = operator
    if schedule:
        firm_data["schedule"] = schedule
    create(conn, "firm", firm_data)
    return conn


def _add_member(
    conn: sqlite3.Connection,
    member_id: str,
    *,
    role: str = "writer",
    description: str = "Writes content",
    reports_to: str | None = None,
    contract_id: str | None = None,
) -> dict:
    return create(conn, "member", {
        "id": member_id,
        "firm_id": "chrisai",
        "name": f"Member {member_id}",
        "role": role,
        "description": description,
        "status": "active",
        "reports_to_member_id": reports_to,
        "contract_id": contract_id,
    })


def _add_project(conn: sqlite3.Connection, project_id: str, **kwargs) -> dict:
    op = create(conn, "operation", {
        "id": f"op-{project_id}",
        "firm_id": "chrisai",
        "name": f"Op for {project_id}",
        "status": "active",
    })
    data = {
        "id": project_id,
        "firm_id": "chrisai",
        "operation_id": op["id"],
        "name": f"Project {project_id}",
        "status": "in_progress",
        "due_date": "2026-12-31",
    }
    data.update(kwargs)
    return create(conn, "project", data)


def _add_unit(
    conn: sqlite3.Connection,
    unit_id: str,
    project_id: str,
    *,
    claimed_by: str | None = None,
    acceptance_criteria: list | None = None,
    depends_on: list | None = None,
    outputs: list | None = None,
) -> dict:
    return create(conn, "unit", {
        "id": unit_id,
        "firm_id": "chrisai",
        "project_id": project_id,
        "name": f"Unit {unit_id}",
        "status": "pending",
        "claimed_by": claimed_by,
        "acceptance_criteria": acceptance_criteria,
        "depends_on": depends_on or [],
        "outputs": outputs,
    })


def _add_contract(
    conn: sqlite3.Connection,
    contract_id: str,
    *,
    runtime_config: dict | None = None,
    pulse_config: dict | None = None,
) -> dict:
    return create(conn, "contract", {
        "id": contract_id,
        "firm_id": "chrisai",
        "name": f"Contract {contract_id}",
        "runtime_type": "claude_code",
        "runtime_config": runtime_config,
        "pulse_config": pulse_config,
    })


# ═══════════════════════════════════════════════════════════════════════════
# Section 1: System Context
# ═══════════════════════════════════════════════════════════════════════════


class TestSystemContext:

    def test_renders_firm_name_and_operator(self):
        conn = _fresh_conn(operator={"name": "Chris", "role": "CEO"})
        result = _render_system_context(conn, "chrisai")
        assert "ChrisAI" in result
        assert "Chris" in result
        assert "Current date:" in result

    def test_renders_business_hours(self):
        conn = _fresh_conn(
            operator={"name": "Chris"},
            schedule={
                "timezone": "America/Chicago",
                "business_hours": {"start": "07:00", "end": "17:00"},
            },
        )
        result = _render_system_context(conn, "chrisai")
        assert "07:00 - 17:00 America/Chicago" in result

    def test_missing_operator_defaults_to_board(self):
        conn = _fresh_conn()
        result = _render_system_context(conn, "chrisai")
        assert "the Board" in result

    def test_missing_firm_returns_not_found(self):
        conn = _fresh_conn()
        result = _render_system_context(conn, "nonexistent")
        assert "not found" in result.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: Member Identity
# ═══════════════════════════════════════════════════════════════════════════


class TestMemberIdentity:

    def test_renders_member_fields(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001", role="Blog Writer", description="Writes blog posts")
        result = _render_member_identity(conn, "MEM-001", "/tmp")
        assert "Member MEM-001" in result
        assert "Blog Writer" in result
        assert "Writes blog posts" in result
        assert "Board (direct report)" in result

    def test_with_instructions_file(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001")
        with tempfile.TemporaryDirectory() as tmpdir:
            instr_dir = os.path.join(tmpdir, ".firm", "instructions")
            os.makedirs(instr_dir)
            with open(os.path.join(instr_dir, "MEM-001.md"), "w") as f:
                f.write("# Custom Instructions\nDo great work.")
            result = _render_member_identity(conn, "MEM-001", tmpdir)
            assert "Custom Instructions" in result
            assert "Do great work." in result

    def test_without_instructions_file(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001")
        result = _render_member_identity(conn, "MEM-001", "/tmp/nonexistent")
        assert "MEM-001" in result
        # Should not error

    def test_with_manager(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-002", role="CMO")
        _add_member(conn, "MEM-001", role="Writer", reports_to="MEM-002")
        result = _render_member_identity(conn, "MEM-001", "/tmp")
        assert "Member MEM-002" in result  # Manager name resolved

    def test_missing_member(self):
        conn = _fresh_conn()
        result = _render_member_identity(conn, "FAKE", "/tmp")
        assert "not found" in result.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Section 3: Operational Context
# ═══════════════════════════════════════════════════════════════════════════


class TestOperationalContext:

    def test_reuses_session_pulse_renders(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001")
        proj = _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")
        result = _render_operational_context(conn, "chrisai")
        # Should contain roster output since there's an active member with a unit
        assert "active-roster" in result

    def test_empty_context(self):
        conn = _fresh_conn()
        # No members, no gates, no goals
        result = _render_operational_context(conn, "chrisai")
        assert "No operational context" in result


# ═══════════════════════════════════════════════════════════════════════════
# Section 4: Unit Briefing
# ═══════════════════════════════════════════════════════════════════════════


class TestUnitBriefing:

    def test_renders_unit_fields(self):
        conn = _fresh_conn()
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001")
        result = _render_unit_briefing(conn, "UNT-001")
        assert "UNT-001" in result
        assert "Unit UNT-001" in result
        assert "Project PRJ-001" in result

    def test_acceptance_criteria(self):
        conn = _fresh_conn()
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", acceptance_criteria=[
            "Blog post is 1000+ words",
            "Includes 3 sources",
        ])
        result = _render_unit_briefing(conn, "UNT-001")
        assert "Blog post is 1000+ words" in result
        assert "Includes 3 sources" in result

    def test_falls_back_to_project_ac(self):
        conn = _fresh_conn()
        _add_project(conn, "PRJ-001", acceptance_criteria=["Project-level AC"])
        _add_unit(conn, "UNT-001", "PRJ-001")  # No unit-level AC
        result = _render_unit_briefing(conn, "UNT-001")
        assert "Project-level AC" in result

    def test_dependency_resolution(self):
        conn = _fresh_conn()
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-dep", "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", depends_on=["UNT-dep"])
        result = _render_unit_briefing(conn, "UNT-001")
        assert "UNT-dep" in result
        assert "pending" in result

    def test_outputs_rendered(self):
        conn = _fresh_conn()
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", outputs=["blog-post.md", "summary.md"])
        result = _render_unit_briefing(conn, "UNT-001")
        assert "blog-post.md" in result
        assert "summary.md" in result

    def test_missing_unit(self):
        conn = _fresh_conn()
        result = _render_unit_briefing(conn, "FAKE")
        assert "not found" in result.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Section 5: Execution Directive
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionDirective:

    def test_renders_cwd_and_rules(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001")
        result = _render_execution_directive(conn, "MEM-001", "/workspace")
        assert "/workspace" in result
        assert "acceptance criteria" in result
        assert "Do NOT modify files outside" in result

    def test_cwd_from_contract_runtime_config(self):
        conn = _fresh_conn()
        con = _add_contract(conn, "CON-001", runtime_config={"cwd": "/projects/blog"})
        _add_member(conn, "MEM-001", contract_id="CON-001")
        result = _render_execution_directive(conn, "MEM-001", "/fallback")
        assert "/projects/blog" in result


# ═══════════════════════════════════════════════════════════════════════════
# Full assembly integration
# ═══════════════════════════════════════════════════════════════════════════


class TestAssemblePrompt:

    def test_full_assembly(self):
        conn = _fresh_conn(
            operator={"name": "Chris", "role": "CEO"},
            schedule={
                "timezone": "America/Chicago",
                "business_hours": {"start": "07:00", "end": "17:00"},
            },
        )
        _add_member(conn, "MEM-001", role="Blog Writer", description="Writes content")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001",
                  claimed_by="MEM-001",
                  acceptance_criteria=["Post is 1000+ words"])

        result = assemble_prompt(conn, "chrisai", "MEM-001", "UNT-001", cwd="/tmp")

        # Section 1
        assert "ChrisAI" in result
        assert "Chris" in result
        # Section 2
        assert "Blog Writer" in result
        assert "MEM-001" in result
        # Section 3
        assert "Operational Context" in result
        # Section 4
        assert "UNT-001" in result
        assert "1000+ words" in result
        # Section 5
        assert "Execution Rules" in result

    def test_all_five_sections_present(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")

        result = assemble_prompt(conn, "chrisai", "MEM-001", "UNT-001", cwd="/tmp")

        assert "System Context" in result
        assert "Your Identity" in result
        assert "Operational Context" in result
        assert "Your Assignment" in result
        assert "Execution Rules" in result

    def test_no_protocols_dir_omits_section(self, tmp_path):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")

        result = assemble_prompt(
            conn, "chrisai", "MEM-001", "UNT-001", cwd=str(tmp_path),
        )

        assert "Firm Protocols" not in result

    def test_protocols_appended_in_filename_order(self, tmp_path):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")
        proto = tmp_path / ".firm" / "protocols"
        proto.mkdir(parents=True)
        (proto / "20-other.md").write_text("SECOND-FRAGMENT")
        (proto / "10-squad.md").write_text("FIRST-FRAGMENT")
        (proto / "ignored.txt").write_text("NOT-MARKDOWN")
        (proto / "30-empty.md").write_text("   \n")

        result = assemble_prompt(
            conn, "chrisai", "MEM-001", "UNT-001", cwd=str(tmp_path),
        )

        assert "Firm Protocols" in result
        assert result.index("FIRST-FRAGMENT") < result.index("SECOND-FRAGMENT")
        assert "NOT-MARKDOWN" not in result
        # protocols ride BELOW the execution directive — appended, never
        # displacing the unit briefing
        assert result.index("Execution Rules") < result.index("FIRST-FRAGMENT")

    def test_member_protocols_reach_only_their_member(self, tmp_path):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001")
        _add_member(conn, "MEM-002")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")
        _add_unit(conn, "UNT-002", "PRJ-001", claimed_by="MEM-002")
        mdir = tmp_path / ".firm" / "protocols" / "_member" / "MEM-001"
        mdir.mkdir(parents=True)
        (mdir / "50-squad-contract.md").write_text("MEM-001-CONTRACT")

        mine = assemble_prompt(conn, "chrisai", "MEM-001", "UNT-001", cwd=str(tmp_path))
        theirs = assemble_prompt(conn, "chrisai", "MEM-002", "UNT-002", cwd=str(tmp_path))

        assert "Your Protocols" in mine and "MEM-001-CONTRACT" in mine
        assert "MEM-001-CONTRACT" not in theirs
        # the _member dir is invisible to the firm-wide seam
        assert "Firm Protocols" not in mine


# ═══════════════════════════════════════════════════════════════════════════
# Utility: acceptance criteria formatting
# ═══════════════════════════════════════════════════════════════════════════


class TestFormatAcceptanceCriteria:

    def test_list_format(self):
        result = _format_acceptance_criteria(["A", "B"])
        assert "- A" in result
        assert "- B" in result

    def test_none_returns_not_specified(self):
        assert "None specified" in _format_acceptance_criteria(None)

    def test_string_passthrough(self):
        assert "raw text" == _format_acceptance_criteria("raw text")
