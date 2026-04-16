"""Comment entity service — create, list, view.

Comments are immutable audit-trail entries attached to any entity via
polymorphic parent_entity_type + parent_entity_id. Supports threaded
replies via in_reply_to self-reference.

ID prefix: COM-NNN
Records events: comment.created
Immutable: no update or delete (DB triggers enforce).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from firm.core import repo
from firm.services._id import next_id
from firm.services._records import log_event
from firm.services._validate import require_exists, validate_parent_ref

COMMENT_PARENT_TYPES = [
    "firm", "member", "operation", "project", "unit", "goal", "gate", "document",
]

COMMENT_AUTHOR_TYPES = ["member", "board"]


def create_comment(
    conn: sqlite3.Connection,
    firm_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Create an immutable Comment with parent validation and Records entry.

    Args:
        conn: SQLite connection.
        firm_id: Firm scope.
        data: Must include 'body', 'parent_entity_type', 'parent_entity_id',
              'author_type'. Optional: author_id, in_reply_to.

    Returns:
        The created comment row as a dict.

    Raises:
        ValueError: If required fields missing, parent ref invalid,
                    author_type invalid, or in_reply_to doesn't exist.
    """
    for required in ("body", "parent_entity_type", "parent_entity_id", "author_type"):
        if required not in data:
            raise ValueError(f"'{required}' is required for comment creation")

    # Validate parent_entity_type against allowed set
    if data["parent_entity_type"] not in COMMENT_PARENT_TYPES:
        raise ValueError(
            f"Invalid parent_entity_type {data['parent_entity_type']!r} — "
            f"must be one of: {', '.join(COMMENT_PARENT_TYPES)}"
        )

    # Validate parent ref exists
    validate_parent_ref(conn, data["parent_entity_type"], data["parent_entity_id"])

    # Validate author_type
    if data["author_type"] not in COMMENT_AUTHOR_TYPES:
        raise ValueError(
            f"Invalid author_type {data['author_type']!r} — "
            f"must be one of: {', '.join(COMMENT_AUTHOR_TYPES)}"
        )

    # Validate in_reply_to FK (if provided)
    if data.get("in_reply_to"):
        require_exists(conn, "comment", data["in_reply_to"])

    comment_id = next_id(conn, "comment", firm_id)

    row_data: dict[str, Any] = {
        "id": comment_id,
        "firm_id": firm_id,
        "parent_entity_type": data["parent_entity_type"],
        "parent_entity_id": data["parent_entity_id"],
        "author_type": data["author_type"],
        "body": data["body"],
    }
    for field in ("author_id", "in_reply_to"):
        if field in data:
            row_data[field] = data[field]

    created = repo.create(conn, "comment", row_data)

    log_event(
        conn,
        firm_id=firm_id,
        event_type="comment.created",
        actor={"type": data["author_type"], "id": data.get("author_id")},
        target_ref={"type": "comment", "id": comment_id},
    )

    return created


def list_comments(
    conn: sqlite3.Connection,
    firm_id: str,
    *,
    parent_type: str | None = None,
    parent_id: str | None = None,
) -> list[dict[str, Any]]:
    """List comments with optional parent filtering.

    Returns:
        List of comment dicts sorted by created_at.
    """
    filters: dict[str, Any] = {"firm_id": firm_id}
    if parent_type is not None:
        filters["parent_entity_type"] = parent_type
    if parent_id is not None:
        filters["parent_entity_id"] = parent_id
    return repo.find(conn, "comment", **filters)


def view_comment(
    conn: sqlite3.Connection,
    comment_id: str,
) -> dict[str, Any]:
    """View a comment by ID. Raises ValueError if not found."""
    return require_exists(conn, "comment", comment_id)
