"""Tests for firm.services.document.register_deliverable — the one audited path
that turns a produced file into a Document row and a non-null unit.outputs.

Both member-facing surfaces (``firm doc register``, ``firm unit complete
--outputs``) and the pulse runner's seam-4 registration land here, so the
version/clobber rules are proven once against the real function rather than
three times against three replicas.
"""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find, get
from firm.services.document import (
    _version_family,
    _version_of,
    create_document,
    register_deliverable,
)
from firm.services.member import create_member
from firm.services.operation import create_operation
from firm.services.project import create_project
from firm.services.unit import create_unit


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    create_member(conn, "chrisai", {"name": "Cooper", "role": "Ops Engineer"})
    create_member(conn, "chrisai", {"name": "Dalton", "role": "Correspondence"})
    create_operation(conn, "chrisai", {"name": "Ops", "owner_member_id": "MEM-001"})
    create_project(conn, "chrisai", {
        "name": "Inbox", "operation_id": "OPS-001", "due_date": "2026-12-31",
    })
    create_unit(conn, "chrisai", {"name": "Triage rules", "project_id": "PROJ-001"})
    create_unit(conn, "chrisai", {"name": "Other work", "project_id": "PROJ-001"})
    return conn


# ---------------------------------------------------------------------------
# Version-family arithmetic — the de-versioned identity of a deliverable
# ---------------------------------------------------------------------------


def test_version_family_strips_the_marker() -> None:
    assert _version_family("d/triage-rules-v3.md") == "d/triage-rules.md"
    assert _version_family("d/triage-rules.md") == "d/triage-rules.md"
    assert _version_family("triage-rules-v12.md") == "triage-rules.md"


def test_version_of_reads_the_marker() -> None:
    assert _version_of("d/triage-rules-v3.md") == 3
    assert _version_of("d/triage-rules.md") == 1
    assert _version_of("triage-rules-v12.md") == 12


# ---------------------------------------------------------------------------
# Registration: create, idempotency, outputs
# ---------------------------------------------------------------------------


def test_register_creates_document_parented_to_unit(tmp_path) -> None:
    conn = _fresh_conn()
    f = tmp_path / "report.md"
    f.write_text("body")

    result = register_deliverable(
        conn, "chrisai", "UNIT-001", str(f),
        member_id="MEM-001", cwd=str(tmp_path),
    )

    assert result["action"] == "created"
    doc = result["document"]
    assert doc["parent_entity_type"] == "unit"
    assert doc["parent_entity_id"] == "UNIT-001"
    assert doc["content_path"] == "report.md"
    assert doc["author_type"] == "member"
    assert doc["author_id"] == "MEM-001"


def test_register_sets_unit_outputs_non_null(tmp_path) -> None:
    """ESC-026: outputs was NULL firm-wide even where a Document existed."""
    conn = _fresh_conn()
    f = tmp_path / "report.md"
    f.write_text("body")
    assert get(conn, "unit", "UNIT-001")["outputs"] is None

    register_deliverable(conn, "chrisai", "UNIT-001", str(f),
                         member_id="MEM-001", cwd=str(tmp_path))

    assert get(conn, "unit", "UNIT-001")["outputs"] == ["report.md"]


def test_register_is_idempotent_on_the_same_path(tmp_path) -> None:
    conn = _fresh_conn()
    f = tmp_path / "report.md"
    f.write_text("body")
    for _ in range(3):
        result = register_deliverable(conn, "chrisai", "UNIT-001", str(f),
                                      member_id="MEM-001", cwd=str(tmp_path))
    assert result["action"] == "existing"
    assert len(find(conn, "document", firm_id="chrisai")) == 1
    assert get(conn, "unit", "UNIT-001")["outputs"] == ["report.md"]


def test_register_accumulates_multiple_deliverables_on_one_unit(tmp_path) -> None:
    conn = _fresh_conn()
    for nm in ("spec.md", "run-report.md"):
        (tmp_path / nm).write_text("x")
        register_deliverable(conn, "chrisai", "UNIT-001", str(tmp_path / nm),
                             member_id="MEM-001", cwd=str(tmp_path))
    assert len(find(conn, "document", firm_id="chrisai")) == 2
    assert get(conn, "unit", "UNIT-001")["outputs"] == ["spec.md", "run-report.md"]


