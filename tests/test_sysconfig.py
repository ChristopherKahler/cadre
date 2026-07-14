"""Tests for firm.sysconfig — platform detection, file surfaces, MCP editor,
variables, inventory."""

from __future__ import annotations

import json
import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find
from firm.sysconfig import service as svc
from firm.sysconfig.platforms import detect_platform


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("CADRE_HOME", str(tmp_path / "cadre-home"))
    ws = tmp_path / "firmws"
    (ws / ".firm").mkdir(parents=True)
    (ws / ".claude").mkdir()
    (ws / "CLAUDE.md").write_text("# Firm instructions\n")
    return ws


@pytest.fixture()
def conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "testco", "name": "TestCo"})
    return conn


def _records(conn, event_type):
    return [r for r in find(conn, "records", firm_id="testco")
            if r["event_type"] == event_type]


# ---- platform detection ----

def test_detects_claude_code(workspace):
    adapter = detect_platform(workspace)
    assert adapter is not None and adapter.id == "claude_code"


def test_no_platform_detected(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    assert detect_platform(bare) is None


def test_describe_shape(workspace):
    d = svc.describe(workspace)
    assert d["platform"] == "claude_code"
    keys = {s["key"] for s in d["surfaces"]}
    assert keys == {"claude-md", "settings", "mcp"}
    claude_md = next(s for s in d["surfaces"] if s["key"] == "claude-md")
    assert claude_md["exists"] is True


# ---- file surfaces ----

def test_read_write_file_with_backup_and_record(conn, workspace):
    out = svc.write_file(conn, "testco", workspace, "claude-md", "# v2\n")
    assert out["backup"] and out["backup"].startswith(".firm/backups/sysconfig/")
    assert (workspace / "CLAUDE.md").read_text() == "# v2\n"
    assert (workspace / out["backup"]).read_text() == "# Firm instructions\n"
    assert len(_records(conn, "sysconfig.file_updated")) == 1

    got = svc.read_file(workspace, "claude-md")
    assert got["content"] == "# v2\n" and got["kind"] == "markdown"


def test_write_json_surface_validates(conn, workspace):
    with pytest.raises(ValueError, match="not valid JSON"):
        svc.write_file(conn, "testco", workspace, "settings", "{nope")
    svc.write_file(conn, "testco", workspace, "settings", '{"model": "opus"}')
    assert json.loads((workspace / ".claude" / "settings.json").read_text()) == {"model": "opus"}


def test_unknown_surface_rejected(workspace):
    with pytest.raises(ValueError, match="unknown config surface"):
        svc.read_file(workspace, "../../etc/passwd")


# ---- MCP editor ----

def test_mcp_add_update_remove(conn, workspace):
    assert svc.mcp_list(workspace) == {"servers": []}
    svc.mcp_set(conn, "testco", workspace, "firm",
                {"command": "cadre", "args": ["env", "exec", "--", "python", "-m", "firm.mcp.server"],
                 "env": {"FIRM_ID": "testco"}})
    listed = svc.mcp_list(workspace)["servers"]
    assert listed[0]["name"] == "firm"
    assert listed[0]["env_keys"] == ["FIRM_ID"]

    out = svc.mcp_set(conn, "testco", workspace, "firm", {"command": "cadre"})
    assert out["op"] == "update"
    svc.mcp_remove(conn, "testco", workspace, "firm")
    assert svc.mcp_list(workspace) == {"servers": []}
    assert len(_records(conn, "sysconfig.mcp_updated")) == 3

    with pytest.raises(ValueError):
        svc.mcp_remove(conn, "testco", workspace, "firm")
    with pytest.raises(ValueError, match="invalid server name"):
        svc.mcp_set(conn, "testco", workspace, "bad name!", {"command": "x"})
    with pytest.raises(ValueError, match="command"):
        svc.mcp_set(conn, "testco", workspace, "nocmd", {})


# ---- variables ----

def test_vars_set_list_masked_reveal_delete(conn, workspace):
    svc.vars_set(conn, "testco", workspace, "API_TOKEN", "sk-abcdef123456", "firm")
    listing = svc.vars_list(workspace)
    row = listing["vars"][0]
    assert row["key"] == "API_TOKEN" and row["tier"] == "firm"
    assert "sk-abcdef" not in row["masked"] and row["masked"].endswith("3456")

    # Record written, value NOT in it.
    rec = _records(conn, "sysconfig.var_set")[0]
    assert "sk-abcdef" not in (rec["details"] or "")

    assert svc.vars_reveal(workspace, "API_TOKEN")["value"] == "sk-abcdef123456"
    svc.vars_delete(conn, "testco", workspace, "API_TOKEN", "firm")
    assert svc.vars_list(workspace)["vars"] == []


def test_vars_import_verifies_and_scrubs(conn, workspace):
    (workspace / ".env").write_text(
        'ELEVENLABS_API_KEY="el-123456789"\n# comment\nBAD LINE\nFOO=bar\n')
    out = svc.vars_import(conn, "testco", workspace, scrub=True)
    assert out["count"] == 2 and out["scrubbed"] is True
    assert not (workspace / ".env").exists()
    merged = {v["key"] for v in svc.vars_list(workspace)["vars"]}
    assert merged == {"ELEVENLABS_API_KEY", "FOO"}
    rec = _records(conn, "sysconfig.env_imported")[0]
    assert "el-123456789" not in (rec["details"] or "")


def test_vars_import_empty_env_rejected(conn, workspace):
    with pytest.raises(ValueError, match="no importable"):
        svc.vars_import(conn, "testco", workspace)


# ---- inventory ----

def test_inventory_skills_and_commands(workspace, monkeypatch):
    skills = workspace / ".claude" / "skills" / "humanizer"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: humanizer\ndescription: Remove AI tells from text\n---\n")
    cmds = workspace / ".claude" / "commands"
    cmds.mkdir()
    (cmds / "pulse.md").write_text("# pulse")
    monkeypatch.setattr(svc, "_base_ext_capable", lambda: False)

    inv = svc.inventory(workspace)
    assert inv["skills"] == [
        {"name": "humanizer", "description": "Remove AI tells from text"}]
    assert inv["commands"] == ["pulse"]
    assert inv["tools"] == [] and inv["tools_source"] is None


def test_base_ext_list_parses_table(monkeypatch):
    stdout = """NAME                 VERSION    HOOKS  DESCRIPTION
──────────────────────────────────────────────
cadre                0.1.0      U      Cadre — run AI agents like a company
nano-banana          0.1.0      none   Image generation via Gemini

2 extension(s) installed.

PLUGIN COMMAND   FROM             DESCRIPTION
──────────────────────────────────────────────
base cadre       ext:cadre        Cadre firm CLI

1 plugin command(s). Invoke: base <name> …
"""
    class FakeProc:
        returncode = 0
        stdout = ""
    FakeProc.stdout = stdout
    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: FakeProc())
    tools = svc._base_ext_list()
    assert [t["name"] for t in tools] == ["cadre", "nano-banana"]
    assert tools[0]["version"] == "0.1.0"


