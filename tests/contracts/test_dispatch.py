"""Tests for firm.contracts.dispatch — stage resolution from Contract.skill_loadout."""

from __future__ import annotations

import json
import sqlite3

import pytest

from firm.contracts.dispatch import list_stages, resolve_stage
from firm.core.migrate import apply_migrations
from firm.core.repo import create


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


QUILL_LOADOUT = json.dumps({
    "stages": {
        "init": "/blog:init",
        "research": "/blog:research",
        "write": "/blog:write",
        "publish": "/blog:publish",
        "full": "/blog:write",
    },
})


def _seed(conn: sqlite3.Connection, *, loadout: str | None = QUILL_LOADOUT) -> None:
    create(conn, "contract", {
        "id": "CON-001",
        "firm_id": "chrisai",
        "name": "Quill Contract",
        "runtime_type": "claude_code",
        "skill_loadout": loadout,
    })
    create(conn, "member", {
        "id": "MEM-001",
        "firm_id": "chrisai",
        "name": "Quill",
        "role": "Blog Author",
        "status": "active",
        "contract_id": "CON-001",
    })


# ---------------------------------------------------------------------------
# resolve_stage
# ---------------------------------------------------------------------------

class TestResolveStage:
    def test_resolves_known_stage(self):
        conn = _fresh_conn()
        _seed(conn)
        assert resolve_stage(conn, "MEM-001", "write") == "/blog:write"

    def test_resolves_full_to_write(self):
        conn = _fresh_conn()
        _seed(conn)
        assert resolve_stage(conn, "MEM-001", "full") == "/blog:write"

    def test_resolves_research(self):
        conn = _fresh_conn()
        _seed(conn)
        assert resolve_stage(conn, "MEM-001", "research") == "/blog:research"

    def test_unknown_stage_raises(self):
        conn = _fresh_conn()
        _seed(conn)
        with pytest.raises(ValueError, match="Stage 'bad' not found"):
            resolve_stage(conn, "MEM-001", "bad")

    def test_error_lists_available_stages(self):
        conn = _fresh_conn()
        _seed(conn)
        with pytest.raises(ValueError, match="full"):
            resolve_stage(conn, "MEM-001", "bad")

    def test_no_member_raises(self):
        conn = _fresh_conn()
        with pytest.raises(ValueError, match="Member.*not found"):
            resolve_stage(conn, "MEM-999", "write")

    def test_no_contract_raises(self):
        conn = _fresh_conn()
        create(conn, "member", {
            "id": "MEM-002",
            "firm_id": "chrisai",
            "name": "Orphan",
            "role": "worker",
            "status": "active",
        })
        with pytest.raises(ValueError, match="has no contract"):
            resolve_stage(conn, "MEM-002", "write")

    def test_invalid_loadout_json_raises(self):
        conn = _fresh_conn()
        _seed(conn, loadout="not json")
        with pytest.raises(ValueError, match="invalid skill_loadout"):
            resolve_stage(conn, "MEM-001", "write")

    def test_no_loadout_raises(self):
        conn = _fresh_conn()
        _seed(conn, loadout=None)
        with pytest.raises(ValueError, match="no skill_loadout"):
            resolve_stage(conn, "MEM-001", "write")


# ---------------------------------------------------------------------------
# list_stages
# ---------------------------------------------------------------------------

class TestListStages:
    def test_returns_all_stages(self):
        conn = _fresh_conn()
        _seed(conn)
        stages = list_stages(conn, "MEM-001")
        assert "write" in stages
        assert "full" in stages
        assert stages["write"] == "/blog:write"
        assert len(stages) == 5

    def test_no_member_returns_empty(self):
        conn = _fresh_conn()
        assert list_stages(conn, "MEM-999") == {}

    def test_no_contract_returns_empty(self):
        conn = _fresh_conn()
        create(conn, "member", {
            "id": "MEM-002",
            "firm_id": "chrisai",
            "name": "Orphan",
            "role": "worker",
            "status": "active",
        })
        assert list_stages(conn, "MEM-002") == {}

    def test_no_loadout_returns_empty(self):
        conn = _fresh_conn()
        _seed(conn, loadout=None)
        assert list_stages(conn, "MEM-001") == {}

    def test_invalid_json_returns_empty(self):
        conn = _fresh_conn()
        _seed(conn, loadout="not json")
        assert list_stages(conn, "MEM-001") == {}
