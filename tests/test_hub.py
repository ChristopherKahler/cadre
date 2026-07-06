"""Tests for the hub — one process serving every firm under a root."""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.dashboard.server import (
    assemble_state,
    discover_firms,
    hub_summary,
    make_hub_handler,
)


def _make_firm(root: Path, folder: str, firm_id: str, name: str) -> Path:
    """Create a firm workspace on disk with a seeded .firm/firm.db."""
    ws = root / folder
    (ws / ".firm").mkdir(parents=True)
    conn = sqlite3.connect(ws / ".firm" / "firm.db")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    create(conn, "firm", {"id": firm_id, "name": name})
    create(conn, "member", {
        "id": "MEM-001", "firm_id": firm_id, "name": "Alpha",
        "role": "Worker", "status": "active",
    })
    conn.commit()
    conn.close()
    return ws


@pytest.fixture()
def firms_root(tmp_path: Path) -> Path:
    _make_firm(tmp_path, "alpha-co", "alpha", "Alpha Co")
    _make_firm(tmp_path, "beta-co", "beta", "Beta Co")
    (tmp_path / "not-a-firm").mkdir()  # no .firm/firm.db — must be skipped
    return tmp_path


def test_discover_firms_ids_from_db_not_folder(firms_root: Path):
    firms = discover_firms(firms_root)
    assert sorted(firms) == ["alpha", "beta"]  # folder names differ deliberately
    assert firms["alpha"]["workspace"].name == "alpha-co"
    assert firms["alpha"]["name"] == "Alpha Co"


def test_discover_firms_missing_root(tmp_path: Path):
    assert discover_firms(tmp_path / "nope") == {}


def test_hub_summary_counts(firms_root: Path):
    db = firms_root / "alpha-co" / ".firm" / "firm.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    create(conn, "gate", {
        "id": "GATE-001", "firm_id": "alpha", "status": "pending",
        "requesting_member_id": "MEM-001", "action": "spend money",
        "target_entity_type": "member", "target_entity_id": "MEM-001",
    })
    create(conn, "escalation", {
        "id": "ESC-001", "firm_id": "alpha", "status": "open",
        "raised_by_member_id": "MEM-001", "title": "help",
        "dedupe_key": "alpha:help",
    })
    # Ancient running row — no contract → 300s default timeout, way past stale.
    create(conn, "member_run", {
        "id": "RUN-001", "firm_id": "alpha", "member_id": "MEM-001",
        "status": "running", "started_at": "2026-01-01T00:00:00+00:00",
    })
    conn.commit()
    conn.close()

    cards = {c["id"]: c for c in hub_summary(discover_firms(firms_root))["firms"]}
    assert cards["alpha"]["needs_you"] == 2
    assert cards["alpha"]["gates_pending"] == 1
    assert cards["alpha"]["escalations_open"] == 1
    assert cards["alpha"]["running"] == 1
    assert cards["alpha"]["stale_runs"] == 1
    assert cards["alpha"]["members_active"] == 1
    assert cards["beta"]["needs_you"] == 0
    assert cards["beta"]["running"] == 0


def test_assemble_state_stale_flag(firms_root: Path):
    db = firms_root / "alpha-co" / ".firm" / "firm.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    create(conn, "member_run", {
        "id": "RUN-001", "firm_id": "alpha", "member_id": "MEM-001",
        "status": "running", "started_at": "2026-01-01T00:00:00+00:00",
    })
    from datetime import datetime, timezone
    create(conn, "member_run", {
        "id": "RUN-002", "firm_id": "alpha", "member_id": "MEM-001",
        "status": "running",
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
    })
    state = assemble_state(conn, "alpha")
    by_id = {r["id"]: r for r in state["runs"]}
    assert by_id["RUN-001"]["stale"] is True
    assert by_id["RUN-002"]["stale"] is False
    conn.close()


