"""Generic member dispatch tests — preflight/postflight for any Member.

Tests that the dispatch module works for all 3 seeded Members,
handles missing units, validates stages, and finalizes runs.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess

import pytest

from firm.commands.member_dispatch import postflight, preflight
from firm.core.migrate import apply_migrations
from firm.core.repo import create, find, get
from firm.seed import seed_chrisai


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _seeded_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    seed_chrisai(conn)
    return conn


# ---------------------------------------------------------------------------
# AC-1: preflight works for any member
# ---------------------------------------------------------------------------

class TestPreflight:

    def test_quill_resolves_write_stage(self):
        conn = _seeded_conn()
        result = preflight(conn, "MEM-001", "write")
        assert result["resolved_cmd"] == "/blog:write"
        assert result["member_id"] == "MEM-001"
        conn.close()

    def test_sterling_resolves_audit_stage(self):
        conn = _seeded_conn()
        result = preflight(conn, "MEM-002", "audit")
        assert result["resolved_cmd"] == "/sterling:audit"
        assert result["member_id"] == "MEM-002"
        conn.close()

    def test_sterling_resolves_queue_stage(self):
        conn = _seeded_conn()
        result = preflight(conn, "MEM-002", "queue")
        assert result["resolved_cmd"] == "/sterling:queue"
        conn.close()

    def test_sage_resolves_surface_stage(self):
        conn = _seeded_conn()
        result = preflight(conn, "MEM-003", "surface")
        assert result["resolved_cmd"] == "/sage:surface"
        assert result["member_id"] == "MEM-003"
        conn.close()

    def test_sage_resolves_analyze_stage(self):
        conn = _seeded_conn()
        result = preflight(conn, "MEM-003", "analyze")
        assert result["resolved_cmd"] == "/sage:analyze"
        conn.close()

    def test_quill_preflight_creates_member_run(self):
        """Quill has UNT-001 claimed — preflight should create a member_run."""
        conn = _seeded_conn()
        result = preflight(conn, "MEM-001", "write")
        assert result["unit"] is not None
        assert result["unit"]["id"] == "UNT-001"
        assert result["run_id"] is not None
        assert result["run_id"].startswith("RUN-")
        # Verify member_run exists in DB
        run = get(conn, "member_run", result["run_id"])
        assert run is not None
        assert run["status"] == "running"
        assert run["member_id"] == "MEM-001"
        conn.close()

    def test_sterling_preflight_no_unit(self):
        """Sterling has no claimed units — preflight returns unit=None, run_id=None."""
        conn = _seeded_conn()
        result = preflight(conn, "MEM-002", "audit")
        assert result["unit"] is None
        assert result["run_id"] is None
        conn.close()

    def test_sage_preflight_no_unit(self):
        conn = _seeded_conn()
        result = preflight(conn, "MEM-003", "surface")
        assert result["unit"] is None
        assert result["run_id"] is None
        conn.close()

    def test_invalid_stage_raises(self):
        conn = _seeded_conn()
        with pytest.raises(ValueError, match="not found in skill_loadout"):
            preflight(conn, "MEM-002", "nonexistent")
        conn.close()

    def test_invalid_member_raises(self):
        conn = _seeded_conn()
        with pytest.raises(ValueError, match="not found"):
            preflight(conn, "MEM-999", "audit")
        conn.close()


# ---------------------------------------------------------------------------
# AC-1: postflight finalizes member_run
# ---------------------------------------------------------------------------

class TestPostflight:

    def test_postflight_completes_run(self):
        conn = _seeded_conn()
        pre = preflight(conn, "MEM-001", "write")
        assert pre["run_id"] is not None
        result = postflight(conn, pre["run_id"], "completed")
        assert result["status"] == "completed"
        # Verify in DB
        run = get(conn, "member_run", pre["run_id"])
        assert run["status"] == "completed"
        assert run["ended_at"] is not None
        conn.close()

    def test_postflight_fails_run(self):
        conn = _seeded_conn()
        pre = preflight(conn, "MEM-001", "write")
        result = postflight(conn, pre["run_id"], "failed")
        assert result["status"] == "failed"
        run = get(conn, "member_run", pre["run_id"])
        assert run["status"] == "failed"
        conn.close()

    def test_postflight_invalid_status_raises(self):
        conn = _seeded_conn()
        pre = preflight(conn, "MEM-001", "write")
        with pytest.raises(ValueError, match="Invalid status"):
            postflight(conn, pre["run_id"], "unknown")
        conn.close()

    def test_postflight_missing_run_raises(self):
        conn = _seeded_conn()
        with pytest.raises(ValueError, match="not found"):
            postflight(conn, "RUN-999", "completed")
        conn.close()
