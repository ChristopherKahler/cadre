"""Tests for firm.heuristics.gaps — gap detection + hire proposal flow."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from firm.core import repo
from firm.core.migrate import apply_migrations
from firm.heuristics.gaps import detect_gaps, propose_hire
from firm.seed import seed_chrisai


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    seed_chrisai(conn)
    return conn


# ---------------------------------------------------------------------------
# detect_gaps
# ---------------------------------------------------------------------------


class TestDetectGaps:

    def test_returns_expected_keys(self) -> None:
        conn = _fresh_conn()
        report = detect_gaps(conn, "chrisai")
        assert set(report.keys()) == {
            "unclaimed_units", "overloaded_members", "stale_goals",
            "coverage_gaps", "summary",
        }

    def test_no_unclaimed_when_all_claimed(self) -> None:
        conn = _fresh_conn()
        # Seed creates UNT-001 claimed by MEM-001
        report = detect_gaps(conn, "chrisai")
        assert report["unclaimed_units"] == []

    def test_unclaimed_unit_surfaces(self) -> None:
        conn = _fresh_conn()
        repo.create(conn, "unit", {
            "id": "UNT-900",
            "firm_id": "chrisai",
            "project_id": "PRJ-001",
            "name": "Unclaimed blog post",
            "status": "pending",
            "depends_on": json.dumps([]),
        })
        report = detect_gaps(conn, "chrisai")
        ids = {u["id"] for u in report["unclaimed_units"]}
        assert "UNT-900" in ids
        assert "1 unclaimed" in report["summary"]

    def test_done_unclaimed_unit_not_surfaced(self) -> None:
        conn = _fresh_conn()
        repo.create(conn, "unit", {
            "id": "UNT-901",
            "firm_id": "chrisai",
            "project_id": "PRJ-001",
            "name": "Already done",
            "status": "done",
            "depends_on": json.dumps([]),
        })
        report = detect_gaps(conn, "chrisai")
        ids = {u["id"] for u in report["unclaimed_units"]}
        assert "UNT-901" not in ids

    def test_overloaded_member_flagged(self) -> None:
        conn = _fresh_conn()
        # MEM-001 already has UNT-001. Add 2 more pending to hit threshold=3.
        for idx in range(2):
            repo.create(conn, "unit", {
                "id": f"UNT-80{idx}",
                "firm_id": "chrisai",
                "project_id": "PRJ-001",
                "name": f"Extra unit {idx}",
                "status": "pending",
                "claimed_by": "MEM-001",
                "depends_on": json.dumps([]),
            })
        report = detect_gaps(conn, "chrisai", overload_threshold=3)
        overloaded_ids = {m["member_id"] for m in report["overloaded_members"]}
        assert "MEM-001" in overloaded_ids
        entry = next(m for m in report["overloaded_members"] if m["member_id"] == "MEM-001")
        assert entry["active_unit_count"] == 3

    def test_overload_respects_threshold(self) -> None:
        conn = _fresh_conn()
        # Seed gives MEM-001 exactly 1 active unit. Threshold=2 → not overloaded.
        report = detect_gaps(conn, "chrisai", overload_threshold=2)
        assert all(m["member_id"] != "MEM-001" for m in report["overloaded_members"])

    def test_stale_goal_detected(self) -> None:
        conn = _fresh_conn()
        # Create an active goal then backdate updated_at past the stale threshold.
        past = (datetime.now(tz=timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        repo.create(conn, "goal", {
            "id": "GOAL-900",
            "firm_id": "chrisai",
            "target": "Publish 10 posts/month",
            "parent_entity_type": "operation",
            "parent_entity_id": "OP-001",
            "status": "active",
        })
        conn.execute(
            "UPDATE goal SET updated_at = ? WHERE id = ?",
            (past, "GOAL-900"),
        )
        conn.commit()
        report = detect_gaps(conn, "chrisai", stale_days=7)
        ids = {g["goal_id"] for g in report["stale_goals"]}
        assert "GOAL-900" in ids

    def test_fresh_goal_not_stale(self) -> None:
        conn = _fresh_conn()
        repo.create(conn, "goal", {
            "id": "GOAL-901",
            "firm_id": "chrisai",
            "target": "Brand refresh",
            "parent_entity_type": "operation",
            "parent_entity_id": "OP-001",
            "status": "active",
        })
        report = detect_gaps(conn, "chrisai", stale_days=7)
        ids = {g["goal_id"] for g in report["stale_goals"]}
        assert "GOAL-901" not in ids

    def test_achieved_goal_not_flagged_even_if_stale(self) -> None:
        conn = _fresh_conn()
        past = (datetime.now(tz=timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        repo.create(conn, "goal", {
            "id": "GOAL-902",
            "firm_id": "chrisai",
            "target": "Q1 launch",
            "parent_entity_type": "operation",
            "parent_entity_id": "OP-001",
            "status": "achieved",
        })
        conn.execute(
            "UPDATE goal SET updated_at = ? WHERE id = ?",
            (past, "GOAL-902"),
        )
        conn.commit()
        report = detect_gaps(conn, "chrisai", stale_days=7)
        ids = {g["goal_id"] for g in report["stale_goals"]}
        assert "GOAL-902" not in ids

    def test_coverage_gap_surfaces(self) -> None:
        conn = _fresh_conn()
        # "Video editing" has no overlap with Quill/Sterling/Sage vocab.
        repo.create(conn, "unit", {
            "id": "UNT-910",
            "firm_id": "chrisai",
            "project_id": "PRJ-001",
            "name": "Video editing montage sequence",
            "status": "pending",
            "depends_on": json.dumps([]),
        })
        report = detect_gaps(conn, "chrisai")
        gap_ids = {g["unit_id"] for g in report["coverage_gaps"]}
        assert "UNT-910" in gap_ids

    def test_covered_unit_not_gap(self) -> None:
        conn = _fresh_conn()
        # "Blog ideate" overlaps with Quill's role + skill_loadout stages.
        repo.create(conn, "unit", {
            "id": "UNT-911",
            "firm_id": "chrisai",
            "project_id": "PRJ-001",
            "name": "Blog ideate session",
            "status": "pending",
            "depends_on": json.dumps([]),
        })
        report = detect_gaps(conn, "chrisai")
        gap_ids = {g["unit_id"] for g in report["coverage_gaps"]}
        assert "UNT-911" not in gap_ids

    def test_summary_clean_when_no_gaps(self) -> None:
        conn = _fresh_conn()
        report = detect_gaps(conn, "chrisai")
        assert report["summary"] == "no gaps detected"


# ---------------------------------------------------------------------------
# propose_hire
# ---------------------------------------------------------------------------


class TestProposeHire:

    def test_creates_hire_gate(self) -> None:
        conn = _fresh_conn()
        gate = propose_hire(
            conn,
            "chrisai",
            "MEM-002",
            proposed_role="Video Editor",
            proposed_description="Produces shortform video cuts",
            justification="2 unclaimed video units this week; no current coverage",
        )
        assert gate["status"] == "pending"
        assert gate["action"] == "hire_member"
        assert gate["requesting_member_id"] == "MEM-002"
        assert gate["target_entity_type"] == "firm"
        assert gate["target_entity_id"] == "chrisai"

        ctx = json.loads(gate["context"])
        assert ctx["proposed_role"] == "Video Editor"
        assert "unclaimed video" in ctx["justification"]

    def test_invalid_proposer_raises(self) -> None:
        conn = _fresh_conn()
        with pytest.raises(ValueError, match="not found"):
            propose_hire(
                conn, "chrisai", "MEM-999",
                proposed_role="X", proposed_description="", justification="why",
            )

    def test_paused_proposer_raises(self) -> None:
        conn = _fresh_conn()
        repo.update(conn, "member", "MEM-002", {"status": "paused"})
        with pytest.raises(ValueError, match="active"):
            propose_hire(
                conn, "chrisai", "MEM-002",
                proposed_role="X", proposed_description="", justification="why",
            )

    def test_missing_role_raises(self) -> None:
        conn = _fresh_conn()
        with pytest.raises(ValueError, match="proposed_role"):
            propose_hire(
                conn, "chrisai", "MEM-002",
                proposed_role="", proposed_description="d", justification="j",
            )

    def test_missing_justification_raises(self) -> None:
        conn = _fresh_conn()
        with pytest.raises(ValueError, match="justification"):
            propose_hire(
                conn, "chrisai", "MEM-002",
                proposed_role="Editor", proposed_description="d", justification="",
            )
