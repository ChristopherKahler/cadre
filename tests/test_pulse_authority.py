"""The authority gate under a real pulse — the harness vs. the model.

Two things the unit tests cannot show on their own:

1. The runner is the completion authority (seam-4). It must complete a
   validated Unit even when the pulse process itself carries a Member
   identity, which happens the moment a Member (which has a shell) fires a
   pulse.
2. The denial is actionable: a Member told to "escalate via firm_escalate"
   must actually be able to escalate. If that path were gated too, the hint
   would be a dead end.
"""

from __future__ import annotations

import json
import sqlite3
from unittest import mock

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import find, get
from firm.pulse.runner import make_runner
from firm.pulse.spawn import SpawnResult, spawn_member_run
from firm.seed import seed_chrisai
from firm.services.authority import AuthorityError, grant_authority
from firm.services.escalation import raise_escalation
from firm.services.unit import complete_unit


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    return conn


def _mock_spawn_result(text="Done. AC-1 satisfied.", cost=0.08):
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "auth"}),
        json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]},
        }),
        json.dumps({
            "type": "result",
            "usage": {"input_tokens": 2000, "output_tokens": 1000,
                      "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0},
            "total_cost_usd": cost,
            "stop_reason": "end_turn",
            "is_error": False,
        }),
    ]
    return SpawnResult(
        returncode=0, stdout="\n".join(lines), stderr="", pid=1, timed_out=False,
    )


# ---------------------------------------------------------------------------
# The identity stamp
# ---------------------------------------------------------------------------


def test_unidentified_spawn_does_not_fabricate_an_identity() -> None:
    # An unidentified spawn must leave the stamp absent rather than invent one.
    # The positive case (member_id present) lives in test_pulse_spawn.py.
    mock_proc = mock.MagicMock()
    mock_proc.pid = 1
    mock_proc.communicate.return_value = ("", "")
    mock_proc.returncode = 0

    with (
        mock.patch(
            "firm.pulse.spawn.resolve_claude_bin",
            return_value=("/usr/bin/claude-test", "test"),
        ),
        mock.patch(
            "firm.pulse.spawn.subprocess.Popen", return_value=mock_proc,
        ) as mock_popen,
    ):
        spawn_member_run("prompt")

    env = mock_popen.call_args.kwargs["env"]
    assert "CADRE_MEMBER_ID" not in env
    # Still inherits the parent env — the child needs PATH etc.
    assert "PATH" in env


@mock.patch("firm.contracts.claude_code.spawn_member_run")
def test_spawn_receives_the_running_members_id(mock_spawn) -> None:
    mock_spawn.return_value = _mock_spawn_result()
    conn = _fresh_conn()
    seed_chrisai(conn)

    make_runner("chrisai", "/tmp")(conn, get(conn, "member", "MEM-001"))

    assert mock_spawn.call_args.kwargs["member_id"] == "MEM-001"


# ---------------------------------------------------------------------------
# The harness is the completion authority
# ---------------------------------------------------------------------------


@mock.patch("firm.contracts.claude_code.spawn_member_run")
def test_runner_completes_unit_for_member_without_the_key(mock_spawn) -> None:
    # The member holds NO key, yet its validated run still completes: the
    # harness completes it, not the model. Gating complete_unit must not
    # break the normal pulse.
    mock_spawn.return_value = _mock_spawn_result()
    conn = _fresh_conn()
    seed_chrisai(conn)

    result = make_runner("chrisai", "/tmp")(conn, get(conn, "member", "MEM-001"))

    assert result["status"] == "completed"
    assert result["validation_passed"] is True


@mock.patch("firm.contracts.claude_code.spawn_member_run")
def test_runner_completes_even_when_pulse_inherits_a_member_identity(
    mock_spawn, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A Member has a shell and can fire `firm pulse`; that pulse process then
    # carries CADRE_MEMBER_ID. Without system_context() the harness would
    # deny its own completion and every future pulse would re-dispatch the
    # same finished work.
    mock_spawn.return_value = _mock_spawn_result()
    conn = _fresh_conn()
    seed_chrisai(conn)
    monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-002")  # a *different* member

    result = make_runner("chrisai", "/tmp")(conn, get(conn, "member", "MEM-001"))

    assert result["status"] == "completed"
    assert result["validation_passed"] is True


# ---------------------------------------------------------------------------
# GM with the key drives; sibling without it is denied and escalates
# ---------------------------------------------------------------------------


def _two_members(conn: sqlite3.Connection) -> tuple[str, str, str]:
    seed_chrisai(conn)
    units = find(conn, "unit", firm_id="chrisai")
    return "MEM-001", "MEM-002", units[0]["id"]


def test_gm_with_key_drives_complete_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _fresh_conn()
    gm, _sib, unit = _two_members(conn)
    grant_authority(conn, gm, comment="GM runs the floor")

    monkeypatch.setenv("CADRE_MEMBER_ID", gm)
    result = complete_unit(conn, "chrisai", unit, gm)

    assert result["ok"] is True
    assert get(conn, "unit", unit)["status"] == "done"


def test_sibling_without_key_is_denied_and_can_escalate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _fresh_conn()
    _gm, sib, unit = _two_members(conn)

    monkeypatch.setenv("CADRE_MEMBER_ID", sib)
    with pytest.raises(AuthorityError) as exc:
        complete_unit(conn, "chrisai", unit, sib)

    assert exc.value.payload["hint"] == "escalate via firm_escalate"
    assert get(conn, "unit", unit)["status"] != "done"  # nothing happened

    # The hint must be honest: escalating is NOT gated, so the denied member
    # has a real next move rather than a dead end.
    raised = raise_escalation(conn, "chrisai", {
        "raised_by_member_id": sib,
        "title": "Cannot complete UNIT — no authority",
    })["escalation"]
    assert raised["status"] == "open"
    assert raised["raised_by_member_id"] == sib


def test_denied_member_can_still_request_a_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The other escape hatch named in the docs — also ungated for members.
    from firm.services.gate import request_gate

    conn = _fresh_conn()
    _gm, sib, unit = _two_members(conn)
    monkeypatch.setenv("CADRE_MEMBER_ID", sib)

    gate = request_gate(conn, "chrisai", {
        "requesting_member_id": sib, "action": "complete the unit",
        "target_entity_type": "unit", "target_entity_id": unit,
    })
    assert gate["status"] == "pending"


def test_read_tools_stay_open_to_members(monkeypatch: pytest.MonkeyPatch) -> None:
    # The gate covers writes. A Member that cannot read its own firm state is
    # useless; only the self-govern surface is gated.
    from firm.services.member import view_member
    from firm.services.unit import checkout_unit, release_unit, view_unit

    conn = _fresh_conn()
    _gm, sib, unit = _two_members(conn)
    monkeypatch.setenv("CADRE_MEMBER_ID", sib)

    assert view_member(conn, sib)["id"] == sib
    assert view_unit(conn, unit)["id"] == unit
    # Claiming and releasing work is how a Member operates — never gated.
    # (The seed leaves this unit claimed, so release first.)
    assert release_unit(conn, unit) is not None
    assert checkout_unit(conn, unit, sib) is not None
