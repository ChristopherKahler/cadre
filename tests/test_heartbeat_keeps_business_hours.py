"""Turning the pulse on or off must never touch the firm's business hours.

Issue #134. One column, ``firm.schedule``, held two different things: the
business hours the pulse gate reads (``check_business_hours``) and the pulse
interval. ``firm heartbeat enable`` wrote ``"30m"`` over the hours,
``heartbeat disable`` wrote NULL over them, and ``doctor --fix`` did either one
to make the row agree with the timer. The gate reads both values as "always
open", so a firm with Mon-Fri hours started pulsing at 03:00 on a Sunday and
nothing said so.

The interval now has its own column, ``firm.pulse_interval`` (migration 015).
The heartbeat, the doctor's timer card and the hub card use it, and ``schedule``
means business hours only. A firm that was enabled before 015 keeps its
interval (015 copies it across) and gets a doctor card telling the Board the
hours it had were overwritten, because the database keeps no history to
restore them from.

Arms are named for the #134 G0 table. Every R arm failed on main 1c870248
before the fix; the C arms are controls that pass on both trees.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import firm.cli.heartbeat as hb
import firm.sched.systemd as sysd
from firm.cli import doctor as doctor_mod
from firm.core.db import get_db_path
from firm.core.migrate import (
    _default_migrations_dir,
    apply_migrations,
    discover_migrations,
)
from firm.core.repo import create
from firm.dashboard import founding
from firm.dashboard.server import discover_firms, hub_summary
from firm.pulse.orchestrator import check_business_hours
from firm.pulse.prompt import _render_system_context
from firm.sched.base import interval_to_seconds
from firm.sched.systemd import SystemdScheduler

FIRM = "hourly"

#: Mon-Fri 09:00-17:00 UTC, the shape 003_pulse.sql documents for the column.
HOURS = {
    "timezone": "UTC",
    "business_hours": {"start": "09:00", "end": "17:00",
                       "days": ["mon", "tue", "wed", "thu", "fri"]},
}

#: A Sunday, 03:00 UTC: outside HOURS by construction, so a firm with HOURS must
#: read CLOSED here. Every arm that asserts CLOSED asserts it before the act too,
#: so a gate that could never say no cannot pass one.
SUNDAY_3AM = datetime(2026, 9, 13, 3, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _local_database(monkeypatch):
    """``connect`` goes remote when CADRE_DB_URL is set; these arms read files."""
    monkeypatch.delenv("CADRE_DB_URL", raising=False)


@pytest.fixture
def units(tmp_path, monkeypatch):
    """Every scheduler call lands in a temp unit directory; systemctl succeeds.

    ``founding.set_pulse`` calls ``run_enable`` and ``run_disable`` with no
    ``unit_dir``, so the scheduler is pinned where ``_sched`` resolves it, not
    only by argument.
    """
    unit_dir = tmp_path / "units"
    monkeypatch.setattr(sysd, "run_cmd", lambda argv, timeout=30: (0, ""))
    monkeypatch.setattr(hb, "resolve_scheduler",
                        lambda: SystemdScheduler(unit_dir=unit_dir))
    monkeypatch.setattr(hb, "resolve_claude_bin",
                        lambda: ("/usr/bin/claude", "test"))
    return unit_dir


def _firm(ws: Path, firm_id: str = FIRM, **fields) -> Path:
    """A real firm workspace: a migrated database holding one firm row."""
    db = get_db_path(ws)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        apply_migrations(conn)
        create(conn, "firm", {"id": firm_id, "name": f"Firm {firm_id}", **fields})
    finally:
        conn.close()
    return ws


def _raw(ws: Path, firm_id: str = FIRM) -> dict:
    """The firm row as stored, bypassing the repo's JSON decoding."""
    conn = sqlite3.connect(get_db_path(ws))
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM firm WHERE id = ?",
                                 (firm_id,)).fetchone())
    finally:
        conn.close()


