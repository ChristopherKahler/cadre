"""Tests for ``cadre roll`` — parsing, RNG bounds, Records persistence."""

from __future__ import annotations

import json

import pytest

from firm.cli.roll import parse_dice, run_roll
from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations
from firm.core.repo import create, find


def _init_workspace(tmp_path, firm_count: int = 1):
    conn = connect(get_db_path(tmp_path))
    apply_migrations(conn)
    for i in range(firm_count):
        fid = "dnd-table" if i == 0 else f"other-{i}"
        create(conn, "firm", {"id": fid, "name": "The Table"})
    conn.commit()
    conn.close()


def test_parse_dice_forms():
    assert parse_dice("1d20") == (1, 20, 0)
    assert parse_dice("2d6+3") == (2, 6, 3)
    assert parse_dice("4d8 - 2") == (4, 8, -2)
    assert parse_dice("1D12+0") == (1, 12, 0)


@pytest.mark.parametrize("bad", ["", "d20", "1d1", "0d6", "1d20+5+2", "20", "1d20x3"])
def test_parse_dice_rejects(bad):
    with pytest.raises(ValueError):
        parse_dice(bad)


def test_roll_writes_record_and_reports(tmp_path, capsys):
    _init_workspace(tmp_path)
    rc = run_roll(tmp_path, "1d20+5", reason="Fen: Sleight of Hand", member_id="MEM-002")
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["record_id"].startswith("LOG-")
    assert out["total"] == out["rolls"][0] + 5
    assert 1 <= out["rolls"][0] <= 20

    conn = connect(get_db_path(tmp_path))
    try:
        recs = find(conn, "records", firm_id="dnd-table")
        rolls = [r for r in recs if r["event_type"] == "game.roll"]
        assert len(rolls) == 1
        details = rolls[0]["details"]  # repo layer deserializes JSON columns
        assert details["total"] == out["total"]
        assert details["reason"] == "Fen: Sleight of Hand"
        assert rolls[0]["actor_type"] == "member"
        assert rolls[0]["actor_id"] == "MEM-002"
    finally:
        conn.close()


def test_roll_rng_bounds(tmp_path, capsys):
    _init_workspace(tmp_path)
    for _ in range(30):
        assert run_roll(tmp_path, "3d6", reason="bounds") == 0
        out = json.loads(capsys.readouterr().out)
        assert len(out["rolls"]) == 3
        assert all(1 <= r <= 6 for r in out["rolls"])
        assert out["total"] == sum(out["rolls"])


def test_roll_advantage_keeps_higher(tmp_path, capsys):
    _init_workspace(tmp_path)
    assert run_roll(tmp_path, "1d20", reason="adv", advantage=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "advantage"
    totals = [a["total"] for a in out["attempts"]]
    assert out["total"] == max(totals)


def test_roll_adv_dis_exclusive(tmp_path, capsys):
    _init_workspace(tmp_path)
    rc = run_roll(tmp_path, "1d20", reason="x", advantage=True, disadvantage=True)
    assert rc == 1
    err = json.loads(capsys.readouterr().err)
    assert "exclusive" in err["message"]


def test_roll_requires_single_firm(tmp_path, capsys):
    _init_workspace(tmp_path, firm_count=2)
    rc = run_roll(tmp_path, "1d20", reason="x")
    assert rc == 1
    err = json.loads(capsys.readouterr().err)
    assert "exactly one firm" in err["message"]


def test_roll_no_db(tmp_path, capsys):
    rc = run_roll(tmp_path / "nowhere", "1d20", reason="x")
    assert rc == 1
    err = json.loads(capsys.readouterr().err)
    assert err["reason"] == "db-not-found"
