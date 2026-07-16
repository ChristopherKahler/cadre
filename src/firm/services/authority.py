"""Authority key — the identity gate on the self-govern management surface.

A spawned Member could otherwise mark its own Unit done, resolve the
Escalation raised about its own work, or rewrite the Goals it is measured
against. This module gates those actions on a Board-granted key living on
the Member record (``member.autonomy["authority"]``).

Identity comes from the process environment (``CADRE_MEMBER_ID``), stamped
by :func:`firm.pulse.spawn.spawn_member_run`. The firm MCP server is a stdio
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
holder could shell out and mint its own authority, which is the same hole
one level up.

Scope, honestly: Members run with ``--dangerously-skip-permissions``, so a
determined one can bypass all of this by opening the SQLite file directly.
This gates the *tool surface* — the path a model actually reaches for. It is
a guardrail against overreach, not a sandbox against an adversary.

Records events: member.authority_granted, member.authority_revoked
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

#: Env var carrying the acting Member's ID into a spawned run.
MEMBER_ID_ENV = "CADRE_MEMBER_ID"

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


def has_authority(conn: sqlite3.Connection, member_id: str) -> bool:
    """Whether *member_id* holds the authority key. Unknown member → False."""
    member = repo.get(conn, "member", member_id)
    if not member:
        return False
    autonomy = member.get("autonomy")
    # repo keeps the raw string when a JSON column fails to parse — a
    # malformed autonomy blob grants nothing rather than crashing the run.
    if not isinstance(autonomy, dict):
        return False
    return autonomy.get("authority") is True


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
    """Revoke the authority key from a Member. Board-only. Idempotent."""
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

    autonomy = member.get("autonomy")
    if not isinstance(autonomy, dict):
        autonomy = {}

    if (autonomy.get("authority") is True) == granted:
        return member  # no state change is not a governance event

    updated = repo.update(
        conn, "member", member_id, {"autonomy": {**autonomy, "authority": granted}},
    )
    assert updated is not None, "member disappeared after require_exists"

    # Logged only on a real transition, matching update_member's
    # status_transition convention — the Records trail carries changes.
    log_event(
        conn,
        firm_id=member["firm_id"],
        event_type="member.authority_granted" if granted else "member.authority_revoked",
        actor={"type": "board", "id": None},
        target_ref={"type": "member", "id": member_id},
        details={"authority": granted, "comment": comment},
    )
    return updated
