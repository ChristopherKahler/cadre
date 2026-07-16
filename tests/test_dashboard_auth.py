"""HTTP boundary tests — every dashboard POST requires the board token.

The gate exists for one reason: Member runs share loopback with the
operator, and an open POST surface would let a member drive Board actions
over HTTP, bypassing the authority gate the MCP tools enforce. Reads stay
open; every mutation 401s without the operator's token.
"""

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
from firm.dashboard import auth as board_auth
from firm.dashboard.server import make_hub_handler


@pytest.fixture()
def cadre_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "cadre-home"
    monkeypatch.setenv("CADRE_HOME", str(home))
    # The server process must read as the Board — a leaked member identity
    # in the test env would (correctly) trip the authority gate instead.
    monkeypatch.delenv("CADRE_MEMBER_ID", raising=False)
    return home


def _make_firm(root: Path, folder: str, firm_id: str, name: str) -> Path:
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
def hub(tmp_path: Path, cadre_home: Path):
    root = tmp_path / "firms"
    root.mkdir()
    ws = _make_firm(root, "alpha-co", "alpha", "Alpha Co")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_hub_handler(root))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", ws
    server.shutdown()


def _request(url: str, method: str = "GET", body: dict | None = None,
             token: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers[board_auth.HEADER] = token
    req = urllib.request.Request(
        url, method=method, headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _member_sovereign(ws: Path) -> list:
    conn = sqlite3.connect(ws / ".firm" / "firm.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT autonomy FROM member WHERE id='MEM-001'").fetchone()
    conn.close()
    if not row or not row["autonomy"]:
        return []
    return (json.loads(row["autonomy"]) or {}).get("sovereign", [])


class TestBoardToken:
    def test_minted_once_with_owner_only_mode(self, cadre_home: Path):
        first = board_auth.board_token()
        assert board_auth.board_token() == first  # idempotent
        path = board_auth.board_token_path()
        assert path == cadre_home / "board.token"
        assert path.read_text().strip() == first
        assert (path.stat().st_mode & 0o777) == 0o600

    def test_rotation_needs_no_restart(self, cadre_home: Path):
        board_auth.board_token()
        board_auth.board_token_path().write_text("rotated-token\n")
        assert board_auth.board_token() == "rotated-token"


class TestFirmPostGate:
    def test_post_without_token_401_and_no_write(self, hub):
        url, ws = hub
        status, out = _request(f"{url}/f/alpha/api/action/member-authority/MEM-001",
                               "POST", {"grant": True})
        assert status == 401
        assert out["error"] == "board_token_required"
        assert "board.token" in out["hint"]
        assert _member_sovereign(ws) == []  # the denied call wrote nothing

    def test_post_with_wrong_token_401(self, hub):
        url, ws = hub
        board_auth.board_token()
        status, _ = _request(f"{url}/f/alpha/api/action/member-authority/MEM-001",
                             "POST", {"grant": True}, token="not-the-token")
        assert status == 401
        assert _member_sovereign(ws) == []

    def test_grant_round_trips_with_token(self, hub):
        """The DoD round-trip: HTTP toggle → same service → autonomy + audit."""
        url, ws = hub
        token = board_auth.board_token()
        status, _ = _request(f"{url}/f/alpha/api/action/member-authority/MEM-001",
                             "POST", {"grant": True, "comment": "GM drives the pulse"},
                             token=token)
        assert status == 200
        assert "authority" in _member_sovereign(ws)

        conn = sqlite3.connect(ws / ".firm" / "firm.db")
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT * FROM records WHERE event_type='member.authority_granted'"
        ).fetchall()
        conn.close()
        assert len(events) == 1
        details = json.loads(events[0]["details"])
        assert details["comment"] == "GM drives the pulse"

    def test_bearer_header_accepted(self, hub):
        url, ws = hub
        token = board_auth.board_token()
        req = urllib.request.Request(
            f"{url}/f/alpha/api/action/member-authority/MEM-001", method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
            data=json.dumps({"grant": True}).encode(),
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
        assert "authority" in _member_sovereign(ws)


class TestReadsStayOpen:
    def test_get_needs_no_token(self, hub):
        url, _ = hub
        status, out = _request(f"{url}/api/hub")
        assert status == 200
        assert [f["id"] for f in out["firms"]] == ["alpha"]
        status, out = _request(f"{url}/f/alpha/api/state")
        assert status == 200
        assert out["firm"]["name"] == "Alpha Co"


class TestHubVerbsGate:
    def test_prefs_gated(self, hub):
        url, _ = hub
        status, out = _request(f"{url}/api/next/prefs", "POST", {"notes": "x"})
        assert status == 401
        assert out["error"] == "board_token_required"
        status, out = _request(f"{url}/api/next/prefs", "POST", {"notes": "x"},
                               token=board_auth.board_token())
        assert status == 200
        assert out["ok"] is True


class TestSpawnNeverHandsTokenToMembers:
    def test_board_token_stripped_from_member_env(self, monkeypatch, tmp_path):
        from firm.pulse import spawn as spawn_mod

        captured: dict = {}

        class FakePopen:
            def __init__(self, *args, **kwargs):
                captured["env"] = kwargs.get("env")
                raise OSError("captured — never exec in tests")

        monkeypatch.setenv("CADRE_BOARD_TOKEN", "leaked-from-shell")
        monkeypatch.setattr(spawn_mod, "resolve_claude_bin",
                            lambda: ("/bin/echo", "test"))
        monkeypatch.setattr(spawn_mod.subprocess, "Popen", FakePopen)

        spawn_mod.spawn_member_run(
            "do the work", cwd=str(tmp_path),
            member_id="MEM-001", firm_id="alpha", run_id="RUN-001",
        )

        env = captured["env"]
        assert env is not None
        assert "CADRE_BOARD_TOKEN" not in env
        assert env["CADRE_MEMBER_ID"] == "MEM-001"
