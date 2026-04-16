"""Generic CRUD repository for the firm framework's 15 entity tables.

Builds on the SQLite schema shipped by migrations 002 + 003. Handles JSON
column serialization, auto-touches ``updated_at`` on update, and guards
against table/column injection by hard-allowlisting names.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Allow-lists and registries
# ---------------------------------------------------------------------------

ALL_TABLES: frozenset[str] = frozenset({
    "firm",
    "contract",
    "member",
    "goal",
    "operation",
    "project",
    "unit",
    "comment",
    "member_run",
    "usage_event",
    "gate",
    "records",
    "firm_secret",
    "document",
    "budget_period",
})

#: Tables whose rows cannot be UPDATEd or DELETEd (enforced at DB level by triggers).
IMMUTABLE_TABLES: frozenset[str] = frozenset({"comment", "records", "usage_event"})

#: Tables that have an ``updated_at`` column and expect it to auto-touch on update.
_TABLES_WITH_UPDATED_AT: frozenset[str] = ALL_TABLES - IMMUTABLE_TABLES

#: Columns that store JSON-encoded Python values (list/dict), per table.
JSON_COLUMNS: dict[str, frozenset[str]] = {
    "firm": frozenset({"operator", "core_values", "partners", "schedule"}),
    "contract": frozenset({"runtime_config", "skill_loadout", "domain_loadout",
                           "pulse_config", "validation_config", "budget_config"}),
    "member": frozenset({"suggested_skills", "suggested_domains", "budget"}),
    "goal": frozenset({"metric"}),
    "operation": frozenset({"goal_ids", "acceptance_criteria", "project_ids"}),
    "project": frozenset({"goal_ids", "acceptance_criteria", "unit_ids", "tags"}),
    "unit": frozenset({"goal_ids", "acceptance_criteria", "depends_on", "outputs", "tags"}),
    "member_run": frozenset({"usage_event_ids", "outputs", "validation_result"}),
    "records": frozenset({"details"}),
    "firm_secret": frozenset({"used_by_member_ids"}),
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class RepoError(Exception):
    """Base class for repository-level errors."""


class ImmutableTableError(RepoError):
    """Raised when a write operation is attempted on an immutable table."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_COLUMN_CACHE: dict[str, frozenset[str]] = {}


def _validate_table(table: str) -> None:
    if table not in ALL_TABLES:
        raise ValueError(f"Unknown table: {table!r}")


def _table_columns(conn: sqlite3.Connection, table: str) -> frozenset[str]:
    if table not in _COLUMN_CACHE:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        _COLUMN_CACHE[table] = frozenset(row[1] for row in rows)
    return _COLUMN_CACHE[table]


def _validate_columns(
    conn: sqlite3.Connection, table: str, cols: Iterable[str]
) -> None:
    known = _table_columns(conn, table)
    for col in cols:
        if col not in known:
            raise ValueError(f"Unknown column {col!r} for table {table!r}")


def _serialize_json(table: str, data: dict[str, Any]) -> dict[str, Any]:
    json_cols = JSON_COLUMNS.get(table, frozenset())
    if not json_cols:
        return dict(data)
    out: dict[str, Any] = {}
    for col, val in data.items():
        if col in json_cols and val is not None and not isinstance(val, str):
            out[col] = json.dumps(val)
        else:
            out[col] = val
    return out


def _deserialize_json(table: str, row: dict[str, Any]) -> dict[str, Any]:
    json_cols = JSON_COLUMNS.get(table, frozenset())
    if not json_cols:
        return row
    for col in json_cols:
        if col not in row:
            continue
        val = row[col]
        if val is None:
            continue
        try:
            row[col] = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            # DB value is authoritative — keep the raw string rather than crash.
            pass
    return row