@pytest.fixture()
def hub_server(firms_root: Path):
    handler = make_hub_handler(firms_root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _get(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_hub_portfolio_and_api(hub_server: str):
    status, body = _get(hub_server + "/")
    assert status == 200
    assert b"Cadre" in body and b"Boardrooms" in body

    status, body = _get(hub_server + "/api/hub")
    assert status == 200
    payload = json.loads(body)
    assert sorted(f["id"] for f in payload["firms"]) == ["alpha", "beta"]


def test_hub_tenant_routing(hub_server: str):
    status, body = _get(hub_server + "/f/alpha/api/state")
    assert status == 200
    assert json.loads(body)["firm"]["name"] == "Alpha Co"

    status, body = _get(hub_server + "/f/beta/api/state")
    assert status == 200
    assert json.loads(body)["firm"]["name"] == "Beta Co"

    status, _ = _get(hub_server + "/f/ghost/api/state")
    assert status == 404


def test_hub_index_gets_base_stamp(hub_server: str):
    status, body = _get(hub_server + "/f/alpha/")
    assert status == 200
    assert b'data-base="/f/alpha"' in body

    # Standalone-style deep path still 404s cleanly rather than crashing.
    status, _ = _get(hub_server + "/f/alpha/api/nope")
    assert status == 404


def test_hub_redirects_bare_firm_path(hub_server: str):
    req = urllib.request.Request(hub_server + "/f/alpha")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(req)
        raise AssertionError("expected 302")
    except urllib.error.HTTPError as exc:
        assert exc.code == 302
        assert exc.headers["Location"] == "/f/alpha/"


def test_pulse_action_dispatches_detached(hub_server: str, monkeypatch):
    from firm.dashboard import server as srv

    calls: dict = {}

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    req = urllib.request.Request(
        hub_server + "/f/alpha/api/action/pulse/now",
        data=b"{}", method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        out = json.loads(resp.read())
    assert out["ok"] is True
    assert out["result"]["unit"].startswith("pulse-alpha-")
    cmd = calls["cmd"]
    assert cmd[0] == "systemd-run"          # detached — never in-request
    assert "--workspace" in cmd
    assert "--firm-id" in cmd and "alpha" in cmd


def test_commission_creates_unit_and_dispatches(hub_server: str, firms_root: Path, monkeypatch):
    from firm.dashboard import server as srv

    # A commission needs an active project to attach to.
    db = firms_root / "alpha-co" / ".firm" / "firm.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    create(conn, "operation", {
        "id": "OP-001", "firm_id": "alpha", "name": "Game", "status": "active",
    })
    create(conn, "project", {
        "id": "PRJ-001", "firm_id": "alpha", "operation_id": "OP-001",
        "name": "Season 1", "status": "in_progress", "due_date": "2026-12-31",
    })
    conn.commit()
    conn.close()

    calls: dict = {}

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr(srv.subprocess, "run",
                        lambda cmd, **kw: calls.setdefault("cmd", cmd) and FakeProc() or FakeProc())

    req = urllib.request.Request(
        hub_server + "/f/alpha/api/action/member-commission/MEM-001",
        data=json.dumps({"instructions": "Render portraits for every registered NPC"}).encode(),
        method="POST", headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        out = json.loads(resp.read())
    assert out["ok"] is True
    unit = out["result"]["unit"]
    assert unit["name"].startswith("Board commission:")
    assert unit["assignee_member_id"] == "MEM-001"

    # Dispatch was member-targeted.
    assert "--only" in calls["cmd"] and "MEM-001" in calls["cmd"]

    # The unit is claimed and the commission is on the record.
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT claimed_by FROM unit WHERE id = ?", (unit["id"],)).fetchone()
    assert row["claimed_by"] == "MEM-001"
    rec = conn.execute(
        "SELECT COUNT(*) FROM records WHERE firm_id='alpha' AND event_type='board.commission'",
    ).fetchone()[0]
    assert rec == 1
    conn.close()


def test_pulse_only_targets_single_member():
    from firm.pulse.orchestrator import pulse

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    create(conn, "firm", {"id": "f1", "name": "F1"})
    create(conn, "operation", {"id": "OP-001", "firm_id": "f1", "name": "O", "status": "active"})
    create(conn, "project", {
        "id": "PRJ-001", "firm_id": "f1", "operation_id": "OP-001",
        "name": "P", "status": "in_progress", "due_date": "2026-12-31",
    })
    for i, name in enumerate(["Ann", "Bob"], start=1):
        create(conn, "member", {
            "id": f"MEM-00{i}", "firm_id": "f1", "name": name,
            "role": "W", "status": "active",
        })
        create(conn, "unit", {
            "id": f"UNT-00{i}", "firm_id": "f1", "project_id": "PRJ-001",
            "name": f"work {i}", "status": "pending", "claimed_by": f"MEM-00{i}",
        })

    ran: list = []
    summary = pulse(conn, "f1",
                    lambda c, m: ran.append(m["id"]) or {"status": "completed"},
                    only_member_id="MEM-002")
    assert ran == ["MEM-002"]
    assert [r["member"]["id"] for r in summary.ran] == ["MEM-002"]
    reasons = {(s.get("member") or {}).get("id"): s["reason"] for s in summary.skipped}
    assert "not targeted" in reasons.get("MEM-001", "")


def test_contract_model_action(firms_root: Path):
    from firm.dashboard.server import perform_action

    db = firms_root / "alpha-co" / ".firm" / "firm.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    create(conn, "contract", {
        "id": "CON-001", "firm_id": "alpha", "name": "Alpha Contract",
        "runtime_type": "claude_code",
        "pulse_config": json.dumps({"timeout_sec": 600}),
    })

    out = perform_action(conn, "contract-model", "CON-001", {"model": "haiku"})
    assert out["model"] == "haiku"
    pc = json.loads(dict(conn.execute(
        "SELECT pulse_config FROM contract WHERE id='CON-001'").fetchone())["pulse_config"])
    assert pc == {"timeout_sec": 600, "model": "haiku"}  # timeout preserved

    out = perform_action(conn, "contract-model", "CON-001", {"model": ""})
    assert out["model"] is None
    pc = json.loads(dict(conn.execute(
        "SELECT pulse_config FROM contract WHERE id='CON-001'").fetchone())["pulse_config"])
    assert "model" not in pc

    state = assemble_state(conn, "alpha")
    assert any(c["id"] == "CON-001" and c["timeout_sec"] == 600
               for c in state["contract_settings"])
    conn.close()


def test_hub_discovers_new_firm_live(hub_server: str, firms_root: Path):
    _make_firm(firms_root, "gamma-co", "gamma", "Gamma Co")
    status, body = _get(hub_server + "/api/hub")
    assert status == 200
    assert "gamma" in [f["id"] for f in json.loads(body)["firms"]]
    status, _ = _get(hub_server + "/f/gamma/api/state")
    assert status == 200
