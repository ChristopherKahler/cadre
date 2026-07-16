"""Signal-quality loop — dashboard verdicts become member marks.

Under test, end to end:
  - a Board verdict writes ONE immutable signal.* record carrying the full
    outcome (attribution, informing comments, reconciliation);
  - the surfacing member (and a distinct presenter) get the feedback
    comment their next spawn renders as Standing Notes — verdict + note
    only, never the scoreboard;
  - the item's declared escalation ref resolves alongside (no zombies);
    a unit ref gets a comment, never an auto-close;
  - the same item cannot be resolved twice;
  - signal_marks derives per-member tallies the Floor consumes.
"""

from __future__ import annotations

import json
import sqlite3

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find, get
from firm.services import signal_quality as sq


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "cos", "name": "Chief of Staff"})
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "cos", "name": "Reeve",
        "role": "Chief of Staff", "status": "active",
    })
    create(conn, "member", {
        "id": "MEM-002", "firm_id": "cos", "name": "Dalton",
        "role": "Correspondence Lead", "status": "active",
    })
    return conn


def _item(**over) -> dict:
    base = {
        "id": "beant", "ask": "Answer Beant about the QuickStart date",
        "surfaced_by": "MEM-002", "presented_by": "MEM-001",
    }
    base.update(over)
    return base


def _member_comments(conn, member_id):
    return find(conn, "comment", parent_entity_type="member",
                parent_entity_id=member_id)


# ── the verdict ─────────────────────────────────────────────


def test_resolve_real_writes_record_and_informs_both():
    conn = _conn()
    out = sq.resolve_view_item(
        conn, "cos", "morning", _item(), "real", note="Handled in Slack.")
    assert out["ok"] is True

    rec = out["record"]
    assert rec["event_type"] == "signal.resolved"
    assert rec["target_entity_type"] == "member"
    assert rec["target_entity_id"] == "MEM-002"      # the finder gets the mark
    details = rec["details"]
    if isinstance(details, str):                     # repo may return it re-parsed
        details = json.loads(details)
    assert details["verdict"] == "real"
    assert details["surfaced_by"] == "MEM-002"
    assert details["presented_by"] == "MEM-001"
    assert details["note"] == "Handled in Slack."

    finder = _member_comments(conn, "MEM-002")
    assert len(finder) == 1 and "REAL SIGNAL" in finder[0]["body"]
    assert finder[0]["author_type"] == "board"
    presenter = _member_comments(conn, "MEM-001")
    assert len(presenter) == 1 and "assist" in presenter[0]["body"].lower()
    # The comment is the feedback channel — verdict and note, no scoreboard.
    for c in finder + presenter:
        assert "xp" not in c["body"].lower()
        assert "score" not in c["body"].lower()


def test_dismiss_noise_counter_marks_and_recalibrates():
    conn = _conn()
    out = sq.resolve_view_item(conn, "cos", "morning", _item(), "noise")
    assert out["record"]["event_type"] == "signal.dismissed"
    finder = _member_comments(conn, "MEM-002")
    assert len(finder) == 1 and "noise" in finder[0]["body"]
    assert "Recalibrate" in finder[0]["body"]


def test_same_finder_and_presenter_gets_one_comment():
    conn = _conn()
    sq.resolve_view_item(
        conn, "cos", "morning", _item(presented_by="MEM-002"), "real")
    assert len(_member_comments(conn, "MEM-002")) == 1


def test_second_verdict_conflicts():
    conn = _conn()
    sq.resolve_view_item(conn, "cos", "morning", _item(), "real")
    again = sq.resolve_view_item(conn, "cos", "morning", _item(), "noise")
    assert again["ok"] is False
    assert again["conflict"]["verdict"] == "real"
    # No double marks, no double comments.
    assert len(_member_comments(conn, "MEM-002")) == 1
    assert sq.signal_marks(conn, "cos")["MEM-002"] == {
        "real": 1, "noise": 0, "assists": 0}


