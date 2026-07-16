"""``firm member grant|revoke authority`` — the canonical grant surface.

Thin CLI wrapper around ``firm.services.authority``. This verb is the
canonical way authority is granted; coboard consumes it, and the dashboard
toggle calls the same service.

Deliberately CLI-only. Granting is NEVER an MCP tool: an authority holder
that could mint authority is the same hole one level up. The service also
refuses any identified caller, so a Member shelling out to this command is
denied like any other Member path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from firm.core.db import connect, get_db_path
from firm.services.authority import (
    AuthorityError,
    grant_authority,
    has_authority,
    revoke_authority,
)


def run_member_authority(
    workspace: Path,
    member_id: str,
    *,
    grant: bool,
    comment: str | None = None,
) -> int:
    """Grant or revoke *member_id*'s authority key in the workspace firm DB.

    Idempotent: re-granting a held key is a no-op success.

    Returns 0 on success; 1 with a JSON error line on structured failure.
    """
    workspace = workspace.expanduser().resolve()
    db_path = get_db_path(workspace)
    if not db_path.exists():
        print(json.dumps({
            "ok": False,
            "reason": "db-not-found",
            "workspace": str(workspace),
        }), file=sys.stderr)
        return 1

    conn = connect(db_path)
    try:
        fn = grant_authority if grant else revoke_authority
        fn(conn, member_id, comment=comment)
        # Re-read through the same predicate the gate uses, rather than
        # re-deriving the shape here — the operator's confirmation must mean
        # what the gate will actually decide, blanket '*' grants included.
        print(json.dumps({
            "ok": True,
            "member_id": member_id,
            "authority": has_authority(conn, member_id),
        }, default=str))
        return 0
    except AuthorityError as exc:
        print(json.dumps({"ok": False, **exc.payload}), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(json.dumps({
            "ok": False, "reason": "error", "message": str(exc),
        }), file=sys.stderr)
        return 1
    finally:
        conn.close()
