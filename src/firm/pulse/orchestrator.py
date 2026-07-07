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
from firm.services.escalation import raise_escalation, resolve_escalation

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
    reaped: list[dict[str, Any]] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# Pre-flight: gather
# ---------------------------------------------------------------------------

def gather_active_members(
    conn: sqlite3.Connection, firm_id: str,
) -> list[dict[str, Any]]:
    """Return all Members with ``status='active'`` for *firm_id*."""
    return repo.find(conn, "member", firm_id=firm_id, status="active")


# ---------------------------------------------------------------------------
# Pre-flight: reap zombie runs
# ---------------------------------------------------------------------------

_REAP_DEFAULT_TIMEOUT_SEC = 300
_REAP_GRACE_SEC = 600


def _contract_timeout_sec(conn: sqlite3.Connection, member_id: str) -> int:
    """Resolve the member's contract ``pulse_config.timeout_sec`` (default 300)."""
    member = repo.get(conn, "member", member_id)
    contract_id = member.get("contract_id") if member else None
    contract = repo.get(conn, "contract", contract_id) if contract_id else None
    pc = contract.get("pulse_config") if contract else None
    if isinstance(pc, str):
        try:
            pc = json.loads(pc)
        except (json.JSONDecodeError, TypeError):
            pc = None
    if isinstance(pc, dict):
        try:
            return int(pc.get("timeout_sec", _REAP_DEFAULT_TIMEOUT_SEC))
        except (TypeError, ValueError):
            return _REAP_DEFAULT_TIMEOUT_SEC
    return _REAP_DEFAULT_TIMEOUT_SEC