def test_register_rejects_unknown_unit(tmp_path) -> None:
    conn = _fresh_conn()
    f = tmp_path / "report.md"
    f.write_text("x")
    with pytest.raises(ValueError, match="UNIT-404"):
        register_deliverable(conn, "chrisai", "UNIT-404", str(f),
                             member_id="MEM-001", cwd=str(tmp_path))


def test_register_rejects_missing_file(tmp_path) -> None:
    """The artifact must EXIST — registering a path that isn't there records a
    deliverable the Board cannot open."""
    conn = _fresh_conn()
    with pytest.raises(ValueError, match="not found"):
        register_deliverable(conn, "chrisai", "UNIT-001",
                             str(tmp_path / "ghost.md"),
                             member_id="MEM-001", cwd=str(tmp_path))


# ---------------------------------------------------------------------------
# Versioning — never-overwrite, never fork
# ---------------------------------------------------------------------------


def test_register_bumps_the_family_instead_of_forking(tmp_path) -> None:
    conn = _fresh_conn()
    (tmp_path / "rules.md").write_text("v1")
    register_deliverable(conn, "chrisai", "UNIT-001", str(tmp_path / "rules.md"),
                         member_id="MEM-001", cwd=str(tmp_path))
    (tmp_path / "rules-v2.md").write_text("v2")
    result = register_deliverable(conn, "chrisai", "UNIT-001",
                                  str(tmp_path / "rules-v2.md"),
                                  member_id="MEM-001", cwd=str(tmp_path))

    assert result["action"] == "versioned"
    docs = find(conn, "document", firm_id="chrisai")
    assert len(docs) == 1, "a -v2 revision bumps the row; it never forks a sibling"
    assert docs[0]["content_path"] == "rules-v2.md"
    assert docs[0]["version"] == 2
    assert (tmp_path / "rules.md").exists(), "never-overwrite: v1 stays on disk"


def test_register_bumps_across_a_version_gap(tmp_path) -> None:
    """v1 → v3 with no v2 registered. Single-step ``_next_version_path`` matching
    would miss the family and fork a sibling row — the exact history-forking the
    never-overwrite rule exists to prevent (live chief-of-staff DOC-001 case)."""
    conn = _fresh_conn()
    (tmp_path / "rules.md").write_text("v1")
    register_deliverable(conn, "chrisai", "UNIT-001", str(tmp_path / "rules.md"),
                         member_id="MEM-001", cwd=str(tmp_path))
    (tmp_path / "rules-v3.md").write_text("v3")
    result = register_deliverable(conn, "chrisai", "UNIT-001",
                                  str(tmp_path / "rules-v3.md"),
                                  member_id="MEM-001", cwd=str(tmp_path))

    assert result["action"] == "versioned"
    docs = find(conn, "document", firm_id="chrisai")
    assert len(docs) == 1
    assert docs[0]["content_path"] == "rules-v3.md"


def test_register_refuses_to_regress_to_an_older_version(tmp_path) -> None:
    """Registering v1 when the row already carries v2 must not drag the Document
    backwards — the live file is the newest one."""
    conn = _fresh_conn()
    (tmp_path / "rules-v2.md").write_text("v2")
    register_deliverable(conn, "chrisai", "UNIT-001", str(tmp_path / "rules-v2.md"),
                         member_id="MEM-001", cwd=str(tmp_path))
    (tmp_path / "rules.md").write_text("v1")
    result = register_deliverable(conn, "chrisai", "UNIT-001",
                                  str(tmp_path / "rules.md"),
                                  member_id="MEM-001", cwd=str(tmp_path))

    assert result["action"] == "superseded"
    docs = find(conn, "document", firm_id="chrisai")
    assert len(docs) == 1
    assert docs[0]["content_path"] == "rules-v2.md", "the newer version stands"


# ---------------------------------------------------------------------------
# The clobber guard — a Member never overwrites another Member's Document
# ---------------------------------------------------------------------------


