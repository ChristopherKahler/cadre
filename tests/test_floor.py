"""Tests for firm.dashboard.server.floor_state — The Floor's derived payload.

The three laws under test:
  1. XP anchors to verified outcomes — zero XP for activity (runs never level).
  2. Derived, never authored — floor_state writes nothing.
  3. Board-facing only — (enforced by construction: nothing here touches
     member prompts; see prompt.py, which has no floor imports).
"""

from __future__ import annotations

import json
import sqlite3
from unittest import mock

from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.dashboard.server import (
    _goal_completed,
    _level_for,
    floor_state,
)

LOADOUT = {
    "skills": ["voice-system"],
    "commands": ["daily-page"],
    "mcp": ["notion", "slack-desk"],
    "cli": ["gws", "jq"],
    "knowledge": [{"path": "/home/x/docs/estate", "teaches": "the estate binder"}],
}
VALIDATION = {
    "gates_required": ["Every outbound draft, without exception"],
    "deny": [
        {"match": "*messages.send*", "reason": "Firm NEVER: drafts everything, sends nothing.",
         "tool": "gws"},
        {"match": "*drafts.send*", "reason": "The drafting scope carries send; this removes it."},
    ],
}


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {
        "id": "chrisai", "name": "ChrisAI", "north_star": "Grow the audience",
    })
    create(conn, "contract", {
        "id": "CON-001", "firm_id": "chrisai", "name": "Standard",
        "runtime_type": "claude_code",
        "skill_loadout": json.dumps(LOADOUT),
        "validation_config": json.dumps(VALIDATION),
        "pulse_config": json.dumps({"timeout_sec": 1800, "model": "sonnet"}),
    })
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai", "name": "Sterling",
        "role": "CMO", "status": "active", "contract_id": "CON-001",
        "description": "Owns the audience number.",
    })
    create(conn, "member", {
        "id": "MEM-002", "firm_id": "chrisai", "name": "Quill",
        "role": "Writer", "status": "active", "reports_to_member_id": "MEM-001",
    })
    create(conn, "operation", {
        "id": "OPS-001", "firm_id": "chrisai", "name": "Content", "status": "active",
    })
    create(conn, "project", {
        "id": "PROJ-001", "firm_id": "chrisai", "operation_id": "OPS-001",
        "name": "IG Engine", "status": "in_progress", "due_date": "2026-12-31",
    })
    return conn


def _floor(conn, tools=None, **kw):
    """floor_state with the machine survey stubbed — tests never shell out."""
    from pathlib import Path
    inv = {"skills": [], "commands": [], "tools": tools or []}
    with mock.patch("firm.dashboard.server.sysconfig_svc.inventory", return_value=inv):
        return floor_state(conn, Path("/tmp"), "chrisai", **kw)


# ---------------------------------------------------------------------------
# Shape: identity, loadout sockets, seals, oaths, budget, tenure, lead
# ---------------------------------------------------------------------------


def test_floor_state_shape_and_loadout():
    conn = _fresh_conn()
    F = _floor(conn, tools=[{"name": "gws", "version": "1.2", "description": "gws — operator@example.com"}])

    assert F["firm"]["id"] == "chrisai"
    assert [m["id"] for m in F["members"]] == ["MEM-001", "MEM-002"]

    m = F["members"][0]
    assert m["name"] == "Sterling"
    assert m["owns"] == "Owns the audience number."
    lo = m["loadout"]
    assert lo["mcp"] == ["notion", "slack-desk"]
    assert lo["skills"] == ["voice-system"]
    assert lo["commands"] == ["daily-page"]
    # CLI instruments wear the live identity the machine reports
    assert lo["cli"][0] == {"name": "gws", "detail": "gws — operator@example.com"}
    assert lo["cli"][1] == {"name": "jq", "detail": ""}
    # knowledge tomes normalize to name + teaches
    assert lo["knowledge"] == [{"name": "estate", "teaches": "the estate binder"}]

    # contract zone: oaths + seals (grouped by the tool they lock) + budget dial
    assert m["oaths"] == ["Every outbound draft, without exception"]
    assert m["seals"][0]["match"] == "*messages.send*"
    assert "sends nothing" in m["seals"][0]["reason"]
    assert m["seals"][0]["tool"] == "gws"
    assert m["seals"][1]["tool"] == ""          # pre-tagging rule → unlabeled bucket
    assert m["budget"] == {"model": "sonnet", "timeout_sec": 1800}

    # org shape: Sterling leads (Quill reports to them), Quill does not
    assert m["lead"] is True
    assert F["members"][1]["lead"] is False
    # both created with the firm → founding tenure
    assert m["tenure"]["founding"] is True

    # a member with no contract renders empty sockets, never crashes
    q = F["members"][1]
    assert q["loadout"]["mcp"] == [] and q["seals"] == [] and q["oaths"] == []
    assert q["budget"]["model"] is None