def reap_stale_runs(
    conn: sqlite3.Connection,
    firm_id: str,
    *,
    now: datetime | None = None,
    write: bool = True,
) -> list[dict[str, Any]]:
    """Close member_run rows stuck at 'running' past any plausible lifetime.

    A run row leaks as 'running' when the pulse process dies mid-run
    (systemd kill, host teardown) — the runner that would have closed it is
    gone, and nothing else ever touches the row (ESC-D field report: RUN-016
    sat open 20h). Stale = older than 2× the contract timeout (a validation
    retry can double a run) plus grace. Reaped rows close as status='failed'
    with error.type='orphaned' (the member_run status CHECK is a closed
    vocabulary; the error payload carries the reap reason). With
    ``write=False`` (dry-run) stale rows are reported but left untouched.
    """
    ref = now or datetime.now(tz=timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    reaped: list[dict[str, Any]] = []
    for run in repo.find(conn, "member_run", firm_id=firm_id, status="running"):
        started_raw = run.get("started_at")
        try:
            started = datetime.fromisoformat(started_raw)
        except (TypeError, ValueError):
            started = None
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        deadline_sec = 2 * _contract_timeout_sec(conn, run["member_id"]) + _REAP_GRACE_SEC
        if started is not None and (ref - started).total_seconds() <= deadline_sec:
            continue  # plausibly still alive — leave it
        if write:
            repo.update(conn, "member_run", run["id"], {
                "status": "failed",
                "ended_at": ref.isoformat(),
                "error": json.dumps({
                    "type": "orphaned",
                    "detail": (
                        f"still 'running' past {deadline_sec}s max lifetime; "
                        "pulse process presumed dead, row reaped at next pulse"
                    ),
                    "started_at": started_raw,
                }),
            })
        reaped.append({
            "run_id": run["id"],
            "member_id": run["member_id"],
            "unit_id": run.get("unit_id"),
            "started_at": started_raw,
        })
    return reaped


# ---------------------------------------------------------------------------
# Pre-flight: load
# ---------------------------------------------------------------------------

def compute_load(conn: sqlite3.Connection, member_id: str) -> int:
    """Count the Member's workable queue: claimed pending/in_progress Units
    PLUS assigned-but-unclaimed pending Units (the runner atomically claims
    those at dispatch).

    Counting only claimed Units gated every Member with assigned work out at
    load=0 — before the runner's auto-claim could ever fire — stalling
    dependency chains forever (Board Proxy field report, 2026-07-03 night).
    A load of 0 means the Member has nothing queued — no point activating.
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM unit "
        "WHERE (claimed_by = ? AND status IN ('pending', 'in_progress')) "
        "   OR (assignee_member_id = ? AND claimed_by IS NULL AND status = 'pending')",
        (member_id, member_id),
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
    """Return the Member's workable Units: claimed pending/in_progress PLUS
    assigned-but-unclaimed pending (same definition as compute_load — the
    topo/blocked analysis must see the same queue the load gate counts and
    the runner dispatches)."""
    by_id: dict[str, dict[str, Any]] = {}
    for u in repo.find(conn, "unit", claimed_by=member_id):
        if u.get("status") in ("pending", "in_progress"):
            by_id[u["id"]] = u
    for u in repo.find(conn, "unit", assignee_member_id=member_id):
        if u.get("status") == "pending" and not u.get("claimed_by"):
            by_id.setdefault(u["id"], u)
    return list(by_id.values())


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

_IDLE_NUDGE_KEY = "backlog-exhausted"


def _idle_nudge_lead(conn: sqlite3.Connection, firm_id: str) -> str | None:
    """The active Member to attribute the idle nudge to — the top of the
    hierarchy (no ``reports_to``), else the first active Member."""
    members = repo.find(conn, "member", firm_id=firm_id, status="active")
    if not members:
        return None
    lead = next((m for m in members if not m.get("reports_to_member_id")), None)
    return (lead or members[0])["id"]


def _reconcile_idle_nudge(conn: sqlite3.Connection, firm_id: str) -> None:
    """Keep the 'firm idle' Board nudge in sync with reality.

    A firm with active projects but ZERO queued units has drained its backlog
    while work remains to be planned — pulses then spawn nobody, silently. Raise
    ONE deduped escalation so that state is visible and actionable, and clear it
    the moment any unit is queued again. This does NOT auto-invent work (that
    would be the paperclip failure mode); it makes an idle firm ask the Board
    for direction instead of stalling in silence.
    """
    open_units = [
        u for u in repo.find(conn, "unit", firm_id=firm_id)
        if u.get("status") in ("pending", "in_progress", "in_review")
    ]
    active_projects = [
        p for p in repo.find(conn, "project", firm_id=firm_id)
        if p.get("status") in ("active", "in_progress")
    ]
    key = f"{_IDLE_NUDGE_KEY}:{firm_id}"
    if not open_units and active_projects:
        lead = _idle_nudge_lead(conn, firm_id)
        if lead is not None:
            raise_escalation(conn, firm_id, {
                "raised_by_member_id": lead,
                "title": "Firm idle — no queued units despite active projects",
                "body": (
                    "Active projects exist but the unit queue is empty, so pulses "
                    "spawn nobody. Queue the next work — decompose an approved plan "
                    "into units, or close the projects if the firm is done. This "
                    "nudge clears automatically once a unit is queued again."
                ),
                "severity": "low",
                "dedupe_key": key,
            })
    else:
        for e in repo.find(conn, "escalation", firm_id=firm_id, dedupe_key=key):
            if e.get("status") in ("open", "acknowledged"):
                resolve_escalation(conn, e["id"], status="resolved")


def pulse(
    conn: sqlite3.Connection,
    firm_id: str,
    run_member: RunMemberFn,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    only_member_id: str | None = None,
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
        only_member_id: Board-targeted pulse — activate ONLY this Member.
            The frequency throttle is waived for the target (an explicit
            Board dispatch outranks a cadence heuristic); load, status,
            business hours, and budget gates still apply.

    Returns:
        ActivationSummary with ran/skipped/errors lists.
    """
    summary = ActivationSummary(dry_run=dry_run)

    # Gate 0: heal leaked state — close zombie 'running' rows before anything
    # else reads them (dry-run detects but does not write).
    summary.reaped = reap_stale_runs(conn, firm_id, now=now, write=not dry_run)

    # Gate 1: business hours
    if not check_business_hours(conn, firm_id, now=now):
        summary.skipped.append({
            "member": None,
            "reason": "outside business hours (firm-wide skip)",
        })
        return summary

    # Keep the idle-firm nudge honest before selecting members (real pulses only):
    # raise it when the backlog is empty, clear it once work is queued again.
    if not dry_run:
        _reconcile_idle_nudge(conn, firm_id)

    # Gate 2: per-member filters
    eligible, skipped = filter_members(conn, firm_id, now=now)
    if only_member_id:
        untargeted = [m for m in eligible if m["id"] != only_member_id]
        eligible = [m for m in eligible if m["id"] == only_member_id]
        if not eligible:
            # Board override: a frequency skip yields to an explicit dispatch;
            # every other skip reason (load=0, inactive) stands.
            for s in skipped:
                m = s.get("member") or {}
                if m.get("id") == only_member_id and "frequency" in s["reason"]:
                    eligible = [m]
                    skipped = [x for x in skipped if x is not s]
                    break
        skipped = [
            s for s in skipped
            if (s.get("member") or {}).get("id") == only_member_id
        ]
        skipped.extend(
            {"member": m, "reason": f"not targeted (only={only_member_id})"}
            for m in untargeted
        )
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
