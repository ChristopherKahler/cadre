"""Tests for the Floor's equip/unequip actions — the audited loadout writes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find, get
from firm.dashboard.server import equip_member, unequip_member


def _fresh(tmp_path) -> tuple[sqlite3.Connection, Path]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    create(conn, "contract", {
        "id": "CON-001", "firm_id": "chrisai", "name": "Standard",
        "runtime_type": "claude_code",
        "skill_loadout": json.dumps({"skills": ["voice-system"], "commands": [],
                                     "mcp": [], "cli": [], "knowledge": []}),
    })
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai", "name": "Sterling",
        "role": "CMO", "status": "active", "contract_id": "CON-001",
    })
    create(conn, "member", {
        "id": "MEM-002", "firm_id": "chrisai", "name": "Loose",
        "role": "No contract", "status": "active",
    })
    return conn, tmp_path


def _loadout(conn) -> dict:
    raw = get(conn, "contract", "CON-001")["skill_loadout"]
    return json.loads(raw) if isinstance(raw, str) else raw


def test_equip_skill_appends_and_logs(tmp_path):
    conn, ws = _fresh(tmp_path)
    out = equip_member(conn, ws, "chrisai", "MEM-001",
                       {"kind": "skills", "name": "humanizer"})
    assert out["name"] == "humanizer" and out["needs_keys"] == []
    assert _loadout(conn)["skills"] == ["voice-system", "humanizer"]
    recs = find(conn, "records", event_type="member.equipped")
    assert len(recs) == 1
    assert recs[0]["target_entity_id"] == "MEM-001"


def test_equip_rejects_duplicates_and_unknown_kind(tmp_path):
    conn, ws = _fresh(tmp_path)
    with pytest.raises(ValueError, match="already equipped"):
        equip_member(conn, ws, "chrisai", "MEM-001",
                     {"kind": "skills", "name": "voice-system"})
    with pytest.raises(ValueError, match="unknown equip kind"):
        equip_member(conn, ws, "chrisai", "MEM-001",
                     {"kind": "spells", "name": "fireball"})


def test_equip_without_contract_fails_loudly(tmp_path):
    conn, ws = _fresh(tmp_path)
    with pytest.raises(ValueError, match="no contract"):
        equip_member(conn, ws, "chrisai", "MEM-002",
                     {"kind": "skills", "name": "humanizer"})


def test_equip_cli_presence_checked_like_the_preflight(tmp_path):
    """Fork 014 alignment: uncataloged wrappers equip when resolvable on
    PATH; absence fails loudly and names what was searched."""
    conn, ws = _fresh(tmp_path)
    with mock.patch("firm.dashboard.server.shutil.which",
                    return_value="/home/x/.local/bin/gws-acct"):
        out = equip_member(conn, ws, "chrisai", "MEM-001",
                           {"kind": "cli", "name": "gws-acct"})
    assert out["name"] == "gws-acct"
    assert _loadout(conn)["cli"] == ["gws-acct"]
    with mock.patch("firm.dashboard.server.shutil.which", return_value=None):
        with pytest.raises(ValueError, match="PATH"):
            equip_member(conn, ws, "chrisai", "MEM-001",
                         {"kind": "cli", "name": "ghost-tool"})


def test_equip_command_stores_bare_name(tmp_path):
    conn, ws = _fresh(tmp_path)
    equip_member(conn, ws, "chrisai", "MEM-001",
                 {"kind": "commands", "name": "/social-engine:script"})
    assert _loadout(conn)["commands"] == ["social-engine:script"]
    # equipping the same command with or without the slash is a duplicate
    with pytest.raises(ValueError, match="already equipped"):
        equip_member(conn, ws, "chrisai", "MEM-001",
                     {"kind": "commands", "name": "social-engine:script"})


def test_equip_mcp_writes_firm_config_and_flags_keys(tmp_path):
    conn, ws = _fresh(tmp_path)
    spec = {"command": "npx", "args": ["skool-bridge"], "env": {"SKOOL_API_KEY": "${SKOOL_API_KEY}"}}
    with mock.patch("firm.dashboard.discovery.raw_specs", return_value={"skool": spec}):
        out = equip_member(conn, ws, "chrisai", "MEM-001",
                           {"kind": "mcp", "name": "skool"})
    assert out["needs_keys"] == ["SKOOL_API_KEY"]
    assert _loadout(conn)["mcp"] == ["skool"]
    written = json.loads((ws / ".mcp.json").read_text())
    assert written["mcpServers"]["skool"]["command"] == "npx"
    # the write went through sysconfig (backup + Records) and the equip logged
    assert len(find(conn, "records", event_type="member.equipped")) == 1


def test_equip_mcp_without_spec_fails(tmp_path):
    conn, ws = _fresh(tmp_path)
    with mock.patch("firm.dashboard.discovery.raw_specs", return_value={}):
        with pytest.raises(ValueError, match="no runnable spec"):
            equip_member(conn, ws, "chrisai", "MEM-001",
                         {"kind": "mcp", "name": "ghost-server"})


def test_equip_mcp_already_in_firm_config_needs_no_spec(tmp_path):
    conn, ws = _fresh(tmp_path)
    (ws / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"local-only": {"command": "python3"}}}))
    with mock.patch("firm.dashboard.discovery.raw_specs", return_value={}):
        out = equip_member(conn, ws, "chrisai", "MEM-001",
                           {"kind": "mcp", "name": "local-only"})
    assert out["name"] == "local-only"
    assert _loadout(conn)["mcp"] == ["local-only"]


def test_equip_and_unequip_knowledge_by_path_or_name(tmp_path):
    conn, ws = _fresh(tmp_path)
    equip_member(conn, ws, "chrisai", "MEM-001", {
        "kind": "knowledge", "path": "/home/x/docs/estate", "teaches": "the estate binder",
    })
    lo = _loadout(conn)
    assert lo["knowledge"] == [{"path": "/home/x/docs/estate", "teaches": "the estate binder"}]
    with pytest.raises(ValueError, match="already attached"):
        equip_member(conn, ws, "chrisai", "MEM-001",
                     {"kind": "knowledge", "path": "/home/x/docs/estate"})
    # the sheet unequips by the tome's display name (the basename)
    unequip_member(conn, "chrisai", "MEM-001", {"kind": "knowledge", "name": "estate"})
    assert _loadout(conn)["knowledge"] == []


def test_unequip_removes_and_logs_but_keeps_firm_mcp(tmp_path):
    conn, ws = _fresh(tmp_path)
    spec = {"command": "npx", "args": ["skool-bridge"]}
    with mock.patch("firm.dashboard.discovery.raw_specs", return_value={"skool": spec}):
        equip_member(conn, ws, "chrisai", "MEM-001", {"kind": "mcp", "name": "skool"})
    out = unequip_member(conn, "chrisai", "MEM-001", {"kind": "mcp", "name": "skool"})
    assert out["name"] == "skool"
    assert _loadout(conn)["mcp"] == []
    # firm-wide armory untouched — another member may share the server
    assert "skool" in json.loads((ws / ".mcp.json").read_text())["mcpServers"]
    assert len(find(conn, "records", event_type="member.unequipped")) == 1
    with pytest.raises(ValueError, match="not equipped"):
        unequip_member(conn, "chrisai", "MEM-001", {"kind": "mcp", "name": "skool"})
