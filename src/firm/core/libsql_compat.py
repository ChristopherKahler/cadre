"""sqlite3-compatible shim over the libsql client (CADRE_DB_URL mode).

The libsql python client speaks to a remote sqld/Turso database but exposes
a narrower API than stdlib sqlite3: rows are plain tuples (no row_factory /
named access), parameters must be positional, cursors aren't iterable, and
server-side pragmas like busy_timeout are rejected. Cadre code is written
against sqlite3 semantics — this module wraps a libsql connection so the
rest of the framework runs unmodified. One code path, only the connection
string differs.

Verified against sqld (libsql-server) 2026-07-06:
  - PRAGMA data_version DOES bump on other connections' commits over remote
    (the dashboard SSE watcher works unchanged in multiplayer)
  - BEGIN IMMEDIATE / commit / rollback / executescript / lastrowid /
    rowcount / description all behave
  - PRAGMA busy_timeout / journal_mode raise "unsupported statement"
    (server-side concerns; swallowed here)
"""

from __future__ import annotations

import re
from typing import Any, Iterator

_NAMED_PARAM_RE = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")


class Row(tuple):
    """Positional + named column access, mapping protocol included —
    ``row[0]``, ``row["id"]``, and ``dict(row)`` all work, mirroring
    ``sqlite3.Row``."""

    _fields: tuple[str, ...]

    def __new__(cls, values: tuple, fields: tuple[str, ...]) -> "Row":
        self = super().__new__(cls, values)
        self._fields = fields
        return self

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._fields.index(key))
            except ValueError:
                raise IndexError(f"no such column: {key}") from None
        return tuple.__getitem__(self, key)

    def keys(self) -> list[str]:
        return list(self._fields)


class Cursor:
    """Wraps a libsql cursor: named rows, iteration, fetch* parity."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def description(self) -> Any:
        return self._raw.description

    @property
    def lastrowid(self) -> Any:
        return self._raw.lastrowid

    @property
    def rowcount(self) -> Any:
        return self._raw.rowcount

    def _fields(self) -> tuple[str, ...]:
        return tuple(d[0] for d in (self._raw.description or ()))

    def fetchone(self) -> Row | None:
        r = self._raw.fetchone()
        return None if r is None else Row(tuple(r), self._fields())

    def fetchall(self) -> list[Row]:
        fields = self._fields()
        return [Row(tuple(r), fields) for r in self._raw.fetchall()]

    def fetchmany(self, size: int = 1) -> list[Row]:
        fields = self._fields()
        return [Row(tuple(r), fields) for r in self._raw.fetchmany(size)]

    def __iter__(self) -> Iterator[Row]:
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row


class _NoopCursor:
    """Result of a swallowed server-side PRAGMA."""

    description = None
    lastrowid = None
    rowcount = -1

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list:
        return []

    def fetchmany(self, size: int = 1) -> list:
        return []

    def __iter__(self) -> Iterator:
        return iter(())


def _to_positional(sql: str, params: dict) -> tuple[str, tuple]:
    """Rewrite ``:name`` placeholders to ``?`` with an ordered param tuple —
    libsql only accepts positional parameters."""
    ordered: list[Any] = []

    def sub(m: re.Match) -> str:
        ordered.append(params[m.group(1)])
        return "?"

    return _NAMED_PARAM_RE.sub(sub, sql), tuple(ordered)


class Connection:
    """sqlite3-shaped facade over a libsql connection."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self.row_factory = None      # accepted for compat; rows are always named
        self.isolation_level = None  # accepted for compat (migrate.py toggles it)

    def execute(self, sql: str, params: Any = ()) -> Cursor | _NoopCursor:
        if isinstance(params, dict):
            sql, params = _to_positional(sql, params)
        try:
            return Cursor(self._raw.execute(sql, tuple(params)))
        except ValueError:
            # Server-side pragma policies vary (sqld: "unsupported statement",
            # Turso cloud: "SQL not allowed statement"). Pragmas are advisory
            # tuning in firm code — a refused one becomes a no-op; callers that
            # NEED a pragma's value (data_version) handle the empty cursor.
            if sql.lstrip().upper().startswith("PRAGMA"):
                return _NoopCursor()
            raise

    def executescript(self, script: str) -> Any:
        return self._raw.executescript(script)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()


def connect_libsql(url: str, auth_token: str | None = None) -> Connection:
    """Open a libsql connection to *url* (Turso / self-hosted sqld) wrapped
    in the sqlite3-compat facade, with firm-standard settings applied."""
    import libsql

    raw = libsql.connect(url, auth_token=auth_token) if auth_token else libsql.connect(url)
    conn = Connection(raw)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