def _gate_open(ws: Path, firm_id: str = FIRM) -> bool:
    conn = sqlite3.connect(get_db_path(ws))
    conn.row_factory = sqlite3.Row
    try:
        return check_business_hours(conn, firm_id, now=SUNDAY_3AM)
    finally:
        conn.close()


def _install_units(unit_dir: Path, ws: Path, interval: str = "30m") -> None:
    """An installed heartbeat, in the two files SystemdScheduler.status reads."""
    unit_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{hb._UNIT_PREFIX}{FIRM}"
    (unit_dir / f"{stem}.timer").write_text(
        f"[Timer]\nOnUnitActiveSec={interval}\n", encoding="utf-8")
    (unit_dir / f"{stem}.service").write_text(
        f"[Service]\nWorkingDirectory={ws}\n", encoding="utf-8")


def _card(ws: Path, unit_dir: Path, key: str) -> dict:
    cards = [c for c in doctor_mod.diagnose(ws, FIRM, unit_dir=unit_dir)
             if c["key"] == key]
    assert len(cards) == 1, f"expected one doctor card keyed {key!r}, found {len(cards)}"
    return cards[0]


def _hub_card(root: Path) -> dict:
    cards = hub_summary(discover_firms(root))["firms"]
    assert len(cards) == 1, f"expected one hub card, found {len(cards)}"
    return cards[0]


# ---------------------------------------------------------------------------
# R1-R2: the heartbeat verbs
# ---------------------------------------------------------------------------

def test_r1_enable_keeps_business_hours_and_records_the_interval(
        tmp_path, units, capsys):
    ws = _firm(tmp_path / FIRM, schedule=HOURS)
    hours = _raw(ws)["schedule"]
    assert _gate_open(ws) is False, "control: the gate must be able to say no"

    assert hb.run_enable(ws, FIRM, "30m", unit_dir=units) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["schedule_recorded"] is True, out
    row = _raw(ws)
    assert row["schedule"] == hours, "enable wrote over the business hours"
    assert row["pulse_interval"] == "30m"
    assert _gate_open(ws) is False, "the business-hours gate opened"


def test_r2_disable_keeps_business_hours(tmp_path, units, capsys):
    ws = _firm(tmp_path / FIRM, schedule=HOURS)
    _install_units(units, ws)
    hours = _raw(ws)["schedule"]
    assert _gate_open(ws) is False, "control: the gate must be able to say no"

    assert hb.run_disable(FIRM, unit_dir=units) == 0

    assert json.loads(capsys.readouterr().out)["schedule_recorded"] is True
    assert _raw(ws)["schedule"] == hours, "disable wrote over the business hours"
    assert _gate_open(ws) is False, "the business-hours gate opened"


def test_r2_disable_clears_the_interval(tmp_path, units, capsys):
    ws = _firm(tmp_path / FIRM, schedule=HOURS, pulse_interval="30m")
    _install_units(units, ws)
    hours = _raw(ws)["schedule"]

    assert hb.run_disable(FIRM, unit_dir=units) == 0

    row = _raw(ws)
    assert row["pulse_interval"] is None
    assert row["schedule"] == hours


# ---------------------------------------------------------------------------
# R3: doctor --fix reconciles the interval, never the hours
# ---------------------------------------------------------------------------

def test_r3_doctor_fix_leaves_business_hours_on_a_firm_with_no_timer(
        tmp_path, units):
    """On main this is the destructive path: the timer card read the hours as
    a cadence, found no timer, and --fix reconciled the row by writing NULL."""
    ws = _firm(tmp_path / FIRM, schedule=HOURS)
    hours = _raw(ws)["schedule"]
    card = _card(ws, units, "schedule")

    fixed = doctor_mod.fix(ws, FIRM, [card], unit_dir=units)

    assert _raw(ws)["schedule"] == hours, f"doctor --fix wrote over the hours: {fixed}"
    assert card["ok"] is True, card


