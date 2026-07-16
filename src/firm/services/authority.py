"""Authority key — the identity gate on the self-govern management surface.

A spawned Member could otherwise mark its own Unit done, resolve the
Escalation raised about its own work, or rewrite the Goals it is measured
against. This module gates those actions on a Board-granted capability
carried in the Member's autonomy block.

Storage is the sovereign-override contract from :mod:`firm.services.autonomy`
— ``member.autonomy`` = ``{"sovereign": ["*"] | [capability, ...]}``. The
authority key is the ``"authority"`` token in that list; ``"*"`` (blanket
sovereignty) implies it. Sharing that block rather than forking a sibling key
keeps this schema-compatible with the Calibration Ladder, which derives trust
tiers from the same override.

Identity comes from the process environment (``CADRE_MEMBER_ID``), stamped by
:func:`firm.pulse.spawn.spawn_member_run`. The firm MCP server is a stdio
server spawned per-client, so it inherits the Member's env; a Bash subshell
inside a Member run inherits it too. One env read therefore covers every way
a Member reaches the service layer.

Three caller classes:

- **No identity** (Board, CLI, dashboard, the pulse harness) — always passes.
- **Identified + key** — passes the gated tools.
- **Identified, no key** — denied with a structured payload it can act on.

Gate decisions and grant/revoke are *board-only*: the key does NOT unlock
them. Grant/revoke is deliberately absent from the MCP surface, and
:func:`require_board_only` closes the CLI path too — otherwise an authority
holder could shell out and mint its own authority, which is the same hole one
level up.

Scope, honestly: Members run with ``--dangerously-skip-permissions``, so a
determined one can bypass all of this by opening the SQLite file directly.
This gates the *tool surface* — the path a model actually reaches for. It is
a guardrail against overreach, not a sandbox against an adversary.

Records events: member.authority_granted, member.authority_revoked (plus
member.autonomy_updated from the shared autonomy write path).
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import sqlite3
from typing import Any, Iterator

from firm.core import repo
from firm.services._records import log_event
from firm.services._validate import require_exists
from firm.services.autonomy import _load, set_sovereign_override

#: Env var carrying the acting Member's ID into a spawned run.
MEMBER_ID_ENV = "CADRE_MEMBER_ID"

#: The capability token that unlocks the gated management tools.
AUTHORITY_CAPABILITY = "authority"

#: Blanket sovereignty — the Board granting everything at once. Implies the
#: authority key without naming it.
BLANKET = "*"

#: True while the harness is acting on its own behalf inside a Member run's
#: process tree. See :func:`system_context`.
_system_actor: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "cadre_system_actor", default=False,
)


class AuthorityError(ValueError):
    """An identified caller was denied a gated action.

    Subclasses ValueError so the existing error paths (the MCP ``_safe``
    wrapper, the dashboard's 400 handler) surface it without new plumbing.
    ``payload`` carries the structured denial a Member run can recover from.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__(payload["error"])


@contextlib.contextmanager
def system_context() -> Iterator[None]:
    """Mark the enclosing block as the harness acting, not the Member.

    The pulse runner completes a validated Unit itself — "the harness, not
    the model, is the completion authority". That call passes the gate today
    because the pulse process carries no ``CADRE_MEMBER_ID``, but it would
    self-deny the moment a pulse is fired from inside a Member run (Members
    have a shell). Declaring the identity beats inheriting it by accident.

    Not reachable by a Member: this is in-process state, and a Member only
    ever reaches the service layer from a separate process.
    """
    token = _system_actor.set(True)
    try:
        yield
    finally:
        _system_actor.reset(token)


def caller_member_id() -> str | None:
    """The acting Member's ID, or None for Board / CLI / dashboard / harness."""
    if _system_actor.get():
        return None
    return os.environ.get(MEMBER_ID_ENV, "").strip() or None


def sovereign_capabilities(
    conn: sqlite3.Connection, member_id: str,
) -> list[str]:
    """The Board's authored override list for *member_id*.

    ``["*"]`` = blanket sovereignty; else the capabilities granted directly.
    ``[]`` = no override. Mirrors ``dashboard.calibration.sovereign_capabilities``
    on the read side; a malformed block grants nothing rather than crashing.
    """
    member = repo.get(conn, "member", member_id)
    if not member:
        return []
    sov = _load(member.get("autonomy")).get("sovereign")
    return [str(s) for s in sov] if isinstance(sov, list) else []


def _holds(capabilities: list[str]) -> bool:
    return BLANKET in capabilities or AUTHORITY_CAPABILITY in capabilities


def has_authority(conn: sqlite3.Connection, member_id: str) -> bool:
    """Whether *member_id* holds the authority key. Unknown member → False."""
    return _holds(sovereign_capabilities(conn, member_id))


def require_authority(conn: sqlite3.Connection, action: str) -> str | None:
    """Gate *action* on the caller's authority key.

    Returns the acting Member's ID, or None when the caller has no identity
    (which always passes).

    Raises:
        AuthorityError: Caller is an identified Member without the key.
    """
    member_id = caller_member_id()
    if member_id is None:
        return None
    if not has_authority(conn, member_id):
        raise AuthorityError({
            "error": "authority_required",
            "hint": "escalate via firm_escalate",
            "action": action,
        })
    return member_id


def require_board_only(action: str, *, hint: str) -> None:
    """Lock *action* to callers with no identity. The key does NOT unlock it.

    Raises:
        AuthorityError: Any identified Member caller, key or not.
    """
    member_id = caller_member_id()
    if member_id is None:
        return
    raise AuthorityError({
        "error": "board_only",
        "hint": hint,
        "action": action,
    })


# ---------------------------------------------------------------------------
# Grant / revoke — the one code path behind both the CLI verb and the
# dashboard toggle. Never an MCP tool.
# ---------------------------------------------------------------------------

_GRANT_HINT = (
    "authority is granted by the Board — ask via firm_escalate; "
    "members cannot grant it to themselves or each other"
)


def grant_authority(
    conn: sqlite3.Connection,
    member_id: str,
    *,
    comment: str | None = None,
) -> dict[str, Any]:
    """Grant the authority key to a Member. Board-only. Idempotent."""
    return _set_authority(conn, member_id, True, comment=comment)


def revoke_authority(
    conn: sqlite3.Connection,
    member_id: str,
    *,
    comment: str | None = None,
) -> dict[str, Any]:
    """Revoke the authority key from a Member. Board-only. Idempotent.

    Raises:
        ValueError: The Member holds blanket sovereignty (``"*"``). Dropping
            the ``authority`` token would leave the key in force via the
            blanket grant, so this refuses rather than report a revoke that
            did not happen.
    """
    return _set_authority(conn, member_id, False, comment=comment)


def _set_authority(
    conn: sqlite3.Connection,
    member_id: str,
    granted: bool,
    *,
    comment: str | None = None,
) -> dict[str, Any]:
    require_board_only(
        "member.authority_granted" if granted else "member.authority_revoked",
        hint=_GRANT_HINT,
    )
    member = require_exists(conn, "member", member_id)
    caps = sovereign_capabilities(conn, member_id)

    if not granted and BLANKET in caps:
        raise ValueError(
            f"{member_id} holds blanket sovereignty ('*'), which grants "
            f"authority regardless of the '{AUTHORITY_CAPABILITY}' token — "
            "dropping the token would revoke nothing. Clear the sovereign "
            "override instead."
        )

    if _holds(caps) == granted:
        return member  # no state change is not a governance event

    if granted:
        caps = [*caps, AUTHORITY_CAPABILITY]
    else:
        caps = [c for c in caps if c != AUTHORITY_CAPABILITY]

    # The shared autonomy write path: normalizes, persists, and records
    # member.autonomy_updated. The authority event below rides on top of it
    # because that one carries the Board's reason, which the audit trail and
    # board pack need and the generic autonomy event has no field for.
    updated = set_sovereign_override(conn, member_id, caps)

    log_event(
        conn,
        firm_id=member["firm_id"],
        event_type="member.authority_granted" if granted else "member.authority_revoked",
        actor={"type": "board", "id": None},
        target_ref={"type": "member", "id": member_id},
        details={"authority": granted, "comment": comment, "sovereign": caps},
    )
    return updated
