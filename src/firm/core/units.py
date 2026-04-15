"""Unit-specific domain logic: atomic checkout and dependency cycle detection.

Builds on ``firm.core.repo`` for standard CRUD; adds operations that need
domain awareness (atomic claim semantics, acyclic ``depends_on`` graphs).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from firm.core.repo import _deserialize_json  # type: ignore[reportPrivateUsage]
from firm.core import repo


class CycleError(Exception):
    """Raised when a ``depends_on`` graph would contain a cycle."""


# ---------------------------------------------------------------------------
# Atomic checkout / release
# ---------------------------------------------------------------------------

def checkout(
    conn: sqlite3.Connection, unit_id: str, member_id: str
) -> dict[str, Any] | None:
    """Atomically claim a Unit for a Member.

    Returns the updated row dict on success, or None if the Unit was already
    claimed or does not exist. The atomicity comes from the
    ``WHERE claimed_by IS NULL`` clause: any concurrent attempt sees the prior
    row state, and only the first committed UPDATE wins.

    Side effects on success:
    - ``claimed_by`` set to ``member_id``
    - ``claimed_at`` set to ``datetime('now')``
    - ``status`` transitions ``pending`` → ``in_progress`` (other statuses are
      preserved — claiming a ``blocked`` Unit keeps it ``blocked``)
    - ``updated_at`` touched

    Raises ``sqlite3.IntegrityError`` if ``member_id`` does not reference an
    existing Member (foreign-key enforcement).
    """
    sql = """
        UPDATE unit
        SET claimed_by = ?,
            claimed_at = datetime('now'),
            status = CASE
                WHEN status = 'pending' THEN 'in_progress'
                ELSE status
            END,
            updated_at = datetime('now')
        WHERE id = ? AND claimed_by IS NULL
        RETURNING *
    """
    row = conn.execute(sql, (member_id, unit_id)).fetchone()
    conn.commit()
    if row is None:
        return None
    return _deserialize_json("unit", dict(row))


def release(
    conn: sqlite3.Connection, unit_id: str
) -> dict[str, Any] | None:
    """Clear a Unit's claim. Does NOT revert status — callers decide.

    Returns the updated row, or None if the Unit does not exist.
    """
    sql = """
        UPDATE unit
        SET claimed_by = NULL,
            claimed_at = NULL,
            updated_at = datetime('now')
        WHERE id = ?
        RETURNING *
    """
    row = conn.execute(sql, (unit_id,)).fetchone()
    conn.commit()
    if row is None:
        return None
    return _deserialize_json("unit", dict(row))


# ---------------------------------------------------------------------------
# Dependency cycle detection
# ---------------------------------------------------------------------------

def _fetch_deps(conn: sqlite3.Connection, unit_id: str) -> list[str]:
    """Return the stored ``depends_on`` list for *unit_id*, or [] if missing/null/invalid."""
    row = conn.execute(
        "SELECT depends_on FROM unit WHERE id = ?", (unit_id,)
    ).fetchone()
    if row is None or row[0] is None:
        return []
    try:
        deps = json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(deps, list):
        return []
    return [str(d) for d in deps]


def validate_no_cycle(
    conn: sqlite3.Connection, unit_id: str, depends_on: list[str]
) -> None:
    """Raise ``CycleError`` if adding *depends_on* to *unit_id* would create a cycle.

    Walks the dependency graph starting from each proposed dep. If any walk
    reaches ``unit_id``, a cycle exists and we raise with the discovered path.

    Silently tolerates depends_on entries pointing at nonexistent Units
    (soft refs by design — the claim graph is application-managed, not FK-enforced).
    """
    if unit_id in depends_on:
        raise CycleError(f"{unit_id} → {unit_id}")

    visited: set[str] = set()
    # Each stack entry is a path from the root-direction frontier to `current`.
    stack: list[list[str]] = [[dep] for dep in depends_on]

    while stack:
        path = stack.pop()
        current = path[-1]
        if current == unit_id:
            full_path = " → ".join([unit_id] + path)
            raise CycleError(f"Cycle detected: {full_path}")
        if current in visited:
            continue
        visited.add(current)
        for child in _fetch_deps(conn, current):
            stack.append(path + [child])


def create_with_deps(
    conn: sqlite3.Connection, data: dict[str, Any]
) -> dict[str, Any]:
    """Validate ``depends_on`` doesn't create a cycle, then create the Unit.

    The cycle check runs BEFORE the INSERT, so a rejected create leaves no
    row behind.
    """
    if "id" not in data:
        raise ValueError("'id' is required when creating a Unit")
    deps = data.get("depends_on") or []
    if not isinstance(deps, list):
        raise ValueError("'depends_on' must be a list of Unit IDs")
    validate_no_cycle(conn, data["id"], [str(d) for d in deps])
    return repo.create(conn, "unit", data)


def set_dependencies(
    conn: sqlite3.Connection, unit_id: str, depends_on: list[str]
) -> dict[str, Any] | None:
    """Update a Unit's ``depends_on`` list after validating no cycle.

    Returns the updated row, or None if the Unit does not exist.
    """
    validate_no_cycle(conn, unit_id, depends_on)
    return repo.update(conn, "unit", unit_id, {"depends_on": depends_on})
