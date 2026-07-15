"""Business/domain tagging for firm entities (fork cadre-entity-business-tags).

An operational firm serves several businesses at once (chief-of-staff runs
Caddy / Extendly / ChrisAI). This stamps WHICH business an escalation / gate /
unit belongs to, so a multi-business firm can filter, route, and give every item
provenance. Board-authored — services-only writes (Invariant #2).

There is no structured firm-business registry yet (a firm's businesses live in
its description prose), so v1 validates a tag as non-empty and surfaces the
SELF-POPULATING set of businesses already used on the firm's entities for the UI
to offer — consistent tags without free-text drift. A formal registry + strict
membership validation is the follow-up (tied to cadre-graphs' domain mapping).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from firm.core import repo
from firm.services._records import log_event

# The entity kinds that carry a business tag (each has a nullable `business`
# column from migration 013). Whitelist — also the SQL-safety guard below.
TAGGABLE = ("escalation", "gate", "unit")


def set_business(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    business: str,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tag (or retag) a firm entity with the business it belongs to.

    Sole write path for `business`: validates the kind + a non-empty value,
    writes via ``repo.update``, and appends a ``<kind>.business_set`` Records
    entry so the provenance is auditable.

    Raises:
        ValueError: untaggable kind, empty tag, or unknown entity.
    """
    if entity_type not in TAGGABLE:
        raise ValueError(
            f"cannot tag a {entity_type!r} with a business; "
            f"taggable: {', '.join(TAGGABLE)}")
    tag = (business or "").strip()
    if not tag:
        raise ValueError("business tag must be a non-empty value")

    row = repo.get(conn, entity_type, entity_id)
    if not row:
        raise ValueError(f"{entity_type} {entity_id!r} not found")

    updated = repo.update(conn, entity_type, entity_id, {"business": tag})
    assert updated is not None, "entity disappeared after repo.get"
    log_event(
        conn,
        firm_id=row["firm_id"],
        event_type=f"{entity_type}.business_set",
        actor=actor or {"type": "board", "id": None},
        target_ref={"type": entity_type, "id": entity_id},
        details={"business": tag, "previous": row.get("business")},
    )
    return updated


def firm_businesses(conn: sqlite3.Connection, firm_id: str) -> list[str]:
    """The businesses already used on this firm's entities — the UI's
    suggestion/filter list, self-populating so tags stay consistent without a
    separate registry. Sorted, de-duplicated across all taggable kinds."""
    seen: set[str] = set()
    for et in TAGGABLE:
        # et is whitelisted above — safe to interpolate the table name.
        rows = conn.execute(
            f"SELECT DISTINCT business FROM {et} "  # noqa: S608 (whitelisted)
            "WHERE firm_id = ? AND business IS NOT NULL AND business != ''",
            (firm_id,),
        )
        seen.update(r[0] for r in rows)
    return sorted(seen)
