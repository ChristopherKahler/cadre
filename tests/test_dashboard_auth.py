"""Dashboard auth gate (audit A3 / A4 / B6) — exercised over real HTTP.

Binds the per-firm and hub handlers on a loopback port and asserts the gate
matrix: a cross-origin or unauthenticated mutation is 403; a same-origin
(loopback Origin) or token-bearing request passes; the extension-install RCE
endpoint (A4) is gone; sensitive sysconfig reads (B6) are gated.
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
from firm.dashboard.server import (
    _ensure_dashboard_token,
    _origin_is_loopback,
    _request_authorized,
    make_handler,
    make_hub_handler,
)


def _seed_firm(root: Path, folder: str, firm_id: str) -> Path:
    ws = root / folder
    (ws / ".firm").mkdir(parents=True)
    conn = sqlite3.connect(ws / ".firm" / "firm.db")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    create(conn, "firm", {"id": firm_id, "name": firm_id.title()})
    create(conn, "member", {"id": "MEM-001", "firm_id": firm_id,
                            "name": "A", "role": "Worker", "status": "active"})
    conn.commit()
    conn.close()
    return ws


def _serve(handler) -> tuple[str, ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", server


def _post(url: str, headers: dict | None = None) -> int:
    req = urllib.request.Request(url, data=b"{}", method="POST",
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _get(url: str, headers: dict | None = None) -> int:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


# ---------------------------------------------------------------------------
# The primitive
# ---------------------------------------------------------------------------

class TestPrimitives:

    def test_origin_loopback_variants(self):
        assert _origin_is_loopback("http://127.0.0.1:8484")
        assert _origin_is_loopback("http://localhost")
        assert not _origin_is_loopback("https://evil.example.com")
        assert not _origin_is_loopback("")

    def test_ensure_token_is_0600_and_stable(self, tmp_path: Path):
        tp = tmp_path / ".firm" / "dashboard.token"
        t1 = _ensure_dashboard_token(tp)
        assert t1 and (tp.stat().st_mode & 0o777) == 0o600
        assert _ensure_dashboard_token(tp) == t1  # idempotent

    def test_request_authorized_logic(self, tmp_path: Path):
        tp = tmp_path / ".firm" / "dashboard.token"
        token = _ensure_dashboard_token(tp)

        class _H:
            def __init__(self, headers):
                self.headers = headers

        assert _request_authorized(_H({"Origin": "http://127.0.0.1:1"}), tp)
        assert not _request_authorized(_H({"Origin": "https://evil.com"}), tp)
        assert _request_authorized(_H({"X-Cadre-Token": token}), tp)
        assert not _request_authorized(_H({"X-Cadre-Token": "wrong"}), tp)
        assert not _request_authorized(_H({}), tp)  # no origin, no token


# ---------------------------------------------------------------------------
# Per-firm server, over HTTP
# ---------------------------------------------------------------------------

class TestFirmServerGate:

    @pytest.fixture()
    def srv(self, tmp_path: Path):
        ws = _seed_firm(tmp_path, "alpha-co", "alpha")
        url, server = _serve(make_handler(ws, "alpha"))
        token = (ws / ".firm" / "dashboard.token").read_text().strip()
        yield url, token
        server.shutdown()

    def test_mutation_without_auth_is_403(self, srv):
        url, _ = srv
        assert _post(url + "/api/action/gate-approve/GATE-001") == 403

    def test_mutation_cross_origin_is_403(self, srv):
        url, _ = srv
        assert _post(url + "/api/action/gate-approve/GATE-001",
                     {"Origin": "https://evil.example.com"}) == 403

    def test_mutation_same_origin_passes_gate(self, srv):
        url, _ = srv
        # 403 would mean auth-blocked; anything else means it reached the
        # handler (the entity may not exist → 400/404, which is fine here).
        assert _post(url + "/api/action/gate-approve/GATE-001",
                     {"Origin": "http://127.0.0.1"}) != 403

    def test_mutation_with_token_passes_gate(self, srv):
        url, token = srv
        assert _post(url + "/api/action/gate-approve/GATE-001",
                     {"X-Cadre-Token": token}) != 403

    def test_extension_install_rce_is_gone(self, srv):
        url, token = srv
        # Even authenticated, the endpoint is 410 Gone (A4 killed, not gated).
        assert _post(url + "/api/extensions/install",
                     {"X-Cadre-Token": token}) == 410

    def test_sysconfig_read_without_auth_is_403(self, srv):
        url, _ = srv
        assert _get(url + "/api/sysconfig/fs?path=/etc") == 403

    def test_sysconfig_read_same_origin_passes(self, srv):
        url, _ = srv
        assert _get(url + "/api/sysconfig",
                    {"Origin": "http://127.0.0.1"}) != 403


# ---------------------------------------------------------------------------
# Hub server, over HTTP
# ---------------------------------------------------------------------------

class TestHubServerGate:

    @pytest.fixture()
    def srv(self, tmp_path: Path):
        _seed_firm(tmp_path, "alpha-co", "alpha")
        url, server = _serve(make_hub_handler(tmp_path))
        token = (tmp_path / ".dashboard.token").read_text().strip()
        yield url, token
        server.shutdown()

    def test_hub_mutation_without_auth_is_403(self, srv):
        url, _ = srv
        assert _post(url + "/api/next/prefs") == 403

    def test_hub_mutation_cross_origin_is_403(self, srv):
        url, _ = srv
        assert _post(url + "/api/next/prefs",
                     {"Origin": "https://evil.example.com"}) == 403

    def test_hub_read_stays_open(self, srv):
        url, _ = srv
        assert _get(url + "/api/hub") == 200  # read-only GETs are not gated

    def test_hub_token_passes_gate(self, srv):
        url, token = srv
        assert _post(url + "/api/next/prefs", {"X-Cadre-Token": token}) != 403
