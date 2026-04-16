"""Document entity service — create, list, view, update.

Documents are named file references attached to any entity via polymorphic
parent_entity_type + parent_entity_id. Track version history via auto-
incrementing version on content_path changes. Status transitions logged.

ID prefix: DOC-NNN
Records events: document.created, document.status_transition, document.updated
"""

from __future__ import annotations

import sqlite3
from typing import Any

from firm.core import repo
from firm.services._id import next_id
from firm.services._records import log_event
from firm.services._validate import require_exists, validate_parent_ref, validate_status

DOCUMENT_PARENT_TYPES = [
    "firm", "member", "operation", "project", "unit", "goal", "gate",
]

DOCUMENT_STATUSES = ["active", "archived", "deprecated"]

DOCUMENT_AUTHOR_TYPES = ["member", "board"]


def create_document(
    conn: sqlite3.Connection,
    firm_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Create a Document with parent validation, required fields, and Records entry.

    Args:
        conn: SQLite connection.
        firm_id: Firm scope.
        data: Must include 'name', 'type', 'content_path', 'parent_entity_type',
              'parent_entity_id'. Optional: author_type, author_id, status.

    Returns:
        The created document row as a dict.

    Raises:
        ValueError: If required fields missing, parent ref invalid, or
                    author_type invalid.
    """
    for required in ("name", "type", "content_path", "parent_entity_type", "parent_entity_id"):
        if required not in data:
            raise ValueError(f"'{required}' is required for document creation")

    # Validate parent_entity_type against allowed set
    if data["parent_entity_type"] not in DOCUMENT_PARENT_TYPES:
        raise ValueError(
            f"Invalid parent_entity_type {data['parent_entity_type']!r} — "
            f"must be one of: {', '.join(DOCUMENT_PARENT_TYPES)}"
        )

    # Validate parent ref exists
    validate_parent_ref(conn, data["parent_entity_type"], data["parent_entity_id"])

    # Validate author_type if provided
    if "author_type" in data and data["author_type"] is not None:
        if data["author_type"] not in DOCUMENT_AUTHOR_TYPES:
            raise ValueError(
                f"Invalid author_type {data['author_type']!r} — "
                f"must be one of: {', '.join(DOCUMENT_AUTHOR_TYPES)}"
            )

    doc_id = next_id(conn, "document", firm_id)

    row_data: dict[str, Any] = {
        "id": doc_id,
        "firm_id": firm_id,
        "name": data["name"],
        "type": data["type"],
        "content_path": data["content_path"],
        "parent_entity_type": data["parent_entity_type"],
        "parent_entity_id": data["parent_entity_id"],
    }
    for field in ("author_type", "author_id", "status"):
        if field in data:
            row_data[field] = data[field]

    created = repo.create(conn, "document", row_data)

    log_event(
        conn,
        firm_id=firm_id,
        event_type="document.created",
        actor={"type": data.get("author_type", "board"), "id": data.get("author_id")},
        target_ref={"type": "document", "id": doc_id},
    )

    return created


def list_documents(
    conn: sqlite3.Connection,
    firm_id: str,
    *,
    parent_type: str | None = None,
    parent_id: str | None = None,
    status: str | None = None,
    doc_type: str | None = None,
) -> list[dict[str, Any]]:
    """List documents with optional filters.

    Returns:
        List of document dicts sorted by created_at.
    """
    filters: dict[str, Any] = {"firm_id": firm_id}
    if parent_type is not None:
        filters["parent_entity_type"] = parent_type
    if parent_id is not None:
        filters["parent_entity_id"] = parent_id
    if status is not None:
        filters["status"] = status
    if doc_type is not None:
        filters["type"] = doc_type
    return repo.find(conn, "document", **filters)


def view_document(
    conn: sqlite3.Connection,
    document_id: str,
) -> dict[str, Any]:
    """View a document by ID. Raises ValueError if not found."""
    return require_exists(conn, "document", document_id)


def update_document(
    conn: sqlite3.Connection,
    document_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Update a document with status transition logging and version auto-increment.

    If content_path changes, version is auto-incremented.
    If status changes, a status_transition Records entry is logged.

    Raises:
        ValueError: If document not found or invalid status.
    """
    existing = require_exists(conn, "document", document_id)

    # Validate status if changing
    if "status" in data:
        validate_status(data["status"], DOCUMENT_STATUSES)

    # Auto-increment version on content_path change
    content_path_changed = (
        "content_path" in data and data["content_path"] != existing.get("content_path")
    )
    if content_path_changed:
        data["version"] = (existing.get("version") or 1) + 1

    # Detect status transition before update
    old_status = existing.get("status")
    new_status = data.get("status")

    updated = repo.update(conn, "document", document_id, data)
    assert updated is not None, "document disappeared after require_exists"

    # Log status transition if changed
    if new_status is not None and new_status != old_status:
        log_event(
            conn,
            firm_id=existing["firm_id"],
            event_type="document.status_transition",
            actor={"type": "board", "id": None},
            target_ref={"type": "document", "id": document_id},
            details={"from": old_status, "to": new_status},
        )

    # Log content update if path changed
    if content_path_changed:
        log_event(
            conn,
            firm_id=existing["firm_id"],
            event_type="document.updated",
            actor={"type": "board", "id": None},
            target_ref={"type": "document", "id": document_id},
            details={"content_path": data["content_path"], "version": data["version"]},
        )

    return updated
