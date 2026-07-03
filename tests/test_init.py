"""Tests for firm.cli.init — the `firm init` command."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from firm.cli.init import run_init
from firm.core.db import get_db_path
from firm.core.migrate import _default_migrations_dir, discover_migrations


def _count_migrations(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    finally:
        conn.close()


def _expected_bundled_count() -> int:
    return len(discover_migrations(_default_migrations_dir()))


def test_fresh_init_creates_db_with_migrations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    exit_code = run_init(workspace)
    assert exit_code == 0

    db_path = get_db_path(workspace)
    assert db_path.is_file()
    assert _count_migrations(db_path) == _expected_bundled_count()

    captured = capsys.readouterr()
    assert "Initialized Cadre" in captured.out
    assert "001_init" in captured.out


def test_init_is_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    first = run_init(workspace)
    assert first == 0

    capsys.readouterr()  # clear captured output from first run

    second = run_init(workspace)
    assert second == 0

    captured = capsys.readouterr()
    assert "Already initialized" in captured.out

    db_path = get_db_path(workspace)
    assert _count_migrations(db_path) == _expected_bundled_count()  # still just one row


def test_init_missing_workspace_returns_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "does_not_exist"

    exit_code = run_init(missing)
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "does not exist" in captured.err


def test_init_creates_firm_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    run_init(workspace)

    assert (workspace / ".firm").is_dir()
    assert (workspace / ".firm" / "firm.db").is_file()


def test_init_force_bypasses_short_circuit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """force=True skips the 'Already initialized' path and re-runs migrations.

    Since the DB is already fully migrated, force is a no-op on content but
    does print the fresh-init success message instead of the short-circuit.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()

    run_init(workspace)
    capsys.readouterr()

    exit_code = run_init(workspace, force=True)
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "Already initialized" not in captured.out
    # --force on an already-migrated DB: no new "Initialized" banner, no pending migrations.
    assert "Initialized Cadre" not in captured.out
    assert "Applied migrations" not in captured.out

    db_path = get_db_path(workspace)
    assert _count_migrations(db_path) == _expected_bundled_count()