def test_bad_verdict_and_missing_id_raise():
    conn = _conn()
    import pytest
    with pytest.raises(ValueError):
        sq.resolve_view_item(conn, "cos", "morning", _item(), "maybe")
    with pytest.raises(ValueError):
        sq.resolve_view_item(conn, "cos", "morning", {"ask": "x"}, "real")


# ── reconciliation — no zombies on either side ─────────────


def test_escalation_ref_resolves_alongside():
    conn = _conn()
    create(conn, "escalation", {
        "id": "ESC-001", "firm_id": "cos", "title": "Beant unanswered",
        "raised_by_member_id": "MEM-002", "severity": "high", "status": "open",
        "dedupe_key": "beant-unanswered",
    })
    out = sq.resolve_view_item(
        conn, "cos", "morning",
        _item(refs={"escalation": "ESC-001"}), "real", note="Replied.")
    assert out["reconciled"]["escalation"] == {"id": "ESC-001", "resolved": True}
    esc = get(conn, "escalation", "ESC-001")
    assert esc["status"] == "resolved"
    assert "morning dashboard" in (esc["resolution"] or "")


def test_unknown_escalation_ref_recorded_not_fatal():
    conn = _conn()
    out = sq.resolve_view_item(
        conn, "cos", "morning", _item(refs={"escalation": "ESC-404"}), "real")
    assert out["ok"] is True
    assert out["reconciled"]["escalation"]["error"] == "unknown escalation"


def test_unit_ref_commented_never_closed():
    conn = _conn()
    create(conn, "operation", {"id": "OP-001", "firm_id": "cos", "name": "Ops"})
    create(conn, "project", {
        "id": "PRJ-001", "firm_id": "cos", "operation_id": "OP-001",
        "name": "P", "due_date": "2026-12-31", "status": "in_progress",
    })
    create(conn, "unit", {
        "id": "UNIT-006", "firm_id": "cos", "project_id": "PRJ-001",
        "name": "Cross-surface pass", "status": "in_progress",
    })
    out = sq.resolve_view_item(
        conn, "cos", "morning", _item(refs={"unit": "UNIT-006"}), "real")
    assert out["reconciled"]["unit"] == {"id": "UNIT-006", "commented": True}
    assert get(conn, "unit", "UNIT-006")["status"] == "in_progress"
    unit_comments = find(conn, "comment", parent_entity_type="unit",
                         parent_entity_id="UNIT-006")
    assert len(unit_comments) == 1


# ── derivation — what the Floor consumes ───────────────────


def test_signal_marks_tallies_finder_and_assist():
    conn = _conn()
    sq.resolve_view_item(conn, "cos", "morning", _item(), "real")
    sq.resolve_view_item(
        conn, "cos", "morning", _item(id="drift", ask="Drift gap"), "noise")
    marks = sq.signal_marks(conn, "cos")
    assert marks["MEM-002"] == {"real": 1, "noise": 1, "assists": 0}
    assert marks["MEM-001"] == {"real": 0, "noise": 0, "assists": 1}


def test_view_resolutions_scopes_by_view():
    conn = _conn()
    sq.resolve_view_item(conn, "cos", "morning", _item(), "real")
    sq.resolve_view_item(
        conn, "cos", "signal", _item(id="utm-leak", surfaced_by=None,
                                     presented_by=None), "noise")
    morning = sq.view_resolutions(conn, "cos", "morning")
    assert [r["item_id"] for r in morning] == ["beant"]
    assert morning[0]["verdict"] == "real"
    assert [r["item_id"] for r in sq.view_resolutions(conn, "cos", "signal")] \
        == ["utm-leak"]


def test_item_without_attribution_still_resolves():
    conn = _conn()
    out = sq.resolve_view_item(
        conn, "cos", "signal",
        {"id": "orphan", "title": "Untagged leak"}, "real")
    assert out["ok"] is True
    assert out["record"]["target_entity_type"] == "firm"
    assert out["informed"] == []


