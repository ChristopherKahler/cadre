"""Calibration Ladder — the graduated-autonomy tier model (T0→T4).

Governance half of the two-part calibration system. The scoring engine (fork
``cadre-calibration-run-scoring``) rates completed runs 1–5; this module turns
that signal into a *tier* — the trust level that decides which enforced seals a
Member may have loosened.

Two axes, deliberately separate (Open Q5): the Floor's LEVEL/XP measures output
volume; the TIER measures earned trust/autonomy. A high-XP member can still be
low-tier if its runs are unrated — output is not trust.

Everything here is DERIVED at read time from the run-score aggregate (Floor law
2 — derived, never authored). Recompute from the aggregate every read, so a
rescore re-tiers for free (mirrors the run-scoring recompute property). The ONLY
authored state the Ladder adds is the sovereign Board override (``member.autonomy``),
framed as Board config exactly as ``run_score`` is framed as Board evaluation.
NOTHING here reaches a member surface (Floor law 3 / Invariant #5): the member
never learns its tier — it simply has, or does not have, a capability
structurally.

**The seam loadout-consolidation v2 codes against — keep these EXACT (ping
toucan before changing a signature):**

    tier_of(conn, firm_id, member_id) -> int
    can_loosen(conn, firm_id, member_id, capability)
        -> {"allowed": bool, "reason": str,
            "via": "tier"|"override"|"denied", "tier": int, "needed_tier": int|None}
    next_tier_requirements(conn, firm_id, member_id)
        -> {"needed_tier": int|None, "needs": {...}, "have": {...}}

Importable with no dashboard side-effects — tests and loadout-v2 import it
directly. It reads ``member_run`` for the score aggregate (single-sourced here
so nothing else re-reads ``run_score``) and ``member.autonomy`` for the override.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

# ---------------------------------------------------------------------------
# The ladder (Open Q1) — one data table to tune. A tier is EARNED when the
# member has at least ``min_rated`` Board-rated runs AND a sustained average
# ``avg`` >= ``min_avg``. ``min_rated`` is the anti-jump guardrail: sustained
# quality, not one lucky 5. Thresholds are monotonic, so the highest satisfied
# rung wins. Applies to every firm-type (Open Q4 — universal ladder); a firm
# that wants a different rung table edits this one place.
# ---------------------------------------------------------------------------

MIN_TIER = 0
MAX_TIER = 4

TIERS: list[dict[str, Any]] = [
    {"tier": 0, "label": "Probation",   "min_rated": 0,  "min_avg": 0.0},
    {"tier": 1, "label": "Provisional", "min_rated": 3,  "min_avg": 3.5},
    {"tier": 2, "label": "Trusted",     "min_rated": 8,  "min_avg": 4.0},
    {"tier": 3, "label": "Autonomous",  "min_rated": 20, "min_avg": 4.3},
    {"tier": 4, "label": "Principal",   "min_rated": 40, "min_avg": 4.6},
]

# Which earned tier unlocks loosening a class of seal (Open Q1/Q3). A seal is a
# ``validation_config.deny`` rule; its risk class maps to the trust it demands.
# Unknown capability → MAX_TIER: the guardrail is the product default, so
# anything unclassified needs full earned trust (or a sovereign override).
CAPABILITY_TIERS: dict[str, int] = {
    "read": 1,           # read-only surfaces
    "write": 2,          # local file writes / edits
    "shell": 3,          # arbitrary shell / command execution
    "network": 4,        # outbound network / fetch
    "credentials": 4,    # secrets, tokens, keys
    "external-post": 4,  # publishing to an outside service
}
DEFAULT_CAPABILITY_TIER = MAX_TIER


# ---------------------------------------------------------------------------
# Capability classification
# ---------------------------------------------------------------------------

def _classify(capability: str) -> str:
    """Map a raw capability / seal token to a risk-class key in
    ``CAPABILITY_TIERS``. Exact match wins; else a coarse keyword heuristic;
    else ``'unknown'`` (which resolves to the guardrail-default tier)."""
    cap = (capability or "").strip().lower()
    if cap in CAPABILITY_TIERS:
        return cap

    def has(*subs: str) -> bool:
        return any(s in cap for s in subs)

    if has("cred", "secret", "token", "key", "auth"):
        return "credentials"
    if has("post", "publish", "send", "tweet", "slack", "email", "deploy"):
        return "external-post"
    if has("web", "fetch", "http", "curl", "net", "url", "download"):
        return "network"
    if has("bash", "shell", "exec", "subprocess", "command", " sh"):
        return "shell"
    if has("write", "edit", "create", "delete", "remove", "modify", "rm ", "mv "):
        return "write"
    if has("read", "view", "list", "cat", "grep"):
        return "read"
    return "unknown"


def capability_needed_tier(capability: str) -> int:
    """The earned tier that structurally unlocks loosening this capability."""
    return CAPABILITY_TIERS.get(_classify(capability), DEFAULT_CAPABILITY_TIER)


def tier_label(tier: int) -> str:
    """Human label for a tier number (Board-facing readout)."""
    for t in TIERS:
        if t["tier"] == tier:
            return str(t["label"])
    return "Unknown"


# ---------------------------------------------------------------------------
# Run-score aggregate — the single derivation of the score signal. floor_state
# calls calibration_aggregate() so scoring stays single-source: nothing outside
# this module re-reads member_run.run_score. Positional column access keeps it
# independent of the caller's row_factory.
# ---------------------------------------------------------------------------

def _empty_aggregate() -> dict[str, Any]:
    return {"avg": None, "rated": 0, "total": 0, "recent_avg": None}


def _member_aggregate(
    conn: sqlite3.Connection, firm_id: str, member_id: str,
) -> dict[str, Any]:
    """The run-score aggregate for one member: ``{avg, rated, total, recent_avg}``."""
    row = conn.execute(
        "SELECT AVG(CASE WHEN run_score IS NOT NULL THEN run_score END), "
        "SUM(CASE WHEN run_score IS NOT NULL THEN 1 ELSE 0 END), "
        "COUNT(*) "
        "FROM member_run WHERE firm_id = ? AND member_id = ?",
        (firm_id, member_id),
    ).fetchone()
    recent = [
        r[0] for r in conn.execute(
            "SELECT run_score FROM member_run "
            "WHERE firm_id = ? AND member_id = ? AND run_score IS NOT NULL "
            "ORDER BY started_at DESC LIMIT 5",
            (firm_id, member_id),
        )
    ]
    avg_raw = row[0] if row else None
    return {
        "avg": round(avg_raw, 2) if avg_raw is not None else None,
        "rated": (row[1] if row else 0) or 0,
        "total": (row[2] if row else 0) or 0,
        "recent_avg": round(sum(recent) / len(recent), 2) if recent else None,
    }


def calibration_aggregate(
    conn: sqlite3.Connection, firm_id: str,
) -> dict[str, dict[str, Any]]:
    """``{member_id: {avg, rated, total, recent_avg}}`` for every member with
    runs. The one derivation of the run-score signal — floor_state consumes this
    (never re-reading run_score inline) so scoring is single-sourced."""
    out: dict[str, dict[str, Any]] = {}
    for r in conn.execute(
        "SELECT member_id, "
        "AVG(CASE WHEN run_score IS NOT NULL THEN run_score END), "
        "SUM(CASE WHEN run_score IS NOT NULL THEN 1 ELSE 0 END), "
        "COUNT(*) "
        "FROM member_run WHERE firm_id = ? GROUP BY member_id",
        (firm_id,),
    ):
        out[r[0]] = {
            "avg": round(r[1], 2) if r[1] is not None else None,
            "rated": r[2] or 0,
            "total": r[3] or 0,
            "recent_avg": None,
        }
    recent: dict[str, list[int]] = {}
    for r in conn.execute(
        "SELECT member_id, run_score FROM member_run "
        "WHERE firm_id = ? AND run_score IS NOT NULL ORDER BY started_at DESC",
        (firm_id,),
    ):
        recent.setdefault(r[0], []).append(r[1])
    for mid, scores in recent.items():
        window = scores[:5]
        out.setdefault(mid, _empty_aggregate())
        out[mid]["recent_avg"] = round(sum(window) / len(window), 2) if window else None
    return out


# ---------------------------------------------------------------------------
# Pure tier derivation (aggregate → tier). The DB-facing seam wraps these so a
# caller that already holds the aggregate (floor_state, per card) avoids a
# re-query.
# ---------------------------------------------------------------------------

def tier_for_aggregate(agg: dict[str, Any]) -> int:
    """Highest earned tier for a run-score aggregate. Anti-jump: a rung needs
    both ``min_rated`` rated runs and ``avg`` >= ``min_avg`` — one high score
    can never graduate a member (rated stays below the next rung's floor)."""
    rated = agg.get("rated") or 0
    avg = agg.get("avg")
    earned = MIN_TIER
    for t in TIERS:
        if t["tier"] == MIN_TIER:
            continue
        if rated >= t["min_rated"] and avg is not None and avg >= t["min_avg"]:
            earned = t["tier"]
    return earned


def next_requirements_for_aggregate(agg: dict[str, Any]) -> dict[str, Any]:
    """What the next rung still needs, given a run-score aggregate.
    ``needed_tier`` is None at the cap. ``needs`` lists only the unmet floors
    (e.g. ``{"rated": 20, "avg": 4.3}``); ``have`` is the current standing."""
    tier = tier_for_aggregate(agg)
    rated = agg.get("rated") or 0
    avg = agg.get("avg")
    have = {"rated": rated, "avg": avg}
    if tier >= MAX_TIER:
        return {"needed_tier": None, "needs": {}, "have": have}
    nxt = next(t for t in TIERS if t["tier"] == tier + 1)
    needs: dict[str, Any] = {}
    if rated < nxt["min_rated"]:
        needs["rated"] = nxt["min_rated"]
    if avg is None or avg < nxt["min_avg"]:
        needs["avg"] = nxt["min_avg"]
    return {"needed_tier": tier + 1, "needs": needs, "have": have}


def tier_progress_for_aggregate(
    agg: dict[str, Any], sovereign: list[str] | None = None,
) -> dict[str, Any]:
    """Board-facing progress readout for the Floor sheet — current tier + label,
    the next rung and what it still needs, and any sovereign grants. Takes a
    precomputed aggregate (+ optional override list) so a card render is
    zero extra score queries."""
    tier = tier_for_aggregate(agg)
    req = next_requirements_for_aggregate(agg)
    nxt = req["needed_tier"]
    return {
        "tier": tier,
        "label": tier_label(tier),
        "next_tier": nxt,
        "next_label": tier_label(nxt) if nxt is not None else None,
        "needs": req["needs"],
        "have": req["have"],
        "sovereign": list(sovereign or []),
    }


# ---------------------------------------------------------------------------
# Sovereign override (the only authored input) — read here, written only via
# services/autonomy.py. member.autonomy JSON: {"sovereign": ["*"|capability...]}.
# ---------------------------------------------------------------------------

def sovereign_capabilities(
    conn: sqlite3.Connection, firm_id: str, member_id: str,
) -> list[str]:
    """The Board's authored override list for this member. ``['*']`` = blanket
    sovereignty; else the specific capabilities/classes granted directly,
    bypassing the ladder. ``[]`` = no override (the guardrail default)."""
    row = conn.execute(
        "SELECT autonomy FROM member WHERE id = ? AND firm_id = ?",
        (member_id, firm_id),
    ).fetchone()
    if not row or not row[0]:
        return []
    raw = row[0]
    try:
        cfg = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(cfg, dict):
        return []
    sov = cfg.get("sovereign")
    return [str(s) for s in sov] if isinstance(sov, list) else []


def _overridden(capability: str, sovereign: list[str]) -> bool:
    """True when the Board has directly granted this capability (blanket ``*``,
    an exact token match, or its risk-class)."""
    if "*" in sovereign:
        return True
    if capability in sovereign:
        return True
    return _classify(capability) in sovereign


# ---------------------------------------------------------------------------
# The seam (KEEP SIGNATURES EXACT — loadout-v2 codes against these)
# ---------------------------------------------------------------------------

def tier_of(conn: sqlite3.Connection, firm_id: str, member_id: str) -> int:
    """The Member's earned trust tier (T0→T4), derived at read time from the
    run-score aggregate. Recomputes on every call — a rescore re-tiers for free."""
    return tier_for_aggregate(_member_aggregate(conn, firm_id, member_id))


def can_loosen(
    conn: sqlite3.Connection, firm_id: str, member_id: str, capability: str,
) -> dict[str, Any]:
    """May this seal / capability be loosened for this Member?

    Resolution order: a sovereign Board override wins outright (``via:"override"``,
    regardless of tier); else the earned tier is compared to the capability's
    required tier — ``via:"tier"`` when it covers it, ``via:"denied"`` (carrying
    ``needed_tier``) when it does not. The result is a *permission* — loadout-v2
    enacts the runtime effect (removing the deny for this member's runs);
    guardrails stay the default until permission is granted (Open Q3). Board-
    facing: the decision is structural, the member never learns its tier."""
    tier = tier_of(conn, firm_id, member_id)
    needed = capability_needed_tier(capability)
    sovereign = sovereign_capabilities(conn, firm_id, member_id)
    if _overridden(capability, sovereign):
        return {
            "allowed": True,
            "reason": f"sovereign Board override for {capability!r}",
            "via": "override", "tier": tier, "needed_tier": needed,
        }
    if tier >= needed:
        return {
            "allowed": True,
            "reason": f"tier T{tier} ({tier_label(tier)}) covers {capability!r} (needs T{needed})",
            "via": "tier", "tier": tier, "needed_tier": needed,
        }
    return {
        "allowed": False,
        "reason": f"{capability!r} needs tier T{needed}; member is T{tier} ({tier_label(tier)})",
        "via": "denied", "tier": tier, "needed_tier": needed,
    }


def next_tier_requirements(
    conn: sqlite3.Connection, firm_id: str, member_id: str,
) -> dict[str, Any]:
    """What the Member still needs to reach the next tier, for the UI
    ("2 more rated runs at ≥4.2 to reach T3"). ``needed_tier`` None at the cap."""
    return next_requirements_for_aggregate(_member_aggregate(conn, firm_id, member_id))
