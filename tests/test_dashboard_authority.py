"""Dashboard authority toggle — the Board's grant surface in the Boardroom.

The toggle must route through the SAME service the CLI verb calls, and the
profile badge must read live off the member record rather than any cached
copy (honest-state rule).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find
from firm.dashboard.server import _INDEX_HTML, member_profile, perform_action
from firm.services.authority import has_authority


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai", "name": "Sterling",
        "role": "CMO", "status": "active",
    })
    return conn


def test_toggle_grants_through_the_same_service() -> None:
    conn = _fresh_conn()
    perform_action(conn, "member-authority", "MEM-001", {
        "grant": True, "comment": "board call",
    })
    assert has_authority(conn, "MEM-001") is True

    # Same audit trail the CLI verb produces — one code path, not two.
    rows = find(conn, "records", event_type="member.authority_granted")
    assert len(rows) == 1
    assert rows[0]["details"]["comment"] == "board call"


def test_toggle_revokes_through_the_same_service() -> None:
    conn = _fresh_conn()
    perform_action(conn, "member-authority", "MEM-001", {"grant": True})
    perform_action(conn, "member-authority", "MEM-001", {"grant": False})

    assert has_authority(conn, "MEM-001") is False
    assert len(find(conn, "records", event_type="member.authority_revoked")) == 1


def test_toggle_round_trips(tmp_path: Path) -> None:
    conn = _fresh_conn()
    assert member_profile(conn, tmp_path, "MEM-001")["authority"] is False

    perform_action(conn, "member-authority", "MEM-001", {"grant": True})
    profile = member_profile(conn, tmp_path, "MEM-001")
    assert profile["authority"] is True
    assert profile["calibration"]["sovereign"] == ["authority"]

    perform_action(conn, "member-authority", "MEM-001", {"grant": False})
    assert member_profile(conn, tmp_path, "MEM-001")["authority"] is False


def test_badge_reads_live_not_cached(tmp_path: Path) -> None:
    # A grant applied outside the dashboard (CLI, coboard) must show up on the
    # next profile open — the badge is derived, never stored.
    conn = _fresh_conn()
    from firm.services.authority import grant_authority

    grant_authority(conn, "MEM-001", comment="via CLI")
    assert member_profile(conn, tmp_path, "MEM-001")["authority"] is True


def test_profile_exposes_blanket_sovereignty(tmp_path: Path) -> None:
    conn = _fresh_conn()
    from firm.services.autonomy import set_sovereign_override

    set_sovereign_override(conn, "MEM-001", ["*"])
    profile = member_profile(conn, tmp_path, "MEM-001")
    # Authority is implied by '*', and the UI needs the raw list to explain
    # why the revoke button is disabled. The list rides in the Calibration
    # Ladder block — the sovereign override is that ladder's authored input.
    assert profile["authority"] is True
    assert profile["calibration"]["sovereign"] == ["*"]


def test_toggle_refuses_revoke_under_blanket_sovereignty() -> None:
    conn = _fresh_conn()
    from firm.services.autonomy import set_sovereign_override

    set_sovereign_override(conn, "MEM-001", ["*"])
    with pytest.raises(ValueError, match="blanket sovereignty"):
        perform_action(conn, "member-authority", "MEM-001", {"grant": False})
    assert has_authority(conn, "MEM-001") is True


def test_toggle_unknown_member_raises() -> None:
    conn = _fresh_conn()
    with pytest.raises(ValueError):
        perform_action(conn, "member-authority", "MEM-404", {"grant": True})


def test_manage_tab_renders_the_toggle() -> None:
    html = _INDEX_HTML.read_text()
    assert "setAuthority(" in html
    assert "member-authority/" in html
    assert "fAuthComment" in html


def test_denied_member_caller_cannot_drive_the_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The dashboard binds loopback, but a Member has a shell and could curl it.
    # The service-layer board-only check is what actually refuses — which is
    # why the gate lives there and not in the HTTP handler.
    from firm.services.authority import AuthorityError

    conn = _fresh_conn()
    monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-001")
    with pytest.raises(AuthorityError) as exc:
        perform_action(conn, "member-authority", "MEM-001", {"grant": True})
    assert exc.value.payload["error"] == "board_only"
    assert has_authority(conn, "MEM-001") is False
