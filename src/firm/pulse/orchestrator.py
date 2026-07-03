"""PULSE orchestrator — stateless pre-flight pipeline and activation loop.

The orchestrator decides WHICH Members activate and in WHAT order.  The
actual execution of a Member run is delegated to a ``run_member`` callback
so downstream plans (spawn, prompt, validation, budget) can be wired in
without modifying orchestrator logic.

All pre-flight gates are pure SQLite queries — zero tokens spent until
a Member is actually activated.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from firm.core import repo

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

RunMemberFn = Callable[[sqlite3.Connection, dict[str, Any]], dict[str, Any]]
"""Callback signature: (conn, member_dict) -> result_dict."""


@dataclasses.dataclass
class ActivationSummary:
    """Result of a ``pulse()`` invocation."""

    ran: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    skipped: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    errors: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Pre-flight: gather
# ---------------------------------------------------------------------------

def gather_active_members(
    conn: sqlite3.Connection, firm_id: str,
) -> list[dict[str, Any]]:
    """Return all Members with ``status='active'`` for *firm_id*."""
    return repo.find(conn, "member", firm_id=firm_id, status="active")


# ---------------------------------------------------------------------------
# Pre-flight: load
# ---------------------------------------------------------------------------

def compute_load(conn: sqlite3.Connection, member_id: str) -> int:
    """Count pending/in_progress Units claimed by *member_id*.

    A load of 0 means the Member has nothing queued — no point activating.
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM unit "
        "WHERE claimed_by = ? AND status IN ('pending', 'in_progress')",
        (member_id,),
    ).fetchone()
    return row[0] or 0


# ---------------------------------------------------------------------------
# Pre-flight: frequency gate
# ---------------------------------------------------------------------------

def check_frequency_gate(
    member: dict[str, Any], *, now: datetime | None = None,
) -> bool:
    """Return True if *member* is eligible to activate (frequency not exceeded).

    Rules:
    - frequency is None or 0  → always eligible (no throttle)
    - last_activated is None   → eligible (never activated)
    - elapsed >= frequency     → eligible
    - elapsed < frequency      → too soon, skip
    """
    freq = member.get("frequency")
    if not freq:
        return True

    last = member.get("last_activated")
    if not last:
        return True

    ref = now or datetime.now(tz=timezone.utc)
    try:
        last_dt = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        return True  # Unparseable → treat as never activated

    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)

    elapsed = (ref - last_dt).total_seconds()
    return elapsed >= freq


# ---------------------------------------------------------------------------
# Pre-flight: business hours gate
# ---------------------------------------------------------------------------

