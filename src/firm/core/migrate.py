"""Migration runner for the firm framework.

Discovers numbered SQL files in ``src/firm/migrations/`` and applies pending
ones in numeric order. Tracks applied migrations in a ``_migrations`` table
inside the SQLite database itself.

Bootstrap ordering note: :func:`ensure_migrations_table` runs before any
migration is applied, so the runner can record that migration 001 (which
may itself create ``_migrations``) was applied. ``CREATE TABLE IF NOT
EXISTS`` makes the overlap harmless.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_MIGRATION_FILENAME_PATTERN = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def _default_migrations_dir() -> Path:
    """Locate the bundled ``migrations/`` directory relative to this module."""
    return Path(__file__).parent.parent / "migrations"


def discover_migrations(migrations_dir: Path) -> list[tuple[int, str, Path]]:
    """Return migrations in numeric order as ``(number, name, path)`` tuples.

    ``name`` is the filename without the ``.sql`` suffix. Files not matching
    ``NNN_name.sql`` are ignored.
    """
    entries: list[tuple[int, str, Path]] = []
    if not migrations_dir.is_dir():
        return entries
    for path in migrations_dir.iterdir():
        match = _MIGRATION_FILENAME_PATTERN.match(path.name)
        if not match:
            continue
        entries.append((int(match.group(1)), path.stem, path))
    entries.sort(key=lambda item: item[0])
    return entries


def ensure_migrations_table(conn: sqlite3.Connection) -> None:
    """Create the ``_migrations`` tracking table if it doesn't exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def applied_migration_names(conn: sqlite3.Connection) -> set[str]:
    """Return the set of migration names already recorded as applied."""
    ensure_migrations_table(conn)
    rows = conn.execute("SELECT name FROM _migrations").fetchall()
    return {row[0] for row in rows}


def _strip_line_comments(content: str) -> str:
    """Remove ``-- ...`` line comments (inline and full-line).

    Naive: looks for the first ``--`` on each line and truncates there. Does
    not understand ``--`` inside string literals; our migrations don't use
    that pattern.
    """
    out: list[str] = []
    for line in content.splitlines():
        idx = line.find("--")
        if idx >= 0:
            line = line[:idx]
        out.append(line.rstrip())
    return "\n".join(out)


def _split_sql(content: str) -> list[str]:
    """Split a SQL script into individual statements.

    Handles:
    - ``--`` line comments (both full-line and inline) via preprocessing
    - Top-level ``;`` as statement terminator
    - ``BEGIN`` ... ``END`` blocks (trigger bodies) — semicolons inside
      do NOT terminate the outer statement; only the matching ``END``
      (followed by its own ``;``) closes the block.

    Does NOT handle: BEGIN/END tokens or ``--`` inside string literals, or
    block comments. Swap for a proper parser when migrations need those.
    """
    text = _strip_line_comments(content)

    def _is_word_boundary(pos: int) -> bool:
        if pos < 0 or pos >= len(text):
            return True
        ch = text[pos]
        return not (ch.isalnum() or ch == "_")

    def _match_word_at(pos: int, word: str) -> bool:
        wlen = len(word)
        if text[pos : pos + wlen].upper() != word:
            return False
        return _is_word_boundary(pos - 1) and _is_word_boundary(pos + wlen)

    statements: list[str] = []
    current: list[str] = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        if _match_word_at(i, "BEGIN"):
            depth += 1
            current.append(text[i : i + 5])
            i += 5
            continue
        if depth > 0 and _match_word_at(i, "END"):
            depth -= 1
            current.append(text[i : i + 3])
            i += 3
            continue
        ch = text[i]
        if ch == ";" and depth == 0:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def apply_migrations(
    conn: sqlite3.Connection,
    migrations_dir: Path | None = None,
) -> list[str]:
    """Apply all pending migrations in numeric order.

    Each migration runs in its own transaction. On failure, that migration's
    transaction rolls back and the exception propagates (migrations applied
    earlier in this call remain committed).

    :returns: names of migrations applied during this call, in order.
    """
    if migrations_dir is None:
        migrations_dir = _default_migrations_dir()

    ensure_migrations_table(conn)
    already_applied = applied_migration_names(conn)

    pending = [
        entry
        for entry in discover_migrations(migrations_dir)
        if entry[1] not in already_applied
    ]

    # Python sqlite3's default deferred isolation does not wrap DDL cleanly
    # (CREATE TABLE can implicit-commit a prior pending transaction, breaking
    # the all-or-nothing guarantee). Switch to manual transaction mode for
    # the duration of migration application and drive BEGIN/COMMIT/ROLLBACK
    # explicitly, then restore the caller's setting.
    saved_isolation = conn.isolation_level
    conn.isolation_level = None
    newly_applied: list[str] = []
    try:
        for _number, name, path in pending:
            statements = _split_sql(path.read_text(encoding="utf-8"))
            conn.execute("BEGIN")
            try:
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO _migrations (name) VALUES (?)", (name,)
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            newly_applied.append(name)
    finally:
        conn.isolation_level = saved_isolation

    return newly_applied
