"""Hierarchy query and delegation validation tests.

Tests get_direct_reports, get_management_chain, and can_delegate_to
using the seeded ChrisAI firm hierarchy:
  Board
  └── Sterling (MEM-002)
      ├── Quill (MEM-001)
      └── Sage (MEM-003)
"""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import get, update
from firm.seed import seed_chrisai
from firm.services.member import (
    can_delegate_to,
    get_direct_reports,
    get_management_chain,
)


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
# AC-2: get_direct_reports
# ---------------------------------------------------------------------------

class TestGetDirectReports:

    def test_sterling_has_two_reports(self):
        conn = _seeded_conn()
        reports = get_direct_reports(conn, "chrisai", "MEM-002")
        ids = {r["id"] for r in reports}
        assert ids == {"MEM-001", "MEM-003"}
        conn.close()

    def test_quill_has_no_reports(self):
        conn = _seeded_conn()
        reports = get_direct_reports(conn, "chrisai", "MEM-001")
        assert reports == []
        conn.close()

    def test_sage_has_no_reports(self):
        conn = _seeded_conn()
        reports = get_direct_reports(conn, "chrisai", "MEM-003")
        assert reports == []
        conn.close()

    def test_nonexistent_member_returns_empty(self):
        conn = _seeded_conn()
        reports = get_direct_reports(conn, "chrisai", "MEM-999")
        assert reports == []
        conn.close()

    def test_wrong_firm_returns_empty(self):
        conn = _seeded_conn()
        reports = get_direct_reports(conn, "other_firm", "MEM-002")
        assert reports == []
        conn.close()


# ---------------------------------------------------------------------------
# AC-2: get_management_chain
# ---------------------------------------------------------------------------

class TestGetManagementChain:

    def test_quill_chain_is_sterling(self):
        conn = _seeded_conn()
        chain = get_management_chain(conn, "MEM-001")
        assert len(chain) == 1
        assert chain[0]["id"] == "MEM-002"
        conn.close()

    def test_sage_chain_is_sterling(self):
        conn = _seeded_conn()
        chain = get_management_chain(conn, "MEM-003")
        assert len(chain) == 1
        assert chain[0]["id"] == "MEM-002"
        conn.close()

    def test_sterling_chain_is_empty(self):
        """Sterling reports to Board (None) — empty chain."""
        conn = _seeded_conn()
        chain = get_management_chain(conn, "MEM-002")
        assert chain == []
        conn.close()

    def test_nonexistent_member_raises(self):
        conn = _seeded_conn()
        with pytest.raises(ValueError, match="MEM-999"):
            get_management_chain(conn, "MEM-999")
        conn.close()

    def test_circular_reference_terminates(self):
        """Safety: circular reports_to chain doesn't infinite loop."""
        conn = _seeded_conn()
        # Create a circular chain: Sterling → Quill (normally Quill → Sterling)
        update(conn, "member", "MEM-002", {"reports_to_member_id": "MEM-001"})
        # Now: MEM-001 → MEM-002 → MEM-001 → ... (cycle)
        chain = get_management_chain(conn, "MEM-001")
        # Should terminate — chain has MEM-002 but stops at cycle detection
        assert len(chain) == 1
        assert chain[0]["id"] == "MEM-002"
        conn.close()


# ---------------------------------------------------------------------------
# AC-3: can_delegate_to
# ---------------------------------------------------------------------------

class TestCanDelegateTo:

    def test_sterling_can_delegate_to_quill(self):
        conn = _seeded_conn()
        assert can_delegate_to(conn, "MEM-002", "MEM-001") is True
        conn.close()

    def test_sterling_can_delegate_to_sage(self):
        conn = _seeded_conn()
        assert can_delegate_to(conn, "MEM-002", "MEM-003") is True
        conn.close()

    def test_quill_cannot_delegate_to_sterling(self):
        conn = _seeded_conn()
        assert can_delegate_to(conn, "MEM-001", "MEM-002") is False
        conn.close()

    def test_quill_cannot_delegate_to_sage(self):
        """Quill and Sage are peers, not manager-report."""
        conn = _seeded_conn()
        assert can_delegate_to(conn, "MEM-001", "MEM-003") is False
        conn.close()

    def test_paused_manager_cannot_delegate(self):
        conn = _seeded_conn()
        update(conn, "member", "MEM-002", {"status": "paused"})
        assert can_delegate_to(conn, "MEM-002", "MEM-001") is False
        conn.close()

    def test_paused_assignee_cannot_receive_delegation(self):
        conn = _seeded_conn()
        update(conn, "member", "MEM-001", {"status": "paused"})
        assert can_delegate_to(conn, "MEM-002", "MEM-001") is False
        conn.close()

    def test_nonexistent_manager_raises(self):
        conn = _seeded_conn()
        with pytest.raises(ValueError, match="MEM-999"):
            can_delegate_to(conn, "MEM-999", "MEM-001")
        conn.close()

    def test_nonexistent_assignee_raises(self):
        conn = _seeded_conn()
        with pytest.raises(ValueError, match="MEM-999"):
            can_delegate_to(conn, "MEM-002", "MEM-999")
        conn.close()
