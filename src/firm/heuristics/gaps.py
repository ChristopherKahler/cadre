"""Gap detection heuristics + hire-proposal flow.

Surfaces "we need X" signals from firm state so Sterling (or the Board)
can act before work silently stalls. No auto-hires — all staffing changes
route through the Gate system for Board approval.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from firm.core import repo
from firm.services import gate as gate_svc
from firm.services._validate import require_exists

# Unit statuses that represent active (not-yet-done) work.
_ACTIVE_UNIT_STATUSES: frozenset[str] = frozenset({"pending", "in_progress"})

# Stop words excluded from keyword coverage matching.
_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "and", "for", "from", "in", "of", "on", "or", "the",
    "to", "with", "by", "at", "as", "is", "it", "this", "that", "first",
    "new", "build", "create", "make",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_timestamp(raw: str) -> datetime | None:
    """Parse SQLite datetime('now') output or ISO-8601. Returns None on failure.

    SQLite's ``datetime('now')`` produces naive UTC strings like
    ``2026-04-17 14:07:40``; service-written fields use ``isoformat()`` with
    timezone. Both are treated as UTC here.
    """
    if not raw:
        return None
    try:
        # fromisoformat handles both "YYYY-MM-DD HH:MM:SS" (3.11+) and ISO-8601
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _keywords(text: str) -> set[str]:
    """Lowercase word tokens from text, minus stop words."""
    if not text:
        return set()
    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return {t for t in tokens if t not in _STOP_WORDS and len(t) > 2}


def _member_coverage_vocab(
    conn: sqlite3.Connection,
    member: dict[str, Any],
) -> set[str]:
    """Build coverage vocabulary for a member from role + contract.skill_loadout stages."""
    vocab = _keywords(member.get("role") or "")
    vocab |= _keywords(member.get("description") or "")

    contract_id = member.get("contract_id")
    if contract_id:
        contract = repo.get(conn, "contract", contract_id)
        if contract:
            loadout = contract.get("skill_loadout") or {}
            if isinstance(loadout, dict):
                stages = loadout.get("stages") or {}
                if isinstance(stages, dict):
                    # Stage names ("init", "ideate", "publish") — the verbs
                    # the member knows how to execute.
                    for stage_name in stages:
                        vocab |= _keywords(stage_name)
    return vocab


# ---------------------------------------------------------------------------
# detect_gaps
# ---------------------------------------------------------------------------


def detect_gaps(
    conn: sqlite3.Connection,
    firm_id: str,
    *,
    stale_days: int = 7,
    overload_threshold: int = 3,
) -> dict[str, Any]:
    """Analyze firm state and surface staffing/coverage/workload gaps.

    Args:
        conn: SQLite connection.
        firm_id: Firm scope.
        stale_days: Goals not updated in this many days are flagged stale.
        overload_threshold: Members with >= this many active units are overloaded.

    Returns:
        Dict with keys: unclaimed_units, overloaded_members, stale_goals,
        coverage_gaps, summary.
    """
    # --- Unclaimed units (claimed_by IS NULL AND status active) ---
    unclaimed_all = repo.find(conn, "unit", firm_id=firm_id, claimed_by=None)
    unclaimed_units = [
        u for u in unclaimed_all if u.get("status") in _ACTIVE_UNIT_STATUSES
    ]

    # --- Overloaded members ---
    members = repo.find(conn, "member", firm_id=firm_id, status="active")
    overloaded_members: list[dict[str, Any]] = []
    for member in members:
        claimed = repo.find(
            conn, "unit", firm_id=firm_id, claimed_by=member["id"]
        )
        active_count = sum(
            1 for u in claimed if u.get("status") in _ACTIVE_UNIT_STATUSES
        )
        if active_count >= overload_threshold:
            overloaded_members.append({
                "member_id": member["id"],
                "name": member.get("name"),
                "role": member.get("role"),
                "active_unit_count": active_count,
            })

    # --- Stale goals ---
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=stale_days)
    all_goals = repo.find(conn, "goal", firm_id=firm_id)
    stale_goals: list[dict[str, Any]] = []
    for goal in all_goals:
        if goal.get("status") != "active":
            continue
        ts = _parse_timestamp(goal.get("updated_at") or "")
        if ts is None or ts < cutoff:
            stale_goals.append({
                "goal_id": goal["id"],
                "target": goal.get("target"),
                "parent_entity_type": goal.get("parent_entity_type"),
                "parent_entity_id": goal.get("parent_entity_id"),
                "updated_at": goal.get("updated_at"),
            })

    # --- Coverage gaps ---
    # For each unclaimed unit, check if any active member's coverage vocab
    # intersects the unit's keyword set. If none do, that's a coverage gap.
    member_vocabs = {m["id"]: _member_coverage_vocab(conn, m) for m in members}
    coverage_gaps: list[dict[str, Any]] = []
    for unit in unclaimed_units:
        unit_vocab = _keywords(unit.get("name") or "")
        tags = unit.get("tags") or []
        if isinstance(tags, list):
            for tag in tags:
                unit_vocab |= _keywords(str(tag))
        if not unit_vocab:
            continue
        covered_by = [
            mid for mid, vocab in member_vocabs.items()
            if vocab & unit_vocab
        ]
        if not covered_by:
            coverage_gaps.append({
                "unit_id": unit["id"],
                "name": unit.get("name"),
                "keywords": sorted(unit_vocab),
            })

    summary_parts = []
    if unclaimed_units:
        summary_parts.append(f"{len(unclaimed_units)} unclaimed units")
    if overloaded_members:
        summary_parts.append(f"{len(overloaded_members)} overloaded members")
    if stale_goals:
        summary_parts.append(f"{len(stale_goals)} stale goals")
    if coverage_gaps:
        summary_parts.append(f"{len(coverage_gaps)} coverage gaps")
    summary = (
        "; ".join(summary_parts) if summary_parts else "no gaps detected"
    )

    return {
        "unclaimed_units": unclaimed_units,
        "overloaded_members": overloaded_members,
        "stale_goals": stale_goals,
        "coverage_gaps": coverage_gaps,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# propose_hire
# ---------------------------------------------------------------------------


def propose_hire(
    conn: sqlite3.Connection,
    firm_id: str,
    proposer_id: str,
    proposed_role: str,
    proposed_description: str,
    justification: str,
) -> dict[str, Any]:
    """Create a hire-request Gate from a proposer (typically Sterling).

    The Board resolves the Gate via the existing approve_gate / reject_gate
    flow. Approval does NOT auto-create the Member — the Board executes that
    separately after reviewing the proposal.

    Args:
        conn: SQLite connection.
        firm_id: Firm scope.
        proposer_id: Member ID requesting the hire (must exist and be active).
        proposed_role: Role title for the proposed new member.
        proposed_description: What the role does.
        justification: Why this hire matters now (gap evidence).

    Returns:
        The created Gate dict.

    Raises:
        ValueError: If proposer not found, not active, or required fields empty.
    """
    if not proposed_role:
        raise ValueError("'proposed_role' is required for hire proposal")
    if not justification:
        raise ValueError("'justification' is required for hire proposal")

    proposer = require_exists(conn, "member", proposer_id)
    if proposer.get("status") != "active":
        raise ValueError(
            f"Proposer {proposer_id!r} is {proposer.get('status')!r}, "
            "not 'active' — only active members can propose hires"
        )

    context_payload = {
        "proposed_role": proposed_role,
        "proposed_description": proposed_description,
        "justification": justification,
    }

    return gate_svc.request_gate(conn, firm_id, {
        "requesting_member_id": proposer_id,
        "action": "hire_member",
        "target_entity_type": "firm",
        "target_entity_id": firm_id,
        "context": json.dumps(context_payload),
    })
