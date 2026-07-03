"""Seed leadership hierarchy tests — Sterling, Sage, reports_to chains.

Validates that seed_chrisai creates the full 3-member hierarchy with
correct Contracts and reports_to relationships, and that re-running
the seed is idempotent.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import get, find, update
from firm.seed import (
    QUILL_SKILL_LOADOUT,
    SAGE_SKILL_LOADOUT,
    STERLING_SKILL_LOADOUT,
    seed_chrisai,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    return conn


# ---------------------------------------------------------------------------
# Seed creates full hierarchy
# ---------------------------------------------------------------------------

class TestSeedHierarchy:
    """AC-1: seed_chrisai creates 3 Members with correct reports_to chain."""

    def test_seed_creates_three_members(self):
        conn = _fresh_conn()
        ids = seed_chrisai(conn)
        members = find(conn, "member", firm_id="chrisai")
        assert len(members) == 3
        conn.close()

    def test_sterling_is_cmo_no_reports_to(self):
        conn = _fresh_conn()
        seed_chrisai(conn)
        sterling = get(conn, "member", "MEM-002")
        assert sterling is not None
        assert sterling["name"] == "Sterling"
        assert sterling["role"] == "Chief Marketing Officer"
        assert sterling["reports_to_member_id"] is None
        assert sterling["contract_id"] == "CON-002"
        assert sterling["status"] == "active"
        conn.close()

    def test_quill_reports_to_sterling(self):
        conn = _fresh_conn()
        seed_chrisai(conn)
        quill = get(conn, "member", "MEM-001")
        assert quill is not None
        assert quill["reports_to_member_id"] == "MEM-002"
        assert quill["contract_id"] == "CON-001"
        conn.close()

    def test_sage_reports_to_sterling(self):
        conn = _fresh_conn()
        seed_chrisai(conn)
        sage = get(conn, "member", "MEM-003")
        assert sage is not None
        assert sage["name"] == "Sage"
        assert sage["role"] == "Content Strategist"
        assert sage["reports_to_member_id"] == "MEM-002"
        assert sage["contract_id"] == "CON-003"
        assert sage["status"] == "active"
        conn.close()

    def test_seed_creates_three_contracts(self):
        conn = _fresh_conn()
        seed_chrisai(conn)
        contracts = find(conn, "contract", firm_id="chrisai")
        assert len(contracts) == 3
        conn.close()

    def test_con002_has_sterling_loadout(self):
        conn = _fresh_conn()
        seed_chrisai(conn)
        con = get(conn, "contract", "CON-002")
        assert con is not None
        assert con["name"] == "Sterling CMO Contract"
        assert con["runtime_type"] == "claude_code"
        # Repo auto-deserializes JSON; handle both str and dict
        loadout = con["skill_loadout"]
        if isinstance(loadout, str):
            loadout = json.loads(loadout)
        assert loadout == STERLING_SKILL_LOADOUT
        conn.close()

    def test_con003_has_sage_loadout(self):
        conn = _fresh_conn()
        seed_chrisai(conn)
        con = get(conn, "contract", "CON-003")
        assert con is not None
        assert con["name"] == "Sage Content Strategist Contract"
        assert con["runtime_type"] == "claude_code"
        loadout = con["skill_loadout"]
        if isinstance(loadout, str):
            loadout = json.loads(loadout)
        assert loadout == SAGE_SKILL_LOADOUT
        conn.close()

    def test_existing_entities_unchanged(self):
        """OP-001, PRJ-001, UNT-001 still created correctly."""
        conn = _fresh_conn()
        ids = seed_chrisai(conn)
        assert get(conn, "operation", "OP-001") is not None
        assert get(conn, "project", "PRJ-001") is not None
        assert get(conn, "unit", "UNT-001") is not None
        conn.close()

    def test_ids_dict_has_all_keys(self):
        conn = _fresh_conn()
        ids = seed_chrisai(conn)
        assert ids["firm"] == "chrisai"
        assert ids["contract_quill"] == "CON-001"
        assert ids["contract_sterling"] == "CON-002"
        assert ids["contract_sage"] == "CON-003"
        assert ids["member_sterling"] == "MEM-002"
        assert ids["member_quill"] == "MEM-001"
        assert ids["member_sage"] == "MEM-003"
        assert ids["operation"] == "OP-001"
        assert ids["project"] == "PRJ-001"
        assert ids["unit"] == "UNT-001"
        conn.close()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestSeedIdempotency:
    """AC-1 (idempotent): running seed twice changes nothing."""

    def test_double_seed_same_member_count(self):
        conn = _fresh_conn()
        seed_chrisai(conn)
        seed_chrisai(conn)
        members = find(conn, "member", firm_id="chrisai")
        assert len(members) == 3
        conn.close()

    def test_double_seed_same_contract_count(self):
        conn = _fresh_conn()
        seed_chrisai(conn)
        seed_chrisai(conn)
        contracts = find(conn, "contract", firm_id="chrisai")
        assert len(contracts) == 3
        conn.close()

    def test_double_seed_reports_to_unchanged(self):
        conn = _fresh_conn()
        seed_chrisai(conn)
        seed_chrisai(conn)
        quill = get(conn, "member", "MEM-001")
        assert quill["reports_to_member_id"] == "MEM-002"
        sage = get(conn, "member", "MEM-003")
        assert sage["reports_to_member_id"] == "MEM-002"
        conn.close()


# ---------------------------------------------------------------------------
# Upgrade path (existing DB without reports_to)
# ---------------------------------------------------------------------------

class TestSeedUpgradePath:
    """AC-1 (upgrade): existing MEM-001 without reports_to gets updated."""

    def test_quill_without_reports_to_gets_upgraded(self):
        conn = _fresh_conn()
        # Simulate Phase 4 state: Quill exists without reports_to
        from firm.core.repo import create
        create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
        create(conn, "contract", {
            "id": "CON-001",
            "firm_id": "chrisai",
            "name": "Quill Blog Author Contract",
            "runtime_type": "claude_code",
            "skill_loadout": json.dumps(QUILL_SKILL_LOADOUT),
            "runtime_config": json.dumps({"cwd": "."}),
            "pulse_config": json.dumps({"timeout_sec": 300}),
        })
        create(conn, "member", {
            "id": "MEM-001",
            "firm_id": "chrisai",
            "name": "Quill",
            "role": "Blog Author",
            "status": "active",
            "contract_id": "CON-001",
        })
        # Verify no reports_to initially
        quill_before = get(conn, "member", "MEM-001")
        assert quill_before["reports_to_member_id"] is None

        # Run seed — should upgrade Quill's reports_to
        seed_chrisai(conn)

        quill_after = get(conn, "member", "MEM-001")
        assert quill_after["reports_to_member_id"] == "MEM-002"
        conn.close()

    def test_quill_with_reports_to_not_overwritten(self):
        """If Quill already reports to Sterling, seed doesn't re-update."""
        conn = _fresh_conn()
        seed_chrisai(conn)
        quill = get(conn, "member", "MEM-001")
        assert quill["reports_to_member_id"] == "MEM-002"

        # Run again — should not trigger update branch
        seed_chrisai(conn)
        quill = get(conn, "member", "MEM-001")
        assert quill["reports_to_member_id"] == "MEM-002"
        conn.close()
