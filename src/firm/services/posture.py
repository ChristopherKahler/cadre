"""Loadout trust posture — the firm default and the per-member override.

LEAN spawns a Member with ``--strict-mcp-config``: its MCP surface is EXACTLY
the firm's ``.mcp.json`` armory. FULL drops the flag, and the Member inherits
the operator's entire user-scope/plugin connector fleet — every Cowork/claude.ai
connector with its write/send/publish/e-sign power — on top of the firm armory,
under ``--dangerously-skip-permissions``. The firm's loadout stops bounding what
that Member can do. LEAN is the default and the safe path (fork:
cadre-loadout-posture-controls, 2026-07-16; origin ESC-086..093).

The only write path for either tier (Invariant #2) — the dashboard's Settings
switch and the Floor Manage switch both land here, so a posture change always
carries a Records row naming who changed it and from what.

Resolution lives in :func:`firm.pulse.spawn.resolve_posture` (member override →
firm row → legacy ``.firm/spawn.json`` → lean). This module owns the writes and
the read-time *effective* view the Board is shown; spawn.py owns the rule.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.pulse.spawn import FULL, LEAN, resolve_posture
from firm.services._records import log_event

_BOARD_ACTOR: dict[str, Any] = {"type": "board", "id": None}

VALID = (LEAN, FULL)


def _check(posture: Any, *, allow_inherit: bool = False) -> str | None:
    """Validate a posture from the wire. Never coerce — an unknown value is a
    caller bug, and silently defaulting it would decide a security posture on
    a typo's behalf.
    """
    if posture is None and allow_inherit:
        return None
    if posture not in VALID:
        raise ValueError(
            f"posture must be {' or '.join(map(repr, VALID))}"
            + (" or None to inherit" if allow_inherit else "")
            + f"; got {posture!r}"
        )
    return str(posture)


def set_firm_posture(
    conn: sqlite3.Connection,
    firm_id: str,
    posture: str | None,
    *,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Set the firm's DEFAULT posture — every member with no override runs it.

    Writing here shadows a legacy ``.firm/spawn.json`` for good: the DB is
    authoritative once the Board has stated a choice. The file is never
    rewritten (two stores would drift; one is honored, one is authoritative).

    ``posture`` is typed optional because it arrives off the wire, not because
    None is meaningful: there is no tier above the firm to inherit from, so
    None is rejected rather than quietly meaning "lean".
    """
    value = _check(posture)
    firm = repo.get(conn, "firm", firm_id)
    if not firm:
        raise ValueError(f"unknown firm {firm_id!r}")
    before = firm.get("loadout_posture")
    repo.update(conn, "firm", firm_id, {"loadout_posture": value})
    log_event(
        conn,
        firm_id=firm_id,
        event_type="firm.posture_updated",
        actor=actor or _BOARD_ACTOR,
        target_ref={"type": "firm", "id": firm_id},
        details={"from": before, "to": value},
    )
    return {"firm_id": firm_id, "posture": value, "from": before}


def set_member_posture(
    conn: sqlite3.Connection,
    member_id: str,
    posture: str | None,
    *,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Set (or clear) a Member's posture override.

    ``posture=None`` clears the override — the Member inherits the firm default
    again, which is the only way back to "whatever the firm says".
    """
    value = _check(posture, allow_inherit=True)
    member = repo.get(conn, "member", member_id)
    if not member:
        raise ValueError(f"unknown member {member_id!r}")
    before = member.get("loadout_posture")
    repo.update(conn, "member", member_id, {"loadout_posture": value})
    log_event(
        conn,
        firm_id=member["firm_id"],
        event_type="member.posture_updated",
        actor=actor or _BOARD_ACTOR,
        target_ref={"type": "member", "id": member_id},
        details={"from": before, "to": value},
    )
    return {
        "member_id": member_id,
        "posture": value,
        "from": before,
        "effective": effective_for_member(
            conn, member["firm_id"], member_id, workspace=None,
        )["posture"],
    }


def firm_default(
    conn: sqlite3.Connection,
    firm_id: str,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    """The firm's effective default posture, and where it came from.

    ``source`` is the honest provenance the Settings page shows: ``firm`` (the
    Board set it), ``legacy_file`` (inherited from ``.firm/spawn.json`` and never
    reviewed — the ESC-086 shape), or ``default`` (nothing set; lean).
    """
    firm = repo.get(conn, "firm", firm_id)
    stated = (firm or {}).get("loadout_posture")
    if stated in VALID:
        return {"posture": stated, "source": "firm", "stated": stated}
    cwd = str(workspace) if workspace else None
    resolved = resolve_posture(cwd=cwd)
    return {
        "posture": resolved,
        "source": "legacy_file" if resolved == FULL else "default",
        "stated": None,
    }


def effective_for_member(
    conn: sqlite3.Connection,
    firm_id: str,
    member_id: str,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    """What THIS member will actually spawn with, and why.

    The Board is always shown the resolved answer — a per-member control that
    displayed only the override would hide the firm default doing the deciding.
    """
    member = repo.get(conn, "member", member_id)
    override = (member or {}).get("loadout_posture")
    override = override if override in VALID else None
    default = firm_default(conn, firm_id, workspace)
    posture = override or default["posture"]
    return {
        "posture": posture,
        "override": override,
        "firm_default": default["posture"],
        "firm_default_source": default["source"],
        "source": "member" if override else default["source"],
    }


def roster_postures(
    conn: sqlite3.Connection,
    firm_id: str,
    workspace: Path | str | None = None,
) -> dict[str, dict[str, Any]]:
    """Effective posture for every member — the Floor's at-a-glance tag.

    Resolves the firm default ONCE rather than per member: this runs on the
    /api/state poll cadence, and the legacy fallback touches the filesystem.
    """
    default = firm_default(conn, firm_id, workspace)
    out: dict[str, dict[str, Any]] = {}
    for m in repo.find(conn, "member", firm_id=firm_id):
        override = m.get("loadout_posture")
        override = override if override in VALID else None
        out[m["id"]] = {
            "posture": override or default["posture"],
            "override": override,
            "firm_default": default["posture"],
            "source": "member" if override else default["source"],
        }
    return out