def test_register_refuses_to_steal_a_path_owned_by_another_unit(tmp_path) -> None:
    conn = _fresh_conn()
    f = tmp_path / "report.md"
    f.write_text("body")
    register_deliverable(conn, "chrisai", "UNIT-001", str(f),
                         member_id="MEM-001", cwd=str(tmp_path))

    result = register_deliverable(conn, "chrisai", "UNIT-002", str(f),
                                  member_id="MEM-002", cwd=str(tmp_path))

    assert result["action"] == "conflict"
    assert result["document"]["parent_entity_id"] == "UNIT-001"
    doc = find(conn, "document", firm_id="chrisai")[0]
    assert doc["parent_entity_id"] == "UNIT-001", "the original owner keeps the row"
    assert doc["author_id"] == "MEM-001"
    assert get(conn, "unit", "UNIT-002")["outputs"] is None, \
        "a conflicting register claims nothing on the caller's unit"


def test_register_never_versions_another_units_document(tmp_path) -> None:
    """The clobber vector: MEM-002 writes ``rules-v2.md`` beside MEM-001's
    registered ``rules.md``. Family matching must be unit-scoped, or MEM-002
    silently moves MEM-001's Document onto its own file."""
    conn = _fresh_conn()
    (tmp_path / "rules.md").write_text("v1")
    register_deliverable(conn, "chrisai", "UNIT-001", str(tmp_path / "rules.md"),
                         member_id="MEM-001", cwd=str(tmp_path))
    (tmp_path / "rules-v2.md").write_text("v2")

    result = register_deliverable(conn, "chrisai", "UNIT-002",
                                  str(tmp_path / "rules-v2.md"),
                                  member_id="MEM-002", cwd=str(tmp_path))

    assert result["action"] == "created", "a new row on the caller's own unit"
    owner = get(conn, "document", "DOC-001")
    assert owner["content_path"] == "rules.md", "MEM-001's row is untouched"
    assert owner["version"] == 1
    assert result["document"]["parent_entity_id"] == "UNIT-002"


def test_register_records_the_member_as_actor(tmp_path) -> None:
    """Records must carry the Member who produced the deliverable, never the
    Board default (the ENGINEERING.md §9 update_document(actor=) precedent)."""
    conn = _fresh_conn()
    f = tmp_path / "report.md"
    f.write_text("x")
    register_deliverable(conn, "chrisai", "UNIT-001", str(f),
                         member_id="MEM-001", cwd=str(tmp_path))

    rows = find(conn, "records", firm_id="chrisai", event_type="document.created")
    assert rows, "registration is an audited act"
    assert rows[-1]["actor_type"] == "member"
    assert rows[-1]["actor_id"] == "MEM-001"


def test_register_records_the_member_on_a_version_bump(tmp_path) -> None:
    conn = _fresh_conn()
    (tmp_path / "rules.md").write_text("v1")
    register_deliverable(conn, "chrisai", "UNIT-001", str(tmp_path / "rules.md"),
                         member_id="MEM-001", cwd=str(tmp_path))
    (tmp_path / "rules-v2.md").write_text("v2")
    register_deliverable(conn, "chrisai", "UNIT-001", str(tmp_path / "rules-v2.md"),
                         member_id="MEM-002", cwd=str(tmp_path))

    rows = find(conn, "records", firm_id="chrisai", event_type="document.updated")
    assert rows[-1]["actor_type"] == "member"
    assert rows[-1]["actor_id"] == "MEM-002"


def test_register_honours_an_explicit_name_and_type(tmp_path) -> None:
    conn = _fresh_conn()
    f = tmp_path / "report.md"
    f.write_text("x")
    result = register_deliverable(
        conn, "chrisai", "UNIT-001", str(f), member_id="MEM-001",
        name="The Armory Report", doc_type="report", cwd=str(tmp_path),
    )
    assert result["document"]["name"] == "The Armory Report"
    assert result["document"]["type"] == "report"


def test_register_defaults_the_name_to_the_filename(tmp_path) -> None:
    conn = _fresh_conn()
    f = tmp_path / "armory-report.md"
    f.write_text("x")
    result = register_deliverable(conn, "chrisai", "UNIT-001", str(f),
                                  member_id="MEM-001", cwd=str(tmp_path))
    assert result["document"]["name"] == "armory-report.md"
