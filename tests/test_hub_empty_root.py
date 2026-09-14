"""The hub must start on a firms root that holds nothing yet.

Issue #113. A new operator could not found their first firm: founding is only
served by the hub, and the hub refused to start until a firm already existed.
``cadre init`` could not break the circle either, because it writes no firm row
without ``--demo`` and so produces a workspace ``discover_firms`` skips.

Nothing in the founding flow was broken. It had simply never been reachable,
which is why these arms drive the real HTTP surface rather than calling
``founding.commit`` directly — a test that called the function would have
passed on the day the defect was live.

Every arm here reads the port out of the payload rather than fixing one, so a
run can never talk to a server it did not start. Several arms come in pairs:
the refusal beside the benign case that proves the refusal discriminates.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import firm as firm_pkg
from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.dashboard.server import build_hub_server, root_state, scan_firms_root

from tests.platform_marks import skip_without_posix_permissions


def _make_firm(root: Path, folder: str, firm_id: str, name: str) -> Path:
    ws = root / folder
    (ws / ".firm").mkdir(parents=True)
    conn = sqlite3.connect(ws / ".firm" / "firm.db")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    create(conn, "firm", {"id": firm_id, "name": name})
    conn.commit()
    conn.close()
    return ws


def _make_half_born(root: Path, folder: str) -> Path:
    """A workspace with a database and no firm row — founding stopped early."""
    ws = root / folder
    (ws / ".firm").mkdir(parents=True)
    conn = sqlite3.connect(ws / ".firm" / "firm.db")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    conn.commit()
    conn.close()
    return ws


@pytest.fixture()
def hub_home(tmp_path: Path, monkeypatch):
    """Keep the minted board token inside the test's own home."""
    monkeypatch.setenv("CADRE_HOME", str(tmp_path / "cadre-home"))
    return tmp_path


