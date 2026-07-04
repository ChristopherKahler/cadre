"""Tests for firm.pulse.orchestrator — PULSE pre-flight pipeline and activation loop."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, get, update
from firm.pulse.orchestrator import (
    ActivationSummary,
    check_business_hours,
    check_frequency_gate,
    compute_load,
    filter_members,
    gather_active_members,
    pulse,
    reap_stale_runs,
    topo_sort_members,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


def _add_member(
    conn: sqlite3.Connection,
    member_id: str,
    *,
    status: str = "active",
    frequency: int | None = None,
    last_activated: str | None = None,
) -> dict:
    return create(conn, "member", {
        "id": member_id,
        "firm_id": "chrisai",
        "name": f"Member {member_id}",
        "role": "worker",
        "status": status,
        "frequency": frequency,
        "last_activated": last_activated,
    })


def _add_unit(
    conn: sqlite3.Connection,
    unit_id: str,
    project_id: str,
    *,
    claimed_by: str | None = None,
    status: str = "pending",
    depends_on: list[str] | None = None,
) -> dict:
    return create(conn, "unit", {
        "id": unit_id,
        "firm_id": "chrisai",
        "project_id": project_id,
        "name": f"Unit {unit_id}",
        "status": status,
        "claimed_by": claimed_by,
        "depends_on": depends_on or [],
    })


def _add_project(conn: sqlite3.Connection, project_id: str) -> dict:
    op = create(conn, "operation", {
        "id": f"op-{project_id}",
        "firm_id": "chrisai",
        "name": f"Op for {project_id}",
        "status": "active",
    })
    return create(conn, "project", {
        "id": project_id,
        "firm_id": "chrisai",
        "operation_id": op["id"],
        "name": f"Project {project_id}",
        "status": "in_progress",
        "due_date": "2026-12-31",
    })


def _noop_callback(conn: sqlite3.Connection, member: dict) -> dict:
    return {"ok": True, "member_id": member["id"]}


def _failing_callback(conn: sqlite3.Connection, member: dict) -> dict:
    raise RuntimeError(f"Simulated failure for {member['id']}")


# ---------------------------------------------------------------------------
# gather_active_members
# ---------------------------------------------------------------------------

class TestGatherActiveMembers:

    def test_returns_active_only(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001", status="active")
        _add_member(conn, "MEM-002", status="active")
        _add_member(conn, "MEM-003", status="paused")

        result = gather_active_members(conn, "chrisai")
        ids = [m["id"] for m in result]
        assert "MEM-001" in ids
        assert "MEM-002" in ids
        assert "MEM-003" not in ids

    def test_empty_when_no_active(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001", status="paused")

        assert gather_active_members(conn, "chrisai") == []


# ---------------------------------------------------------------------------
# compute_load
# ---------------------------------------------------------------------------

class TestComputeLoad:

    def test_zero_when_no_units(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001")
        assert compute_load(conn, "MEM-001") == 0

    def test_counts_pending_and_in_progress(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001")
        proj = _add_project(conn, "PROJ-001")
        _add_unit(conn, "UNIT-001", "PROJ-001", claimed_by="MEM-001", status="pending")
        _add_unit(conn, "UNIT-002", "PROJ-001", claimed_by="MEM-001", status="in_progress")
        _add_unit(conn, "UNIT-003", "PROJ-001", claimed_by="MEM-001", status="done")

        assert compute_load(conn, "MEM-001") == 2

    def test_ignores_other_members(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001")
        _add_member(conn, "MEM-002")
        _add_project(conn, "PROJ-001")
        _add_unit(conn, "UNIT-001", "PROJ-001", claimed_by="MEM-002", status="pending")

        assert compute_load(conn, "MEM-001") == 0


# ---------------------------------------------------------------------------
# check_frequency_gate
# ---------------------------------------------------------------------------

class TestFrequencyGate:

    def test_no_throttle_when_frequency_none(self):
        assert check_frequency_gate({"frequency": None, "last_activated": None}) is True

    def test_no_throttle_when_frequency_zero(self):
        assert check_frequency_gate({"frequency": 0, "last_activated": None}) is True

    def test_eligible_when_never_activated(self):
        assert check_frequency_gate({"frequency": 3600, "last_activated": None}) is True

    def test_eligible_when_enough_time_passed(self):
        now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
        two_hours_ago = (now - timedelta(hours=2)).isoformat()
        member = {"frequency": 3600, "last_activated": two_hours_ago}
        assert check_frequency_gate(member, now=now) is True

    def test_too_soon(self):
        now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
        ten_min_ago = (now - timedelta(minutes=10)).isoformat()
        member = {"frequency": 3600, "last_activated": ten_min_ago}
        assert check_frequency_gate(member, now=now) is False

    def test_unparseable_last_activated_passes(self):
        assert check_frequency_gate({"frequency": 3600, "last_activated": "garbage"}) is True


# ---------------------------------------------------------------------------
# check_business_hours
# ---------------------------------------------------------------------------

class TestBusinessHours:

    def test_open_when_no_schedule(self):
        conn = _fresh_conn()
        assert check_business_hours(conn, "chrisai") is True

    def test_open_when_override_open(self):
        conn = _fresh_conn()
        update(conn, "firm", "chrisai", {
            "schedule": {"timezone": "UTC", "business_hours": {"start": "09:00", "end": "17:00", "days": ["mon"]}, "override_open": True},
        })
        # Sunday outside hours, but override_open is True
        sunday = datetime(2026, 4, 19, 22, 0, 0, tzinfo=timezone.utc)
        assert check_business_hours(conn, "chrisai", now=sunday) is True

    def test_within_hours(self):
        conn = _fresh_conn()
        update(conn, "firm", "chrisai", {
            "schedule": {"timezone": "UTC", "business_hours": {"start": "09:00", "end": "17:00", "days": ["mon", "tue", "wed", "thu", "fri"]}, "override_open": False},
        })
        # Wednesday 12:00 UTC
        wed_noon = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        assert check_business_hours(conn, "chrisai", now=wed_noon) is True

    def test_outside_hours(self):
        conn = _fresh_conn()
        update(conn, "firm", "chrisai", {
            "schedule": {"timezone": "UTC", "business_hours": {"start": "09:00", "end": "17:00", "days": ["mon", "tue", "wed", "thu", "fri"]}, "override_open": False},
        })
        # Wednesday 22:00 UTC
        wed_late = datetime(2026, 4, 15, 22, 0, 0, tzinfo=timezone.utc)
        assert check_business_hours(conn, "chrisai", now=wed_late) is False

    def test_weekend_rejected(self):
        conn = _fresh_conn()
        update(conn, "firm", "chrisai", {
            "schedule": {"timezone": "UTC", "business_hours": {"start": "09:00", "end": "17:00", "days": ["mon", "tue", "wed", "thu", "fri"]}, "override_open": False},
        })
        # Saturday 12:00 UTC
        sat_noon = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
        assert check_business_hours(conn, "chrisai", now=sat_noon) is False


# ---------------------------------------------------------------------------
# filter_members
# ---------------------------------------------------------------------------

class TestFilterMembers:

    def test_filters_by_load_and_frequency(self):
        conn = _fresh_conn()
        now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)

        _add_member(conn, "MEM-001")  # active, no units → load=0 → skipped
        _add_member(conn, "MEM-002")  # active, has unit → eligible
        _add_member(conn, "MEM-003", frequency=3600, last_activated=(now - timedelta(minutes=10)).isoformat())  # too soon

        _add_project(conn, "PROJ-001")
        _add_unit(conn, "UNIT-001", "PROJ-001", claimed_by="MEM-002", status="pending")
        _add_unit(conn, "UNIT-002", "PROJ-001", claimed_by="MEM-003", status="pending")

        eligible, skipped = filter_members(conn, "chrisai", now=now)

        eligible_ids = [m["id"] for m in eligible]
        skipped_reasons = {s["member"]["id"]: s["reason"] for s in skipped}

        assert eligible_ids == ["MEM-002"]
        assert "MEM-001" in skipped_reasons
        assert "load=0" in skipped_reasons["MEM-001"]
        assert "MEM-003" in skipped_reasons
        assert "frequency" in skipped_reasons["MEM-003"]


# ---------------------------------------------------------------------------
# topo_sort_members
# ---------------------------------------------------------------------------

class TestTopoSort:

    def test_independent_members_stable_by_id(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-002")
        _add_member(conn, "MEM-001")
        _add_project(conn, "PROJ-001")
        _add_unit(conn, "UNIT-001", "PROJ-001", claimed_by="MEM-001", status="pending")
        _add_unit(conn, "UNIT-002", "PROJ-001", claimed_by="MEM-002", status="pending")

        members = [get(conn, "member", "MEM-002"), get(conn, "member", "MEM-001")]
        sorted_m, blocked = topo_sort_members(conn, members)

        assert [m["id"] for m in sorted_m] == ["MEM-001", "MEM-002"]
        assert blocked == []

    def test_dependency_ordering(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-A")
        _add_member(conn, "MEM-B")
        _add_project(conn, "PROJ-001")
        _add_unit(conn, "UNIT-001", "PROJ-001", claimed_by="MEM-A", status="pending")
        _add_unit(conn, "UNIT-002", "PROJ-001", claimed_by="MEM-B", status="pending", depends_on=["UNIT-001"])

        members = [get(conn, "member", "MEM-B"), get(conn, "member", "MEM-A")]
        sorted_m, blocked = topo_sort_members(conn, members)

        ids = [m["id"] for m in sorted_m]
        assert ids.index("MEM-A") < ids.index("MEM-B")
        assert blocked == []

    def test_fully_blocked_member_skipped(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-A")
        _add_member(conn, "MEM-B")
        _add_member(conn, "MEM-C", status="paused")  # NOT eligible
        _add_project(conn, "PROJ-001")
        # MEM-A has a unit with no deps
        _add_unit(conn, "UNIT-001", "PROJ-001", claimed_by="MEM-A", status="pending")
        # UNIT-003 owned by paused MEM-C (not eligible this pulse)
        _add_unit(conn, "UNIT-003", "PROJ-001", claimed_by="MEM-C", status="pending")
        # MEM-B depends on UNIT-003 (owned by non-eligible member) → truly blocked
        _add_unit(conn, "UNIT-002", "PROJ-001", claimed_by="MEM-B", status="pending", depends_on=["UNIT-003"])

        # Only pass eligible members (MEM-A and MEM-B) — MEM-C is not eligible
        members = [get(conn, "member", "MEM-A"), get(conn, "member", "MEM-B")]
        sorted_m, blocked = topo_sort_members(conn, members)

        assert [m["id"] for m in sorted_m] == ["MEM-A"]
        assert len(blocked) == 1
        assert blocked[0]["member"]["id"] == "MEM-B"

    def test_empty_members_returns_empty(self):
        conn = _fresh_conn()
        sorted_m, blocked = topo_sort_members(conn, [])
        assert sorted_m == []
        assert blocked == []


# ---------------------------------------------------------------------------
# pulse() — integration
# ---------------------------------------------------------------------------

class TestPulse:

    def test_two_members_both_succeed(self):
        conn = _fresh_conn()
        now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
        _add_member(conn, "MEM-001")
        _add_member(conn, "MEM-002")
        _add_project(conn, "PROJ-001")
        _add_unit(conn, "UNIT-001", "PROJ-001", claimed_by="MEM-001", status="pending")
        _add_unit(conn, "UNIT-002", "PROJ-001", claimed_by="MEM-002", status="pending")

        summary = pulse(conn, "chrisai", _noop_callback, now=now)

        assert len(summary.ran) == 2
        assert len(summary.errors) == 0
        assert summary.dry_run is False

    def test_one_succeeds_one_fails(self):
        conn = _fresh_conn()
        now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
        _add_member(conn, "MEM-001")
        _add_member(conn, "MEM-002")
        _add_project(conn, "PROJ-001")
        _add_unit(conn, "UNIT-001", "PROJ-001", claimed_by="MEM-001", status="pending")
        _add_unit(conn, "UNIT-002", "PROJ-001", claimed_by="MEM-002", status="pending")

        call_count = 0
        def mixed_callback(conn, member):
            nonlocal call_count
            call_count += 1
            if member["id"] == "MEM-002":
                raise RuntimeError("Simulated failure")
            return {"ok": True}

        summary = pulse(conn, "chrisai", mixed_callback, now=now)

        assert len(summary.ran) == 1
        assert len(summary.errors) == 1
        assert summary.errors[0]["error_type"] == "RuntimeError"
        assert call_count == 2  # Both were attempted

    def test_dry_run_no_callback(self):
        conn = _fresh_conn()
        now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
        _add_member(conn, "MEM-001")
        _add_project(conn, "PROJ-001")
        _add_unit(conn, "UNIT-001", "PROJ-001", claimed_by="MEM-001", status="pending")

        call_count = 0
        def tracking_callback(conn, member):
            nonlocal call_count
            call_count += 1
            return {"ok": True}

        summary = pulse(conn, "chrisai", tracking_callback, dry_run=True, now=now)

        assert len(summary.ran) == 1
        assert summary.dry_run is True
        assert call_count == 0  # Callback never called

    def test_outside_business_hours_skips_all(self):
        conn = _fresh_conn()
        update(conn, "firm", "chrisai", {
            "schedule": {"timezone": "UTC", "business_hours": {"start": "09:00", "end": "17:00", "days": ["mon", "tue", "wed", "thu", "fri"]}, "override_open": False},
        })
        _add_member(conn, "MEM-001")
        _add_project(conn, "PROJ-001")
        _add_unit(conn, "UNIT-001", "PROJ-001", claimed_by="MEM-001", status="pending")

        # Saturday late night
        sat_night = datetime(2026, 4, 18, 23, 0, 0, tzinfo=timezone.utc)
        summary = pulse(conn, "chrisai", _noop_callback, now=sat_night)

        assert len(summary.ran) == 0
        assert len(summary.skipped) == 1
        assert "business hours" in summary.skipped[0]["reason"]

    def test_last_activated_updated_on_success(self):
        conn = _fresh_conn()
        now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
        _add_member(conn, "MEM-001")
        _add_project(conn, "PROJ-001")
        _add_unit(conn, "UNIT-001", "PROJ-001", claimed_by="MEM-001", status="pending")

        # Confirm last_activated is None before pulse
        before = get(conn, "member", "MEM-001")
        assert before["last_activated"] is None

        pulse(conn, "chrisai", _noop_callback, now=now)

        after = get(conn, "member", "MEM-001")
        assert after["last_activated"] is not None
        assert now.isoformat() in after["last_activated"]

    def test_no_eligible_members_returns_empty(self):
        conn = _fresh_conn()
        now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
        _add_member(conn, "MEM-001", status="paused")

        summary = pulse(conn, "chrisai", _noop_callback, now=now)

        assert len(summary.ran) == 0
        assert len(summary.errors) == 0


# ---------------------------------------------------------------------------
# reap_stale_runs — zombie 'running' rows (ESC-D)
# ---------------------------------------------------------------------------

def _add_run(
    conn: sqlite3.Connection,
    run_id: str,
    member_id: str,
    unit_id: str,
    *,
    age_sec: int,
    status: str = "running",
) -> dict:
    started = datetime.now(tz=timezone.utc) - timedelta(seconds=age_sec)
    return create(conn, "member_run", {
        "id": run_id,
        "firm_id": "chrisai",
        "member_id": member_id,
        "unit_id": unit_id,
        "status": status,
        "started_at": started.isoformat(),
        "invocation_source": "pulse",
    })


class TestReapStaleRuns:

    def _seed(self, conn):
        _add_member(conn, "MEM-001")
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by="MEM-001")

    def test_stale_running_row_reaped_as_orphaned(self):
        conn = _fresh_conn()
        self._seed(conn)
        # Default timeout 300 → max lifetime 2*300+600 = 1200s; 2h is dead.
        _add_run(conn, "RUN-001", "MEM-001", "UNT-001", age_sec=7200)

        reaped = reap_stale_runs(conn, "chrisai")

        assert [r["run_id"] for r in reaped] == ["RUN-001"]
        row = get(conn, "member_run", "RUN-001")
        assert row["status"] == "failed"
        assert row["ended_at"] is not None
        assert json.loads(row["error"])["type"] == "orphaned"

    def test_fresh_running_row_left_alone(self):
        conn = _fresh_conn()
        self._seed(conn)
        _add_run(conn, "RUN-001", "MEM-001", "UNT-001", age_sec=60)

        reaped = reap_stale_runs(conn, "chrisai")

        assert reaped == []
        assert get(conn, "member_run", "RUN-001")["status"] == "running"

    def test_closed_rows_never_touched(self):
        conn = _fresh_conn()
        self._seed(conn)
        _add_run(conn, "RUN-001", "MEM-001", "UNT-001", age_sec=99999, status="completed")

        assert reap_stale_runs(conn, "chrisai") == []
        assert get(conn, "member_run", "RUN-001")["status"] == "completed"

    def test_contract_timeout_extends_lifetime(self):
        conn = _fresh_conn()
        contract = create(conn, "contract", {
            "id": "CON-001",
            "firm_id": "chrisai",
            "name": "Long contract",
            "runtime_type": "claude_code",
            "pulse_config": json.dumps({"timeout_sec": 1800}),
        })
        member = create(conn, "member", {
            "id": "MEM-001",
            "firm_id": "chrisai",
            "name": "Member MEM-001",
            "role": "worker",
            "status": "active",
            "contract_id": contract["id"],
        })
        _add_project(conn, "PRJ-001")
        _add_unit(conn, "UNT-001", "PRJ-001", claimed_by=member["id"])
        # Lifetime = 2*1800+600 = 4200s: 3000s-old row is plausibly alive,
        # 5000s-old row is dead.
        _add_run(conn, "RUN-001", "MEM-001", "UNT-001", age_sec=3000)
        _add_run(conn, "RUN-002", "MEM-001", "UNT-001", age_sec=5000)

        reaped = reap_stale_runs(conn, "chrisai")

        assert [r["run_id"] for r in reaped] == ["RUN-002"]
        assert get(conn, "member_run", "RUN-001")["status"] == "running"
        assert get(conn, "member_run", "RUN-002")["status"] == "failed"

    def test_write_false_detects_without_writing(self):
        conn = _fresh_conn()
        self._seed(conn)
        _add_run(conn, "RUN-001", "MEM-001", "UNT-001", age_sec=7200)

        reaped = reap_stale_runs(conn, "chrisai", write=False)

        assert [r["run_id"] for r in reaped] == ["RUN-001"]
        assert get(conn, "member_run", "RUN-001")["status"] == "running"

    def test_pulse_reaps_before_gates(self):
        conn = _fresh_conn()
        self._seed(conn)
        _add_run(conn, "RUN-001", "MEM-001", "UNT-001", age_sec=7200)

        summary = pulse(conn, "chrisai", _noop_callback)

        assert [r["run_id"] for r in summary.reaped] == ["RUN-001"]
        reaped_row = get(conn, "member_run", "RUN-001")
        assert reaped_row["status"] == "failed"
        assert json.loads(reaped_row["error"])["type"] == "orphaned"

    def test_dry_run_pulse_reports_but_does_not_write(self):
        conn = _fresh_conn()
        self._seed(conn)
        _add_run(conn, "RUN-001", "MEM-001", "UNT-001", age_sec=7200)

        summary = pulse(conn, "chrisai", _noop_callback, dry_run=True)

        assert [r["run_id"] for r in summary.reaped] == ["RUN-001"]
        assert get(conn, "member_run", "RUN-001")["status"] == "running"
