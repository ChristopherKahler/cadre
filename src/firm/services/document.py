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


def _next_version_path(content_path: str, current_version: int) -> str:
    """Compute the next never-overwrite version path for a deliverable.

    Rule (Board policy, 2026-07): a rewrite never touches the prior file.
    ``foo-v1.md`` → ``foo-v2.md``; a path with no ``-vN`` marker is treated
    as v1 and becomes ``foo-v2.md``. This keeps every version on disk so the
    Board can diff v1↔v2. Directory and extension are preserved.
    """
    import os
    import re

    directory, base = os.path.split(content_path)
    stem, ext = os.path.splitext(base)
    m = re.search(r"^(?P<head>.*?)-v(?P<n>\d+)$", stem)
    if m:
        nxt = int(m.group("n")) + 1
        new_stem = f"{m.group('head')}-v{nxt}"
    else:
        nxt = max(current_version, 1) + 1
        new_stem = f"{stem}-v{nxt}"
    return os.path.join(directory, f"{new_stem}{ext}") if directory else f"{new_stem}{ext}"


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
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update a document with status transition logging and version auto-increment.

    If content_path changes, version is auto-incremented.
    If status changes, a status_transition Records entry is logged.

    Args:
        actor: Who is making the change, e.g. {"type": "member", "id": "MEM-004"}.
               Defaults to the Board. A Member revising its own deliverable must
               pass itself — Records has to carry who actually moved the version,
               not whoever the default happens to be.

    Raises:
        ValueError: If document not found or invalid status.
    """
    existing = require_exists(conn, "document", document_id)
    actor = actor or {"type": "board", "id": None}

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
            actor=actor,
            target_ref={"type": "document", "id": document_id},
            details={"from": old_status, "to": new_status},
        )

    # Log content update if path changed
    if content_path_changed:
        log_event(
            conn,
            firm_id=existing["firm_id"],
            event_type="document.updated",
            actor=actor,
            target_ref={"type": "document", "id": document_id},
            details={"content_path": data["content_path"], "version": data["version"]},
        )

    return updated


def request_revision(
    conn: sqlite3.Connection,
    firm_id: str,
    document_id: str,
    comment_body: str,
) -> dict[str, Any]:
    """Board revision request: comment on the document + a revision Unit.

    The comment alone is invisible to Members (briefings inject unit
    threads, not document threads) — the Unit is what carries the Board's
    direction into someone's next pulse. The revision Unit lands in the
    producing unit's project, assigned to whoever made the deliverable,
    with the Board's comment inline in its description.

    Returns:
        {"comment": row, "unit": row}

    Raises:
        ValueError: If the document is unknown, the comment is empty, or
                    the producing unit/project cannot be resolved.
    """
    from firm.services.comment import create_comment
    from firm.services.unit import create_unit

    if not comment_body or not comment_body.strip():
        raise ValueError("comment_body is required for a revision request")

    doc = require_exists(conn, "document", document_id)

    comment = create_comment(conn, firm_id, {
        "parent_entity_type": "document",
        "parent_entity_id": document_id,
        "body": comment_body,
        "author_type": "board",
    })

    # Resolve producing unit → project + assignee
    src_unit = None
    if doc.get("parent_entity_type") == "unit" and doc.get("parent_entity_id"):
        src_unit = repo.get(conn, "unit", doc["parent_entity_id"])
    if not src_unit or not src_unit.get("project_id"):
        raise ValueError(
            f"document {document_id} has no producing unit with a project — "
            "create the revision unit manually via unit-create"
        )
    assignee = src_unit.get("assignee_member_id") or src_unit.get("claimed_by")

    old_path = doc.get("content_path") or ""
    new_path = _next_version_path(old_path, doc.get("version") or 1)

    unit_data: dict[str, Any] = {
        "name": f"Revise {document_id}: {doc.get('name', '')}"[:120],
        "project_id": src_unit["project_id"],
        "priority": "high",
        "description": (
            f"THE BOARD requested a revision of {document_id} ({old_path}).\n\n"
            f"Board comment:\n{comment_body}\n\n"
            f"Original unit: {src_unit['id']}. NEVER-OVERWRITE RULE: do not touch "
            f"{old_path} — copy it to {new_path}, edit ONLY the copy, and leave the "
            "original byte-for-byte so the Board can diff versions. Register the "
            f"revised file as a new version of {document_id} (update its "
            f"content_path to {new_path}; the version field auto-increments). "
            "Then complete this unit."
        ),
        "acceptance_criteria": [
            f"The Board's comment on {document_id} is addressed in the deliverable",
            f"Revision saved to a NEW file {new_path} (never editing {old_path})",
            f"The original {old_path} is left untouched for diffing",
        ],
        "tags": ["revision", document_id],
    }
    if assignee:
        unit_data["assignee_member_id"] = assignee
    unit = create_unit(conn, firm_id, unit_data)

    log_event(
        conn,
        firm_id=firm_id,
        event_type="document.revision_requested",
        actor={"type": "board", "id": None},
        target_ref={"type": "document", "id": document_id},
        details={"unit_id": unit["id"], "assignee": assignee},
    )

    return {"comment": comment, "unit": unit}
