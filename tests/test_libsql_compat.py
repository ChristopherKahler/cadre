"""Tests for firm.core.libsql_compat — the sqlite3-shaped remote shim.

The shim's translation layers (named rows, dict params, PRAGMA swallowing)
are tested against fakes; the CADRE_DB_URL routing in firm.core.db is
tested with a stub libsql module. Live behavior against sqld was verified
by hand 2026-07-06 (see module docstring) and is re-verified in the lab's
multiplayer smoke, not here — unit tests must not need a server.
"""

from __future__ import annotations

import sys
import types

import pytest

from firm.core.libsql_compat import Connection, Cursor, Row, _to_positional


def test_row_positional_named_and_mapping():
    row = Row(("UNT-1", "pending"), ("id", "status"))
    assert row[0] == "UNT-1" and row["status"] == "pending"
    assert dict(row) == {"id": "UNT-1", "status": "pending"}
    assert row.keys() == ["id", "status"]
    with pytest.raises(IndexError, match="no such column"):
        row["nope"]


def test_named_params_become_positional():
    sql, params = _to_positional(
        "SELECT * FROM t WHERE a = :a AND b = :b AND a2 = :a",
        {"a": 1, "b": 2})
    assert sql == "SELECT * FROM t WHERE a = ? AND b = ? AND a2 = ?"
    assert params == (1, 2, 1)


class _FakeRawCursor:
    def __init__(self, rows, description):
        self._rows = list(rows)
        self.description = description
        self.lastrowid = 7
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


class _FakeRawConn:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        if "unsupported" in sql:
            raise ValueError("Hrana: `api error: `{...unsupported statement...}``")
        return _FakeRawCursor([("a", 1), ("b", 2)], (("k",), ("v",)))


def test_cursor_wraps_rows_named_and_iterable():
    cur = Cursor(_FakeRawCursor([("a", 1), ("b", 2)], (("k",), ("v",))))
    rows = list(cur)  # libsql cursors aren't iterable; the shim's are
    assert [r["k"] for r in rows] == ["a", "b"]
    assert rows[0][1] == 1


def test_connection_converts_dict_params_and_swallows_pragmas():
    conn = Connection(_FakeRawConn())
    conn.execute("SELECT * FROM t WHERE k = :k", {"k": "a"})
    sql, params = conn._raw.executed[-1]
    assert sql == "SELECT * FROM t WHERE k = ?" and params == ("a",)

    # server-side pragmas are swallowed (no-op cursor), other errors raise
    out = conn.execute("PRAGMA unsupported_thing = 5")
    assert out.fetchone() is None and out.fetchall() == []
    with pytest.raises(ValueError):
        conn.execute("SELECT unsupported FROM t")


def test_cadre_db_url_routes_connect_to_libsql(tmp_path, monkeypatch):
    from firm.core import db as db_mod

    calls = {}

    def fake_connect(url, auth_token=None):
        calls["url"], calls["token"] = url, auth_token
        return _FakeRawConn()

    monkeypatch.setitem(sys.modules, "libsql",
                        types.SimpleNamespace(connect=fake_connect))
    monkeypatch.setenv("CADRE_DB_URL", "http://sqld.example:8080")
    monkeypatch.setenv("CADRE_DB_TOKEN", "tok-123")

    assert db_mod.db_is_remote()
    conn = db_mod.connect(tmp_path / "ignored.db")
    assert isinstance(conn, Connection)
    assert calls == {"url": "http://sqld.example:8080", "token": "tok-123"}
    # foreign_keys pragma went through the shim on connect
    assert conn._raw.executed[0][0] == "PRAGMA foreign_keys = ON"
    # and no local file was created
    assert not (tmp_path / "ignored.db").exists()


def test_without_env_connect_stays_sqlite(tmp_path, monkeypatch):
    from firm.core import db as db_mod

    monkeypatch.delenv("CADRE_DB_URL", raising=False)
    assert not db_mod.db_is_remote()
    conn = db_mod.connect(tmp_path / "firm.db")
    try:
        assert (tmp_path / "firm.db").exists()
    finally:
        conn.close()