def test_tool_install_requires_base(conn, workspace, monkeypatch):
    monkeypatch.setattr(svc, "_base_ext_capable", lambda: False)
    with pytest.raises(ValueError, match="BASE CLI"):
        svc.tool_install(conn, "testco", workspace, "owner/repo")


def test_tool_install_rejects_non_manifest(conn, workspace, monkeypatch):
    monkeypatch.setattr(svc, "_base_ext_capable", lambda: True)
    with pytest.raises(ValueError, match="not a manifest"):
        svc.tool_install(conn, "testco", workspace, "owner/repo")
    with pytest.raises(ValueError, match="not a manifest"):
        svc.tool_install(conn, "testco", workspace, str(workspace / "nope.toml"))


def test_tool_install_verb_from_dist_block(conn, workspace, monkeypatch, tmp_path):
    monkeypatch.setattr(svc, "_base_ext_capable", lambda: True)
    calls = []

    class Ok:
        returncode = 0
        stdout = "installed"
        stderr = ""

    monkeypatch.setattr(svc.subprocess, "run", lambda cmd, **k: calls.append(cmd) or Ok())

    plain = tmp_path / "plain.toml"
    plain.write_text('[extension]\nname = "plain"\nversion = "0.1.0"\n')
    out = svc.tool_install(conn, "testco", workspace, str(plain))
    assert out["verb"] == "install" and calls[-1][:3] == ["base", "ext", "install"]

    dist = tmp_path / "dist.toml"
    dist.write_text(
        '[extension]\nname = "d"\nversion = "0.1.0"\n'
        '[dist]\nrepo = "o/r"\nversion = "0.1.0"\nbinary = "d"\n')
    out = svc.tool_install(conn, "testco", workspace, str(dist))
    assert out["verb"] == "add" and calls[-1][:3] == ["base", "ext", "add"]

    bad = tmp_path / "bad.toml"
    bad.write_text("[not closed")
    with pytest.raises(ValueError, match="not valid TOML"):
        svc.tool_install(conn, "testco", workspace, str(bad))


# ---- manifest file browser ----

def test_fs_browse_lists_dirs_and_toml_only(tmp_path):
    root = tmp_path / "jail"
    (root / "tools").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "tools" / "ext.toml").write_text("x")
    (root / "tools" / "readme.md").write_text("x")
    (root / "top.toml").write_text("x")

    out = svc.fs_browse(None, root=root)
    assert out["parent"] is None
    assert out["dirs"] == ["tools"]              # .git pruned
    assert [f["name"] for f in out["files"]] == ["top.toml"]

    sub = svc.fs_browse(str(root / "tools"), root=root)
    assert sub["parent"] == str(root)
    assert [f["name"] for f in sub["files"]] == ["ext.toml"]


def test_fs_browse_jail_enforced(tmp_path):
    root = tmp_path / "jail"
    root.mkdir()
    with pytest.raises(ValueError, match="outside"):
        svc.fs_browse(str(tmp_path), root=root)
    with pytest.raises(ValueError, match="outside"):
        svc.fs_browse(str(root / ".." / ".."), root=root)


def test_fs_browse_search_recursive(tmp_path):
    root = tmp_path / "jail"
    deep = root / "a" / "b"
    deep.mkdir(parents=True)
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "node_modules" / "x" / "nano.toml").write_text("x")   # pruned
    (deep / "nano-banana.toml").write_text("x")
    (deep / "other.toml").write_text("x")

    out = svc.fs_browse(str(root), q="nano", root=root)
    assert [f["name"] for f in out["files"]] == ["nano-banana.toml"]
    assert out["truncated"] is False