def test_r3_doctor_fix_records_the_interval_the_timer_runs(tmp_path, units):
    ws = _firm(tmp_path / FIRM, schedule=HOURS)
    _install_units(units, ws, "30m")
    hours = _raw(ws)["schedule"]
    card = _card(ws, units, "schedule")
    # A timer runs and no interval is recorded. Business hours are not a cadence.
    assert card["ok"] is False, card

    fixed = doctor_mod.fix(ws, FIRM, [card], unit_dir=units)

    row = _raw(ws)
    assert row["pulse_interval"] == "30m"
    assert row["schedule"] == hours
    assert fixed == ["pulse_interval: reconciled to '30m'"]


def test_r3_doctor_fix_clears_an_interval_with_no_timer(tmp_path, units):
    ws = _firm(tmp_path / FIRM, schedule=HOURS, pulse_interval="30m")
    hours = _raw(ws)["schedule"]
    card = _card(ws, units, "schedule")
    assert card["ok"] is False, card

    doctor_mod.fix(ws, FIRM, [card], unit_dir=units)

    row = _raw(ws)
    assert row["pulse_interval"] is None
    assert row["schedule"] == hours


# ---------------------------------------------------------------------------
# R4-R5: the hub
# ---------------------------------------------------------------------------

def test_r4_hub_pulse_toggle_keeps_business_hours(tmp_path, units):
    root = tmp_path / "firms"
    ws = _firm(root / FIRM, schedule=HOURS)
    hours = _raw(ws)["schedule"]
    assert _gate_open(ws) is False, "control: the gate must be able to say no"

    on = founding.set_pulse(root, FIRM, "30m", enable=True)
    assert on.get("ok") is True, on
    assert _raw(ws)["schedule"] == hours, "the hub's pulse switch wrote over the hours"
    assert _raw(ws)["pulse_interval"] == "30m"
    assert _gate_open(ws) is False

    off = founding.set_pulse(root, FIRM, "30m", enable=False)
    assert off.get("ok") is True, off
    assert _raw(ws)["schedule"] == hours, "switching the pulse off wrote over the hours"
    assert _raw(ws)["pulse_interval"] is None
    assert _gate_open(ws) is False


def test_r5_hub_card_is_not_operational_on_business_hours_alone(tmp_path):
    root = tmp_path / "firms"
    _firm(root / FIRM, schedule=HOURS)

    card = _hub_card(root)

    # Hours say when the firm may work, not that anything will wake it.
    assert card["operational"] is False, card
    assert card["schedule"] is None, card


def test_r5_hub_card_shows_the_pulse_interval_as_the_cadence(tmp_path):
    root = tmp_path / "firms"
    _firm(root / FIRM, schedule=HOURS, pulse_interval="30m")

    card = _hub_card(root)

    assert card["schedule"] == "30m", card
    assert card["operational"] is True, card


# ---------------------------------------------------------------------------
# R6: migration 015 and the doctor's business-hours card
# ---------------------------------------------------------------------------

#: What firm.schedule can hold before 015, and what 015 puts in pulse_interval.
#: C4 re-derives every expectation from interval_to_seconds, the grammar
#: `heartbeat enable` validates with, so this table cannot drift from the code.
SHAPES = [
    ("30m", "30m"), ("1800s", "1800s"), ("15min", "15min"), ("2h", "2h"),
    ("1d", "1d"), ("0m", "0m"), (" 45m\t", "45m"),
    ("30", None), ("m", None), ("min", None), ("s", None), ("", None),
    ("1h30m", None), ("monthly", None), ("30 m", None), ("30M", None),
    ("m30", None), ("5mmin", None), ("weekend", None),
    (json.dumps(HOURS), None),
]


def _db_at_014(path: Path) -> sqlite3.Connection:
    """A database migrated through 014 only: every firm's shape before #134."""
    older = path.parent / "migrations-through-014"
    older.mkdir()
    copied = 0
    for number, _name, source in discover_migrations(_default_migrations_dir()):
        if number <= 14:
            shutil.copy(source, older / source.name)
            copied += 1
    assert copied == 14, f"copied {copied} migrations, expected 001-014"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    apply_migrations(conn, older)
    return conn


