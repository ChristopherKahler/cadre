"""Sovereign autonomy override — the ONE authored input to the Calibration
Ladder (fork ``cadre-calibration-ladder``).

The Board directly grants a Member a capability, bypassing the earned tier (the
Board owns the risk). Written here — services are the only write path (Invariant
#2) — and read at derivation time by ``dashboard/calibration.py``. Board config,
never member-authored, never member-read (Invariant #5).

Store: ``member.autonomy`` JSON — ``{"sovereign": ["*"] | [capability, ...]}``.
``'*'`` = blanket sovereignty; a list = exactly those capabilities/risk-classes
granted directly. Empty clears the override (back to the guardrail default: the
ladder gates every loosening). Named ``autonomy`` so loadout-consolidation v2
extends the SAME member-level block instead of forking a sibling.

Records events: member.autonomy_updated
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from firm.core import repo
from firm.services._records import log_event


def _normalize(capabilities: Any) -> list[str]:
    """Coerce the input to a clean, de-duplicated list of capability tokens.
    A bare string becomes a single-element list; None / empty → ``[]``."""
    if capabilities is None:
        return []
    if isinstance(capabilities, str):
        capabilities = [capabilities]
    seen: set[str] = set()
    out: list[str] = []
    for c in capabilities:
        s = str(c).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _load(v: Any) -> dict[str, Any]:
    """Parse the stored autonomy block (dict or JSON string) → dict."""
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, str) and v.strip():
        try:
            parsed = json.loads(v)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def set_sovereign_override(
    conn: sqlite3.Connection,
    member_id: str,
    capabilities: Any,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Set (or clear) a Member's sovereign capability grants. Board write path.

    Args:
        conn: SQLite connection with migrations applied.
        member_id: The Member whose autonomy block is being set.
        capabilities: A list of capability/risk-class tokens, ``"*"`` for
            blanket sovereignty, or ``[]``/``None`` to clear the override.
        actor: ``{"type", "id"}`` — defaults to the Board.

    Raises:
        ValueError: member not found.
    """
    member = repo.get(conn, "member", member_id)
    if not member:
        raise ValueError(f"member {member_id!r} not found")

    caps = _normalize(capabilities)
    cfg = _load(member.get("autonomy"))
    if caps:
        cfg["sovereign"] = caps
    else:
        cfg.pop("sovereign", None)

    updated = repo.update(
        conn, "member", member_id,
        {"autonomy": json.dumps(cfg) if cfg else None},
    )
    assert updated is not None, "member disappeared after repo.get"

    log_event(
        conn,
        firm_id=member["firm_id"],
        event_type="member.autonomy_updated",
        actor=actor or {"type": "board", "id": None},
        target_ref={"type": "member", "id": member_id},
        details={"sovereign": caps},
    )
    return updated


def set_authority_grant(
    conn: sqlite3.Connection,
    member_id: str,
    tools: Any,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Grant (or clear) a Member's authority to drive the five self-govern
    MCP tools (audit A6). Board write path — the companion to
    ``firm.services.authority.has_authority`` (the read/check side).

    Writes the ``"authority"`` key inside the same ``member.autonomy`` blob
    ``set_sovereign_override`` writes ``"sovereign"`` into, preserving the
    other key (each mutates only its own). ``"*"`` grants all five governed
    tools; a list grants exactly those; ``[]``/``None`` clears the grant.

    Args:
        conn: SQLite connection with migrations applied.
        member_id: The Member being granted (or cleared).
        tools: ``"*"``, a list of governed tool names, or ``[]``/``None``.
        actor: ``{"type", "id"}`` — defaults to the Board.

    Raises:
        ValueError: member not found, or a grant token that is neither ``"*"``
            nor a governed tool name (a grant for a tool this gate does not
            govern is a Board mistake, caught here rather than silently stored).
    """
    from firm.services.authority import GOVERNED_TOOLS

    member = repo.get(conn, "member", member_id)
    if not member:
        raise ValueError(f"member {member_id!r} not found")

    grants = _normalize(tools)
    unknown = [g for g in grants if g != "*" and g not in GOVERNED_TOOLS]
    if unknown:
        raise ValueError(
            f"not governed self-govern tools: {unknown}. Grant '*' or one of "
            f"{sorted(GOVERNED_TOOLS)}."
        )

    cfg = _load(member.get("autonomy"))
    if grants:
        cfg["authority"] = grants
    else:
        cfg.pop("authority", None)

    updated = repo.update(
        conn, "member", member_id,
        {"autonomy": json.dumps(cfg) if cfg else None},
    )
    assert updated is not None, "member disappeared after repo.get"

    log_event(
        conn,
        firm_id=member["firm_id"],
        event_type="member.autonomy_updated",
        actor=actor or {"type": "board", "id": None},
        target_ref={"type": "member", "id": member_id},
        details={"authority": grants},
    )
    return updated
