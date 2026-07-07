"""Generative-spend ledger service — the firm-agnostic record of API cost.

Callers pass raw *units* + context; the platform's adapter
(firm.services.gen_adapters) supplies kind / unit_label / $ cost. The boardroom
reads `summary` (one row per platform) and `history` (drill-down).

Works over sqlite and the libsql compat shim (Turso firms) — plain
`conn.execute`, no repo layer, since this is a high-volume append-only log.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from firm.services import gen_adapters


def _now() -> str:
    return datetime.now(tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def record(
    conn: Any,
    firm_id: str,
    *,
    platform: str,
    units: float,
    asset_path: str | None = None,
    member_id: str | None = None,
    ref: str | None = None,
    meta: dict[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Append one generation to the ledger. `units` is raw (chars/images/…);
    the adapter derives kind, unit_label, and cost. Unknown platforms still
    log (cost 0, kind 'unknown') so nothing is silently dropped."""
    a = gen_adapters.get(platform)
    kind = a.kind if a else "unknown"
    unit_label = a.unit_label if a else None
    cost = a.cost(units) if a else 0.0
    conn.execute(
        "INSERT INTO gen_spend "
        "(firm_id, platform, kind, units, unit_label, cost_usd, asset_path, "
        " member_id, ref, meta, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (firm_id, platform, kind, float(units), unit_label, cost, asset_path,
         member_id, ref, json.dumps(meta) if meta else None, now or _now()))
    return {"platform": platform, "kind": kind, "units": units, "cost_usd": cost}


_SUMMARY_COLS = ["platform", "kind", "unit_label", "events", "units",
                 "cost_usd", "last_at"]


def summary(conn: Any, firm_id: str, *, since: str | None = None,
            with_balance: bool = False) -> list[dict[str, Any]]:
    """One aggregated row per platform for *firm_id* — the boardroom line
    items. Newest-spend-first. `with_balance` probes each adapter's live
    balance (a network call) — leave it off in the hot state-poll path and
    turn it on for the dedicated meter endpoint."""
    q = ("SELECT platform, MAX(kind), MAX(unit_label), COUNT(*), "
         "COALESCE(SUM(units),0), COALESCE(SUM(cost_usd),0), MAX(created_at) "
         "FROM gen_spend WHERE firm_id = ?")
    args: list[Any] = [firm_id]
    if since:
        q += " AND created_at >= ?"
        args.append(since)
    q += " GROUP BY platform ORDER BY 6 DESC"
    out = []
    for row in conn.execute(q, args).fetchall():
        d = dict(zip(_SUMMARY_COLS, tuple(row)))
        a = gen_adapters.get(d["platform"])
        d["label"] = a.display() if a else d["platform"]
        d["balance"] = a.balance() if (with_balance and a and a.balance) else None
        out.append(d)
    return out


_HISTORY_COLS = ["id", "kind", "units", "unit_label", "cost_usd",
                 "asset_path", "member_id", "ref", "created_at"]


def history(conn: Any, firm_id: str, platform: str, *, limit: int = 200) -> list[dict[str, Any]]:
    """Per-platform event log for the drill-down (roster attribution + the
    actual asset paths → the operator's library)."""
    rows = conn.execute(
        "SELECT id, kind, units, unit_label, cost_usd, asset_path, member_id, "
        "ref, created_at FROM gen_spend WHERE firm_id = ? AND platform = ? "
        "ORDER BY id DESC LIMIT ?", (firm_id, platform, int(limit))).fetchall()
    return [dict(zip(_HISTORY_COLS, tuple(r))) for r in rows]
