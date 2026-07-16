"""Loadout trust posture — resolution, the boot command, and the Board's switches.

The control under test: LEAN spawns a Member with ``--strict-mcp-config`` (its
MCP surface is exactly the firm armory); FULL drops the flag and the Member
inherits the operator's entire connector fleet, write/send power included.

Discipline this suite follows (ENGINEERING §8): the flag assertions run the REAL
``spawn_member_run`` and read the REAL argv it hands Popen — an offline replica
of the resolver would only prove the test agrees with itself (ESC-021). And
every case is tested in BOTH directions: that FULL reaches full, and that
nothing else does. A posture control that can only be checked for "does full
work" passes at 100% while silently unbounding the whole firm.
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

from firm.core import repo
from firm.core.migrate import apply_migrations
from firm.dashboard import auth as board_auth
from firm.dashboard.server import assemble_state, floor_state, make_hub_handler, member_profile
from firm.pulse import spawn as spawn_mod
from firm.pulse.spawn import resolve_posture
from firm.services import posture as posture_svc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def cadre_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "cadre-home"
    monkeypatch.setenv("CADRE_HOME", str(home))
    monkeypatch.delenv("CADRE_MEMBER_ID", raising=False)
    return home


def _make_firm(root: Path, folder: str, firm_id: str) -> Path:
    ws = root / folder
    (ws / ".firm").mkdir(parents=True)
    conn = sqlite3.connect(ws / ".firm" / "firm.db")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    repo.create(conn, "firm", {"id": firm_id, "name": folder})
    repo.create(conn, "member", {
        "id": "MEM-001", "firm_id": firm_id, "name": "Alpha",
        "role": "Worker", "status": "active",
    })
    repo.create(conn, "member", {
        "id": "MEM-002", "firm_id": firm_id, "name": "Beta",
        "role": "Worker", "status": "active",
    })
    conn.commit()
    conn.close()
    return ws


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_migrations(c)
    repo.create(c, "firm", {"id": "alpha", "name": "Alpha Co"})
    repo.create(c, "member", {
        "id": "MEM-001", "firm_id": "alpha", "name": "Alpha",
        "role": "Worker", "status": "active",
    })
    repo.create(c, "member", {
        "id": "MEM-002", "firm_id": "alpha", "name": "Beta",
        "role": "Worker", "status": "active",
    })
    return c


def _legacy_full(ws: Path, full: bool = True) -> Path:
    """Write the pre-UI founding file the posture used to live in."""
    firm_dir = ws / ".firm"
    firm_dir.mkdir(parents=True, exist_ok=True)
    (firm_dir / "spawn.json").write_text(json.dumps({"full": full}))
    return ws


def _capture_argv(monkeypatch) -> dict:
    """Run the real spawn and capture the real boot command."""
    captured: dict = {}

    class FakePopen:
        def __init__(self, *args, **kwargs):
            captured["cmd"] = args[0] if args else kwargs.get("args")
            raise OSError("captured — never exec in tests")

    monkeypatch.setattr(spawn_mod, "resolve_claude_bin", lambda: ("/bin/echo", "test"))
    monkeypatch.setattr(spawn_mod.subprocess, "Popen", FakePopen)
    return captured


# ---------------------------------------------------------------------------
# Resolution — override → firm default → legacy file → lean
# ---------------------------------------------------------------------------

class TestResolvePosture:
    def test_nothing_set_is_lean(self, tmp_path):
        assert resolve_posture(member={}, firm={}, cwd=str(tmp_path)) == "lean"

    def test_lean_when_no_member_no_firm_no_cwd(self):
        assert resolve_posture() == "lean"

    def test_firm_default_applies_when_member_has_no_override(self, tmp_path):
        assert resolve_posture(
            member={"loadout_posture": None},
            firm={"loadout_posture": "full"},
            cwd=str(tmp_path),
        ) == "full"

    def test_member_override_beats_firm_default(self, tmp_path):
        assert resolve_posture(
            member={"loadout_posture": "full"},
            firm={"loadout_posture": "lean"},
            cwd=str(tmp_path),
        ) == "full"

    def test_member_lean_override_beats_full_firm(self, tmp_path):
        """The pin that matters: one member held back from a full-load firm."""
        assert resolve_posture(
            member={"loadout_posture": "lean"},
            firm={"loadout_posture": "full"},
            cwd=str(tmp_path),
        ) == "lean"

    def test_legacy_file_is_the_firm_default_when_db_says_nothing(self, tmp_path):
        _legacy_full(tmp_path)
        assert resolve_posture(member={}, firm={}, cwd=str(tmp_path)) == "full"

    def test_db_firm_posture_shadows_the_legacy_file(self, tmp_path):
        """Once the Board states a posture, the founding file stops deciding."""
        _legacy_full(tmp_path)
        assert resolve_posture(
            member={}, firm={"loadout_posture": "lean"}, cwd=str(tmp_path),
        ) == "lean"

    def test_member_override_beats_the_legacy_file(self, tmp_path):
        _legacy_full(tmp_path)
        assert resolve_posture(
            member={"loadout_posture": "lean"}, firm={}, cwd=str(tmp_path),
        ) == "lean"

    def test_legacy_file_saying_false_is_lean(self, tmp_path):
        _legacy_full(tmp_path, full=False)
        assert resolve_posture(member={}, firm={}, cwd=str(tmp_path)) == "lean"

    @pytest.mark.parametrize("junk", ["FULL", "Full", "yes", "true", 1, True, "", "strict"])
    def test_unrecognised_posture_inherits_rather_than_unbounding(self, junk, tmp_path):
        """Garbage never resolves to full — the dangerous path is affirmative only."""
        assert resolve_posture(
            member={"loadout_posture": junk}, firm={}, cwd=str(tmp_path),
        ) == "lean"
        assert resolve_posture(
            member={}, firm={"loadout_posture": junk}, cwd=str(tmp_path),
        ) == "lean"

    def test_malformed_legacy_file_is_lean(self, tmp_path):
        (tmp_path / ".firm").mkdir()
        (tmp_path / ".firm" / "spawn.json").write_text("{not json")
        assert resolve_posture(member={}, firm={}, cwd=str(tmp_path)) == "lean"


# ---------------------------------------------------------------------------
# The boot command — the only thing that actually enforces the posture
# ---------------------------------------------------------------------------

class TestStrictFlagOnTheRealArgv:
    def test_lean_keeps_strict_mcp_config(self, monkeypatch, tmp_path):
        captured = _capture_argv(monkeypatch)
        spawn_mod.spawn_member_run("work", cwd=str(tmp_path), posture="lean")
        assert "--strict-mcp-config" in captured["cmd"]

    def test_full_drops_strict_mcp_config(self, monkeypatch, tmp_path):
        captured = _capture_argv(monkeypatch)
        spawn_mod.spawn_member_run("work", cwd=str(tmp_path), posture="full")
        assert "--strict-mcp-config" not in captured["cmd"]

    def test_no_posture_and_no_file_keeps_strict(self, monkeypatch, tmp_path):
        captured = _capture_argv(monkeypatch)
        spawn_mod.spawn_member_run("work", cwd=str(tmp_path))
        assert "--strict-mcp-config" in captured["cmd"]

    def test_no_posture_honours_the_legacy_file(self, monkeypatch, tmp_path):
        """Back-compat: a full-load firm founded before the switch keeps working."""
        _legacy_full(tmp_path)
        captured = _capture_argv(monkeypatch)
        spawn_mod.spawn_member_run("work", cwd=str(tmp_path))
        assert "--strict-mcp-config" not in captured["cmd"]

    @pytest.mark.parametrize("junk", ["FULL", "yes", "", "strict"])
    def test_unrecognised_posture_keeps_strict(self, monkeypatch, tmp_path, junk):
        """The flag comes off on purpose or not at all."""
        captured = _capture_argv(monkeypatch)
        spawn_mod.spawn_member_run("work", cwd=str(tmp_path), posture=junk)
        assert "--strict-mcp-config" in captured["cmd"]

    def test_explicit_lean_overrides_a_legacy_full_file(self, monkeypatch, tmp_path):
        """A member pinned lean stays strict inside a legacy full-load firm."""
        _legacy_full(tmp_path)
        captured = _capture_argv(monkeypatch)
        spawn_mod.spawn_member_run("work", cwd=str(tmp_path), posture="lean")
        assert "--strict-mcp-config" in captured["cmd"]


class TestAdapterResolvesPerMember:
    """The adapter is the seam that turns a member row into a boot command."""

    def _invoke(self, conn, monkeypatch, tmp_path, member_id="MEM-001"):
        from firm.contracts.claude_code import ClaudeCodeRuntime

        captured = _capture_argv(monkeypatch)
        monkeypatch.setattr(
            "firm.contracts.claude_code.assemble_prompt",
            lambda *a, **k: "prompt",
        )
        member = repo.get(conn, "member", member_id)
        ClaudeCodeRuntime().invoke(
            conn, {"pulse_config": None}, member, {"id": "UNIT-001", "model": None},
            cwd=str(tmp_path), run_id="RUN-001",
        )
        return captured["cmd"]

    def test_full_firm_default_unbounds_a_member_with_no_override(
        self, conn, monkeypatch, tmp_path,
    ):
        posture_svc.set_firm_posture(conn, "alpha", "full")
        assert "--strict-mcp-config" not in self._invoke(conn, monkeypatch, tmp_path)

    def test_member_lean_override_holds_inside_a_full_firm(
        self, conn, monkeypatch, tmp_path,
    ):
        posture_svc.set_firm_posture(conn, "alpha", "full")
        posture_svc.set_member_posture(conn, "MEM-001", "lean")
        assert "--strict-mcp-config" in self._invoke(conn, monkeypatch, tmp_path)
        # ...and its peer, with no override, still inherits the firm's full.
        assert "--strict-mcp-config" not in self._invoke(
            conn, monkeypatch, tmp_path, "MEM-002",
        )

    def test_member_full_override_inside_a_lean_firm(self, conn, monkeypatch, tmp_path):
        posture_svc.set_member_posture(conn, "MEM-001", "full")
        assert "--strict-mcp-config" not in self._invoke(conn, monkeypatch, tmp_path)
        assert "--strict-mcp-config" in self._invoke(conn, monkeypatch, tmp_path, "MEM-002")

    def test_default_firm_is_lean(self, conn, monkeypatch, tmp_path):
        assert "--strict-mcp-config" in self._invoke(conn, monkeypatch, tmp_path)


# ---------------------------------------------------------------------------
# Service layer — the only write path
# ---------------------------------------------------------------------------

class TestPostureService:
    def test_set_firm_posture_writes_and_records(self, conn):
        out = posture_svc.set_firm_posture(conn, "alpha", "full")
        assert out["posture"] == "full"
        assert repo.get(conn, "firm", "alpha")["loadout_posture"] == "full"
        events = [r for r in repo.find(conn, "records", firm_id="alpha")
                  if r["event_type"] == "firm.posture_updated"]
        assert len(events) == 1
        assert events[0]["details"] == {"from": None, "to": "full"}

    def test_set_member_posture_writes_and_records(self, conn):
        out = posture_svc.set_member_posture(conn, "MEM-001", "full")
        assert out["posture"] == "full"
        assert out["effective"] == "full"
        assert repo.get(conn, "member", "MEM-001")["loadout_posture"] == "full"
        events = [r for r in repo.find(conn, "records", firm_id="alpha")
                  if r["event_type"] == "member.posture_updated"]
        assert len(events) == 1

    def test_clearing_a_member_override_returns_to_the_firm_default(self, conn):
        posture_svc.set_firm_posture(conn, "alpha", "full")
        posture_svc.set_member_posture(conn, "MEM-001", "lean")
        assert posture_svc.effective_for_member(conn, "alpha", "MEM-001")["posture"] == "lean"
        out = posture_svc.set_member_posture(conn, "MEM-001", None)
        assert out["posture"] is None
        assert out["effective"] == "full"   # inheriting a full firm IS an escalation
        assert repo.get(conn, "member", "MEM-001")["loadout_posture"] is None

    @pytest.mark.parametrize("junk", ["FULL", "yes", "", "strict", 1])
    def test_invalid_posture_is_refused_not_coerced(self, conn, junk):
        with pytest.raises(ValueError):
            posture_svc.set_firm_posture(conn, "alpha", junk)
        with pytest.raises(ValueError):
            posture_svc.set_member_posture(conn, "MEM-001", junk)
        assert repo.get(conn, "firm", "alpha")["loadout_posture"] is None

    def test_firm_tier_refuses_none(self, conn):
        """There is no tier above the firm — None must not quietly mean lean."""
        with pytest.raises(ValueError):
            posture_svc.set_firm_posture(conn, "alpha", None)

    def test_unknown_entities_raise(self, conn):
        with pytest.raises(ValueError):
            posture_svc.set_firm_posture(conn, "nope", "lean")
        with pytest.raises(ValueError):
            posture_svc.set_member_posture(conn, "MEM-404", "lean")

    def test_effective_reports_its_source(self, conn):
        assert posture_svc.effective_for_member(conn, "alpha", "MEM-001")["source"] == "default"
        posture_svc.set_firm_posture(conn, "alpha", "full")
        eff = posture_svc.effective_for_member(conn, "alpha", "MEM-001")
        assert (eff["posture"], eff["source"]) == ("full", "firm")
        posture_svc.set_member_posture(conn, "MEM-001", "lean")
        eff = posture_svc.effective_for_member(conn, "alpha", "MEM-001")
        assert (eff["posture"], eff["source"], eff["firm_default"]) == ("lean", "member", "full")

    def test_legacy_file_is_named_as_the_source(self, conn, tmp_path):
        """An unreviewed founding file must not read as a Board decision."""
        _legacy_full(tmp_path)
        default = posture_svc.firm_default(conn, "alpha", tmp_path)
        assert default == {"posture": "full", "source": "legacy_file", "stated": None}
        eff = posture_svc.effective_for_member(conn, "alpha", "MEM-001", tmp_path)
        assert (eff["posture"], eff["source"]) == ("full", "legacy_file")

    def test_roster_postures_resolves_every_member(self, conn):
        posture_svc.set_firm_posture(conn, "alpha", "full")
        posture_svc.set_member_posture(conn, "MEM-002", "lean")
        out = posture_svc.roster_postures(conn, "alpha")
        assert out["MEM-001"]["posture"] == "full"
        assert out["MEM-001"]["override"] is None
        assert out["MEM-002"]["posture"] == "lean"
        assert out["MEM-002"]["override"] == "lean"


# ---------------------------------------------------------------------------
# Read surfaces — the Board must be shown the EFFECTIVE posture
# ---------------------------------------------------------------------------

class TestPayloadsShowEffectivePosture:
    def test_state_carries_the_firm_default_and_every_member(self, conn):
        posture_svc.set_firm_posture(conn, "alpha", "full")
        posture_svc.set_member_posture(conn, "MEM-002", "lean")
        block = assemble_state(conn, "alpha")["loadout_posture"]
        assert block["posture"] == "full"
        assert block["source"] == "firm"
        assert block["members"]["MEM-001"]["posture"] == "full"
        assert block["members"]["MEM-002"]["posture"] == "lean"

    def test_state_defaults_to_lean(self, conn):
        assert assemble_state(conn, "alpha")["loadout_posture"]["posture"] == "lean"

    def test_state_tells_the_truth_about_a_legacy_full_firm(self, tmp_path, conn):
        """The Settings page must never render Lean for a firm running Full.

        This is the ESC-021 shape: a control that reports green while the thing
        it describes is doing something else.
        """
        _legacy_full(tmp_path)
        block = assemble_state(conn, "alpha", tmp_path)["loadout_posture"]
        assert block["posture"] == "full"
        assert block["source"] == "legacy_file"

    def test_profile_carries_the_resolved_posture(self, tmp_path, conn):
        posture_svc.set_firm_posture(conn, "alpha", "full")
        prof = member_profile(conn, tmp_path, "MEM-001")
        assert prof["posture"]["posture"] == "full"
        assert prof["posture"]["override"] is None
        assert prof["posture"]["firm_default"] == "full"

    def test_floor_card_carries_the_posture_tag(self, tmp_path, conn):
        posture_svc.set_member_posture(conn, "MEM-001", "full")
        cards = {c["id"]: c for c in floor_state(conn, tmp_path, "alpha")["members"]}
        assert cards["MEM-001"]["posture"]["posture"] == "full"
        assert cards["MEM-002"]["posture"]["posture"] == "lean"

    def test_posture_never_reaches_the_member_prompt(self, tmp_path, conn):
        """Board-facing only (Invariant #5) — the member never sees its posture."""
        posture_svc.set_member_posture(conn, "MEM-001", "full")
        prof = member_profile(conn, tmp_path, "MEM-001")
        assert "loadout_posture" not in (prof["prompt_preview"] or "")
        assert "posture" not in (prof["prompt_preview"] or "").lower()


# ---------------------------------------------------------------------------
# HTTP boundary — every posture write is a Board action
# ---------------------------------------------------------------------------

@pytest.fixture()
def hub(tmp_path: Path, cadre_home: Path):
    root = tmp_path / "firms"
    root.mkdir()
    ws = _make_firm(root, "alpha-co", "alpha")
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


def _stored(ws: Path, table: str, id: str) -> str | None:
    conn = sqlite3.connect(ws / ".firm" / "firm.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        f"SELECT loadout_posture FROM {table} WHERE id = ?", (id,),
    ).fetchone()
    conn.close()
    return row["loadout_posture"] if row else None


class TestPostureHttpBoundary:
    def test_firm_posture_without_token_401s_and_writes_nothing(self, hub):
        url, ws = hub
        status, _ = _request(f"{url}/f/alpha/api/action/firm-posture/default",
                             "POST", {"posture": "full"})
        assert status == 401
        assert _stored(ws, "firm", "alpha") is None

    def test_member_posture_without_token_401s_and_writes_nothing(self, hub):
        url, ws = hub
        status, _ = _request(f"{url}/f/alpha/api/action/member-posture/MEM-001",
                             "POST", {"posture": "full"})
        assert status == 401
        assert _stored(ws, "member", "MEM-001") is None

    def test_firm_posture_with_token_writes(self, hub):
        url, ws = hub
        status, out = _request(f"{url}/f/alpha/api/action/firm-posture/default",
                               "POST", {"posture": "full"},
                               token=board_auth.board_token())
        assert status == 200, out
        assert _stored(ws, "firm", "alpha") == "full"

    def test_member_posture_with_token_writes_and_clears(self, hub):
        url, ws = hub
        token = board_auth.board_token()
        status, _ = _request(f"{url}/f/alpha/api/action/member-posture/MEM-001",
                             "POST", {"posture": "full"}, token=token)
        assert status == 200
        assert _stored(ws, "member", "MEM-001") == "full"
        status, _ = _request(f"{url}/f/alpha/api/action/member-posture/MEM-001",
                             "POST", {"posture": None}, token=token)
        assert status == 200
        assert _stored(ws, "member", "MEM-001") is None

    def test_missing_posture_key_is_refused_not_read_as_clear(self, hub):
        """A body that forgot the field must not silently re-inherit."""
        url, ws = hub
        token = board_auth.board_token()
        _request(f"{url}/f/alpha/api/action/member-posture/MEM-001",
                 "POST", {"posture": "lean"}, token=token)
        status, _ = _request(f"{url}/f/alpha/api/action/member-posture/MEM-001",
                             "POST", {}, token=token)
        assert status == 400
        assert _stored(ws, "member", "MEM-001") == "lean"

    def test_invalid_posture_over_http_is_refused(self, hub):
        url, ws = hub
        status, _ = _request(f"{url}/f/alpha/api/action/firm-posture/default",
                             "POST", {"posture": "FULL"},
                             token=board_auth.board_token())
        assert status == 400
        assert _stored(ws, "firm", "alpha") is None

    def test_state_read_stays_open_and_reports_posture(self, hub):
        url, _ = hub
        status, out = _request(f"{url}/f/alpha/api/state")
        assert status == 200
        assert out["loadout_posture"]["posture"] == "lean"