def test_find_view_item_deep_walk():
    payload = {
        "as_of": "x",
        "needs_you": [{"id": "a"}, {"id": "beant", "ask": "hello"}],
        "nested": {"leaking": [{"id": "utm", "title": "UTM leak"}]},
    }
    assert sq.find_view_item(payload, "beant")["ask"] == "hello"
    assert sq.find_view_item(payload, "utm")["title"] == "UTM leak"
    assert sq.find_view_item(payload, "nope") is None


# ── the Floor consumes the marks ────────────────────────────


def test_floor_stats_carry_signal_quality():
    from firm.dashboard.server import floor_state
    from pathlib import Path
    conn = _conn()
    sq.resolve_view_item(conn, "cos", "morning", _item(), "real")
    state = floor_state(conn, Path("/nonexistent"), "cos")
    cards = {c["id"]: c for c in state["members"]}
    assert cards["MEM-002"]["stats"]["signals_real"] == 1
    assert cards["MEM-002"]["stats"]["signals_noise"] == 0
    assert cards["MEM-001"]["stats"]["signal_assists"] == 1
    # A real signal is an acted-on outcome — it levels (5 XP, honesty tier).
    assert cards["MEM-002"]["xp"] >= 5


# ── the HTTP boundary — token-gated action, open resolutions read ──


def _view_firm(root, monkeypatch):
    """A firm workspace with a morning view + attributed item on disk."""
    import threading
    from http.server import ThreadingHTTPServer
    from firm.dashboard.server import make_hub_handler

    ws = root / "cos-co"
    d = ws / ".firm"
    (d / "dashboard" / "views").mkdir(parents=True)
    (d / "dashboard" / "data").mkdir(parents=True)
    conn = sqlite3.connect(d / "firm.db")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    create(conn, "firm", {"id": "cos", "name": "Chief of Staff"})
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "cos", "name": "Reeve",
        "role": "CoS", "status": "active",
    })
    create(conn, "member", {
        "id": "MEM-002", "firm_id": "cos", "name": "Dalton",
        "role": "Correspondence", "status": "active",
    })
    conn.commit()
    conn.close()
    (d / "dashboard" / "views.json").write_text(json.dumps({"views": [{
        "id": "morning", "title": "Morning",
        "fragment": "dashboard/views/morning.html",
        "files": {"morning": "dashboard/data/morning.json"},
    }]}), encoding="utf-8")
    (d / "dashboard" / "views" / "morning.html").write_text(
        "<div id='cosMorningRoot'></div>", encoding="utf-8")
    (d / "dashboard" / "data" / "morning.json").write_text(json.dumps({
        "needs_you": [_item()],
    }), encoding="utf-8")

    monkeypatch.setenv("CADRE_HOME", str(root / "cadre-home"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_hub_handler(root))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _http(url, method="GET", body=None, token=False):
    import urllib.error
    import urllib.request
    from firm.dashboard import auth as board_auth

    headers = {"Content-Type": "application/json"}
    if token:
        headers[board_auth.HEADER] = board_auth.board_token()
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else {}


def test_http_resolve_requires_token_and_works_with_it(tmp_path, monkeypatch):
    server, base = _view_firm(tmp_path, monkeypatch)
    try:
        act = base + "/f/cos/api/action/view-item-resolve/morning"

        # must-block: no token → 401, nothing written
        status, out = _http(act, "POST", {"item_id": "beant", "verdict": "real"})
        assert status == 401 and out["error"] == "board_token_required"

        # must-allow: token → 200, ledger + resolutions live
        status, out = _http(act, "POST",
                            {"item_id": "beant", "verdict": "real",
                             "note": "Handled."}, token=True)
        assert status == 200 and out["ok"] is True

        status, out = _http(base + "/f/cos/api/views/morning/resolutions")
        assert status == 200
        assert [r["item_id"] for r in out["resolutions"]] == ["beant"]

        # conflict on the second verdict
        status, out = _http(act, "POST",
                            {"item_id": "beant", "verdict": "noise"}, token=True)
        assert status == 409 and out["ok"] is False

        # unknown item → 400
        status, out = _http(act, "POST",
                            {"item_id": "ghost", "verdict": "real"}, token=True)
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()
