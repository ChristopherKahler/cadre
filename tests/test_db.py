"""Tests for firm.core.db — SQLite connection helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from firm.core.db import connect, db_connection, get_db_path


def test_get_db_path_returns_expected_layout(tmp_path: Path) -> None:
    assert get_db_path(tmp_path) == tmp_path / ".firm" / "firm.db"


def test_connect_creates_parent_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "subdir" / "firm.db"
    conn = connect(db_path)
    try:
        assert db_path.parent.is_dir()
        assert db_path.is_file()
    finally:
        conn.close()


def test_connect_enables_foreign_keys(tmp_path: Path) -> None:
    conn = connect(tmp_path / "firm.db")
    try:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1
    finally:
        conn.close()


def test_connect_uses_row_factory(tmp_path: Path) -> None:
    conn = connect(tmp_path / "firm.db")
    try:
        conn.execute("CREATE TABLE t (a INTEGER, b TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'x')")
        conn.commit()
        row = conn.execute("SELECT a, b FROM t").fetchone()
        assert isinstance(row, sqlite3.Row)
        assert row["a"] == 1
        assert row["b"] == "x"
    finally:
        conn.close()


def test_db_connection_commits_on_clean_exit(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with db_connection(workspace) as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.execute("INSERT INTO t VALUES (42)")

    # Re-open and verify persisted
    with db_connection(workspace) as conn:
        row = conn.execute("SELECT a FROM t").fetchone()
        assert row[0] == 42


def test_db_connection_rolls_back_on_exception(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    # Seed a table so the insert would otherwise succeed
    with db_connection(workspace) as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")

    with pytest.raises(RuntimeError):
        with db_connection(workspace) as conn:
            conn.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("boom")

    # The insert should have been rolled back
    with db_connection(workspace) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM t").fetchone()
        assert rows[0] == 0


def test_bump_rev_counts_writes(tmp_path: Path) -> None:
    from firm.core.db import bump_rev, get_rev
    conn = connect(tmp_path / "firm.db")
    try:
        assert get_rev(conn) == 0
        bump_rev(conn)
        bump_rev(conn)
        conn.commit()
        assert get_rev(conn) == 2
    finally:
        conn.close()


def test_log_event_bumps_rev(tmp_path: Path) -> None:
    from firm.core.db import get_rev
    from firm.core.migrate import apply_migrations
    from firm.services._records import log_event
    conn = connect(tmp_path / "firm.db")
    try:
        apply_migrations(conn)
        conn.execute("INSERT INTO firm (id, name) VALUES ('f1', 'F1')")
        before = get_rev(conn)
        log_event(conn, firm_id="f1", event_type="unit.created",
                  actor={"type": "board", "id": None},
                  target_ref={"type": "unit", "id": "UNT-1"})
        conn.commit()
        assert get_rev(conn) == before + 1
    finally:
        conn.close()
