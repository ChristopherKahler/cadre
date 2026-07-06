"""SQLite connection helpers for the firm framework.

One code path, two backends: by default connections open the local
``.firm/firm.db`` file via stdlib sqlite3. When ``CADRE_DB_URL`` is set
(a Turso / self-hosted sqld URL, with ``CADRE_DB_TOKEN`` for auth), every
connection goes to that shared remote database instead — the multiplayer
mode. Game and firm code never branches on the backend; the compat shim
in :mod:`firm.core.libsql_compat` keeps sqlite3 semantics.

Scope note: the override redirects ALL connects in the process, so it is
for single-firm processes (a firm's pulse, engine commands, its dashboard).
A hub serving multiple firms must not set it.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def db_is_remote() -> bool:
    """True when CADRE_DB_URL points this process at a shared remote DB."""
    return bool(os.environ.get("CADRE_DB_URL"))


def get_db_path(workspace: Path) -> Path:
    """Return the canonical ``.firm/firm.db`` path inside *workspace*."""
    return workspace / ".firm" / "firm.db"


def connect(db_path: Path) -> Any:
    """Open a firm DB connection with firm-standard settings.

    - ``PRAGMA foreign_keys = ON``
    - ``row_factory = sqlite3.Row`` (named column access)
    - Parent directory is created if missing (local mode).

    With ``CADRE_DB_URL`` set, *db_path* is ignored and the connection goes
    to the shared remote database via the libsql compat shim.
    """
    url = os.environ.get("CADRE_DB_URL")
    if url:
        from firm.core.libsql_compat import connect_libsql
        return connect_libsql(url, os.environ.get("CADRE_DB_TOKEN"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def bump_rev(conn: Any) -> None:
    """Increment the firm's write counter (joins the caller's transaction).

    Change-signal fallback for backends that refuse ``PRAGMA data_version``
    (Turso cloud): every meaningful write path bumps it, the dashboard SSE
    watcher polls it. Local SQLite keeps using data_version; the bump is
    harmless there. Best-effort — never fails a write."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS firm_rev "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), n INTEGER NOT NULL DEFAULT 0)")
        conn.execute(
            "INSERT INTO firm_rev (id, n) VALUES (1, 1) "
            "ON CONFLICT(id) DO UPDATE SET n = n + 1")
    except Exception:
        pass


def get_rev(conn: Any) -> int:
    try:
        row = conn.execute("SELECT n FROM firm_rev WHERE id = 1").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


@contextmanager
def db_connection(workspace: Path) -> Iterator[Any]:
    """Context manager yielding a firm DB connection for *workspace*.

    Commits on clean exit, rolls back on exception, always closes.
    """
    conn = connect(get_db_path(workspace))
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
