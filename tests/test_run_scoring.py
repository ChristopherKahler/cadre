"""Tests for operator run-scoring & calibration (fork cadre-calibration-run-scoring).

Covers: the 010 migration, the score_run service (initial + rescore + rejects),
suggest_score mapping, the run-score / firm-setting Board actions, the derived
Floor/profile aggregates (recompute-at-read, no batch job), the toggleable
unrated-runs nudge, and — the hard requirement — member blindness: run_score
reaches NO member-facing surface (MCP read tools + assembled prompt).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from firm.core.migrate import apply_migrations, applied_migration_names
from firm.core.repo import create
from firm.dashboard.server import (
    assemble_state,
    floor_state,
    member_profile,
    perform_action,
)
from firm.pulse.prompt import assemble_prompt
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
        "skill_loadout": json.dumps({
            "tools": ["base nano-banana"],
            "duties": ["draft the thing"],
            "policies": ["never invent facts"],
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


def _run(conn, run_id="RUN-001", member="MEM-001", status="completed",
         started="2026-07-05T10:00:00+00:00", validation=None):
    create(conn, "member_run", {
        "id": run_id, "firm_id": "chrisai", "member_id": member,
        "unit_id": "UNIT-001", "status": status, "started_at": started,
        "ended_at": "2026-07-05T10:05:00+00:00",
        "validation_result": json.dumps(validation) if validation is not None else None,
    })


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def test_migration_adds_run_scoring_columns_idempotently():
    conn = _conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(member_run)")}
    assert {"run_score", "run_score_notes", "reviewed_at", "reviewed_by"} <= cols
    assert "010_run_scoring" in applied_migration_names(conn)
    # Re-applying is a no-op — never re-ALTERs an existing column.
    assert apply_migrations(conn) == []


# ---------------------------------------------------------------------------
# score_run service
# ---------------------------------------------------------------------------

def test_score_run_initial_then_rescore_emits_distinct_records():
    conn = _conn(); _seed(conn); _run(conn)
    out = run_svc.score_run(conn, "RUN-001", 4, "solid work")
    assert out["run_score"] == 4
    assert out["run_score_notes"] == "solid work"
    assert out["reviewed_at"]

    events = [r["event_type"] for r in conn.execute(
        "SELECT event_type FROM records WHERE target_entity_id='RUN-001' ORDER BY id")]
    assert events == ["run.scored"]

    out2 = run_svc.score_run(conn, "RUN-001", 2)
    assert out2["run_score"] == 2
    events = [r["event_type"] for r in conn.execute(
        "SELECT event_type FROM records WHERE target_entity_id='RUN-001' ORDER BY id")]
    assert events == ["run.scored", "run.rescored"]

    # The immutable trail carries the history the column cannot.
    detail = json.loads(next(r["details"] for r in conn.execute(
        "SELECT details FROM records WHERE event_type='run.rescored'")))
    assert detail["previous"] == 4 and detail["score"] == 2


def test_score_run_rejects_out_of_range_and_missing():
    conn = _conn(); _seed(conn); _run(conn)
    for bad in (0, 6, -1, "nope"):
        with pytest.raises(ValueError):
            run_svc.score_run(conn, "RUN-001", bad)
    with pytest.raises(ValueError):
        run_svc.score_run(conn, "RUN-404", 3)


def test_suggest_score_maps_from_validation_signal():
    s = run_svc.suggest_score
    assert s({"status": "completed", "validation_result": {"passed": True}}) == 4
    assert s({"status": "completed", "validation_result": {"passed": True, "retry_triggered": True}}) == 3
    assert s({"status": "completed", "validation_result": {"passed": False}}) == 2
    assert s({"status": "completed", "validation_result": None}) == 3
    assert s({"status": "completed"}) == 3
    assert s({"status": "failed"}) == 2
    assert s({"status": "timed_out"}) == 2
    assert s({"status": "running"}) is None
    # Accepts a JSON-string validation_result (repo may hand back either).
    assert s({"status": "completed", "validation_result": '{"passed": true}'}) == 4


# ---------------------------------------------------------------------------
# Board actions + derived reads
# ---------------------------------------------------------------------------

def test_run_score_action_reflected_across_reads():
    conn = _conn(); _seed(conn); _run(conn, validation={"passed": True})
    res = perform_action(conn, "run-score", "RUN-001", {"score": 5, "notes": "clean"})
    assert res["run_score"] == 5

    state = assemble_state(conn, "chrisai")
    run = next(r for r in state["runs"] if r["id"] == "RUN-001")
    assert run["run_score"] == 5
    assert run["run_score_suggested"] == 4  # passed, first try

    profile = member_profile(conn, Path("/tmp"), "MEM-001")
    assert profile["stats"]["run_score_avg"] == 5.0
    assert profile["stats"]["runs_rated"] == 1

    floor = floor_state(conn, Path("/tmp"), "chrisai")
    card = next(m for m in floor["members"] if m["id"] == "MEM-001")
    assert card["stats"]["run_score_avg"] == 5.0
    assert card["stats"]["runs_rated"] == 1
    assert floor["calibration"]["MEM-001"] == {
        "avg": 5.0, "rated": 1, "total": 1, "recent_avg": 5.0,
    }


def test_rescore_recomputes_aggregates_with_no_batch_job():
    conn = _conn(); _seed(conn); _run(conn)
    run_svc.score_run(conn, "RUN-001", 5)
    assert member_profile(conn, Path("/tmp"), "MEM-001")["stats"]["run_score_avg"] == 5.0
    # A single-column rescore — the very next read recomputes everything.
    run_svc.score_run(conn, "RUN-001", 1)
    assert member_profile(conn, Path("/tmp"), "MEM-001")["stats"]["run_score_avg"] == 1.0
    assert floor_state(conn, Path("/tmp"), "chrisai")["calibration"]["MEM-001"]["avg"] == 1.0


def test_calibration_recent_window_is_last_five():
    conn = _conn(); _seed(conn)
    # Six rated runs, newest last by started_at; recent window = the last five.
    for i, score in enumerate([1, 1, 5, 5, 5, 5]):
        rid = f"RUN-{i:03d}"
        _run(conn, run_id=rid, started=f"2026-07-0{i+1}T10:00:00+00:00")
        run_svc.score_run(conn, rid, score)
    cal = floor_state(conn, Path("/tmp"), "chrisai")["calibration"]["MEM-001"]
    assert cal["rated"] == 6 and cal["total"] == 6
    assert cal["avg"] == round((1 + 1 + 5 + 5 + 5 + 5) / 6, 2)
    assert cal["recent_avg"] == round((5 + 5 + 5 + 5 + 1) / 5, 2)  # newest five


def test_firm_setting_toggle_and_nudge_count():
    conn = _conn(); _seed(conn); _run(conn)  # one completed, unrated run
    state = assemble_state(conn, "chrisai")
    assert state["run_review"] == {"nudge_enabled": False, "unrated_count": 1}

    perform_action(conn, "firm-setting", "run_review_nudge", {"value": True})
    assert assemble_state(conn, "chrisai")["run_review"]["nudge_enabled"] is True

    # Rating the run clears it from the unrated tally (derived count).
    run_svc.score_run(conn, "RUN-001", 4)
    assert assemble_state(conn, "chrisai")["run_review"]["unrated_count"] == 0

    with pytest.raises(ValueError):
        perform_action(conn, "firm-setting", "bogus_key", {"value": True})


# ---------------------------------------------------------------------------
# Member blindness (Invariant #5) — the hard requirement
# ---------------------------------------------------------------------------

def test_run_score_never_reaches_the_assembled_member_prompt():
    conn = _conn(); _seed(conn); _run(conn)
    run_svc.score_run(conn, "RUN-001", 5, "board-only rationale")
    prompt = assemble_prompt(conn, "chrisai", "MEM-001", "UNIT-001", cwd="/tmp")
    assert "run_score" not in prompt
    assert "reviewed_by" not in prompt
    assert "board-only rationale" not in prompt


def test_run_score_never_reaches_the_mcp_read_tools(monkeypatch):
    conn = _conn(); _seed(conn); _run(conn)
    run_svc.score_run(conn, "RUN-001", 5, "board-only rationale")

    from firm.mcp import tools as mcp_tools
    monkeypatch.setattr(mcp_tools, "_conn_factory", lambda: conn)

    blob = "\n".join([
        mcp_tools.firm_view_member("MEM-001"),
        mcp_tools.firm_list_members(),
        mcp_tools.firm_list_units(),
    ])
    assert "run_score" not in blob
    assert "board-only rationale" not in blob
