"""Snapshots — the safety net under seed resyncs and bulk writes.

The 2026-07-12 loss: Board-locked unit text overwritten by a seed, with no
recovery path anywhere. These tests prove the snapshot makes that class of
loss a diff instead.
"""

from __future__ import annotations

import json
from pathlib import Path

from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations
from firm.core.snapshot import take


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / ".firm").mkdir(parents=True)
    conn = connect(get_db_path(ws))
    apply_migrations(conn)
    conn.execute("INSERT INTO firm (id, name) VALUES ('f1', 'F1')")
    conn.execute(
        "INSERT INTO operation (id, firm_id, name, status) "
        "VALUES ('OP-001', 'f1', 'Ops', 'active')")
    conn.execute(
        "INSERT INTO project (id, firm_id, operation_id, name, status, due_date) "
        "VALUES ('PRJ-001', 'f1', 'OP-001', 'P', 'in_progress', '2026-12-31')")
    conn.execute(
        "INSERT INTO unit (id, firm_id, project_id, name, description, status) "
        "VALUES ('UNT-001', 'f1', 'PRJ-001', 'U', "
        "'Board-locked: use Tiled+YATI', 'pending')")
    conn.commit()
    conn.close()
    return ws


def test_take_writes_parseable_snapshot_with_counts(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    conn = connect(get_db_path(ws))
    try:
        path = take(conn, ws, label="pre-seed")
    finally:
        conn.close()

    assert path.exists()
    assert path.parent == ws / ".firm" / "snapshots"
    data = json.loads(path.read_text())
    assert data["label"] == "pre-seed"
    assert data["counts"]["firm"] == 1
    assert data["counts"]["unit"] == 1
    assert data["entities"]["unit"][0]["id"] == "UNT-001"


def test_snapshot_skips_locks_and_keeps_secret_registry(tmp_path: Path) -> None:
    # firm_secret is a REGISTRY — no value column exists; values live only in
    # the Fernet vault file. The registry is recoverable state and belongs in
    # the snapshot. Locks and counters do not.
    ws = _workspace(tmp_path)
    conn = connect(get_db_path(ws))
    try:
        conn.execute(
            "INSERT INTO firm_secret (id, firm_id, name, source, env_var_name) "
            "VALUES ('SEC-001', 'f1', 'Slack bot token', 'env', 'CADRE_SLACK_TOKEN')")
        conn.commit()
        path = take(conn, ws)
    finally:
        conn.close()

    data = json.loads(path.read_text())
    assert data["entities"]["firm_secret"][0]["env_var_name"] == "CADRE_SLACK_TOKEN"
    assert "value" not in data["entities"]["firm_secret"][0]
    assert "pulse_lock" not in data["entities"]
    assert "firm_rev" not in data["entities"]


def test_bad_write_is_recoverable_from_snapshot(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    conn = connect(get_db_path(ws))
    try:
        path = take(conn, ws, label="pre-seed")
        # The loss scenario: a resync clobbers Board-locked definitional text.
        conn.execute(
            "UPDATE unit SET description = 'generic reseeded text' "
            "WHERE id = 'UNT-001'")
        conn.commit()
        live = conn.execute(
            "SELECT description FROM unit WHERE id = 'UNT-001'").fetchone()[0]
    finally:
        conn.close()

    assert live == "generic reseeded text"
    saved = json.loads(path.read_text())["entities"]["unit"][0]["description"]
    assert saved == "Board-locked: use Tiled+YATI"


def test_same_second_snapshots_never_clobber(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    conn = connect(get_db_path(ws))
    try:
        p1 = take(conn, ws, label="pre-seed")
        p2 = take(conn, ws, label="pre-seed")
    finally:
        conn.close()
    assert p1 != p2
    assert p1.exists() and p2.exists()


def test_cli_backup_smoke(tmp_path: Path, capsys) -> None:
    from firm.cli.backup import run_backup

    ws = _workspace(tmp_path)
    assert run_backup(ws, label="manual") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["rows"] >= 4
    assert (ws / out["snapshot"]).exists()


def test_cli_backup_no_db(tmp_path: Path, capsys) -> None:
    from firm.cli.backup import run_backup

    assert run_backup(tmp_path) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "db-not-found"
