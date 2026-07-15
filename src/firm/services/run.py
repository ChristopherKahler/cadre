"""Member-run scoring service — the Board's post-run quality evaluation.

The sole write path for ``run_score``. One function serves the initial score and
every rescore: the column holds the *current* value, the immutable Records trail
(``run.scored`` / ``run.rescored``) preserves the history. Every dependent Floor
stat recomputes at read time — there is no cached aggregate to invalidate, so a
retroactive rescore is free.

Board evaluation, never member-authored. ``run_score`` must never appear on any
member-read surface (the firm MCP tools, the ``pulse/prompt.py`` renderers).
Structural blindness, not prompt wording (Invariant #5) — a Member that sees its
score games the score.

ID prefix: n/a (scores a member_run in place).
Records events: run.scored, run.rescored
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from firm.core import repo
from firm.services._records import log_event

SCORE_MIN = 1
SCORE_MAX = 5


def _validation(run: dict[str, Any]) -> dict[str, Any]:
    """Parse a run's ``validation_result`` (dict or JSON string) → dict."""
    vr = run.get("validation_result")
    if isinstance(vr, dict):
        return vr
    if isinstance(vr, str) and vr.strip():
        try:
            parsed = json.loads(vr)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def suggest_score(run: dict[str, Any]) -> int | None:
    """A pre-fill the operator confirms or overrides — derived from the run's
    own validation signal so rating is normally one tap, not homework.

    Mapping (tune freely; it is only a default):
      completed + validation passed, first try  → 4
      completed + validation passed after retry → 3
      completed, no validation signal           → 3  (neutral)
      completed + validation failed             → 2
      failed / timed_out                        → 2
    Returns None for a run with no terminal outcome (nothing to suggest).
    """
    status = run.get("status")
    if status in ("failed", "timed_out"):
        return 2
    if status == "completed":
        vr = _validation(run)
        passed = vr.get("passed")
        if passed is True:
            return 3 if vr.get("retry_triggered") else 4
        if passed is False:
            return 2
        return 3
    return None


def score_run(
    conn: sqlite3.Connection,
    run_id: str,
    score: Any,
    notes: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score (or rescore) a member run. Board write path only.

    Writes ``run_score`` + note + review provenance through ``repo.update`` and
    appends a ``run.scored`` (first time) or ``run.rescored`` (subsequent)
    Records entry. The previous value rides in the Records ``details`` so the
    immutable trail carries the full history the column cannot.

    Args:
        conn: SQLite connection with migrations applied.
        run_id: The member_run to score.
        score: Integer in ``[SCORE_MIN, SCORE_MAX]``.
        notes: Optional Board note (Board-only; never shown to the member).
        actor: {"type", "id"} — defaults to the Board.

    Raises:
        ValueError: run not found, or score not an int in range.
    """
    run = repo.get(conn, "member_run", run_id)
    if not run:
        raise ValueError(f"run {run_id!r} not found")

    try:
        score_int = int(score)
    except (TypeError, ValueError):
        raise ValueError(f"score must be an integer {SCORE_MIN}-{SCORE_MAX}")
    if not SCORE_MIN <= score_int <= SCORE_MAX:
        raise ValueError(
            f"score {score_int} out of range {SCORE_MIN}-{SCORE_MAX}"
        )

    actor = actor or {"type": "board", "id": None}
    previous = run.get("run_score")
    rescore = previous is not None
    now = datetime.now(tz=timezone.utc).isoformat()

    updated = repo.update(conn, "member_run", run_id, {
        "run_score": score_int,
        "run_score_notes": notes or None,
        "reviewed_at": now,
        "reviewed_by": actor.get("id"),
    })
    assert updated is not None, "member_run disappeared after repo.get"

    log_event(
        conn,
        firm_id=run["firm_id"],
        event_type="run.rescored" if rescore else "run.scored",
        actor=actor,
        target_ref={"type": "member_run", "id": run_id},
        details={"score": score_int, "previous": previous, "has_note": bool(notes)},
        run_id=run_id,
    )
    return updated
