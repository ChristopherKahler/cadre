"""`firm escalation raise` must resolve the Member the way `firm gate request` does.

Both verbs came out of the same MCP->CLI write-surface migration, and until
2026-09-10 they disagreed: `gate request` fell back to ``$CADRE_MEMBER_ID`` via
``caller_member_id()``, while `escalation raise` had ``required=True`` on
``--member`` and no fallback at all.

``spawn_member_run`` exports ``CADRE_MEMBER_ID`` into every Member run. So a
Member could ask for a Gate without knowing its own id, but had to hardcode
that id to raise an Escalation — on the exact surface the migration existed to
make usable. The end-to-end acceptance harness drove both verbs the way a
Member does and tripped on it.

Four arms, because "it works now" is not the thing that regresses:

  1. env only, no flag         -> resolves from the environment
  2. flag AND env, disagreeing -> the explicit flag wins
  3. neither                   -> a clean rc-1 error, and NO row written
  4. CONTROL on arm 3          -> the error must NAME BOTH routes

Arm 4 is the one that matters long-term. Arm 3 is satisfied by any failure at
all, including a vague one that sends the reader nowhere; arm 4 requires the
message to stay useful.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from firm.cli.escalation import run_escalation_raise


@pytest.fixture()
def firm_ws(tmp_path: Path) -> Path:
    """A minimal real on-disk firm: migrations applied, one firm, one member.

    ``run_escalation_raise`` opens its own connection through
    ``db_connection(workspace)``, so this has to be a real database on disk
    rather than the ``:memory:`` connection the service-level tests use.
    """
    from firm.core.db import connect, get_db_path
    from firm.core.migrate import apply_migrations
    from firm.core.repo import create

    ws = tmp_path / "ws"
    (ws / ".firm").mkdir(parents=True)
    conn = connect(get_db_path(ws))
    apply_migrations(conn)
    create(conn, "firm", {"id": "acme", "name": "Acme"})
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "acme", "name": "Pen",
        "role": "Writer", "status": "active",
    })
    create(conn, "member", {
        "id": "MEM-002", "firm_id": "acme", "name": "Edit",
        "role": "Editor", "status": "active",
    })
    conn.commit()
    conn.close()
    return ws


def _escalations(ws: Path) -> list[dict]:
    from firm.core.db import get_db_path
    c = sqlite3.connect(f"file:{get_db_path(ws)}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute("select * from escalation")]
    finally:
        c.close()


# Raising notifies the Board. That is correct behavior and irrelevant here, so
# it is stubbed rather than exercised — an un-stubbed notify would make these
# tests depend on a Slack/webhook config they have nothing to do with.
NOTIFY = "firm.services.escalation.notify.send_board_dm"
SENT = {"sent": True, "reason": "test"}


@mock.patch(NOTIFY, return_value=SENT)
def test_resolves_the_member_from_the_environment(
    _dm, firm_ws: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    """ARM 1 — a Member run has CADRE_MEMBER_ID set and passes no flag."""
    monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-001")
    rc = run_escalation_raise(firm_ws, title="env-resolved escalation")
    assert rc == 0, capsys.readouterr()
    rows = _escalations(firm_ws)
    assert len(rows) == 1, rows
    assert rows[0]["raised_by_member_id"] == "MEM-001"


@mock.patch(NOTIFY, return_value=SENT)
def test_an_explicit_flag_beats_the_environment(
    _dm, firm_ws: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    """ARM 2 — the flag is the override, so it must WIN, not merely work.

    Without this, an implementation that ignored the flag whenever the env var
    happened to be set would pass arm 1 and look correct.
    """
    monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-001")
    rc = run_escalation_raise(
        firm_ws, raised_by_member_id="MEM-002", title="flag-resolved escalation")
    assert rc == 0, capsys.readouterr()
    rows = _escalations(firm_ws)
    assert len(rows) == 1, rows
    assert rows[0]["raised_by_member_id"] == "MEM-002", (
        "the environment overrode an explicit --member; the flag is the "
        "override and must win"
    )


@mock.patch(NOTIFY, return_value=SENT)
def test_no_identity_anywhere_is_a_clean_failure(
    _dm, firm_ws: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    """ARM 3 — neither route. rc 1, structured JSON, and NO row written."""
    monkeypatch.delenv("CADRE_MEMBER_ID", raising=False)
    rc = run_escalation_raise(firm_ws, title="should not land")
    assert rc == 1
    assert _escalations(firm_ws) == [], (
        "an escalation was written with no identified actor; an Escalation "
        "records WHO is asking"
    )
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert "error" in payload


@mock.patch(NOTIFY, return_value=SENT)
def test_control_the_failure_message_names_both_routes(
    _dm, firm_ws: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    """ARM 4, the CONTROL on arm 3.

    Arm 3 is satisfied by any failure, including one that tells the reader
    nothing. This requires the message to name both ways to supply an
    identity, so a regression to a bare error fails here rather than quietly
    degrading the surface the migration existed to improve.
    """
    monkeypatch.delenv("CADRE_MEMBER_ID", raising=False)
    run_escalation_raise(firm_ws, title="should not land")
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    msg = payload["error"]
    for route in ("--member", "CADRE_MEMBER_ID"):
        assert route in msg, (
            f"the failure message does not mention {route!r}: {msg!r}. "
            "A reader with no identity needs to be told both ways to give one."
        )


def test_the_flag_is_not_argparse_required_any_more() -> None:
    """The CLI surface itself, not just the function underneath it.

    The function could resolve the environment perfectly while argparse still
    refuses to run without the flag — which is exactly the state this change
    fixes, and it is invisible from the function's own tests.
    """
    from firm.__main__ import _build_parser

    ns = _build_parser().parse_args(["escalation", "raise", "--title", "t"])
    assert ns.raised_by_member_id is None, (
        "parsing succeeded but --member did not default to None"
    )
