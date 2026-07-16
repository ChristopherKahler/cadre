"""A6 authority gate — the five self-govern tools require a Board grant.

Matrix: each governed tool × {authorized, unauthorized, no-identity}, plus
the cross-cutting guarantees (no partial mutation on denial, the grant never
leaks to a member surface, a denial returns {"error"} and never raises).

Identity is the process env var CADRE_MEMBER_ID, set per-test. Grants are
written through the Board write path (services.autonomy.set_authority_grant)
onto the shared in-memory seed, so the tool's own _get_conn sees them.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.mcp import tools as mcp_tools
from firm.seed import seed_chrisai
from firm.services import authority as authority_svc
from firm.services import autonomy as autonomy_svc

GOVERNED = [
    "firm_create_member",
    "firm_update_member",
    "firm_complete_unit",
    "firm_resolve_escalation",
    "firm_update_goal",
]

_test_conn: sqlite3.Connection | None = None


def _make_test_conn() -> sqlite3.Connection:
    assert _test_conn is not None, "Test connection not initialized"
    return _test_conn


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    global _test_conn
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    seed_chrisai(conn)
    _test_conn = conn
    monkeypatch.setattr(mcp_tools, "_conn_factory", _make_test_conn)
    # Default: no member identity in env (cleared per-test as needed).
    monkeypatch.delenv("CADRE_MEMBER_ID", raising=False)
    yield
    _test_conn = None


def _grant(member_id: str, tools):
    autonomy_svc.set_authority_grant(_test_conn, member_id, tools)


def _authority_error(payload: dict) -> bool:
    return "error" in payload and "Board-granted authority" in payload["error"]


# ---------------------------------------------------------------------------
# The gate primitive
# ---------------------------------------------------------------------------

class TestHasAuthority:

    def test_no_identity_allowed(self):
        ok, _ = authority_svc.has_authority(_test_conn, "", "firm_update_goal")
        assert ok

    def test_ungranted_member_denied(self):
        ok, reason = authority_svc.has_authority(_test_conn, "MEM-003", "firm_update_goal")
        assert not ok and "MEM-003" in reason

    def test_blanket_star_grant(self):
        _grant("MEM-001", "*")
        for tool in GOVERNED:
            ok, _ = authority_svc.has_authority(_test_conn, "MEM-001", tool)
            assert ok, tool

    def test_per_tool_grant_is_scoped(self):
        _grant("MEM-001", ["firm_update_goal"])
        assert authority_svc.has_authority(_test_conn, "MEM-001", "firm_update_goal")[0]
        assert not authority_svc.has_authority(_test_conn, "MEM-001", "firm_create_member")[0]

    def test_clear_grant_revokes(self):
        _grant("MEM-001", "*")
        _grant("MEM-001", [])
        assert not authority_svc.has_authority(_test_conn, "MEM-001", "firm_update_goal")[0]

    def test_grant_rejects_ungoverned_tool(self):
        with pytest.raises(ValueError):
            _grant("MEM-001", ["firm_list_members"])

    def test_grant_rejects_unknown_member(self):
        with pytest.raises(ValueError):
            _grant("MEM-NOPE", "*")


# ---------------------------------------------------------------------------
# Per-tool: unauthorized (member, no grant) is denied — the whole matrix row
# ---------------------------------------------------------------------------

class TestUnauthorizedDenied:

    @pytest.mark.parametrize("tool", GOVERNED)
    def test_member_without_grant_is_denied(self, tool, monkeypatch):
        monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-003")
        args = {
            "firm_create_member": lambda: mcp_tools.firm_create_member("X", "Role"),
            "firm_update_member": lambda: mcp_tools.firm_update_member("MEM-002", role="Hacked"),
            "firm_complete_unit": lambda: mcp_tools.firm_complete_unit("UNT-001", "MEM-003"),
            "firm_resolve_escalation": lambda: mcp_tools.firm_resolve_escalation("ESC-001"),
            "firm_update_goal": lambda: mcp_tools.firm_update_goal("GOAL-001", status="done"),
        }[tool]
        result = json.loads(args())
        assert _authority_error(result), f"{tool} should be authority-denied, got {result}"


# ---------------------------------------------------------------------------
# Per-tool: no-identity (Board/CLI) passes the gate; granted member passes
# ---------------------------------------------------------------------------

class TestGatePasses:

    def test_no_identity_creates_member(self):
        # env has no CADRE_MEMBER_ID (fixture default) → Board path
        result = json.loads(mcp_tools.firm_create_member("Nova", "Engineer"))
        assert "error" not in result and result.get("role") == "Engineer"

    def test_granted_member_creates_member(self, monkeypatch):
        _grant("MEM-001", ["firm_create_member"])
        monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-001")
        result = json.loads(mcp_tools.firm_create_member("Ada", "Engineer"))
        assert "error" not in result and result.get("role") == "Engineer"

    def test_granted_member_updates_member(self, monkeypatch):
        _grant("MEM-001", "*")
        monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-001")
        result = json.loads(mcp_tools.firm_update_member("MEM-002", role="VP Marketing"))
        assert "error" not in result and result.get("role") == "VP Marketing"

    @pytest.mark.parametrize("tool_call", [
        lambda: mcp_tools.firm_complete_unit("UNT-001", "MEM-001"),
        lambda: mcp_tools.firm_resolve_escalation("ESC-001"),
        lambda: mcp_tools.firm_update_goal("GOAL-001", status="active"),
    ])
    def test_granted_member_passes_gate_to_service(self, tool_call, monkeypatch):
        # A granted caller must get PAST the authority gate. The underlying
        # service may still error on entity state — we only assert the error,
        # if any, is NOT the authority error.
        _grant("MEM-001", "*")
        monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-001")
        result = json.loads(tool_call())
        assert not _authority_error(result)


# ---------------------------------------------------------------------------
# Cross-cutting guarantees
# ---------------------------------------------------------------------------

class TestCrossCutting:

    def test_denied_update_does_not_mutate(self, monkeypatch):
        before = json.loads(mcp_tools.firm_view_member("MEM-002"))["role"]
        monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-003")
        json.loads(mcp_tools.firm_update_member("MEM-002", role="Hacked"))
        after = json.loads(mcp_tools.firm_view_member("MEM-002"))["role"]
        assert after == before == "Chief Marketing Officer"

    def test_denied_create_adds_no_member(self, monkeypatch):
        before = len(json.loads(mcp_tools.firm_list_members()))
        monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-003")
        json.loads(mcp_tools.firm_create_member("Ghost", "Intruder"))
        after = len(json.loads(mcp_tools.firm_list_members()))
        assert after == before

    def test_authority_grant_never_leaks_to_member_surface(self):
        _grant("MEM-001", "*")
        result = json.loads(mcp_tools.firm_view_member("MEM-001"))
        assert "autonomy" not in result
        assert "authority" not in json.dumps(result)

    def test_denial_returns_error_never_raises(self, monkeypatch):
        monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-003")
        # Must not raise; must be a well-formed error dict.
        result = json.loads(mcp_tools.firm_resolve_escalation("ESC-001"))
        assert isinstance(result, dict) and "error" in result
