"""SQLite connection helpers for the firm framework."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def get_db_path(workspace: Path) -> Path:
    """Return the canonical ``.firm/firm.db`` path inside *workspace*."""
    return workspace / ".firm" / "firm.db"


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with firm-standard settings.

    - ``PRAGMA foreign_keys = ON``
    - ``row_factory = sqlite3.Row`` (named column access)
    - Parent directory is created if missing.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_connection(workspace: Path) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a SQLite connection for *workspace*.

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
