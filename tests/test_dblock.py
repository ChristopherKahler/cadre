"""Tests for firm.pulse.dblock — the DB-row pulse lock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from firm.core.db import connect
from firm.core.migrate import apply_migrations
from firm.pulse import dblock


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "firm.db")
    apply_migrations(c)
    yield c
    c.close()


def test_acquire_release_cycle(conn):
    assert dblock.acquire(conn, "f1", "host-a:1:x")
    assert dblock.current_holder(conn, "f1") == "host-a:1:x"
    dblock.release(conn, "f1", "host-a:1:x")
    assert dblock.current_holder(conn, "f1") is None


def test_second_acquirer_refused_while_holder_alive(conn):
    assert dblock.acquire(conn, "f1", "host-a:1:x")
    assert not dblock.acquire(conn, "f1", "host-b:2:y")
    assert dblock.current_holder(conn, "f1") == "host-a:1:x"


def test_locks_are_per_firm(conn):
    assert dblock.acquire(conn, "f1", "host-a:1:x")
    assert dblock.acquire(conn, "f2", "host-b:2:y")


def test_stale_lock_is_stolen(conn):
    assert dblock.acquire(conn, "f1", "host-a:1:x")
    # holder dies: age its heartbeat past the TTL
    stale = (datetime.now(tz=timezone.utc)
             - timedelta(seconds=dblock.LOCK_TTL_SEC + 5)).isoformat()
    conn.execute("UPDATE pulse_lock SET heartbeat_at = ? WHERE firm_id = 'f1'",
                 (stale,))
    conn.commit()
    assert dblock.acquire(conn, "f1", "host-b:2:y")
    assert dblock.current_holder(conn, "f1") == "host-b:2:y"


def test_heartbeat_keeps_lock_fresh_and_detects_loss(conn):
    assert dblock.acquire(conn, "f1", "host-a:1:x")
    assert dblock.heartbeat(conn, "f1", "host-a:1:x")
    # after a steal, the old holder's heartbeat reports the loss
    stale = (datetime.now(tz=timezone.utc)
             - timedelta(seconds=dblock.LOCK_TTL_SEC + 5)).isoformat()
    conn.execute("UPDATE pulse_lock SET heartbeat_at = ? WHERE firm_id = 'f1'",
                 (stale,))
    conn.commit()
    assert dblock.acquire(conn, "f1", "host-b:2:y")
    assert not dblock.heartbeat(conn, "f1", "host-a:1:x")


def test_release_by_non_holder_is_noop(conn):
    assert dblock.acquire(conn, "f1", "host-a:1:x")
    dblock.release(conn, "f1", "host-b:2:y")
    assert dblock.current_holder(conn, "f1") == "host-a:1:x"
