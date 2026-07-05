"""Budget enforcement for PULSE Member runs.

Pre-flight checks prevent activation when limits are exceeded.
Post-run updates record usage and flag limit breaches.
Rate-limit awareness evaluates utilization against thresholds.

Specification: BRIEF.md Decision D2 (dual-track tokens + USD).
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from firm.core import repo
from firm.services._id import next_id


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class BudgetCheck:
    """Result of a pre-flight budget check."""

    allowed: bool
    reason: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_budget_config(
    conn: sqlite3.Connection, member_id: str,
) -> dict[str, Any] | None:
    """Resolve budget_config from Member's Contract."""
    member = repo.get(conn, "member", member_id)
    if not member or not member.get("contract_id"):
        return None
    contract = repo.get(conn, "contract", member["contract_id"])
    if not contract:
        return None
    bc = contract.get("budget_config")
    if not bc:
        return None
    if isinstance(bc, str):
        try:
            bc = json.loads(bc)
        except (json.JSONDecodeError, TypeError):
            return None
    return bc if isinstance(bc, dict) else None


def _get_active_budget_period(
    conn: sqlite3.Connection, member_id: str,
) -> dict[str, Any] | None:
    """Find the active budget_period for a Member."""
    periods = repo.find(conn, "budget_period", member_id=member_id, status="active")
    return periods[0] if periods else None


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def check_budget_preflight(
    conn: sqlite3.Connection, member_id: str,
) -> BudgetCheck:
    """Check whether a Member is within budget limits.

    Returns BudgetCheck(allowed=True) if no config, no active period,
    or all limits are under threshold.
    """
    bc = _get_budget_config(conn, member_id)
    if not bc:
        return BudgetCheck(allowed=True)

    period = _get_active_budget_period(conn, member_id)
    if not period:
        return BudgetCheck(allowed=True)  # No period = no tracking yet

    limits = bc.get("limits", {})
    if not isinstance(limits, dict):
        return BudgetCheck(allowed=True)

    # Check run count
    max_runs = limits.get("max_runs_per_period")
    if max_runs is not None and period.get("run_count", 0) >= max_runs:
        return BudgetCheck(
            allowed=False,
            reason=f"Run limit reached: {period['run_count']}/{max_runs} runs",
        )

    # Check cost
    max_cost = limits.get("max_total_cost_per_period_usd")
    if max_cost is not None and period.get("total_cost_usd", 0.0) >= max_cost:
        return BudgetCheck(
            allowed=False,
            reason=f"Cost limit reached: ${period['total_cost_usd']:.2f}/${max_cost:.2f}",
        )

    return BudgetCheck(allowed=True)


# ---------------------------------------------------------------------------
# Post-run
# ---------------------------------------------------------------------------

def update_budget_postrun(
    conn: sqlite3.Connection,
    member_id: str,
    firm_id: str,
    parsed_result: dict[str, Any],
    *,
    run_id: str | None = None,
    unit_id: str | None = None,
) -> None:
    """Update budget_period totals and create usage_event from run result.

    If no active budget_period exists, creates one for the current period.
    If any limit is now exceeded, sets status to 'limit_reached'.
    run_id/unit_id link the usage_event to its run so per-run cost is
    attributable (they were silently dropped before).
    """
    # Find or create active period
    period = _get_active_budget_period(conn, member_id)
    if not period:
        now = datetime.now(tz=timezone.utc)
        period_id = next_id(conn, "budget_period", firm_id)
        period = repo.create(conn, "budget_period", {
            "id": period_id,
            "firm_id": firm_id,
            "member_id": member_id,
            "period_start": now.isoformat(),
            "period_end": "9999-12-31T23:59:59+00:00",  # Open-ended until closed
            "status": "active",
        })

    usage = parsed_result.get("usage", {})
    cost = parsed_result.get("total_cost_usd") or 0.0

    # Update totals
    new_run_count = (period.get("run_count") or 0) + 1
    new_input = (period.get("total_input_tokens") or 0) + usage.get("input_tokens", 0)
    new_output = (period.get("total_output_tokens") or 0) + usage.get("output_tokens", 0)
    new_cost = (period.get("total_cost_usd") or 0.0) + cost

    repo.update(conn, "budget_period", period["id"], {
        "run_count": new_run_count,
        "total_input_tokens": new_input,
        "total_output_tokens": new_output,
        "total_cost_usd": new_cost,
    })

    # Create usage_event (schema: timestamp, plan, tokens_in, tokens_out, dollar_equivalent)
    usage_id = next_id(conn, "usage_event", firm_id)
    repo.create(conn, "usage_event", {
        "id": usage_id,
        "firm_id": firm_id,
        "member_id": member_id,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "plan": "claude_pro_200",
        "tokens_in": usage.get("input_tokens", 0),
        "tokens_out": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read", 0),
        "cache_create_tokens": usage.get("cache_create", 0),
        "dollar_equivalent": cost,
        "run_id": run_id,
        "unit_id": unit_id,
    })

    # Check if any limit now exceeded
    bc = _get_budget_config(conn, member_id)
    if bc:
        limits = bc.get("limits", {})
        max_runs = limits.get("max_runs_per_period")
        max_cost = limits.get("max_total_cost_per_period_usd")
        if (max_runs is not None and new_run_count >= max_runs) or \
           (max_cost is not None and new_cost >= max_cost):
            repo.update(conn, "budget_period", period["id"], {
                "status": "limit_reached",
            })


# ---------------------------------------------------------------------------
# Rate limit awareness
# ---------------------------------------------------------------------------

def check_rate_limit(
    rate_limit_events: list[dict[str, Any]],
    alert_threshold_pct: int = 80,
) -> bool:
    """Return True if any rate_limit_event has utilization above threshold.

    Args:
        rate_limit_events: From parsed_result["rate_limit_events"].
        alert_threshold_pct: Percentage threshold (0-100).

    Returns:
        True if any event exceeds threshold (warning condition).
    """
    threshold = alert_threshold_pct / 100.0
    for event in rate_limit_events:
        utilization = event.get("utilization")
        if utilization is not None and utilization > threshold:
            return True
    return False
