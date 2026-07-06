"""Tests for firm.services.pulse_queue — the turn-request queue."""

from __future__ import annotations

import pytest

from firm.core.db import connect
from firm.core.migrate import apply_migrations
from firm.services import pulse_queue


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "firm.db")
    apply_migrations(c)
    yield c
    c.close()


def test_request_claim_complete_lifecycle(conn):
    req = pulse_queue.request_pulse(conn, "f1", requested_by="board",
                                    note="board move")
    conn.commit()
    assert req["id"] > 0
    assert pulse_queue.pending(conn, "f1")[0]["status"] == "pending"

    claimed = pulse_queue.claim_next(conn, "f1", "host-a:1:x")
    assert claimed and claimed["id"] == req["id"]
    assert claimed["requested_by"] == "board"
    assert pulse_queue.pending(conn, "f1")[0]["status"] == "claimed"

    pulse_queue.complete(conn, claimed["id"])
    assert pulse_queue.pending(conn, "f1") == []


def test_claims_are_fifo_and_exclusive(conn):
    r1 = pulse_queue.request_pulse(conn, "f1")
    r2 = pulse_queue.request_pulse(conn, "f1")
    conn.commit()
    c1 = pulse_queue.claim_next(conn, "f1", "claimer-a")
    c2 = pulse_queue.claim_next(conn, "f1", "claimer-b")
    assert c1["id"] == r1["id"] and c2["id"] == r2["id"]  # oldest first, no double-claim
    assert pulse_queue.claim_next(conn, "f1", "claimer-c") is None


def test_queue_scoped_per_firm(conn):
    pulse_queue.request_pulse(conn, "f1")
    conn.commit()
    assert pulse_queue.claim_next(conn, "f2", "x") is None
    assert pulse_queue.claim_next(conn, "f1", "x") is not None


def test_abandon_marks_and_clears_from_pending(conn):
    req = pulse_queue.request_pulse(conn, "f1")
    conn.commit()
    claimed = pulse_queue.claim_next(conn, "f1", "x")
    pulse_queue.abandon(conn, claimed["id"], note="lock wait timed out")
    assert pulse_queue.pending(conn, "f1") == []
    row = conn.execute("SELECT status, note FROM pulse_request WHERE id=?",
                       (req["id"],)).fetchone()
    assert row["status"] == "abandoned" and "timed out" in row["note"]
