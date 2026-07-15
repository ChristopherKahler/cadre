"""Tests for the Calibration Ladder (fork cadre-calibration-ladder).

Covers the graduated-autonomy tier model and its enforcement seam:
  - the 012 migration (member.autonomy, idempotent)
  - tier_of: T0 for unrated/low, climbs with sustained quality, ANTI-JUMP
    (never advances on a single rated run)
  - can_loosen: via "tier" (covered), "denied" (with needed_tier), "override"
    (sovereign, regardless of tier)
  - rescore re-tiers on the NEXT read — no batch job (derived-recompute)
  - floor_state carries per-card tier / tier_label / tier_progress
  - next_tier_requirements shape for the UI
  - member blindness (Invariant #5): tier + labels + override reach NO
    member-facing surface (MCP read tools + assembled prompt)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from firm.core.migrate import apply_migrations, applied_migration_names
from firm.core.repo import create
from firm.dashboard import calibration as cal
from firm.dashboard.server import floor_state, perform_action
from firm.pulse.prompt import assemble_prompt
from firm.services import autonomy as autonomy_svc
from firm.services import run as run_svc


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    create(conn, "contract", {
        "id": "CON-001", "firm_id": "chrisai", "name": "Writer",
        "runtime_type": "claude_code",
        "validation_config": json.dumps({
            "deny": [{"match": "curl", "reason": "no outbound", "tool": "Bash"}],
        }),
    })
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai", "name": "Sterling",
        "role": "CMO", "status": "active", "contract_id": "CON-001",
    })
    create(conn, "operation", {
        "id": "OPS-001", "firm_id": "chrisai", "name": "Content", "status": "active",
    })
    create(conn, "project", {
        "id": "PROJ-001", "firm_id": "chrisai", "operation_id": "OPS-001",
        "name": "IG Engine", "status": "in_progress", "due_date": "2026-12-31",
    })
    create(conn, "unit", {
        "id": "UNIT-001", "firm_id": "chrisai", "project_id": "PROJ-001",
        "name": "Pillar map", "status": "pending", "claimed_by": "MEM-001",
    })


def _rate(conn, n, score, member="MEM-001", start_day=1):
    """Create + Board-score ``n`` completed runs at ``score`` for a member."""
    for i in range(n):
        rid = f"RUN-{member}-{start_day:02d}-{i:03d}"
        create(conn, "member_run", {
            "id": rid, "firm_id": "chrisai", "member_id": member,
            "unit_id": "UNIT-001", "status": "completed",
            "started_at": f"2026-07-{start_day:02d}T{i % 24:02d}:00:00+00:00",
        })
        run_svc.score_run(conn, rid, score)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def test_migration_adds_autonomy_column_idempotently():
    conn = _conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(member)")}
    assert "autonomy" in cols
    assert "012_autonomy_override" in applied_migration_names(conn)
    assert apply_migrations(conn) == []  # re-apply is a no-op


# ---------------------------------------------------------------------------
# tier_of — anti-jump + sustained climb
# ---------------------------------------------------------------------------

def test_unrated_member_is_tier_zero():
    conn = _conn(); _seed(conn)
    assert cal.tier_of(conn, "chrisai", "MEM-001") == 0


def test_single_high_rated_run_never_jumps_a_tier():
    conn = _conn(); _seed(conn)
    _rate(conn, 1, 5)  # one perfect score
    # rated=1 is below T1's min_rated (3) — sustained quality, not luck.
    assert cal.tier_of(conn, "chrisai", "MEM-001") == 0


def test_tier_climbs_with_sustained_quality():
    conn = _conn(); _seed(conn)
    _rate(conn, 3, 5, start_day=1)                     # rated 3 @5.0
    assert cal.tier_of(conn, "chrisai", "MEM-001") == 1
    _rate(conn, 5, 5, start_day=2)                     # rated 8 @5.0
    assert cal.tier_of(conn, "chrisai", "MEM-001") == 2
    _rate(conn, 12, 5, start_day=3)                    # rated 20 @5.0
    assert cal.tier_of(conn, "chrisai", "MEM-001") == 3
    _rate(conn, 20, 5, start_day=4)                    # rated 40 @5.0
    assert cal.tier_of(conn, "chrisai", "MEM-001") == 4


def test_high_count_low_average_stays_low_tier():
    conn = _conn(); _seed(conn)
    _rate(conn, 30, 3, start_day=1)  # plenty of runs, but avg 3.0 < T1's 3.5
    assert cal.tier_of(conn, "chrisai", "MEM-001") == 0


# ---------------------------------------------------------------------------
# can_loosen — tier / denied / override
# ---------------------------------------------------------------------------

def test_can_loosen_via_tier_when_covered():
    conn = _conn(); _seed(conn)
    _rate(conn, 8, 5)  # T2
    res = cal.can_loosen(conn, "chrisai", "MEM-001", "read")  # needs T1
    assert res == {
        "allowed": True, "reason": res["reason"],
        "via": "tier", "tier": 2, "needed_tier": 1,
    }
    assert res["allowed"] is True and res["via"] == "tier"


def test_can_loosen_denied_below_needed_tier():
    conn = _conn(); _seed(conn)
    _rate(conn, 8, 5)  # T2
    res = cal.can_loosen(conn, "chrisai", "MEM-001", "network")  # needs T4
    assert res["allowed"] is False
    assert res["via"] == "denied"
    assert res["tier"] == 2 and res["needed_tier"] == 4


def test_unknown_capability_needs_full_trust_by_default():
    conn = _conn(); _seed(conn)
    _rate(conn, 20, 5)  # T3
    res = cal.can_loosen(conn, "chrisai", "MEM-001", "something-weird")
    assert res["via"] == "denied" and res["needed_tier"] == cal.MAX_TIER


def test_can_loosen_via_override_regardless_of_tier():
    conn = _conn(); _seed(conn)  # MEM-001 is T0, unrated
    # Per-capability sovereign grant.
    autonomy_svc.set_sovereign_override(conn, "MEM-001", ["network"])
    res = cal.can_loosen(conn, "chrisai", "MEM-001", "network")
    assert res["allowed"] is True and res["via"] == "override"
    assert res["tier"] == 0  # tier still derived; override just stops gating

    # Blanket sovereignty covers everything.
    autonomy_svc.set_sovereign_override(conn, "MEM-001", ["*"])
    res = cal.can_loosen(conn, "chrisai", "MEM-001", "anything-at-all")
    assert res["allowed"] is True and res["via"] == "override"

    # Clearing the override restores the guardrail default.
    autonomy_svc.set_sovereign_override(conn, "MEM-001", [])
    res = cal.can_loosen(conn, "chrisai", "MEM-001", "network")
    assert res["allowed"] is False and res["via"] == "denied"


def test_override_via_risk_class_matches_capability_token():
    conn = _conn(); _seed(conn)
    # Grant the risk-class; a concrete capability that classifies into it loosens.
    autonomy_svc.set_sovereign_override(conn, "MEM-001", ["network"])
    res = cal.can_loosen(conn, "chrisai", "MEM-001", "WebFetch")  # classifies → network
    assert res["allowed"] is True and res["via"] == "override"


def test_member_sovereign_board_action_persists_and_loosens():
    conn = _conn(); _seed(conn)
    perform_action(conn, "member-sovereign", "MEM-001", {"capabilities": ["*"]})
    assert cal.sovereign_capabilities(conn, "chrisai", "MEM-001") == ["*"]
    assert cal.can_loosen(conn, "chrisai", "MEM-001", "shell")["via"] == "override"
    # A member.autonomy_updated Records row proves the audited write path.
    events = [r["event_type"] for r in conn.execute(
        "SELECT event_type FROM records WHERE target_entity_id='MEM-001'")]
    assert "member.autonomy_updated" in events


# ---------------------------------------------------------------------------
# Rescore re-tiers on the next read (no batch job)
# ---------------------------------------------------------------------------

def test_rescore_retiers_on_next_read():
    conn = _conn(); _seed(conn)
    # Three runs at 4 → avg 4.0, rated 3 → T1.
    ids = []
    for i in range(3):
        rid = f"RUN-{i:03d}"
        create(conn, "member_run", {
            "id": rid, "firm_id": "chrisai", "member_id": "MEM-001",
            "unit_id": "UNIT-001", "status": "completed",
            "started_at": f"2026-07-0{i+1}T10:00:00+00:00",
        })
        run_svc.score_run(conn, rid, 4)
        ids.append(rid)
    assert cal.tier_of(conn, "chrisai", "MEM-001") == 1

    # A single rescore drops the average below T1's floor — the very next read
    # re-tiers to T0. No cached tier, no batch job.
    run_svc.score_run(conn, ids[0], 1)  # avg now (1+4+4)/3 = 3.0 < 3.5
    assert cal.tier_of(conn, "chrisai", "MEM-001") == 0


# ---------------------------------------------------------------------------
# next_tier_requirements
# ---------------------------------------------------------------------------

def test_next_tier_requirements_reports_the_gap():
    conn = _conn(); _seed(conn)
    _rate(conn, 3, 5)  # T1
    req = cal.next_tier_requirements(conn, "chrisai", "MEM-001")
    assert req["needed_tier"] == 2
    assert req["needs"]["rated"] == 8   # needs 8 rated for T2
    assert req["have"]["rated"] == 3


def test_next_tier_requirements_none_at_cap():
    conn = _conn(); _seed(conn)
    _rate(conn, 40, 5)  # T4 (cap)
    req = cal.next_tier_requirements(conn, "chrisai", "MEM-001")
    assert req["needed_tier"] is None and req["needs"] == {}


# ---------------------------------------------------------------------------
# floor_state exposure
# ---------------------------------------------------------------------------

def test_floor_state_carries_tier_for_rated_and_unrated():
    conn = _conn(); _seed(conn)
    # A second, unrated member.
    create(conn, "member", {
        "id": "MEM-002", "firm_id": "chrisai", "name": "Quinn",
        "role": "Writer", "status": "active", "contract_id": "CON-001",
    })
    _rate(conn, 8, 5, member="MEM-001")  # T2

    floor = floor_state(conn, Path("/tmp"), "chrisai")
    rated = next(m for m in floor["members"] if m["id"] == "MEM-001")
    unrated = next(m for m in floor["members"] if m["id"] == "MEM-002")

    assert rated["tier"] == 2
    assert rated["tier_label"] == "Trusted"
    assert rated["tier_progress"]["tier"] == 2
    assert rated["tier_progress"]["next_tier"] == 3

    assert unrated["tier"] == 0
    assert unrated["tier_label"] == "Probation"
    assert unrated["tier_progress"]["needs"]["rated"] == 3


def test_floor_state_tier_progress_shows_sovereign_grants():
    conn = _conn(); _seed(conn)
    autonomy_svc.set_sovereign_override(conn, "MEM-001", ["*"])
    floor = floor_state(conn, Path("/tmp"), "chrisai")
    card = next(m for m in floor["members"] if m["id"] == "MEM-001")
    assert card["tier_progress"]["sovereign"] == ["*"]


# ---------------------------------------------------------------------------
# Member blindness (Invariant #5) — the hard requirement
# ---------------------------------------------------------------------------

_TIER_TOKENS = [
    "tier", "Probation", "Provisional", "Trusted", "Autonomous", "Principal",
    "sovereign", "autonomy", "can_loosen", "calibration",
]


def test_tier_never_reaches_the_assembled_member_prompt():
    conn = _conn(); _seed(conn)
    _rate(conn, 20, 5)  # T3
    autonomy_svc.set_sovereign_override(conn, "MEM-001", ["*"])
    prompt = assemble_prompt(conn, "chrisai", "MEM-001", "UNIT-001", cwd="/tmp")
    low = prompt.lower()
    for token in _TIER_TOKENS:
        assert token.lower() not in low, f"{token!r} leaked into the member prompt"


def test_tier_never_reaches_the_mcp_read_tools(monkeypatch):
    conn = _conn(); _seed(conn)
    _rate(conn, 20, 5)  # T3
    autonomy_svc.set_sovereign_override(conn, "MEM-001", ["*"])

    from firm.mcp import tools as mcp_tools
    monkeypatch.setattr(mcp_tools, "_conn_factory", lambda: conn)

    blob = "\n".join([
        mcp_tools.firm_view_member("MEM-001"),
        mcp_tools.firm_list_members(),
        mcp_tools.firm_list_units(),
    ]).lower()
    for token in _TIER_TOKENS:
        assert token.lower() not in blob, f"{token!r} leaked into an MCP tool"
