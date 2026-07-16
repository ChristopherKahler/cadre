"""Signal-quality loop — dashboard items become member performance marks.

A firm dashboard surfaces items (a morning-page ask, a leak on the signal
view) that carry WHO found them (``surfaced_by``) and who curated them onto
the page (``presented_by``). When the Board resolves one in place, the
verdict closes three loops at once:

1. an immutable Records entry (``signal.resolved`` / ``signal.dismissed``)
   — the resolution ledger the views filter against and the Floor derives
   per-member signal-quality stats from (Floor law 2: derived, never
   authored);
2. a Board comment to the surfacing member (and the presenter, when
   distinct) — delivered on their next run via the Standing Notes section
   of the spawn prompt. The comment carries the verdict and the Board's
   note, NEVER the aggregate scoreboard (a member that knows the score
   games the score);
3. reconciliation with the item's declared source — an ``refs.escalation``
   is resolved alongside so the dashboard and the boardroom never disagree
   about what is open; a ``refs.unit`` gets a Board comment, never an
   auto-close (unit completion stays behind its validation gate).

Verdicts are deliberately binary — ``real`` or ``noise`` — the Board's
note carries any nuance. Dismissals mark the counter-stat so the metric
cannot be gamed by volume.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from firm.core import repo
from firm.services import comment as comment_svc
from firm.services import escalation as escalation_svc
from firm.services._records import log_event

EVENT_RESOLVED = "signal.resolved"
EVENT_DISMISSED = "signal.dismissed"
VERDICTS = {"real": EVENT_RESOLVED, "noise": EVENT_DISMISSED}


def find_view_item(payload: Any, item_id: str) -> dict[str, Any] | None:
    """Deep-walk a view file's JSON for the dict whose ``id`` == *item_id*.

    The server passes the DECLARED file contents, so attribution and refs
    are read from the firm's own data — the browser only ever names the
    item and the verdict, it cannot supply who gets the credit.
    """
    if isinstance(payload, dict):
        if payload.get("id") == item_id:
            return payload
        for v in payload.values():
            hit = find_view_item(v, item_id)
            if hit is not None:
                return hit
    elif isinstance(payload, list):
        for v in payload:
            hit = find_view_item(v, item_id)
            if hit is not None:
                return hit
    return None


def _signal_records(
    conn: sqlite3.Connection, firm_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, event_type, details, timestamp FROM records "
        "WHERE firm_id = ? AND event_type IN (?, ?) ORDER BY timestamp",
        (firm_id, EVENT_RESOLVED, EVENT_DISMISSED),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["details"] = json.loads(d.get("details") or "{}")
        except (TypeError, json.JSONDecodeError):
            d["details"] = {}
        out.append(d)
    return out


def view_resolutions(
    conn: sqlite3.Connection, firm_id: str, view_id: str,
) -> list[dict[str, Any]]:
    """Every resolution for one view — what the fragment filters against."""
    return [
        {
            "item_id": r["details"].get("item_id"),
            "verdict": r["details"].get("verdict"),
            "record_id": r["id"],
            "at": r["timestamp"],
        }
        for r in _signal_records(conn, firm_id)
        if r["details"].get("view") == view_id
    ]


def resolve_view_item(
    conn: sqlite3.Connection,
    firm_id: str,
    view_id: str,
    item: dict[str, Any],
    verdict: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Board verdict on a dashboard item. Raises ValueError on bad input.

    *item* is the server-resolved dict from the view's declared files —
    never the request body. Idempotent per (view, item): a second verdict
    on the same item returns the existing resolution as a conflict.
    """
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {sorted(VERDICTS)}, got {verdict!r}")
    item_id = str(item.get("id") or "")
    if not item_id:
        raise ValueError("item has no id")

    existing = [
        r for r in view_resolutions(conn, firm_id, view_id)
        if r["item_id"] == item_id
    ]
    if existing:
        return {"ok": False, "conflict": existing[0],
                "error": f"item {item_id!r} already resolved"}

    label = str(item.get("ask") or item.get("title") or item_id)
    surfacer = item.get("surfaced_by") or None
    presenter = item.get("presented_by") or None
    raw_refs = item.get("refs")
    refs: dict[str, Any] = raw_refs if isinstance(raw_refs, dict) else {}

    # 2 — inform the humans-in-silicon. Verdict + note only; no scoreboard.
    informed: list[str] = []
    if surfacer:
        body = (
            f"Signal feedback — your find “{label}” on the {view_id} "
            f"dashboard was resolved by the Board as REAL SIGNAL."
            if verdict == "real" else
            f"Signal feedback — your item “{label}” on the {view_id} "
            f"dashboard was dismissed as noise. Recalibrate what earns "
            f"dashboard space: measured, actionable, decision-relevant."
        )
        if note:
            body += f" Board note: {note}"
        informed.append(comment_svc.create_comment(conn, firm_id, {
            "parent_entity_type": "member", "parent_entity_id": surfacer,
            "author_type": "board", "body": body,
        })["id"])
    if presenter and presenter != surfacer:
        body = (
            f"Presentation assist — “{label}”, which you put on the "
            f"{view_id} dashboard, was resolved by the Board as real signal. "
            f"Good curation."
            if verdict == "real" else
            f"Presentation feedback — “{label}”, which you put on the "
            f"{view_id} dashboard, was dismissed as noise. Curation is the "
            f"filter: tighten what makes the page."
        )
        if note:
            body += f" Board note: {note}"
        informed.append(comment_svc.create_comment(conn, firm_id, {
            "parent_entity_type": "member", "parent_entity_id": presenter,
            "author_type": "board", "body": body,
        })["id"])

    # 3 — reconcile the underlying source so neither side zombies. Items name
    # escalations either as refs.escalation (one) or escalation_refs (the
    # morning page's existing list convention) — honor both.
    reconciled: dict[str, Any] = {}
    esc_ids = [str(e) for e in (item.get("escalation_refs") or []) if e]
    if refs.get("escalation") and str(refs["escalation"]) not in esc_ids:
        esc_ids.append(str(refs["escalation"]))
    esc_outcomes = []
    for esc_id in esc_ids:
        esc = repo.get(conn, "escalation", esc_id)
        if esc is None:
            esc_outcomes.append({"id": esc_id, "error": "unknown escalation"})
        elif esc.get("status") == "resolved":
            esc_outcomes.append({"id": esc_id, "already": "resolved"})
        else:
            escalation_svc.resolve_escalation(
                conn, esc_id, status="resolved",
                resolution=(
                    f"Actioned from the {view_id} dashboard "
                    f"({'real signal' if verdict == 'real' else 'dismissed as noise'})"
                    + (f": {note}" if note else ".")
                ),
            )
            esc_outcomes.append({"id": esc_id, "resolved": True})
    if esc_outcomes:
        reconciled["escalations"] = esc_outcomes
        # Single-ref items keep the flat key readers already know.
        if len(esc_outcomes) == 1:
            reconciled["escalation"] = esc_outcomes[0]
    unit_id = refs.get("unit")
    if unit_id:
        unit = repo.get(conn, "unit", str(unit_id))
        if unit is None:
            reconciled["unit"] = {"id": unit_id, "error": "unknown unit"}
        else:
            # Never auto-close: completion stays behind the validation gate.
            comment_svc.create_comment(conn, firm_id, {
                "parent_entity_type": "unit", "parent_entity_id": str(unit_id),
                "author_type": "board",
                "body": (
                    f"Dashboard resolution — “{label}” ({view_id}) was "
                    f"{'resolved as real signal' if verdict == 'real' else 'dismissed as noise'}"
                    + (f": {note}" if note else ".")
                ),
            })
            reconciled["unit"] = {"id": unit_id, "commented": True}

    # 1 — the ledger entry, written last so it carries the full outcome.
    record = log_event(
        conn,
        firm_id=firm_id,
        event_type=VERDICTS[verdict],
        actor={"type": "board", "id": None},
        target_ref=(
            {"type": "member", "id": surfacer} if surfacer
            else {"type": "firm", "id": firm_id}
        ),
        details={
            "view": view_id, "item_id": item_id, "verdict": verdict,
            "label": label, "note": note or None,
            "surfaced_by": surfacer, "presented_by": presenter,
            "refs": refs or None, "informed": informed,
            "reconciled": reconciled or None,
        },
    )
    return {"ok": True, "record": record, "informed": informed,
            "reconciled": reconciled}


def signal_marks(
    conn: sqlite3.Connection, firm_id: str,
) -> dict[str, dict[str, int]]:
    """Per-member signal-quality tallies, derived from the records ledger.

    Board-facing only — feeds the Floor stats; never a member surface.
    ``real``/``noise`` credit the finder; ``assists`` credit a distinct
    presenter whose curated item was resolved as real.
    """
    marks: dict[str, dict[str, int]] = {}

    def bucket(member_id: str) -> dict[str, int]:
        return marks.setdefault(member_id, {"real": 0, "noise": 0, "assists": 0})

    for r in _signal_records(conn, firm_id):
        d = r["details"]
        verdict = d.get("verdict")
        surfacer, presenter = d.get("surfaced_by"), d.get("presented_by")
        if surfacer and verdict in ("real", "noise"):
            bucket(surfacer)[verdict] += 1
        if presenter and presenter != surfacer and verdict == "real":
            bucket(presenter)["assists"] += 1
    return marks