def test_floor_hired_later_is_not_founding():
    conn = _fresh_conn()
    create(conn, "member", {
        "id": "MEM-003", "firm_id": "chrisai", "name": "Late",
        "role": "Hire", "status": "active",
        "created_at": "2099-01-01 00:00:00",
    })
    F = _floor(conn)
    late = next(m for m in F["members"] if m["id"] == "MEM-003")
    assert late["tenure"]["founding"] is False
    assert late["tenure"]["since"] == "2099-01-01 00:00:00"


def test_floor_state_is_pure_read():
    """Law 2: derived, never authored — no rows appear anywhere."""
    conn = _fresh_conn()
    counts_before = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("records", "unit", "document", "gate", "escalation", "member")
    }
    _floor(conn)
    counts_after = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in counts_before
    }
    assert counts_before == counts_after


def test_floor_survives_broken_machine_survey():
    conn = _fresh_conn()
    from pathlib import Path
    with mock.patch("firm.dashboard.server.sysconfig_svc.inventory",
                    side_effect=RuntimeError("base exploded")):
        F = floor_state(conn, Path("/tmp"), "chrisai")
    assert F["members"][0]["loadout"]["cli"][0] == {"name": "gws", "detail": ""}


# ---------------------------------------------------------------------------
# The XP economy — law 1: outcomes only, zero XP for activity
# ---------------------------------------------------------------------------


def _run(conn, i, status="completed", member="MEM-001"):
    create(conn, "member_run", {
        "id": f"RUN-{i:03d}", "firm_id": "chrisai", "member_id": member,
        "status": status, "started_at": "2026-07-05T10:00:00+00:00",
    })


def test_runs_never_drive_xp():
    conn = _fresh_conn()
    for i in range(12):
        _run(conn, i)
    m = _floor(conn)["members"][0]
    assert m["stats"]["runs_total"] == 12
    assert m["stats"]["runs_survived"] == 12
    assert m["xp"] == 0            # the whole point
    assert m["level"] == 1


def test_xp_counts_shipped_gates_and_actioned_escalations():
    conn = _fresh_conn()
    # a closed unit WITH a registered deliverable → 10
    create(conn, "unit", {
        "id": "UNIT-001", "firm_id": "chrisai", "project_id": "PROJ-001",
        "name": "Pillar map", "status": "done", "assignee_member_id": "MEM-001",
    })
    create(conn, "document", {
        "id": "DOC-001", "firm_id": "chrisai", "name": "The map",
        "type": "report", "content_path": "docs/map.md",
        "parent_entity_type": "unit", "parent_entity_id": "UNIT-001",
    })
    # a closed unit WITHOUT a deliverable → 0 (closed, not shipped)
    create(conn, "unit", {
        "id": "UNIT-002", "firm_id": "chrisai", "project_id": "PROJ-001",
        "name": "Quiet close", "status": "done", "assignee_member_id": "MEM-001",
    })
    # gate raised → approved → 5; a pending one → 0
    create(conn, "gate", {
        "id": "GATE-001", "firm_id": "chrisai", "requesting_member_id": "MEM-001",
        "action": "Ship it", "target_entity_type": "unit",
        "target_entity_id": "UNIT-001", "status": "approved",
    })
    create(conn, "gate", {
        "id": "GATE-002", "firm_id": "chrisai", "requesting_member_id": "MEM-001",
        "action": "Later", "target_entity_type": "unit",
        "target_entity_id": "UNIT-002", "status": "pending",
    })
    # escalation raised and resolved by the Board → 5; open one → 0
    create(conn, "escalation", {
        "id": "ESC-001", "firm_id": "chrisai", "raised_by_member_id": "MEM-001",
        "title": "Need a decision", "status": "resolved", "dedupe_key": "esc-1",
    })
    create(conn, "escalation", {
        "id": "ESC-002", "firm_id": "chrisai", "raised_by_member_id": "MEM-001",
        "title": "Still open", "status": "open", "dedupe_key": "esc-2",
    })

    m = _floor(conn)["members"][0]
    st = m["stats"]
    assert st["units_closed"] == 2
    assert st["units_shipped"] == 1
    assert st["deliverables"] == 1
    assert st["gates_raised"] == 2 and st["gates_approved"] == 1
    assert st["escalations_raised"] == 2 and st["escalations_actioned"] == 1
    assert m["xp"] == 10 + 5 + 5
    assert m["level"] == 1 and m["level_next_at"] == 25 and m["level_floor"] == 0