def _row_to_dict(table: str, row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return _deserialize_json(table, dict(row))


# ---------------------------------------------------------------------------
# Public CRUD API
# ---------------------------------------------------------------------------

def create(
    conn: sqlite3.Connection, table: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Insert a row. ``data`` must include ``id``. Returns the inserted row."""
    _validate_table(table)
    if "id" not in data:
        raise ValueError(f"'id' is required when creating a row in {table!r}")
    prepared = _serialize_json(table, data)
    cols = list(prepared.keys())
    _validate_columns(conn, table, cols)
    placeholders = ", ".join("?" for _ in cols)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    )
    conn.execute(sql, tuple(prepared.values()))
    conn.commit()
    inserted = get(conn, table, data["id"])
    assert inserted is not None, "row disappeared after insert"
    return inserted


def get(
    conn: sqlite3.Connection, table: str, id: str
) -> dict[str, Any] | None:
    """Fetch a row by primary key. Returns None if not found."""
    _validate_table(table)
    row = conn.execute(
        f"SELECT * FROM {table} WHERE id = ?", (id,)
    ).fetchone()
    return _row_to_dict(table, row)


def update(
    conn: sqlite3.Connection,
    table: str,
    id: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """Update named fields on a row. Touches ``updated_at`` for mutable tables.

    Returns the updated row, or None if no row with that id existed.
    Raises ImmutableTableError if ``table`` is in IMMUTABLE_TABLES (nicer
    than letting the trigger fire with a generic IntegrityError).
    """
    _validate_table(table)
    if table in IMMUTABLE_TABLES:
        raise ImmutableTableError(f"{table!r} is immutable; UPDATE rejected")
    if not data:
        # No-op update: still touch updated_at if applicable
        if table in _TABLES_WITH_UPDATED_AT:
            conn.execute(
                f"UPDATE {table} SET updated_at = datetime('now') WHERE id = ?",
                (id,),
            )
            conn.commit()
        return get(conn, table, id)

    prepared = _serialize_json(table, data)
    cols = list(prepared.keys())
    _validate_columns(conn, table, cols)
    set_clause_parts = [f"{col} = ?" for col in cols]
    params: list[Any] = list(prepared.values())
    if table in _TABLES_WITH_UPDATED_AT:
        set_clause_parts.append("updated_at = datetime('now')")
    set_clause = ", ".join(set_clause_parts)
    sql = f"UPDATE {table} SET {set_clause} WHERE id = ?"
    params.append(id)
    cursor = conn.execute(sql, params)
    conn.commit()
    if cursor.rowcount == 0:
        return None
    return get(conn, table, id)


def find(
    conn: sqlite3.Connection,
    table: str,
    **filters: Any,
) -> list[dict[str, Any]]:
    """Equality-filtered list. ``None`` filter values match NULL columns.

    Rows returned ordered by ``created_at, id`` for stable output.

    Named ``find`` (not ``list``) to avoid shadowing the ``list`` builtin; the
    plan's spec used ``list(...)``, but a module-level function named ``list``
    both confuses static analyzers and trips callers who do ``from repo import *``.
    """
    _validate_table(table)
    _validate_columns(conn, table, filters.keys())

    where_parts: list[str] = []
    params: list[Any] = []
    for col, val in filters.items():
        if val is None:
            where_parts.append(f"{col} IS NULL")
        else:
            where_parts.append(f"{col} = ?")
            params.append(val)
    where_sql = " AND ".join(where_parts) if where_parts else "1=1"
    sql = (
        f"SELECT * FROM {table} WHERE {where_sql} "
        f"ORDER BY created_at, id"
    )
    rows = conn.execute(sql, params).fetchall()
    return [_deserialize_json(table, dict(row)) for row in rows]


def delete(
    conn: sqlite3.Connection, table: str, id: str
) -> int:
    """Delete a row by id. Returns the rowcount (0 or 1).

    Immutable tables raise IntegrityError from the trigger; we let that propagate
    instead of short-circuiting here so the DB-level invariant is visible.
    """
    _validate_table(table)
    cursor = conn.execute(f"DELETE FROM {table} WHERE id = ?", (id,))
    conn.commit()
    return cursor.rowcount
