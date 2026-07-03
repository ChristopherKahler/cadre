"""Unit tests for firm.hooks.session_pulse and firm.hooks.render.

Seeded in-memory DB fixtures mirror MEMBERS-DESIGN's ChrisAI roster so output
matches the worked examples in ``02-01-BRIEF.md`` §2.1–2.3. Deterministic
``now`` values are injected where the renderer consumes time so tests do not
flake at clock boundaries.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.hooks.render import classify_expiry, resolve_entity_name, time_ago
from firm.hooks.session_pulse import (
    render,
    render_active_roster,
    render_goal_health,
    render_pending_gates,
)

FIXED_NOW = datetime(2026, 4, 15, 20, 0, 0)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    return conn


def _seed_chrisai(conn: sqlite3.Connection) -> None:
    """Seed the ChrisAI firm roster per MEMBERS-DESIGN + BRIEF §2.1 example."""
    create(conn, "firm", {
        "id": "chrisai",
        "name": "ChrisAI",
        "operator": {"name": "Chris Kahler", "role": "Board / Founder"},
    })
    # Sterling is the manager (reports_to NULL). Created first so Quill/Sage
    # can reference it.
    create(conn, "member", {
        "id": "MEM-002", "firm_id": "chrisai",
        "name": "Sterling", "role": "CMO",
    })
    create(conn, "contract", {
        "id": "CON-001", "firm_id": "chrisai",
        "name": "Quill Blog Author Contract",
        "runtime_type": "claude_code",
        "runtime_config": {"entry_command": "/quill:run"},
        "member_id": "MEM-001",
    })
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai",
        "name": "Quill", "role": "Blog Author",
        "reports_to_member_id": "MEM-002",
        "contract_id": "CON-001",
    })
    create(conn, "member", {
        "id": "MEM-003", "firm_id": "chrisai",
        "name": "Sage", "role": "Content Strategist",
        "reports_to_member_id": "MEM-002",
    })


# ---------------------------------------------------------------------------
# render.py — resolve_entity_name
# ---------------------------------------------------------------------------

def test_resolve_entity_name_finds_member() -> None:
    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        assert resolve_entity_name(conn, "member", "MEM-001") == "Quill"
    finally:
        conn.close()


def test_resolve_entity_name_finds_goal_via_target_column() -> None:
    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        create(conn, "operation", {
            "id": "OPS-001", "firm_id": "chrisai", "name": "Content Publishing",
        })
        create(conn, "goal", {
            "id": "GOAL-001", "firm_id": "chrisai", "level": "operation",
            "parent_entity_type": "operation", "parent_entity_id": "OPS-001",
            "target": "Publish 2 longform posts per week",
        })
        # goal has no `name` column; dispatcher routes to `target`
        assert resolve_entity_name(conn, "goal", "GOAL-001") == (
            "Publish 2 longform posts per week"
        )
    finally:
        conn.close()


def test_resolve_entity_name_missing_row_returns_none() -> None:
    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        assert resolve_entity_name(conn, "member", "MEM-404") is None
    finally:
        conn.close()


def test_resolve_entity_name_rejects_unknown_type() -> None:
    conn = _fresh_conn()
    try:
        # unknown type → None (defensive; DB CHECK constraint is the real guard)
        assert resolve_entity_name(conn, "comment", "COM-001") is None
        assert resolve_entity_name(conn, "records", "LOG-001") is None
        assert resolve_entity_name(conn, "haxxor", "x") is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# render.py — time_ago
# ---------------------------------------------------------------------------

def test_time_ago_just_now() -> None:
    assert time_ago("2026-04-15 20:00:00", now=datetime(2026, 4, 15, 20, 0, 3)) == "just now"


def test_time_ago_minutes() -> None:
    assert time_ago("2026-04-15 19:45:00", now=FIXED_NOW) == "15m ago"


def test_time_ago_hours() -> None:
    assert time_ago("2026-04-15 17:00:00", now=FIXED_NOW) == "3h ago"


def test_time_ago_days() -> None:
    assert time_ago("2026-04-14 20:00:00", now=FIXED_NOW) == "1d ago"


def test_time_ago_future() -> None:
    assert time_ago("2026-04-15 23:00:00", now=FIXED_NOW) == "in 3h"


# ---------------------------------------------------------------------------
# render.py — classify_expiry
# ---------------------------------------------------------------------------

def test_classify_expiry_null_is_standard() -> None:
    assert classify_expiry(None, now=FIXED_NOW) == "STANDARD"


def test_classify_expiry_past_is_expired() -> None:
    assert classify_expiry("2026-04-15 19:00:00", now=FIXED_NOW) == "EXPIRED"


def test_classify_expiry_within_24h_is_urgent() -> None:
    assert classify_expiry("2026-04-15 23:00:00", now=FIXED_NOW) == "URGENT"
    # 23h59m ahead → URGENT (strict <)
    assert classify_expiry("2026-04-16 19:59:00", now=FIXED_NOW) == "URGENT"


def test_classify_expiry_beyond_24h_is_standard() -> None:
    # exactly 24h ahead → not < now+24h → STANDARD
    assert classify_expiry("2026-04-16 20:00:00", now=FIXED_NOW) == "STANDARD"
    # 24h1m ahead → STANDARD
    assert classify_expiry("2026-04-16 20:01:00", now=FIXED_NOW) == "STANDARD"


def test_classify_expiry_boundary_expires_exactly_now() -> None:
    # expires_at == now → not strictly < now → URGENT (not EXPIRED)
    assert classify_expiry("2026-04-15 20:00:00", now=FIXED_NOW) == "URGENT"


# ---------------------------------------------------------------------------
# AC-1: <active-roster>
# ---------------------------------------------------------------------------

def test_active_roster_renders_full_chrisai_structure() -> None:
    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        out = render_active_roster(conn, "chrisai")
        assert out is not None
        assert '<active-roster members="3">' in out
        assert "[BOARD] — Chris Kahler (Board / Founder)" in out
        assert "[MANAGERS]" in out
        assert "[MEM-002] Sterling (CMO) — (no contract wired yet)" in out
        assert "[INDIVIDUAL CONTRIBUTORS]" in out
        assert "[MEM-001] Quill (Blog Author) reports to Sterling — /quill:run" in out
        assert "[MEM-003] Sage (Content Strategist) reports to Sterling — (no contract wired yet)" in out
        assert "BEHAVIOR: This context is PASSIVE AWARENESS ONLY." in out
        assert out.rstrip().endswith("</active-roster>")
    finally:
        conn.close()


def test_active_roster_shows_currently_on_unit() -> None:
    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        create(conn, "operation", {"id": "OPS-001", "firm_id": "chrisai", "name": "Ops"})
        create(conn, "project", {
            "id": "PROJ-001", "firm_id": "chrisai", "operation_id": "OPS-001",
            "name": "P", "status": "in_progress", "due_date": "2026-12-31",
        })
        create(conn, "unit", {
            "id": "UNIT-014", "firm_id": "chrisai", "project_id": "PROJ-001",
            "name": "Blog post #14 draft", "status": "in_progress",
            "claimed_by": "MEM-001",
        })
        out = render_active_roster(conn, "chrisai")
        assert out is not None
        assert "CURRENTLY ON: [UNIT-014] Blog post #14 draft (in_progress)" in out
    finally:
        conn.close()


def test_active_roster_silent_when_no_active_members() -> None:
    conn = _fresh_conn()
    try:
        create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
        assert render_active_roster(conn, "chrisai") is None
    finally:
        conn.close()


def test_active_roster_omits_board_line_when_operator_missing() -> None:
    conn = _fresh_conn()
    try:
        # Firm without operator JSON
        create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
        create(conn, "member", {
            "id": "MEM-001", "firm_id": "chrisai", "name": "Solo", "role": "Dev",
        })
        out = render_active_roster(conn, "chrisai")
        assert out is not None
        assert "[BOARD]" not in out
        assert "[MANAGERS]" in out
    finally:
        conn.close()


def test_active_roster_excludes_paused_and_retired() -> None:
    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        create(conn, "member", {
            "id": "MEM-004", "firm_id": "chrisai",
            "name": "Retired Rex", "role": "Former Editor",
            "status": "retired",
        })
        out = render_active_roster(conn, "chrisai")
        assert out is not None
        assert 'members="3"' in out
        assert "Rex" not in out
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-2: <pending-gates>
# ---------------------------------------------------------------------------

def test_pending_gates_silent_when_empty() -> None:
    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        assert render_pending_gates(conn, "chrisai", now=FIXED_NOW) is None
    finally:
        conn.close()


def test_pending_gates_groups_by_expiry_class() -> None:
    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        # Seed a Unit target for the gates
        create(conn, "operation", {"id": "OPS-001", "firm_id": "chrisai", "name": "Ops"})
        create(conn, "project", {
            "id": "PROJ-001", "firm_id": "chrisai", "operation_id": "OPS-001",
            "name": "P", "status": "in_progress", "due_date": "2026-12-31",
        })
        create(conn, "unit", {
            "id": "UNIT-014", "firm_id": "chrisai", "project_id": "PROJ-001",
            "name": "Blog post #14 draft",
        })

        # EXPIRED: 1h ago
        create(conn, "gate", {
            "id": "GATE-EXP", "firm_id": "chrisai",
            "requesting_member_id": "MEM-001",
            "action": "publish_post",
            "target_entity_type": "unit", "target_entity_id": "UNIT-014",
            "context": "Expired case",
            "expires_at": "2026-04-15 19:00:00",
        })
        # URGENT: 3h ahead
        create(conn, "gate", {
            "id": "GATE-URG", "firm_id": "chrisai",
            "requesting_member_id": "MEM-001",
            "action": "publish_post",
            "target_entity_type": "unit", "target_entity_id": "UNIT-014",
            "context": "Urgent case",
            "expires_at": "2026-04-15 23:00:00",
        })
        # STANDARD: NULL expires_at
        create(conn, "gate", {
            "id": "GATE-STD", "firm_id": "chrisai",
            "requesting_member_id": "MEM-001",
            "action": "hire_member",
            "target_entity_type": "firm", "target_entity_id": "chrisai",
            "context": "Standard case",
        })

        out = render_pending_gates(conn, "chrisai", now=FIXED_NOW)
        assert out is not None
        assert '<pending-gates count="3">' in out
        # Sections appear in order EXPIRED → URGENT → STANDARD
        exp_idx = out.index("[EXPIRED]")
        urg_idx = out.index("[URGENT]")
        std_idx = out.index("[STANDARD]")
        assert exp_idx < urg_idx < std_idx

        # Polymorphic target-name resolution works
        assert 'on unit "Blog post #14 draft"' in out
        assert 'on firm "ChrisAI"' in out
        assert "requested by Quill" in out

        # Context line present
        assert "Context: Urgent case" in out

        # BEHAVIOR + closing tag
        assert "Use /gate:decide" in out
        assert out.rstrip().endswith("</pending-gates>")
    finally:
        conn.close()


def test_pending_gates_missing_target_renders_fallback() -> None:
    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        # Seed a Unit, then craft a gate pointing at a non-existent document
        # (doesn't have FK on target_entity_id).
        create(conn, "gate", {
            "id": "GATE-GHOST", "firm_id": "chrisai",
            "requesting_member_id": "MEM-001",
            "action": "archive_document",
            "target_entity_type": "document", "target_entity_id": "DOC-MISSING",
        })
        out = render_pending_gates(conn, "chrisai", now=FIXED_NOW)
        assert out is not None
        assert "(target missing)" in out
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-3: <goal-health>
# ---------------------------------------------------------------------------

def test_goal_health_silent_when_empty() -> None:
    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        assert render_goal_health(conn, "chrisai", now=FIXED_NOW) is None
    finally:
        conn.close()


def test_goal_health_renders_three_goals_with_null_metrics() -> None:
    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        create(conn, "operation", {
            "id": "OPS-001", "firm_id": "chrisai", "name": "Content Publishing",
        })
        create(conn, "goal", {
            "id": "GOAL-001", "firm_id": "chrisai", "level": "operation",
            "parent_entity_type": "operation", "parent_entity_id": "OPS-001",
            "target": "Publish 2 longform blog posts per week",
            "metric": {
                "type": "publish_rate",
                "value": 2,
                "unit": "posts_per_week",
                "current": None,
            },
        })
        create(conn, "goal", {
            "id": "GOAL-002", "firm_id": "chrisai", "level": "operation",
            "parent_entity_type": "operation", "parent_entity_id": "OPS-001",
            "target": "Monthly unique visitors trending upward",
            "metric": {
                "type": "unique_visitors",
                "value": None,
                "unit": "per_month",
                "current": None,
                "trend": "growing",
            },
        })
        create(conn, "goal", {
            "id": "GOAL-003", "firm_id": "chrisai", "level": "operation",
            "parent_entity_type": "operation", "parent_entity_id": "OPS-001",
            "target": "Unique-visitor-to-subscriber ratio held or growing",
            "metric": {
                "type": "conversion_ratio",
                "value": None,
                "unit": "subs_per_unique",
                "current": None,
                "trend": "stable_or_growing",
            },
        })

        out = render_goal_health(conn, "chrisai", now=FIXED_NOW)
        assert out is not None
        assert '<goal-health goals="3">' in out
        assert "[OPERATION-LEVEL]" in out

        # GOAL-001: concrete target value, null current
        assert "[GOAL-001] Publish 2 longform blog posts per week" in out
        assert 'parent: operation "Content Publishing"' in out
        assert "target 2 posts_per_week" in out
        assert "current not-yet-baselined" in out

        # GOAL-002: null target value, trend present
        assert "target null per_month" in out
        assert "Trend growing" in out

        # GOAL-003: trend with underscores preserved
        assert "Trend stable_or_growing" in out

        # No crash on deadline-less metrics (no Deadline line emitted)
        assert "Deadline" not in out

        # Footer
        assert "v1 metrics are manually refreshed" in out
        assert out.rstrip().endswith("</goal-health>")
    finally:
        conn.close()


def test_goal_health_renders_deadline_overdue() -> None:
    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        create(conn, "operation", {"id": "OPS-001", "firm_id": "chrisai", "name": "Ops"})
        create(conn, "goal", {
            "id": "GOAL-001", "firm_id": "chrisai", "level": "operation",
            "parent_entity_type": "operation", "parent_entity_id": "OPS-001",
            "target": "Ship MVP",
            "metric": {"type": "completion", "value": 1, "unit": "bool",
                       "deadline": "2026-04-10"},
        })
        out = render_goal_health(conn, "chrisai", now=FIXED_NOW)
        assert out is not None
        assert "Deadline 2026-04-10 — OVERDUE" in out
        assert "5d past" in out
    finally:
        conn.close()


def test_goal_health_orders_firm_before_operation_before_project() -> None:
    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        create(conn, "operation", {"id": "OPS-001", "firm_id": "chrisai", "name": "Ops"})
        create(conn, "project", {
            "id": "PROJ-001", "firm_id": "chrisai", "operation_id": "OPS-001",
            "name": "P", "status": "in_progress", "due_date": "2026-12-31",
        })
        # Create in reverse-render order to prove ORDER BY does work
        create(conn, "goal", {
            "id": "GOAL-P", "firm_id": "chrisai", "level": "project",
            "parent_entity_type": "project", "parent_entity_id": "PROJ-001",
            "target": "Project goal",
        })
        create(conn, "goal", {
            "id": "GOAL-O", "firm_id": "chrisai", "level": "operation",
            "parent_entity_type": "operation", "parent_entity_id": "OPS-001",
            "target": "Operation goal",
        })
        create(conn, "goal", {
            "id": "GOAL-F", "firm_id": "chrisai", "level": "firm",
            "parent_entity_type": "firm", "parent_entity_id": "chrisai",
            "target": "Firm goal",
        })
        out = render_goal_health(conn, "chrisai", now=FIXED_NOW)
        assert out is not None
        f_idx = out.index("GOAL-F")
        o_idx = out.index("GOAL-O")
        p_idx = out.index("GOAL-P")
        assert f_idx < o_idx < p_idx
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Orchestrator: read-only guarantee + empty firm
# ---------------------------------------------------------------------------

def test_render_returns_empty_string_for_empty_firm() -> None:
    conn = _fresh_conn()
    try:
        create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
        assert render(conn, "chrisai", now=FIXED_NOW) == ""
    finally:
        conn.close()


def test_render_produces_no_db_writes() -> None:
    """Row counts across all tables must be stable across a render call."""
    from firm.core.repo import ALL_TABLES

    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        create(conn, "operation", {"id": "OPS-001", "firm_id": "chrisai", "name": "Ops"})
        create(conn, "goal", {
            "id": "GOAL-001", "firm_id": "chrisai", "level": "operation",
            "parent_entity_type": "operation", "parent_entity_id": "OPS-001",
            "target": "x", "metric": {"type": "t", "value": 1, "unit": "u"},
        })

        def snapshot() -> dict[str, int]:
            return {
                t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ALL_TABLES
            }

        before = snapshot()
        render(conn, "chrisai", now=FIXED_NOW)
        after = snapshot()
        assert before == after
    finally:
        conn.close()


def test_render_concatenates_non_none_tags_with_blank_line() -> None:
    conn = _fresh_conn()
    try:
        _seed_chrisai(conn)
        create(conn, "operation", {"id": "OPS-001", "firm_id": "chrisai", "name": "Ops"})
        create(conn, "goal", {
            "id": "GOAL-001", "firm_id": "chrisai", "level": "operation",
            "parent_entity_type": "operation", "parent_entity_id": "OPS-001",
            "target": "x",
        })
        out = render(conn, "chrisai", now=FIXED_NOW)
        # Two tags emitted (roster + goals; no gates)
        assert "</active-roster>" in out
        assert "<goal-health" in out
        assert "</pending-gates>" not in out
        # Tags separated by "\n\n"
        segments = out.split("\n\n")
        # Blank line between tags means at least one empty split entry
        assert any(s.startswith("<active-roster") for s in segments)
        assert any(s.startswith("<goal-health") for s in segments)
    finally:
        conn.close()
