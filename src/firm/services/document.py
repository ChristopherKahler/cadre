"""Document entity service — create, list, view, update, register.

Documents are named file references attached to any entity via polymorphic
parent_entity_type + parent_entity_id. Track version history via auto-
incrementing version on content_path changes. Status transitions logged.

ID prefix: DOC-NNN
Records events: document.created, document.status_transition, document.updated
"""

from __future__ import annotations

import os
import re
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


#: A deliverable's version marker: ``triage-rules-v3.md`` → head + 3.
_VERSION_RE = re.compile(r"^(?P<head>.*?)-v(?P<n>\d+)$")


def _next_version_path(content_path: str, current_version: int) -> str:
    """Compute the next never-overwrite version path for a deliverable.

    Rule (Board policy, 2026-07): a rewrite never touches the prior file.
    ``foo-v1.md`` → ``foo-v2.md``; a path with no ``-vN`` marker is treated
    as v1 and becomes ``foo-v2.md``. This keeps every version on disk so the
    Board can diff v1↔v2. Directory and extension are preserved.
    """
    directory, base = os.path.split(content_path)
    stem, ext = os.path.splitext(base)
    m = _VERSION_RE.search(stem)
    if m:
        nxt = int(m.group("n")) + 1
        new_stem = f"{m.group('head')}-v{nxt}"
    else:
        nxt = max(current_version, 1) + 1
        new_stem = f"{stem}-v{nxt}"
    return os.path.join(directory, f"{new_stem}{ext}") if directory else f"{new_stem}{ext}"


def _version_family(content_path: str) -> str:
    """The de-versioned identity of a deliverable path.

    ``d/rules-v3.md`` and ``d/rules.md`` are the same document at different
    versions, so both reduce to ``d/rules.md``. Matching on the family rather
    than on ``_next_version_path``'s single step is what lets a v1→v3 jump land
    as a version bump instead of forking a sibling row — the live
    chief-of-staff DOC-001 case, where v3 arrived with no v2 registered.
    """
    directory, base = os.path.split(content_path)
    stem, ext = os.path.splitext(base)
    m = _VERSION_RE.search(stem)
    if m:
        stem = m.group("head")
    return os.path.join(directory, f"{stem}{ext}") if directory else f"{stem}{ext}"


def _version_of(content_path: str) -> int:
    """The version a path declares. No ``-vN`` marker means v1."""
    stem, _ext = os.path.splitext(os.path.basename(content_path))
    m = _VERSION_RE.search(stem)
    return int(m.group("n")) if m else 1


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


def register_deliverable(
    conn: sqlite3.Connection,
    firm_id: str,
    unit_id: str,
    path: str,
    *,
    member_id: str,
    name: str | None = None,
    doc_type: str = "draft",
    cwd: str | None = None,
) -> dict[str, Any]:
    """Register a produced file as *unit_id*'s deliverable. The one audited path.

    The firm's rules say the artifact must exist and be REGISTERED before the
    Unit closes, but nothing a Member could call did it: Records saw 3 of 26
    chief-of-staff deliverables and ``unit.outputs`` was NULL firm-wide even
    where a Document row existed (ESC-026). Both member-facing surfaces
    (``firm doc register``, ``firm unit complete --outputs``) and the pulse
    runner's seam-4 registration route here, so the version and ownership rules
    are decided once instead of drifting across three call sites.

    Resolution order, and why each rule is here:

    1. **The file must exist.** Registering a path that is not on disk records a
       deliverable the Board cannot open — worse than no row, because it reads
       as done.
    2. **Exact path already registered → no move.** Idempotent for the caller's
       own retries; for anyone else's unit it is a ``conflict``, reported rather
       than performed. A Member must never overwrite another Member's Document
       by naming its path.
    3. **Same version family, same unit → bump that row.** A revision writes
       ``foo-v2.md`` beside ``foo.md``; forking a sibling DOC row there loses
       the Board's v1↔v2 diff. Family matching is *unit-scoped* precisely
       because the unscoped form is the clobber vector: MEM-002 writing
       ``foo-v2.md`` next to MEM-001's registered ``foo.md`` would silently
       drag MEM-001's row onto MEM-002's file.
    4. **An older version of a family we already carry → superseded, no write.**
       The live file is the newest one; re-registering v1 must not drag the
       Document backwards.
    5. Otherwise create a row parented to the caller's unit.

    Every path but ``conflict`` records ``path`` in ``unit.outputs``.

    Args:
        member_id: The producing Member. Rides onto Records as the actor — the
            Board default would credit every Member's deliverable to the Board.
        cwd: Root the stored ``content_path`` is made relative to (the firm
            workspace). Absolute paths in the DB do not survive a move.

    Returns:
        ``{"action": "created"|"versioned"|"existing"|"superseded"|"conflict",
        "document": row, "content_path": str}``

    Raises:
        ValueError: Unknown unit, or the file is not on disk.
    """
    unit = require_exists(conn, "unit", unit_id)

    abspath = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(abspath):
        raise ValueError(
            f"deliverable not found on disk: {path} — the artifact must exist "
            "before it can be registered"
        )
    rel = os.path.relpath(abspath, os.path.abspath(cwd)) if cwd else abspath

    siblings = repo.find(conn, "document", firm_id=firm_id)

    exact = next((d for d in siblings if d.get("content_path") == rel), None)
    if exact is not None:
        if exact.get("parent_entity_id") != unit_id:
            return {
                "action": "conflict",
                "document": exact,
                "content_path": rel,
            }
        _append_output(conn, unit, rel)
        return {"action": "existing", "document": exact, "content_path": rel}

    family = _version_family(rel)
    kin = next(
        (
            d for d in siblings
            if d.get("parent_entity_type") == "unit"
            and d.get("parent_entity_id") == unit_id
            and _version_family(d.get("content_path") or "") == family
        ),
        None,
    )
    if kin is not None:
        if _version_of(rel) <= _version_of(kin.get("content_path") or ""):
            return {"action": "superseded", "document": kin, "content_path": rel}
        updated = update_document(
            conn, kin["id"], {"content_path": rel},
            actor={"type": "member", "id": member_id},
        )
        _append_output(conn, unit, rel)
        return {"action": "versioned", "document": updated, "content_path": rel}

    created = create_document(conn, firm_id, {
        "name": name or os.path.basename(abspath),
        "type": doc_type,
        "content_path": rel,
        "parent_entity_type": "unit",
        "parent_entity_id": unit_id,
        "author_type": "member",
        "author_id": member_id,
    })
    _append_output(conn, unit, rel)
    return {"action": "created", "document": created, "content_path": rel}


def _append_output(
    conn: sqlite3.Connection, unit: dict[str, Any], rel: str,
) -> None:
    """Record *rel* on the unit's ``outputs``, idempotently.

    A Document row alone left ``outputs`` NULL firm-wide (ESC-026), so the
    Unit — the thing the Board reads — carried no trace of what it produced.
    """
    from firm.services.unit import update_unit

    current = unit.get("outputs") or []
    if not isinstance(current, list):
        current = []
    if rel in current:
        return
    update_unit(conn, unit["id"], {"outputs": [*current, rel]})


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
