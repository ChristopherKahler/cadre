"""Tests for firm.core.migrate — the migration runner."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from firm.core.migrate import (
    _split_sql,
    apply_migrations,
    applied_migration_names,
    discover_migrations,
    ensure_migrations_table,
)


def _fresh_conn() -> sqlite3.Connection:
    """In-memory SQLite connection with firm-standard settings."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def test_fresh_db_applies_all_bundled_migrations() -> None:
    """Fresh DB → applies every bundled migration in numeric order."""
    from firm.core.migrate import _default_migrations_dir, discover_migrations

    expected = [name for _num, name, _path in discover_migrations(_default_migrations_dir())]
    assert "001_init" in expected  # sanity: at least the bootstrap is present

    conn = _fresh_conn()
    try:
        applied = apply_migrations(conn)
        assert applied == expected
        names = applied_migration_names(conn)
        assert names == set(expected)
    finally:
        conn.close()


def test_rerunning_is_noop() -> None:
    """Second apply_migrations call returns an empty list; _migrations unchanged."""
    from firm.core.migrate import _default_migrations_dir, discover_migrations

    expected = {name for _num, name, _path in discover_migrations(_default_migrations_dir())}

    conn = _fresh_conn()
    try:
        apply_migrations(conn)
        second_run = apply_migrations(conn)
        assert second_run == []
        names = applied_migration_names(conn)
        assert names == expected
    finally:
        conn.close()


def test_discover_migrations_sorts_by_number(tmp_path: Path) -> None:
    """Migration files are returned in numeric order regardless of disk order."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "010_late.sql").write_text("SELECT 1;")
    (migrations_dir / "002_middle.sql").write_text("SELECT 1;")
    (migrations_dir / "003_also_middle.sql").write_text("SELECT 1;")
    # Files that should be ignored:
    (migrations_dir / "not_numbered.sql").write_text("SELECT 1;")
    (migrations_dir / "001_init.txt").write_text("SELECT 1;")

    entries = discover_migrations(migrations_dir)
    numbers = [num for num, _name, _path in entries]
    names = [name for _num, name, _path in entries]

    assert numbers == [2, 3, 10]
    assert names == ["002_middle", "003_also_middle", "010_late"]


def test_discover_migrations_returns_empty_when_dir_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    assert discover_migrations(missing) == []


def test_apply_migrations_respects_numeric_order(tmp_path: Path) -> None:
    """Custom migrations_dir runs in numeric order, records each name."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_create_a.sql").write_text(
        "CREATE TABLE a (id INTEGER PRIMARY KEY);"
    )
    (migrations_dir / "002_create_b.sql").write_text(
        "CREATE TABLE b (id INTEGER PRIMARY KEY, a_id INTEGER REFERENCES a(id));"
    )

    conn = _fresh_conn()
    try:
        applied = apply_migrations(conn, migrations_dir=migrations_dir)
        assert applied == ["001_create_a", "002_create_b"]

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "a" in tables
        assert "b" in tables
        assert "_migrations" in tables
    finally:
        conn.close()


def test_migration_failure_rolls_back(tmp_path: Path) -> None:
    """A failing migration rolls back its transaction and isn't recorded."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_good.sql").write_text(
        "CREATE TABLE good_table (id INTEGER PRIMARY KEY);"
    )
    (migrations_dir / "002_bad.sql").write_text(
        "CREATE TABLE bad_table (id INTEGER PRIMARY KEY);\n"
        "INVALID SQL STATEMENT HERE;"
    )

    conn = _fresh_conn()
    try:
        with pytest.raises(sqlite3.Error):
            apply_migrations(conn, migrations_dir=migrations_dir)

        # 001 should be applied; 002 should have rolled back
        names = applied_migration_names(conn)
        assert "001_good" in names
        assert "002_bad" not in names

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "good_table" in tables
        assert "bad_table" not in tables  # rolled back
    finally:
        conn.close()


def test_ensure_migrations_table_is_idempotent() -> None:
    conn = _fresh_conn()
    try:
        ensure_migrations_table(conn)
        ensure_migrations_table(conn)
        # Table should exist and be empty
        rows = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()
        assert rows[0] == 0
    finally:
        conn.close()


def test_split_sql_drops_comments_and_empty_statements() -> None:
    sql = """
    -- leading comment
    CREATE TABLE foo (id INTEGER);

    -- another comment
    INSERT INTO foo VALUES (1);
    ;
    """
    statements = _split_sql(sql)
    assert statements == [
        "CREATE TABLE foo (id INTEGER)",
        "INSERT INTO foo VALUES (1)",
    ]
