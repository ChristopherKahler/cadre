"""Tests for firm.services.authority — the self-govern identity gate.

Covers the fork's Definition of Done: identified callers are denied the gated
tools without the key and allowed with it, no-identity callers always pass,
gate decisions stay Board-only even WITH the key, grant/revoke is board-only
and idempotent and audited, and grant/revoke never reaches the MCP surface.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find
from firm.services.authority import (
    AUTHORITY_CAPABILITY,
    AuthorityError,
    caller_member_id,
    grant_authority,
    has_authority,
    require_authority,
    require_board_only,
    revoke_authority,
    sovereign_capabilities,
    system_context,
)
from firm.services.escalation import raise_escalation, resolve_escalation
from firm.services.gate import approve_gate, reject_gate, request_gate
from firm.services.goal import create_goal, update_goal, update_goal_metric
from firm.services.member import create_member, update_member
from firm.services.operation import create_operation
from firm.services.project import create_project
from firm.services.unit import checkout_unit, complete_unit, create_unit


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


def _seed(conn: sqlite3.Connection) -> tuple[str, str, str]:
    """A GM member, a sibling member, and a unit the sibling claimed.

    Seeds through the services (as the other service tests do) so FK and
    NOT NULL defaults come from the real create paths.
    """
    gm = create_member(conn, "chrisai", {"name": "Sterling", "role": "GM"})
    sib = create_member(conn, "chrisai", {"name": "Quill", "role": "Writer"})
    op = create_operation(conn, "chrisai", {
        "name": "Ops", "owner_member_id": gm["id"],
    })
    project = create_project(conn, "chrisai", {
        "name": "Proj", "operation_id": op["id"], "due_date": "2026-12-31",
    })
    unit = create_unit(conn, "chrisai", {
        "name": "Work", "project_id": project["id"],
    })
    checkout_unit(conn, unit["id"], sib["id"])
    return gm["id"], sib["id"], unit["id"]


def _as_member(monkeypatch: pytest.MonkeyPatch, member_id: str | None) -> None:
    """Impersonate a spawned Member run (or the Board when None)."""
    if member_id is None:
        monkeypatch.delenv("CADRE_MEMBER_ID", raising=False)
    else:
        monkeypatch.setenv("CADRE_MEMBER_ID", member_id)


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------


def test_no_env_is_board(monkeypatch: pytest.MonkeyPatch) -> None:
    _as_member(monkeypatch, None)
    assert caller_member_id() is None


def test_env_identifies_member(monkeypatch: pytest.MonkeyPatch) -> None:
    _as_member(monkeypatch, "MEM-001")
    assert caller_member_id() == "MEM-001"


def test_blank_env_is_board(monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty var is "set" but names nobody — it must not identify a caller.
    monkeypatch.setenv("CADRE_MEMBER_ID", "   ")
    assert caller_member_id() is None


def test_system_context_masks_member_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _as_member(monkeypatch, "MEM-001")
    with system_context():
        assert caller_member_id() is None
    assert caller_member_id() == "MEM-001"  # restored on exit


# ---------------------------------------------------------------------------
# DoD: gated tools — no key denied, key allowed, no identity allowed
# ---------------------------------------------------------------------------


def _gated_calls(conn: sqlite3.Connection, sib: str, unit: str) -> dict:
    goal = create_goal(conn, "chrisai", {
        "target": "ship", "parent_entity_type": "member", "parent_entity_id": sib,
    })
    esc = raise_escalation(conn, "chrisai", {
        "raised_by_member_id": sib, "title": "blocked",
    })["escalation"]
    return {
        "member.create": lambda: create_member(
            conn, "chrisai", {"name": "New", "role": "Hire"}),
        "member.update": lambda: update_member(conn, sib, {"role": "Editor"}),
        "unit.complete": lambda: complete_unit(conn, "chrisai", unit, sib),
        "escalation.resolve": lambda: resolve_escalation(conn, esc["id"]),
        "goal.update": lambda: update_goal(conn, goal["id"], {"status": "achieved"}),
        "goal.update_metric": lambda: update_goal_metric(conn, goal["id"], current=6),
    }


def test_identified_member_without_key_denied_on_every_gated_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _fresh_conn()
    _gm, sib, unit = _seed(conn)
    calls = _gated_calls(conn, sib, unit)

    _as_member(monkeypatch, sib)
    for name, call in calls.items():
        with pytest.raises(AuthorityError) as exc:
            call()
        assert exc.value.payload["error"] == "authority_required", name
        assert exc.value.payload["hint"] == "escalate via firm_escalate", name


def test_identified_member_with_key_allowed_on_every_gated_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _fresh_conn()
    _gm, sib, unit = _seed(conn)
    calls = _gated_calls(conn, sib, unit)
    grant_authority(conn, sib, comment="trusted")  # Board grants (no identity)

    _as_member(monkeypatch, sib)
    for name, call in calls.items():
        call()  # must not raise


def test_no_identity_caller_allowed_on_every_gated_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _fresh_conn()
    _gm, sib, unit = _seed(conn)
    calls = _gated_calls(conn, sib, unit)

    _as_member(monkeypatch, None)
    for name, call in calls.items():
        call()  # Board / CLI / dashboard always passes


def test_unknown_member_id_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    # A caller naming a member that does not exist resolves to no key.
    conn = _fresh_conn()
    _gm, sib, _unit = _seed(conn)
    _as_member(monkeypatch, "MEM-999")
    with pytest.raises(AuthorityError):
        update_member(conn, sib, {"role": "Editor"})


# ---------------------------------------------------------------------------
# DoD: gates are human-only — denied even WITH the key
# ---------------------------------------------------------------------------


def test_gate_decisions_denied_to_member_even_with_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _fresh_conn()
    _gm, sib, unit = _seed(conn)
    grant_authority(conn, sib)

    gates = [
        request_gate(conn, "chrisai", {
            "requesting_member_id": sib, "action": "ship it",
            "target_entity_type": "unit", "target_entity_id": unit,
        })["id"]
        for _ in range(2)
    ]

    _as_member(monkeypatch, sib)
    assert has_authority(conn, sib)  # the key IS held...

    for fn, gate_id in ((approve_gate, gates[0]), (reject_gate, gates[1])):
        with pytest.raises(AuthorityError) as exc:
            fn(conn, gate_id)  # ...and still denied
        assert exc.value.payload["error"] == "board_only"

    # Neither gate was decided.
    for gate_id in gates:
        assert find(conn, "gate", id=gate_id)[0]["status"] == "pending"


def test_gate_decisions_allowed_for_board(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _fresh_conn()
    _gm, sib, unit = _seed(conn)
    gate = request_gate(conn, "chrisai", {
        "requesting_member_id": sib, "action": "ship it",
        "target_entity_type": "unit", "target_entity_id": unit,
    })
    _as_member(monkeypatch, None)
    assert approve_gate(conn, gate["id"])["status"] == "approved"


# ---------------------------------------------------------------------------
# DoD: grant / revoke — board-only, idempotent, audited, sovereign-shaped
# ---------------------------------------------------------------------------


def test_zero_grants_by_default() -> None:
    conn = _fresh_conn()
    gm, sib, _unit = _seed(conn)
    for m in (gm, sib):
        assert sovereign_capabilities(conn, m) == []
        assert has_authority(conn, m) is False


def test_grant_writes_sovereign_token_and_autonomy_json() -> None:
    conn = _fresh_conn()
    gm, _sib, _unit = _seed(conn)
    grant_authority(conn, gm, comment="runs the floor")

    assert has_authority(conn, gm) is True
    assert sovereign_capabilities(conn, gm) == [AUTHORITY_CAPABILITY]
    # Stored on the shared autonomy block, as the Calibration Ladder reads it.
    raw = conn.execute(
        "SELECT autonomy FROM member WHERE id = ?", (gm,),
    ).fetchone()[0]
    assert json.loads(raw) == {"sovereign": [AUTHORITY_CAPABILITY]}


def test_grant_and_revoke_are_idempotent() -> None:
    conn = _fresh_conn()
    gm, _sib, _unit = _seed(conn)

    grant_authority(conn, gm)
    grant_authority(conn, gm)  # no-op
    assert sovereign_capabilities(conn, gm) == [AUTHORITY_CAPABILITY]
    assert len(find(conn, "records", event_type="member.authority_granted")) == 1

    revoke_authority(conn, gm)
    revoke_authority(conn, gm)  # no-op
    assert has_authority(conn, gm) is False
    assert len(find(conn, "records", event_type="member.authority_revoked")) == 1


def test_grant_revoke_write_audit_rows_with_reason() -> None:
    conn = _fresh_conn()
    gm, _sib, _unit = _seed(conn)

    grant_authority(conn, gm, comment="promoted to GM")
    row = find(conn, "records", event_type="member.authority_granted")[0]
    assert row["target_entity_id"] == gm
    assert row["actor_type"] == "board"
    # repo hydrates records.details (a JSON column) on read.
    assert row["details"]["authority"] is True
    assert row["details"]["comment"] == "promoted to GM"

    revoke_authority(conn, gm, comment="rotated off")
    row = find(conn, "records", event_type="member.authority_revoked")[0]
    assert row["details"]["comment"] == "rotated off"

    # The shared autonomy write path records its own event too — the
    # Calibration Ladder's view of the same change.
    assert len(find(conn, "records", event_type="member.autonomy_updated")) == 2


def test_revoke_preserves_unrelated_sovereign_grants() -> None:
    # The authority verb owns exactly its own token; a Board grant of some
    # other capability must survive it.
    conn = _fresh_conn()
    gm, _sib, _unit = _seed(conn)
    from firm.services.autonomy import set_sovereign_override

    set_sovereign_override(conn, gm, ["spend", AUTHORITY_CAPABILITY])
    revoke_authority(conn, gm)
    assert sovereign_capabilities(conn, gm) == ["spend"]
    assert has_authority(conn, gm) is False


def test_blanket_sovereignty_implies_authority() -> None:
    conn = _fresh_conn()
    gm, _sib, _unit = _seed(conn)
    from firm.services.autonomy import set_sovereign_override

    set_sovereign_override(conn, gm, ["*"])
    assert has_authority(conn, gm) is True
    grant_authority(conn, gm)  # already holds it — idempotent no-op
    assert sovereign_capabilities(conn, gm) == ["*"]


def test_revoke_refuses_under_blanket_sovereignty() -> None:
    # Dropping the 'authority' token from ['*'] would leave authority in
    # force. Refusing beats reporting a revoke that did not happen.
    conn = _fresh_conn()
    gm, _sib, _unit = _seed(conn)
    from firm.services.autonomy import set_sovereign_override

    set_sovereign_override(conn, gm, ["*"])
    with pytest.raises(ValueError, match="blanket sovereignty"):
        revoke_authority(conn, gm)
    assert has_authority(conn, gm) is True  # unchanged, honestly reported


def test_member_cannot_grant_itself_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The hole one level up: closed for the CLI path too, not just MCP.
    conn = _fresh_conn()
    _gm, sib, _unit = _seed(conn)
    _as_member(monkeypatch, sib)
    with pytest.raises(AuthorityError) as exc:
        grant_authority(conn, sib)
    assert exc.value.payload["error"] == "board_only"
    assert has_authority(conn, sib) is False


def test_authority_holder_cannot_mint_authority_for_a_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _fresh_conn()
    gm, sib, _unit = _seed(conn)
    grant_authority(conn, gm)
    _as_member(monkeypatch, gm)
    with pytest.raises(AuthorityError):
        grant_authority(conn, sib)
    assert has_authority(conn, sib) is False


def test_grant_unknown_member_raises() -> None:
    conn = _fresh_conn()
    _seed(conn)
    with pytest.raises(ValueError):
        grant_authority(conn, "MEM-999")


def test_malformed_autonomy_grants_nothing() -> None:
    # The DB value is authoritative; a corrupt block must fail closed rather
    # than crash a run.
    conn = _fresh_conn()
    gm, _sib, _unit = _seed(conn)
    conn.execute("UPDATE member SET autonomy = ? WHERE id = ?", ("not json", gm))
    conn.commit()
    assert sovereign_capabilities(conn, gm) == []
    assert has_authority(conn, gm) is False


# ---------------------------------------------------------------------------
# DoD: grant/revoke is absent from the MCP tool surface
# ---------------------------------------------------------------------------


def test_grant_revoke_never_exposed_as_mcp_tools() -> None:
    from firm.mcp.tools import mcp

    names = [t.name for t in asyncio.run(mcp.list_tools())]
    assert names, "tool surface should not be empty"
    for name in names:
        assert "authority" not in name
        assert "grant" not in name
        assert "revoke" not in name
    # And the module exports no callable a tool could be wired to later
    # without someone noticing this test.
    import firm.mcp.tools as tools_mod

    assert not hasattr(tools_mod, "firm_grant_authority")
    assert not hasattr(tools_mod, "firm_revoke_authority")


def test_denied_tool_returns_structured_payload_not_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A denied run must get a code + next action it can branch on.
    import firm.mcp.tools as tools_mod

    conn = _fresh_conn()
    _gm, sib, unit = _seed(conn)
    monkeypatch.setattr(tools_mod, "_conn_factory", lambda: conn)
    _as_member(monkeypatch, sib)

    result = json.loads(tools_mod.firm_update_member(sib, role="Editor"))
    assert result == {
        "error": "authority_required",
        "hint": "escalate via firm_escalate",
        "action": "member.update",
    }

    gate = request_gate(conn, "chrisai", {
        "requesting_member_id": sib, "action": "ship",
        "target_entity_type": "unit", "target_entity_id": unit,
    })
    _as_member(monkeypatch, None)  # the Board grants; a member never can
    grant_authority(conn, sib)
    _as_member(monkeypatch, sib)
    result = json.loads(tools_mod.firm_approve_gate(gate["id"]))
    assert result["error"] == "board_only"  # key held, still refused


# ---------------------------------------------------------------------------
# require_* helpers
# ---------------------------------------------------------------------------


def test_require_authority_returns_caller_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _fresh_conn()
    gm, _sib, _unit = _seed(conn)
    grant_authority(conn, gm)
    _as_member(monkeypatch, gm)
    assert require_authority(conn, "x") == gm


def test_require_board_only_passes_without_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _as_member(monkeypatch, None)
    require_board_only("x", hint="h")  # must not raise