def check_business_hours(
    conn: sqlite3.Connection, firm_id: str, *, now: datetime | None = None,
) -> bool:
    """Return True if *now* falls within the Firm's business hours.

    If the Firm has no ``schedule`` column or ``override_open`` is True,
    returns True (always open).
    """
    firm = repo.get(conn, "firm", firm_id)
    if firm is None:
        return True  # No firm → no schedule → open

    schedule = firm.get("schedule")
    if not schedule:
        return True
    if isinstance(schedule, str):
        try:
            schedule = json.loads(schedule)
        except (json.JSONDecodeError, TypeError):
            return True

    if schedule.get("override_open"):
        return True

    bh = schedule.get("business_hours")
    if not bh:
        return True

    tz_name = schedule.get("timezone", "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except (KeyError, Exception):
        tz = timezone.utc

    ref = now or datetime.now(tz=timezone.utc)
    local = ref.astimezone(tz)

    # Day check
    allowed_days = bh.get("days", [])
    if allowed_days:
        day_name = local.strftime("%a").lower()
        if day_name not in allowed_days:
            return False

    # Time check
    start_str = bh.get("start", "00:00")
    end_str = bh.get("end", "23:59")
    try:
        start_h, start_m = (int(x) for x in start_str.split(":"))
        end_h, end_m = (int(x) for x in end_str.split(":"))
    except (ValueError, TypeError):
        return True

    local_minutes = local.hour * 60 + local.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m

    return start_minutes <= local_minutes < end_minutes


# ---------------------------------------------------------------------------
# Pre-flight: combined filter
# ---------------------------------------------------------------------------

def filter_members(
    conn: sqlite3.Connection,
    firm_id: str,
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run all pre-flight gates and return (eligible, skipped).

    Each skipped entry is ``{"member": dict, "reason": str}``.
    """
    members = gather_active_members(conn, firm_id)
    eligible: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for m in members:
        mid = m["id"]

        load = compute_load(conn, mid)
        if load == 0:
            skipped.append({"member": m, "reason": "load=0 (no queued Units)"})
            continue

        if not check_frequency_gate(m, now=now):
            skipped.append({"member": m, "reason": "frequency gate (too soon)"})
            continue

        eligible.append(m)

    return eligible, skipped


# ---------------------------------------------------------------------------
# Topological sort across Members
# ---------------------------------------------------------------------------

def _member_units(
    conn: sqlite3.Connection, member_id: str,
) -> list[dict[str, Any]]:
    """Return pending/in_progress Units claimed by *member_id*."""
    return [
        u for u in repo.find(conn, "unit", claimed_by=member_id)
        if u.get("status") in ("pending", "in_progress")
    ]


def _unit_deps(unit: dict[str, Any]) -> list[str]:
    """Extract depends_on list from a Unit dict."""
    deps = unit.get("depends_on")
    if not deps:
        return []
    if isinstance(deps, str):
        try:
            deps = json.loads(deps)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(deps, list):
        return []
    return [str(d) for d in deps]


def topo_sort_members(
    conn: sqlite3.Connection,
    members: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sort *members* by inter-Member Unit dependencies. Return (sorted, blocked).

    A Member is "blocked" if ALL of their Units have at least one unmet
    dependency (upstream Unit not in 'done' status).  Blocked Members are
    removed from the sorted list and returned separately.

    Members whose Units are independent sort stably by ``id``.
    """
    if not members:
        return [], []

    # Build member → units mapping
    member_units: dict[str, list[dict[str, Any]]] = {}
    for m in members:
        member_units[m["id"]] = _member_units(conn, m["id"])

    # Build unit_id → member_id ownership map (across all eligible members)
    unit_owner: dict[str, str] = {}
    for mid, units in member_units.items():
        for u in units:
            unit_owner[u["id"]] = mid

    # Compute member dependency edges: member A depends on member B if
    # any of A's units depend on any of B's units.
    member_deps: dict[str, set[str]] = {m["id"]: set() for m in members}
    for mid, units in member_units.items():
        for u in units:
            for dep_uid in _unit_deps(u):
                dep_owner = unit_owner.get(dep_uid)
                if dep_owner and dep_owner != mid:
                    member_deps[mid].add(dep_owner)

    # Detect fully-blocked Members: ALL units have at least one unmet dep
    # where "unmet" means the dep unit is NOT done AND is NOT owned by
    # another eligible member in this pulse cycle.  If the dep is owned
    # by an eligible member, it's an ordering constraint (topo sort handles
    # it), not a blocker.
    eligible_member_ids = {m["id"] for m in members}
    blocked: list[dict[str, Any]] = []
    remaining_members: list[dict[str, Any]] = []
    member_by_id = {m["id"]: m for m in members}

    for m in members:
        units = member_units[m["id"]]
        if not units:
            remaining_members.append(m)
            continue

        all_blocked = True
        for u in units:
            deps = _unit_deps(u)
            if not deps:
                all_blocked = False
                break
            # Check if ALL deps are truly unmet (not resolvable this pulse)
            has_unresolvable_dep = False
            for dep_uid in deps:
                dep_row = repo.get(conn, "unit", dep_uid)
                if dep_row is None:
                    has_unresolvable_dep = True
                    break
                if dep_row.get("status") == "done":
                    continue  # Already satisfied
                # Not done — but is it owned by an eligible member?
                dep_owner = unit_owner.get(dep_uid)
                if dep_owner and dep_owner in eligible_member_ids:
                    continue  # Will be resolved by topo ordering this pulse
                has_unresolvable_dep = True
                break
            if not has_unresolvable_dep:
                all_blocked = False
                break

        if all_blocked:
            blocked.append({"member": m, "reason": "all Units have unmet dependencies"})
        else:
            remaining_members.append(m)

    # Kahn's algorithm for topological sort on remaining members
    in_degree: dict[str, int] = {m["id"]: 0 for m in remaining_members}
    remaining_ids = set(in_degree.keys())
    for mid in remaining_ids:
        for dep_mid in member_deps.get(mid, set()):
            if dep_mid in remaining_ids:
                in_degree[mid] = in_degree.get(mid, 0) + 1

    # Seed queue with zero-degree members (sorted by id for stability)
    queue = sorted(
        [mid for mid, deg in in_degree.items() if deg == 0],
    )
    sorted_ids: list[str] = []

    while queue:
        current = queue.pop(0)
        sorted_ids.append(current)
        # Reduce in-degree for members that depend on current
        for mid in remaining_ids:
            if current in member_deps.get(mid, set()):
                in_degree[mid] -= 1
                if in_degree[mid] == 0:
                    queue.append(mid)
        queue.sort()  # Stable tie-breaking by id

    # Handle cycles: any remaining members not in sorted_ids get appended
    for mid in remaining_ids:
        if mid not in sorted_ids:
            sorted_ids.append(mid)

    sorted_members = [member_by_id[mid] for mid in sorted_ids if mid in member_by_id]
    return sorted_members, blocked


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def pulse(
    conn: sqlite3.Connection,
    firm_id: str,
    run_member: RunMemberFn,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> ActivationSummary:
    """Execute a single PULSE cycle for *firm_id*.

    1. Business-hours gate (firm-wide)
    2. Filter Members (active, load, frequency)
    3. Topological sort (dependency ordering)
    4. Sequential activation loop (calls *run_member* per Member)

    Args:
        conn: SQLite connection with migrations applied.
        firm_id: Firm scope.
        run_member: Callback ``(conn, member) -> result_dict``.
        dry_run: If True, skip callback but list who would run.
        now: Optional datetime override for deterministic tests.

    Returns:
        ActivationSummary with ran/skipped/errors lists.
    """
    summary = ActivationSummary(dry_run=dry_run)

    # Gate 1: business hours
    if not check_business_hours(conn, firm_id, now=now):
        summary.skipped.append({
            "member": None,
            "reason": "outside business hours (firm-wide skip)",
        })
        return summary

    # Gate 2: per-member filters
    eligible, skipped = filter_members(conn, firm_id, now=now)
    summary.skipped.extend(skipped)

    if not eligible:
        return summary

    # Gate 3: dependency ordering + block detection
    sorted_members, blocked = topo_sort_members(conn, eligible)
    summary.skipped.extend(blocked)

    if not sorted_members:
        return summary

    # Activation loop (sequential, max 1 concurrent in v1)
    for member in sorted_members:
        if dry_run:
            summary.ran.append({"member": member, "result": "dry_run"})
            continue

        try:
            result = run_member(conn, member)

            # A run that failed or timed out is an error, not a success —
            # "ran" must mean "executed and produced output", or the summary
            # reads healthy when every Member died at spawn.
            status = result.get("status") if isinstance(result, dict) else None
            if status in ("failed", "timed_out"):
                summary.errors.append({
                    "member": member,
                    "error": result,
                    "error_type": status,
                })
            else:
                summary.ran.append({"member": member, "result": result})

            # Update last_activated
            repo.update(conn, "member", member["id"], {
                "last_activated": (now or datetime.now(tz=timezone.utc)).isoformat(),
            })
        except Exception as exc:
            summary.errors.append({
                "member": member,
                "error": str(exc),
                "error_type": type(exc).__name__,
            })

    return summary
