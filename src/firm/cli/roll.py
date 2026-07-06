"""``cadre roll`` — the only legitimate dice in a Cadre game firm.

Game firms (The Table) bind a structural rule: models never generate dice
results. Every roll happens here — OS-grade randomness via ``secrets``,
resolved to a total, and written to Records in the same transaction the
result is reported. A roll that isn't in Records didn't happen; the
dashboard's dice ledger renders Records, so fabricated numbers have
nowhere to appear.

Usage::

    cadre roll 1d20+5 --reason "Fen: Sleight of Hand vs gate ledger" \
        --member MEM-004 --workspace ~/firms/dnd-table
    cadre roll 2d6+3 --adv --reason "..."   # roll twice, keep the better total
"""

from __future__ import annotations

import json
import re
import secrets
import sys
from pathlib import Path

from firm.core import repo
from firm.core.db import connect, get_db_path
from firm.services._records import log_event

_DICE_RE = re.compile(
    r"^\s*(?P<count>\d{1,2})d(?P<sides>\d{1,4})"
    r"(?:\s*(?P<sign>[+-])\s*(?P<mod>\d{1,4}))?\s*$",
    re.IGNORECASE,
)


def parse_dice(expr: str) -> tuple[int, int, int]:
    """Parse ``NdS[+/-M]`` → (count, sides, modifier). Raises ValueError."""
    m = _DICE_RE.match(expr)
    if not m:
        raise ValueError(f"cannot parse dice expression {expr!r} (expected NdS or NdS+M)")
    count, sides = int(m["count"]), int(m["sides"])
    if count < 1 or sides < 2:
        raise ValueError(f"degenerate dice {expr!r}")
    mod = int(m["mod"]) if m["mod"] else 0
    if m["sign"] == "-":
        mod = -mod
    return count, sides, mod


def _roll_once(count: int, sides: int, mod: int) -> tuple[list[int], int]:
    rolls = [secrets.randbelow(sides) + 1 for _ in range(count)]
    return rolls, sum(rolls) + mod


def run_roll(
    workspace: Path,
    expr: str,
    *,
    reason: str,
    member_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    advantage: bool = False,
    disadvantage: bool = False,
) -> int:
    """Roll *expr*, write the Records entry, print the result JSON.

    Returns 0 on success; 1 with a JSON error line on structured failure.
    """
    if advantage and disadvantage:
        print(json.dumps({"ok": False, "reason": "error",
                          "message": "--adv and --dis are mutually exclusive"}),
              file=sys.stderr)
        return 1

    workspace = workspace.expanduser().resolve()
    db_path = get_db_path(workspace)
    if not db_path.exists():
        print(json.dumps({"ok": False, "reason": "db-not-found",
                          "workspace": str(workspace)}), file=sys.stderr)
        return 1

    try:
        count, sides, mod = parse_dice(expr)
    except ValueError as exc:
        print(json.dumps({"ok": False, "reason": "error", "message": str(exc)}),
              file=sys.stderr)
        return 1

    conn = connect(db_path)
    try:
        firms = repo.find(conn, "firm")
        if len(firms) != 1:
            print(json.dumps({"ok": False, "reason": "error",
                              "message": "workspace must contain exactly one firm"}),
                  file=sys.stderr)
            return 1
        firm_id = firms[0]["id"]

        attempts = [_roll_once(count, sides, mod)]
        mode = "straight"
        if advantage or disadvantage:
            attempts.append(_roll_once(count, sides, mod))
            mode = "advantage" if advantage else "disadvantage"
        pick = max if advantage else min
        rolls, total = pick(attempts, key=lambda a: a[1]) if len(attempts) > 1 else attempts[0]

        details = {
            "expr": f"{count}d{sides}{mod:+d}" if mod else f"{count}d{sides}",
            "rolls": rolls,
            "modifier": mod,
            "total": total,
            "mode": mode,
            "reason": reason,
        }
        if mode != "straight":
            details["attempts"] = [{"rolls": r, "total": t} for r, t in attempts]

        actor = {"type": "member", "id": member_id} if member_id else {"type": "board", "id": None}
        target = {"type": target_type or "firm", "id": target_id or firm_id}
        record = log_event(
            conn,
            firm_id=firm_id,
            event_type="game.roll",
            actor=actor,
            target_ref=target,
            details=details,
        )
        conn.commit()
        print(json.dumps({"ok": True, "record_id": record["id"], **details}))
        return 0
    finally:
        conn.close()