def test_level_curve():
    assert _level_for(0) == (1, 25)
    assert _level_for(24) == (1, 25)
    assert _level_for(25) == (2, 60)
    assert _level_for(120) == (4, 220)
    assert _level_for(1500) == (10, None)
    assert _level_for(99999) == (10, None)


def test_spend_is_a_stat_not_a_driver():
    conn = _fresh_conn()
    create(conn, "usage_event", {
        "id": "USE-001", "firm_id": "chrisai", "member_id": "MEM-001",
        "timestamp": "2026-07-05T10:00:00+00:00", "plan": "api",
        "dollar_equivalent": 13.9857,
    })
    m = _floor(conn)["members"][0]
    assert m["stats"]["spend_usd"] == 13.9857
    assert m["stats"]["cost_per_deliverable"] is None   # no deliverables yet
    assert m["xp"] == 0


# ---------------------------------------------------------------------------
# Goal completion — the only jackpot in the game
# ---------------------------------------------------------------------------


def test_goal_completed_direction_aware():
    assert _goal_completed({"metric": json.dumps({"current": 6, "value": 5})}) is True
    assert _goal_completed({"metric": json.dumps({"current": 4, "value": 5})}) is False
    assert _goal_completed({"metric": json.dumps(
        {"current": 0, "target": 0, "direction": "lower_is_better"})}) is True
    assert _goal_completed({"metric": json.dumps(
        {"current": 3, "target": 0, "direction": "lower_is_better"})}) is False
    # prose, missing numbers, or an unbaselined current never complete
    assert _goal_completed({"metric": "grow the audience", "target": ">= 5 assets"}) is False
    assert _goal_completed({"metric": json.dumps({"current": None, "target": 0})}) is False
    assert _goal_completed({}) is False


def test_firm_goal_unlocks_floor_wide_achievement():
    conn = _fresh_conn()
    create(conn, "goal", {
        "id": "GOAL-001", "firm_id": "chrisai", "level": "firm",
        "parent_entity_type": "firm", "parent_entity_id": "chrisai",
        "target": "500 followers",
        "metric": json.dumps({"name": "followers", "current": 512, "value": 500}),
        "status": "active",
    })
    F = _floor(conn)
    assert F["goal_completed"] is True
    for m in F["members"]:
        goal_ach = next(a for a in m["achievements"] if a["track"] == "goal")
        assert goal_ach["unlocked"] is True


# ---------------------------------------------------------------------------
# Achievements — derived at render, progress visible
# ---------------------------------------------------------------------------


def test_achievement_tracks_and_progress():
    conn = _fresh_conn()
    for i in range(3):
        _run(conn, i)
    _run(conn, 99, status="failed")
    create(conn, "document", {
        "id": "DOC-001", "firm_id": "chrisai", "name": "First artifact",
        "type": "report", "content_path": "docs/a.md",
        "parent_entity_type": "firm", "parent_entity_id": "chrisai",
        "author_id": "MEM-001",
    })
    create(conn, "escalation", {
        "id": "ESC-001", "firm_id": "chrisai", "raised_by_member_id": "MEM-001",
        "title": "Flag", "status": "open", "dedupe_key": "esc-flag",
    })

    m = _floor(conn)["members"][0]
    by_name = {a["name"]: a for a in m["achievements"]}

    hundred = by_name["Hundred survived"]
    assert hundred["track"] == "service"
    assert hundred["progress"] == 3 and hundred["target"] == 100
    assert hundred["unlocked"] is False     # failed runs never count

    assert by_name["First artifact"]["unlocked"] is True
    assert by_name["Raised the flag"]["unlocked"] is True    # honesty pays on raise
    assert by_name["Ten shipped"]["progress"] == 0
    assert by_name["The number hit"]["unlocked"] is False
