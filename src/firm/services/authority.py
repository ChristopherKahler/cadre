"""Board-granted authority to drive the five self-govern MCP tools (audit A6).

`firm_create_member`, `firm_update_member`, `firm_complete_unit`,
`firm_resolve_escalation`, and `firm_update_goal` let a caller act *as* the
firm's self-governance surface — create colleagues, close its own work,
resolve its own escalations, retune its own goals. Left ungated, any spawned
Member could self-govern: the same class of bypass the constitutional line at
``tools.py`` (Gate resolution is not a Member tool) exists to prevent, at the
places that line was never drawn.

The fix is **authority-gate, not remove** — a Board-designated role (e.g. a
General Manager driving the Pulse) must keep calling these tools. So a Member
may call them only with an explicit Board grant; every other Member is denied;
and a no-identity caller (the Board via dashboard/CLI, no ``CADRE_MEMBER_ID``
in env) is allowed, because there is no Member to check — the same answer the
PreToolUse hook already gives.

Grants live in ``member.autonomy["authority"]`` — a second key alongside the
Calibration Ladder's ``"sovereign"``, deliberately disjoint: ``sovereign``
answers "how much local risk may this Member's own actions take" (a *tier*
override); ``authority`` answers "is this Member the Board-designated
self-governance driver." Folding one into the other would let a grant made for
one reason read as the other. ``"*"`` = all five tools; an explicit list =
per-tool. Board-authored only (write path: ``services.autonomy.set_authority_grant``),
never member-authored, and scrubbed from every outbound MCP response by the
existing ``_BOARD_ONLY_FIELDS`` guard — a Member cannot read its own grant.

Design of record: the firm's own ``UNT-A6-SPEC`` design spec (Board-approved
2026-07-16). One deliberate divergence: the spec's ``has_authority`` carried a
``firm_id`` argument; ``member.id`` is a global ``PRIMARY KEY``, so this
resolves by member id alone — which also lets the three tools that carry no
``firm_id`` (update_member / resolve_escalation / update_goal) call it cleanly.
"""

from __future__ import annotations

import json
import sqlite3

# The five tools this gate governs (audit A6). A grant token is either "*"
# (all five) or one of these exact names.
GOVERNED_TOOLS: frozenset[str] = frozenset({
    "firm_create_member",
    "firm_update_member",
    "firm_complete_unit",
    "firm_resolve_escalation",
    "firm_update_goal",
})


def authority_grants(conn: sqlite3.Connection, member_id: str) -> list[str]:
    """The Board's authored ``authority`` grant list for this Member.

    ``["*"]`` = blanket (all governed tools); else the specific tool names
    granted; ``[]`` = no grant (the default — the Member is denied every
    governed tool). Reads ``member.autonomy["authority"]`` by the Member's
    primary key; a malformed blob fails closed to ``[]`` (mirrors
    ``dashboard.calibration.sovereign_capabilities``)."""
    if not member_id:
        return []
    row = conn.execute(
        "SELECT autonomy FROM member WHERE id = ?", (member_id,),
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
    grants = cfg.get("authority")
    return [str(g) for g in grants] if isinstance(grants, list) else []


def has_authority(
    conn: sqlite3.Connection, member_id: str, tool_name: str,
) -> tuple[bool, str]:
    """May this caller invoke ``tool_name`` (one of the five governed tools)?

    - No ``member_id`` (``CADRE_MEMBER_ID`` unset) → allowed: a Board / CLI
      session, no Member to check (same rule as the PreToolUse hook).
    - ``"*"`` or ``tool_name`` in the Member's ``authority`` grants → allowed.
    - Otherwise → denied.

    Returns ``(allowed, reason)``; the reason is surfaced verbatim in the
    tool's ``{"error": ...}`` on denial. Never raises — a self-govern check
    must not itself become a failure mode."""
    if not member_id:
        return True, "no member identity in env — Board/CLI session"
    grants = authority_grants(conn, member_id)
    if "*" in grants or tool_name in grants:
        return True, f"Board-granted authority for {tool_name}"
    return (
        False,
        f"{tool_name} requires Board-granted authority; member {member_id} "
        "has none. This is a governed self-governance tool (audit A6) — a "
        "Member cannot grant itself authority; the Board grants it.",
    )
