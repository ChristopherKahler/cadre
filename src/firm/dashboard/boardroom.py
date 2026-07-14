"""Boardroom payload — the portfolio as the Board reads it.

``hub_summary`` (server.py) answers *how is the machine*: members, spend,
running, last run. That is telemetry, and it is what the current landing page
shows. A Board walking in at 7am wants a different question answered — *what
did my companies do, and what do they need from me.*

This module wraps hub_summary and adds the answer: the open decisions in the
Board's own words, and the last things each firm actually produced. The old
payload is untouched; this is served alongside it at ``/api/next/hub``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.core.db import connect

_RECENT_LIMIT = 3
_WEEK = timedelta(days=7)


def _when(row: dict[str, Any], *fields: str) -> str | None:
    for f in fields:
        v = row.get(f)
        if v:
            return str(v)
    return None


def _aware(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _decisions(conn: sqlite3.Connection, fid: str,
               names: dict[str, str]) -> list[dict[str, Any]]:
    """What the Board is being asked — in the asker's words, not an entity id.

    A Gate carries the ``action`` it wants approved; an escalation carries the
    ``title`` of what it's stuck on. Those strings were written by a Member for
    a human to read, which is exactly what a decision card needs.
    """
    out: list[dict[str, Any]] = []
    for g in repo.find(conn, "gate", firm_id=fid):
        if g.get("status") != "pending" or g.get("dismissed_at"):
            continue
        out.append({
            "kind": "gate",
            "id": g.get("id"),
            "who": names.get(g.get("requesting_member_id"), "Someone"),
            "ask": g.get("action") or "wants a decision",
            "raised_at": _when(g, "created_at"),
        })
    for e in repo.find(conn, "escalation", firm_id=fid):
        if e.get("status") not in ("open", "acknowledged"):
            continue
        out.append({
            "kind": "escalation",
            "id": e.get("id"),
            "who": names.get(e.get("raised_by_member_id"), "Someone"),
            "ask": e.get("title") or "is blocked",
            "severity": e.get("severity") or "normal",
            "raised_at": _when(e, "created_at"),
        })
    out.sort(key=lambda d: d["raised_at"] or "", reverse=True)
    return out


def _shipped(conn: sqlite3.Connection, fid: str) -> list[dict[str, Any]]:
    """The last things this firm actually produced. Deliverables, then done work."""
    items: list[dict[str, Any]] = []
    for d in repo.find(conn, "document", firm_id=fid):
        items.append({
            "what": d.get("title") or d.get("name") or "Untitled document",
            "at": _when(d, "created_at", "updated_at"),
        })
    if len(items) < _RECENT_LIMIT:
        for u in repo.find(conn, "unit", firm_id=fid):
            if u.get("status") != "done":
                continue
            items.append({
                "what": u.get("name") or "Unnamed unit",
                "at": _when(u, "completed_at", "updated_at", "created_at"),
            })
    items.sort(key=lambda i: i["at"] or "", reverse=True)
    return items[:_RECENT_LIMIT]


def _roster(conn: sqlite3.Connection, fid: str) -> dict[str, str]:
    return {m["id"]: m.get("name") or m["id"]
            for m in repo.find(conn, "member", firm_id=fid)}


def _in_flight(conn: sqlite3.Connection, fid: str,
               names: dict[str, str]) -> list[dict[str, Any]]:
    """Who is at their desk right now, and on what."""
    units = {u["id"]: u.get("name") for u in repo.find(conn, "unit", firm_id=fid)}
    out = []
    for r in repo.find(conn, "member_run", firm_id=fid, status="running"):
        out.append({
            "who": names.get(r.get("member_id"), "Someone"),
            "on": units.get(r.get("unit_id")) or "work in progress",
        })
    return out


def _week_output(conn: sqlite3.Connection, fid: str) -> int:
    cutoff = datetime.now(tz=timezone.utc) - _WEEK
    n = 0
    for u in repo.find(conn, "unit", firm_id=fid):
        if u.get("status") != "done":
            continue
        at = _aware(_when(u, "completed_at", "updated_at"))
        if at and at >= cutoff:
            n += 1
    return n


def enrich(cards: list[dict[str, Any]],
           firms: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold Board-facing narrative onto the telemetry cards from hub_summary."""
    for card in cards:
        info = firms.get(card["id"])
        if not info:
            continue
        try:
            conn = connect(Path(info["db_path"]))
        except Exception:
            continue
        try:
            names = _roster(conn, card["id"])
            card["decisions"] = _decisions(conn, card["id"], names)
            card["shipped"] = _shipped(conn, card["id"])
            card["in_flight"] = _in_flight(conn, card["id"], names)
            card["shipped_this_week"] = _week_output(conn, card["id"])
        except Exception:
            # A firm whose DB predates a table must not take the boardroom down —
            # the 2026-07-08 lesson: one bad row 500'd the entire dashboard.
            card.setdefault("decisions", [])
            card.setdefault("shipped", [])
            card.setdefault("in_flight", [])
            card.setdefault("shipped_this_week", 0)
        finally:
            conn.close()
    return cards