def test_r6_migration_015_moves_an_interval_and_keeps_schedule(tmp_path):
    conn = _db_at_014(tmp_path / "firm.db")
    try:
        for i, (value, _moved) in enumerate(SHAPES):
            create(conn, "firm", {"id": f"f{i}", "name": f"f{i}", "schedule": value})
        create(conn, "firm", {"id": "no-schedule", "name": "no schedule"})
        before = {r["id"]: r["schedule"]
                  for r in conn.execute("SELECT id, schedule FROM firm")}

        applied = apply_migrations(conn)

        assert applied[:1] == ["015_pulse_interval"], applied
        rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM firm")}
    finally:
        conn.close()
    checked = 0
    for i, (value, moved) in enumerate(SHAPES):
        row = rows[f"f{i}"]
        assert row["schedule"] == before[f"f{i}"], f"015 changed schedule {value!r}"
        assert row["pulse_interval"] == moved, f"schedule {value!r}"
        checked += 1
    assert rows["no-schedule"]["pulse_interval"] is None
    assert checked == len(SHAPES) > 0


def test_r6_doctor_names_business_hours_lost_to_the_interval(tmp_path, units):
    checked = 0
    for label, schedule, named in (("plain", "30m", "'30m'"),
                                   ("padded", " 1800s\t", "'1800s'")):
        ws = _firm(tmp_path / label, schedule=schedule)
        card = _card(ws, units, "business-hours")
        assert card["ok"] is False, (label, card)
        assert card["route"] == "board", (label, card)
        assert named in card["detail"], (label, card)
        checked += 1
    assert checked == 2


def test_r6_doctor_business_hours_card_passes_real_hours_and_none(tmp_path, units):
    """The discriminating half of the card: it must not fire on every firm."""
    checked = 0
    for label, fields in (("hours", {"schedule": HOURS}), ("none", {})):
        ws = _firm(tmp_path / label, **fields)
        card = _card(ws, units, "business-hours")
        assert card["ok"] is True, (label, card)
        checked += 1
    assert checked == 2


# ---------------------------------------------------------------------------
# Controls: pass before and after the fix
# ---------------------------------------------------------------------------

def test_c1_a_firm_without_business_hours_stays_open(tmp_path, units, capsys):
    ws = _firm(tmp_path / FIRM)
    assert _gate_open(ws) is True

    assert hb.run_enable(ws, FIRM, "30m", unit_dir=units) == 0
    assert _gate_open(ws) is True

    assert hb.run_disable(FIRM, unit_dir=units) == 0
    assert _gate_open(ws) is True


def test_c2_migrations_leave_business_hours_alone(tmp_path):
    conn = _db_at_014(tmp_path / "firm.db")
    try:
        create(conn, "firm", {"id": FIRM, "name": "Firm hourly", "schedule": HOURS})
        read = "SELECT schedule FROM firm WHERE id = ?"
        before = conn.execute(read, (FIRM,)).fetchone()[0]

        apply_migrations(conn)

        assert conn.execute(read, (FIRM,)).fetchone()[0] == before
        assert check_business_hours(conn, FIRM, now=SUNDAY_3AM) is False
    finally:
        conn.close()


def test_c3_prompt_never_shows_an_interval_as_business_hours():
    rendered = {}
    for label, schedule in (("interval", "30m"), ("hours", HOURS)):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        apply_migrations(conn)
        create(conn, "firm", {"id": FIRM, "name": "Firm hourly", "schedule": schedule})
        rendered[label] = _render_system_context(conn, FIRM)
        conn.close()
    assert "Business hours:" not in rendered["interval"], rendered["interval"]
    # The control that the prompt CAN print the line, or the absence above is empty.
    assert "Business hours: 09:00 - 17:00 UTC" in rendered["hours"], rendered["hours"]


def test_c4_shape_table_is_the_grammar_heartbeat_enforces():
    checked = 0
    for value, moved in SHAPES:
        try:
            interval_to_seconds(value)
            accepted = True
        except ValueError:
            accepted = False
        assert accepted == (moved is not None), value
        if accepted:
            assert moved == value.strip(), value
        checked += 1
    assert checked == len(SHAPES) > 0