class _Served:
    """A built hub, actually serving, torn down on exit."""

    def __init__(self, root: Path):
        self.server, self.payload = build_hub_server(root, port=0)
        self.thread = None
        if self.server is not None:
            self.thread = threading.Thread(
                target=self.server.serve_forever, daemon=True)
            self.thread.start()

    @property
    def url(self) -> str:
        return self.payload["url"]

    def get(self, path: str) -> tuple[int, bytes]:
        try:
            with urllib.request.urlopen(self.url + path) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def get_no_redirect(self, path: str) -> tuple[int, str | None]:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(self.url + path) as resp:
                return resp.status, resp.headers.get("Location")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Location")

    def post(self, path: str, body: dict) -> tuple[int, bytes]:
        from firm.dashboard import auth as board_auth

        req = urllib.request.Request(
            self.url + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     board_auth.HEADER: board_auth.board_token()})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def close(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()


# --------------------------------------------------------------------- E1-E4
def test_e1_empty_root_serves_the_founding_screen(hub_home: Path):
    """The socket opens on a root with nothing in it, and /next/ answers.

    Asserting a 200 carrying real founding text rather than merely that a port
    is listening: a hub that bound and served nothing would satisfy a port
    check and fail the operator, and that is the failure this arm has to be
    able to see.
    """
    root = hub_home / "firms"
    root.mkdir()
    hub = _Served(root)
    try:
        assert hub.server is not None
        assert hub.payload["ok"] is True
        assert hub.payload["firms"] == []
        status, body = hub.get("/next/")
        assert status == 200
        assert b"The building is empty." in body
        assert b"Found a firm" in body
    finally:
        hub.close()


def test_e2_cold_start_redirects_to_founding(hub_home: Path):
    root = hub_home / "firms"
    root.mkdir()
    hub = _Served(root)
    try:
        status, location = hub.get_no_redirect("/")
        assert status == 302
        assert location == "/next/"
    finally:
        hub.close()


def test_e2b_a_populated_root_still_gets_the_portfolio(hub_home: Path):
    """Control for E2. The redirect has to discriminate, or it is not a rule
    about empty roots — it is just a redirect."""
    root = hub_home / "firms"
    root.mkdir()
    _make_firm(root, "alpha-co", "alpha", "Alpha Co")
    hub = _Served(root)
    try:
        status, location = hub.get_no_redirect("/")
        assert status == 200
        assert location is None
    finally:
        hub.close()


def test_e3_founding_from_a_cold_start_produces_a_discoverable_firm(
        hub_home: Path):
    """The whole point of the lane, over the surface a person actually uses.

    Driven through HTTP on purpose. founding.commit already worked on the day
    the defect was live, so an arm that called it would have been green while
    a new operator was completely stuck.
    """
    root = hub_home / "firms"
    root.mkdir()
    hub = _Served(root)
    try:
        assert scan_firms_root(root)[0] == {}
        status, body = hub.post("/api/next/commit", {"proposal": {
            "firm_id": "acme",
            "name": "Acme",
            "premise": "sell anvils",
            "north_star": {"target": "100 anvils sold", "metric_value": 100,
                           "metric_unit": "anvils"},
            "operations": [{"name": "Sales", "purpose": "sell anvils"}],
            "members": [{"name": "Ann", "role": "Seller",
                         "operation": "Sales", "leads": True}],
        }})
        assert status == 200
        assert json.loads(body)["ok"] is True

        # Discoverable afterwards — not merely "the POST returned 200".
        assert list(scan_firms_root(root)[0]) == ["acme"]

        # And live in the running hub, with no restart.
        status, body = hub.get("/api/hub")
        assert status == 200
        assert [f["id"] for f in json.loads(body)["firms"]] == ["acme"]
        status, _ = hub.get("/f/acme/api/state")
        assert status == 200
    finally:
        hub.close()


def test_e4_payload_advertises_the_bound_port_and_the_founding_url(
        hub_home: Path):
    """With port 0 the kernel picks. A payload echoing the argument would say
    ":0", and any harness reading it would talk to whatever else was
    listening."""
    root = hub_home / "firms"
    root.mkdir()
    hub = _Served(root)
    try:
        bound = hub.server.server_address[1]
        assert bound != 0
        assert hub.payload["url"].endswith(f":{bound}")
        assert hub.payload["founding_url"] == f"{hub.payload['url']}/next/"
    finally:
        hub.close()


# --------------------------------------------------------------------- E5-E7
def test_e5_absent_root_with_an_existing_parent_is_created(hub_home: Path):
    """The default root is ~/firms, which does not exist on a machine that has
    never run Cadre. Refusing there would be the old lockout with a new
    reason string."""
    root = hub_home / "firms"
    assert not root.exists()
    assert root_state(root) == "absent"
    hub = _Served(root)
    try:
        assert hub.server is not None
        assert hub.payload["created"] is True
        assert root.is_dir()
        status, body = hub.get("/next/")
        assert status == 200
        assert b"The building is empty." in body
    finally:
        hub.close()


def test_e5b_an_existing_root_is_not_reported_as_created(hub_home: Path):
    """Control for E5. `created` has to mean something."""
    root = hub_home / "firms"
    root.mkdir()
    hub = _Served(root)
    try:
        assert hub.payload["created"] is False
    finally:
        hub.close()


def test_e6_absent_root_with_a_missing_parent_is_refused_and_creates_nothing(
        hub_home: Path):
    """A typo'd --firms-root must not silently build a directory tree.

    Asserting that the directory was NOT created and no socket was opened,
    rather than that a refusal message was printed: a guard is only proven by
    the case that would have performed the guarded action but for the guard.
    """
    root = hub_home / "no-such-parent" / "firms"
    assert root_state(root) == "unreachable"
    server, payload = build_hub_server(root, port=0)
    assert server is None
    assert payload["ok"] is False
    assert payload["reason"] == "firms-root-unreachable"
    assert not root.exists()
    assert not root.parent.exists()


@skip_without_posix_permissions
def test_e7_unreadable_root_is_refused_without_a_traceback(hub_home: Path):
    """It used to raise PermissionError straight out of discover_firms, so the
    operator got a stack trace instead of a sentence."""
    root = hub_home / "firms"
    root.mkdir()
    os.chmod(root, 0o000)
    try:
        assert root_state(root) == "unreadable"
        server, payload = build_hub_server(root, port=0)
        assert server is None
        assert payload["reason"] == "firms-root-unreadable"
        assert str(root) in payload["root"]
    finally:
        os.chmod(root, 0o755)


@skip_without_posix_permissions
def test_e7b_the_same_root_readable_starts_normally(hub_home: Path):
    """Control for E7. Same directory, only the mode differs."""
    root = hub_home / "firms"
    root.mkdir()
    os.chmod(root, 0o000)
    os.chmod(root, 0o755)
    hub = _Served(root)
    try:
        assert hub.server is not None
        assert hub.payload["ok"] is True
    finally:
        hub.close()


# -------------------------------------------------------------------- E8-E10
def test_e8_a_half_born_firm_is_reported_not_dropped(hub_home: Path):
    """A database with no firm row used to be skipped in silence, and the
    operator was then told no firms were found while the folder sat on disk in
    front of them."""
    root = hub_home / "firms"
    root.mkdir()
    _make_half_born(root, "halfborn")
    hub = _Served(root)
    try:
        assert hub.server is not None          # a broken firm is not a lockout
        assert hub.payload["firms"] == []
        assert len(hub.payload["skipped"]) == 1
        entry = hub.payload["skipped"][0]
        assert entry["reason"] == "no-firm-row"
        assert entry["path"] == str(root / "halfborn")
        assert entry["detail"]
    finally:
        hub.close()


def test_e8b_a_healthy_root_skips_nothing(hub_home: Path):
    """Control for E8, E9 and E10, and the blindness control for the mutation
    run: it must stay green while the lockout is reinstated, or the mutation
    is landing somewhere other than the clause under test."""
    root = hub_home / "firms"
    root.mkdir()
    _make_firm(root, "alpha-co", "alpha", "Alpha Co")
    hub = _Served(root)
    try:
        assert hub.payload["firms"] == ["alpha"]
        assert hub.payload["skipped"] == []
    finally:
        hub.close()


def test_e9_a_corrupt_database_is_reported_as_corrupt(hub_home: Path):
    root = hub_home / "firms"
    (root / "bad" / ".firm").mkdir(parents=True)
    (root / "bad" / ".firm" / "firm.db").write_bytes(b"this is not a database")
    firms, skipped = scan_firms_root(root)
    assert firms == {}
    assert [s["reason"] for s in skipped] == ["corrupt-db"]
    assert skipped[0]["path"] == str(root / "bad")


@skip_without_posix_permissions
def test_e10_an_unreadable_database_is_not_reported_as_corrupt(hub_home: Path):
    """Permission and corruption both arrive as sqlite3.Error, and telling an
    operator their database is corrupt when it is merely unreadable sends them
    to fix the wrong thing. The read-a-byte probe is what separates them."""
    root = hub_home / "firms"
    root.mkdir()
    ws = _make_firm(root, "locked", "locked", "Locked")
    db = ws / ".firm" / "firm.db"
    os.chmod(db, 0o000)
    try:
        firms, skipped = scan_firms_root(root)
        assert firms == {}
        assert [s["reason"] for s in skipped] == ["unreadable-db"]
    finally:
        os.chmod(db, 0o644)

    # Control: the same database, readable, is served.
    firms, skipped = scan_firms_root(root)
    assert list(firms) == ["locked"]
    assert skipped == []


# ------------------------------------------------------------------- E12a/b
def test_e12a_the_hub_api_reports_the_broken_firm_to_the_founding_screen(
        hub_home: Path):
    """Amendment A1. The founding screen must be able to say what is wrong,
    so the payload it reads has to carry it."""
    root = hub_home / "firms"
    root.mkdir()
    _make_half_born(root, "halfborn")
    hub = _Served(root)
    try:
        status, body = hub.get("/api/next/hub")
        assert status == 200
        payload = json.loads(body)
        assert payload["firms"] == []
        assert len(payload["skipped"]) == 1
        assert payload["skipped"][0]["reason"] == "no-firm-row"
        assert payload["skipped"][0]["path"] == str(root / "halfborn")
    finally:
        hub.close()


def test_e12b_the_founding_screen_branches_on_the_skipped_list():
    """SOURCE-LEVEL ARM, deliberately labelled as one.

    /next/ is a static file and the hollow screen is rendered by browser
    JavaScript, so no arm in this suite can assert what a person actually
    sees. This checks only that the shipped page has the branch and reads the
    field the API sends. What it cannot prove is that the branch renders
    correctly, and that half is a live-parked line owned by godwit: the
    Windows acceptance leg points a browser at a broken-firm-only root and
    records what the screen says.

    Kept rather than dropped because it does catch the specific regression of
    someone deleting the branch or renaming the field, which would silently
    restore "The building is empty." over a broken firm.
    """
    from firm.dashboard import server as srv

    page = srv._NEXT_HTML.read_text(encoding="utf-8")
    assert "S.skipped" in page
    assert "SKIP_REASONS" in page
    assert "no-firm-row" in page
    # The empty-building sentence must sit behind the branch, not before it.
    branch = page.index("const skipped = S.skipped")
    empty_line = page.index("The building is empty.", branch)
    assert branch < empty_line


# ----------------------------------------------------------------------- E13
def test_e13_the_startup_line_reaches_a_parent_that_pipes_it(tmp_path: Path):
    """The hub must print its startup payload where a parent can read it.

    This is the one arm that pins ``flush=True``, and it exists because the
    arm that appeared to pin it did not. Python block-buffers stdout when it
    is not a terminal, and ``run_hub`` then blocks in ``serve_forever``
    forever, so without the flush the startup line sits in a buffer that never
    drains and every supervisor, harness or CI step waits for a line the hub
    has already printed. The refusal path flushed by accident, because
    returning exits the process -- so the only observable path was the failure
    path, and the bound port and founding_url were unreachable by exactly the
    readers they were added for.

    PYTHONUNBUFFERED is explicitly REMOVED from the child's environment. An
    acceptance row once set it as a safety measure and thereby forced the very
    condition whose absence is the defect: it passed on a tree with the flush
    deleted. An instrument whose only failure direction is clean is worse than
    none.

    The timeout path distinguishes "the hub buffered its line" from "the hub
    never started" by asserting the child is still ALIVE when the read gives
    up. A dead child would fail this arm for an unrelated reason and send the
    next reader to the wrong place.
    """
    import subprocess
    import sys as _sys

    root = tmp_path / "firms"
    root.mkdir()

    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
    env["CADRE_HOME"] = str(tmp_path / "cadre-home")
    # The child must import the same firm this test did, whatever put it on
    # the path -- never a different checkout via an editable .pth.
    env["PYTHONPATH"] = str(Path(firm_pkg.__file__).resolve().parents[1])

    proc = subprocess.Popen(
        [_sys.executable, "-m", "firm", "hub",
         "--firms-root", str(root), "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)

    payload: dict = {}
    try:
        def _read() -> None:
            for raw in iter(proc.stdout.readline, b""):
                try:
                    line = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(line, dict) and (line.get("url")
                                               or line.get("reason")):
                    payload.update(line)
                    return

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(timeout=45)

        if not payload:
            still_running = proc.poll() is None
            assert still_running, (
                "the hub exited instead of serving, so this arm says nothing "
                "about buffering -- investigate the child, not the flush")
            raise AssertionError(
                "the hub bound its port and served, but printed no startup "
                "line a piped parent could read within 45s. That is the "
                "missing flush on run_hub's payload.")

        assert payload.get("ok") is True
        assert payload.get("url", "").startswith("http://127.0.0.1:")
        assert payload["founding_url"] == payload["url"] + "/next/"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        if proc.stdout is not None:
            proc.stdout.close()
