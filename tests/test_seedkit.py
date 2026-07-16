"""seedkit — the seed can finally do the job the guide assigns it: amend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations
from firm.seedkit import RUNTIME_FIELDS, ensure, seed_session


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / ".firm").mkdir(parents=True)
    conn = connect(get_db_path(ws))
    apply_migrations(conn)
    conn.commit()
    conn.close()
    return ws


def _unit(desc: str = "v1 description", status: str = "pending") -> dict:
    return {
        "id": "UNT-001", "firm_id": "f1", "project_id": "PRJ-001",
        "name": "U", "description": desc, "status": status,
    }


def _scaffold(conn) -> None:
    ensure(conn, "firm", {"id": "f1", "name": "F1"}, quiet=True)
    ensure(conn, "operation",
           {"id": "OP-001", "firm_id": "f1", "name": "Ops", "status": "active"},
           quiet=True)
    ensure(conn, "project",
           {"id": "PRJ-001", "firm_id": "f1", "operation_id": "OP-001",
            "name": "P", "status": "in_progress", "due_date": "2026-12-31"},
           quiet=True)


def test_ensure_creates_then_amends(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    conn = connect(get_db_path(ws))
    try:
        _scaffold(conn)
        assert ensure(conn, "unit", _unit(), resync=("description",),
                      quiet=True) == "created"
        # The Defect-1 scenario: edit the seed, re-run, the edit must LAND.
        assert ensure(conn, "unit", _unit("v2 — Board amended this"),
                      resync=("description",), quiet=True) == "resynced"
        live = conn.execute(
            "SELECT description FROM unit WHERE id = 'UNT-001'").fetchone()[0]
        assert live == "v2 — Board amended this"
        # And an unchanged re-run is a no-op.
        assert ensure(conn, "unit", _unit("v2 — Board amended this"),
                      resync=("description",), quiet=True) == "exists"
    finally:
        conn.close()


def test_ensure_skips_resync_field_absent_from_payload(tmp_path: Path) -> None:
    """A resync field the payload omits is skipped, not a KeyError.

    Field failure 2026-07-16: a unit dict naming `depends_on` in resync but
    omitting it from the payload crashed the whole seed on re-run."""
    ws = _ws(tmp_path)
    conn = connect(get_db_path(ws))
    try:
        _scaffold(conn)
        ensure(conn, "unit", _unit(), resync=("description",), quiet=True)
        # Re-run naming depends_on in resync but NOT providing it — must not raise.
        result = ensure(conn, "unit", _unit("v2"),
                        resync=("description", "depends_on"), quiet=True)
        assert result == "resynced"  # description changed; depends_on skipped
        live = conn.execute(
            "SELECT description FROM unit WHERE id = 'UNT-001'").fetchone()[0]
        assert live == "v2"
    finally:
        conn.close()


def test_ensure_never_touches_runtime_state(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    conn = connect(get_db_path(ws))
    try:
        _scaffold(conn)
        ensure(conn, "member",
               {"id": "MEM-001", "firm_id": "f1", "name": "M",
                "role": "worker", "status": "active"}, quiet=True)
        ensure(conn, "unit", _unit(), resync=("description",), quiet=True)
        # A member claimed it and started work mid-flight.
        conn.execute("UPDATE unit SET status = 'in_progress', "
                     "claimed_by = 'MEM-001' WHERE id = 'UNT-001'")
        # Re-seed with amended definition AND a stale status in the data dict.
        ensure(conn, "unit", _unit("v2", status="pending"),
               resync=("description",), quiet=True)
        row = conn.execute(
            "SELECT description, status, claimed_by FROM unit "
            "WHERE id = 'UNT-001'").fetchone()
        assert row[0] == "v2"                 # definition amended
        assert row[1] == "in_progress"        # runtime state untouched
        assert row[2] == "MEM-001"
    finally:
        conn.close()


def test_ensure_refuses_runtime_fields_in_resync(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    conn = connect(get_db_path(ws))
    try:
        _scaffold(conn)
        for field in sorted(RUNTIME_FIELDS):
            with pytest.raises(ValueError, match="runtime state"):
                ensure(conn, "unit", _unit(), resync=(field,), quiet=True)
    finally:
        conn.close()


def test_ensure_norm_treats_json_string_and_parsed_as_equal(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    conn = connect(get_db_path(ws))
    try:
        _scaffold(conn)
        data = dict(_unit(), acceptance_criteria=json.dumps(["a", "b"]))
        ensure(conn, "unit", data, resync=("acceptance_criteria",), quiet=True)
        # Same value, different spelling (parsed vs string) → no phantom resync.
        data2 = dict(_unit(), acceptance_criteria=["a", "b"])
        assert ensure(conn, "unit", data2,
                      resync=("acceptance_criteria",), quiet=True) == "exists"
    finally:
        conn.close()


def test_seed_session_snapshots_and_commits(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    with seed_session(ws) as conn:
        _scaffold(conn)
        ensure(conn, "unit", _unit(), resync=("description",), quiet=True)

    snaps = list((ws / ".firm" / "snapshots").glob("*-pre-seed.json"))
    assert len(snaps) == 1, "every seed run must leave a pre-seed snapshot"

    conn = connect(get_db_path(ws))
    try:
        assert conn.execute("SELECT COUNT(*) FROM unit").fetchone()[0] == 1
    finally:
        conn.close()


def test_seed_session_snapshot_predates_all_seed_writes(tmp_path: Path) -> None:
    # repo commits per call, so rollback can't save a dying seed — the
    # pre-seed snapshot is the recovery path and must land BEFORE any write.
    ws = _ws(tmp_path)
    conn = connect(get_db_path(ws))
    try:
        _scaffold(conn)
        ensure(conn, "unit", _unit("the Board-locked original"),
               resync=("description",), quiet=True)
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError):
        with seed_session(ws) as conn:
            ensure(conn, "unit", _unit("clobbered by a bad seed"),
                   resync=("description",), quiet=True)
            raise RuntimeError("mid-seed explosion")

    snaps = sorted((ws / ".firm" / "snapshots").glob("*-pre-seed.json"))
    assert snaps, "the snapshot must exist even though the seed died"
    saved = json.loads(snaps[-1].read_text())["entities"]["unit"][0]["description"]
    assert saved == "the Board-locked original"   # recoverable from the diff
