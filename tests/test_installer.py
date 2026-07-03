"""Tests for the Cadre installer: demo seed, hook installation, end-to-end init."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from firm.cli.init import run_init
from firm.cli.install_hooks import HOOK_COMMAND, HOOK_SCRIPT_NAME, install_hooks
from firm.core import repo
from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations
from firm.seed_demo import seed_demo, summary_line


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    return conn


class TestSeedDemo:

    def test_seeds_expected_entities(self) -> None:
        conn = _fresh_conn()
        seed_demo(conn)
        assert repo.get(conn, "firm", "demo") is not None
        members = repo.find(conn, "member", firm_id="demo")
        ops = repo.find(conn, "operation", firm_id="demo")
        projects = repo.find(conn, "project", firm_id="demo")
        units = repo.find(conn, "unit", firm_id="demo")
        assert len(members) == 2
        assert len(ops) == 1
        assert len(projects) == 1
        assert len(units) == 1

    def test_writer_reports_to_editor(self) -> None:
        conn = _fresh_conn()
        seed_demo(conn)
        writer = repo.get(conn, "member", "MEM-001")
        assert writer is not None
        assert writer["reports_to_member_id"] == "MEM-002"

    def test_unit_intentionally_unclaimed(self) -> None:
        conn = _fresh_conn()
        seed_demo(conn)
        unit = repo.get(conn, "unit", "UNT-001")
        assert unit is not None
        assert unit["claimed_by"] is None

    def test_idempotent(self) -> None:
        conn = _fresh_conn()
        seed_demo(conn)
        first_ids = {m["id"] for m in repo.find(conn, "member", firm_id="demo")}
        seed_demo(conn)
        second_ids = {m["id"] for m in repo.find(conn, "member", firm_id="demo")}
        assert first_ids == second_ids

    def test_no_chrisai_coupling(self) -> None:
        conn = _fresh_conn()
        seed_demo(conn)
        assert repo.get(conn, "firm", "chrisai") is None

    def test_summary_line_format(self) -> None:
        conn = _fresh_conn()
        seed_demo(conn)
        line = summary_line(conn)
        assert "2 members" in line
        assert "1 unclaimed" in line


class TestInstallHooks:

    def test_creates_hook_script(self, tmp_path: Path) -> None:
        rc, msgs = install_hooks(tmp_path)
        assert rc == 0
        hook_path = tmp_path / ".claude" / "hooks" / HOOK_SCRIPT_NAME
        assert hook_path.exists()
        content = hook_path.read_text()
        assert content.startswith("#!/usr/bin/env python3")

    def test_registers_in_settings_json(self, tmp_path: Path) -> None:
        install_hooks(tmp_path)
        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        session_start = settings["hooks"]["SessionStart"]
        commands = []
        for entry in session_start:
            for h in entry.get("hooks", []):
                commands.append(h.get("command"))
        assert HOOK_COMMAND in commands

    def test_idempotent(self, tmp_path: Path) -> None:
        _, first = install_hooks(tmp_path)
        _, second = install_hooks(tmp_path)
        assert any("already" in m.lower() for m in second)
        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        commands = []
        for entry in settings["hooks"]["SessionStart"]:
            for h in entry.get("hooks", []):
                commands.append(h.get("command"))
        assert commands.count(HOOK_COMMAND) == 1

    def test_preserves_existing_settings(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        existing = {"permissions": {"allow": ["Bash"]}}
        (claude_dir / "settings.json").write_text(json.dumps(existing))
        install_hooks(tmp_path)
        settings = json.loads((claude_dir / "settings.json").read_text())
        assert settings["permissions"]["allow"] == ["Bash"]
        assert "hooks" in settings


class TestRunInitEnd2End:

    def test_init_with_demo_and_hooks(self, tmp_path: Path) -> None:
        rc = run_init(tmp_path, demo=True, install_hooks_flag=True)
        assert rc == 0
        assert (tmp_path / ".firm" / "firm.db").exists()
        assert (tmp_path / ".claude" / "hooks" / HOOK_SCRIPT_NAME).exists()
        conn = connect(get_db_path(tmp_path))
        try:
            assert repo.get(conn, "firm", "demo") is not None
            assert repo.get(conn, "unit", "UNT-001") is not None
        finally:
            conn.close()

    def test_init_plain_still_works(self, tmp_path: Path) -> None:
        rc = run_init(tmp_path)
        assert rc == 0
        assert (tmp_path / ".firm" / "firm.db").exists()
        # No demo firm should exist
        conn = connect(get_db_path(tmp_path))
        try:
            assert repo.get(conn, "firm", "demo") is None
        finally:
            conn.close()

    def test_init_rerun_is_idempotent(self, tmp_path: Path) -> None:
        run_init(tmp_path, demo=True, install_hooks_flag=True)
        rc = run_init(tmp_path, demo=True, install_hooks_flag=True)
        assert rc == 0
        conn = connect(get_db_path(tmp_path))
        try:
            members = repo.find(conn, "member", firm_id="demo")
            assert len(members) == 2  # not duplicated
        finally:
            conn.close()

    def test_missing_workspace_errors(self, tmp_path: Path) -> None:
        rc = run_init(tmp_path / "does-not-exist")
        assert rc == 1
