"""SessionStart pulse renderer — produces up to three injection tags.

Read-only. No DB writes. Designed to be driven by a thin entrypoint script
that Claude Code fires on ``SessionStart:startup`` (see ``install/``).

Tag specs are locked in ``.paul/phases/02-hook-layer/02-01-BRIEF.md`` §2.1–2.3.
If you change output here, also update ``tests/golden/session-pulse-chrisai.txt``
so the e2e test reflects intent rather than regression.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from .render import classify_expiry, resolve_entity_name, time_ago

# ---------------------------------------------------------------------------
# <active-roster>
# ---------------------------------------------------------------------------

_ACTIVE_ROSTER_SQL = """
SELECT
  m.id               AS member_id,
  m.name             AS member_name,
  m.role             AS member_role,
  m.status           AS member_status,
  m.reports_to_member_id,
  manager.name       AS manager_name,
  c.runtime_config   AS runtime_config_json,
  u.id               AS claimed_unit_id,
  u.name             AS claimed_unit_name,
  u.status           AS claimed_unit_status
FROM member m
LEFT JOIN contract c ON c.id = m.contract_id
LEFT JOIN member manager ON manager.id = m.reports_to_member_id
LEFT JOIN unit u ON u.claimed_by = m.id AND u.firm_id = m.firm_id
WHERE m.firm_id = ? AND m.status = 'active'
ORDER BY
  CASE WHEN m.reports_to_member_id IS NULL THEN 0 ELSE 1 END,
  COALESCE(m.reports_to_member_id, ''),
  m.id
"""

_ROSTER_BEHAVIOR = (
    "BEHAVIOR: This context is PASSIVE AWARENESS ONLY.\n"
    "Do NOT proactively mention roster state unless the user asks who's working on what\n"
    "or a Member's current Unit is blocked."
)


def _entry_command(runtime_config_json: str | None) -> str | None:
    if not runtime_config_json:
        return None
    try:
        cfg = json.loads(runtime_config_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(cfg, dict):
        return None
    cmd = cfg.get("entry_command")
    return cmd if isinstance(cmd, str) and cmd else None


def _fetch_operator(conn: sqlite3.Connection, firm_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT operator FROM firm WHERE id = ?", (firm_id,)
    ).fetchone()
    if row is None:
        return None
    raw = row["operator"] if isinstance(row, sqlite3.Row) else row[0]
    if not raw:
        return None
    try:
        op = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return op if isinstance(op, dict) else None


def _render_member_line(row: sqlite3.Row, is_contributor: bool) -> str:
    mem_id = row["member_id"]
    name = row["member_name"]
    role = row["member_role"]
    if row["runtime_config_json"]:
        entry = _entry_command(row["runtime_config_json"]) or "(no entry command)"
    else:
        entry = "(no contract wired yet)"
    prefix = f"  - [{mem_id}] {name} ({role})"
    if is_contributor and row["manager_name"]:
        prefix += f" reports to {row['manager_name']}"
    line = f"{prefix} — {entry}"
    if row["claimed_unit_id"]:
        line += (
            f"\n    CURRENTLY ON: [{row['claimed_unit_id']}] "
            f"{row['claimed_unit_name']} ({row['claimed_unit_status']})"
        )
    return line


def render_active_roster(conn: sqlite3.Connection, firm_id: str) -> str | None:
    """Render the ``<active-roster>`` block. Returns None when no active Members."""
    rows = conn.execute(_ACTIVE_ROSTER_SQL, (firm_id,)).fetchall()
    # Dedupe on member_id — the unit LEFT JOIN can in theory produce duplicates
    # if a Member has multiple claimed units (atomic checkout prevents this in
    # normal flow, but nothing at the schema level forbids it).
    seen: set[str] = set()
    deduped: list[sqlite3.Row] = []
    for r in rows:
        if r["member_id"] in seen:
            continue
        seen.add(r["member_id"])
        deduped.append(r)

    if not deduped:
        return None

    lines: list[str] = [f'<active-roster members="{len(deduped)}">']
    operator = _fetch_operator(conn, firm_id)
    if operator and operator.get("name"):
        op_role = operator.get("role", "Board")
        lines.append(f"[BOARD] — {operator['name']} ({op_role})")

    managers = [r for r in deduped if r["reports_to_member_id"] is None]
    contributors = [r for r in deduped if r["reports_to_member_id"] is not None]

    if managers:
        lines.append("[MANAGERS]")
        for r in managers:
            lines.append(_render_member_line(r, is_contributor=False))
    if contributors:
        lines.append("[INDIVIDUAL CONTRIBUTORS]")
        for r in contributors:
            lines.append(_render_member_line(r, is_contributor=True))

    lines.append("")
    lines.append(_ROSTER_BEHAVIOR)
    lines.append("</active-roster>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# <pending-gates>
# ---------------------------------------------------------------------------

_PENDING_GATES_SQL = """
SELECT
  g.id                    AS gate_id,
  g.action                AS action,
  g.context               AS context,
  g.target_entity_type    AS target_entity_type,
  g.target_entity_id      AS target_entity_id,
  g.expires_at            AS expires_at,
  g.created_at            AS created_at,
  m.name                  AS requesting_member_name
FROM gate g
JOIN member m ON m.id = g.requesting_member_id
WHERE g.firm_id = ? AND g.status = 'pending'
ORDER BY
  CASE
    WHEN g.expires_at IS NOT NULL AND g.expires_at < datetime('now') THEN 0
    WHEN g.expires_at IS NOT NULL THEN 1
    ELSE 2
  END,
  g.expires_at,
  g.created_at ASC
"""

_GATES_BEHAVIOR = (
    "BEHAVIOR: This context is PASSIVE AWARENESS ONLY.\n"
    "Do NOT proactively mention pending gates unless the user asks about approvals\n"
    "OR a gate is expired and unacknowledged this session.\n"
    "Use /gate:decide {id} approve|reject \"{comment}\" to act."
)


def _render_gate_line(
    conn: sqlite3.Connection, row: sqlite3.Row, now: datetime
) -> str:
    target_name = (
        resolve_entity_name(conn, row["target_entity_type"], row["target_entity_id"])
        or "(target missing)"
    )
    expiry_note: str
    if row["expires_at"]:
        expiry_note = time_ago(row["expires_at"], now=now)
        if classify_expiry(row["expires_at"], now=now) == "EXPIRED":
            expiry_note = f"expired {expiry_note.replace(' ago', '')} ago"
        else:
            # time_ago returns "in Xh" for future — render as "expires in Xh"
            expiry_note = expiry_note.replace("in ", "expires in ")
    else:
        expiry_note = "no expiry"

    line = (
        f'  - [{row["gate_id"]}] {row["action"]} on {row["target_entity_type"]} '
        f'"{target_name}" (requested by {row["requesting_member_name"]}, '
        f'{expiry_note})'
    )
    if row["context"]:
        line += f"\n    Context: {row['context']}"
    return line


def render_pending_gates(
    conn: sqlite3.Connection,
    firm_id: str,
    now: datetime | None = None,
) -> str | None:
    """Render the ``<pending-gates>`` block. Silent (None) when zero pending Gates."""
    rows = conn.execute(_PENDING_GATES_SQL, (firm_id,)).fetchall()
    if not rows:
        return None

    from .render import _utcnow_naive  # local import to avoid re-export
    ref = now if now is not None else _utcnow_naive()

    grouped: dict[str, list[sqlite3.Row]] = {"EXPIRED": [], "URGENT": [], "STANDARD": []}
    for r in rows:
        grouped[classify_expiry(r["expires_at"], now=ref)].append(r)

    lines: list[str] = [f'<pending-gates count="{len(rows)}">']
    for section in ("EXPIRED", "URGENT", "STANDARD"):
        section_rows = grouped[section]
        if not section_rows:
            continue
        lines.append(f"[{section}]")
        for r in section_rows:
            lines.append(_render_gate_line(conn, r, now=ref))

    lines.append("")
    lines.append(_GATES_BEHAVIOR)
    lines.append("</pending-gates>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# <goal-health>
# ---------------------------------------------------------------------------

_GOAL_HEALTH_SQL = """
SELECT
  g.id                AS goal_id,
  g.level             AS level,
  g.target            AS target,
  g.metric            AS metric_json,
  g.status            AS status,
  g.updated_at        AS updated_at,
  g.parent_entity_type AS parent_entity_type,
  g.parent_entity_id  AS parent_entity_id
FROM goal g
WHERE g.firm_id = ? AND g.status = 'active'
ORDER BY
  CASE g.level
    WHEN 'firm'      THEN 1
    WHEN 'operation' THEN 2
    WHEN 'project'   THEN 3
    WHEN 'member'    THEN 4
    WHEN 'unit'      THEN 5
    ELSE 6
  END,
  g.created_at,
  g.id
"""

_GOAL_BEHAVIOR = (
    "BEHAVIOR: This context is PASSIVE AWARENESS ONLY.\n"
    "v1 metrics are manually refreshed (no auto-polling). Stale metric.current reflects\n"
    "last manual update; do not infer actual progress from injection alone. Refresh with\n"
    "`cadre goal update <id> --current <value>` (CLI) or firm_update_goal_metric (MCP)."
)

_LEVEL_HEADERS = {
    "firm": "[FIRM-LEVEL]",
    "operation": "[OPERATION-LEVEL]",
    "project": "[PROJECT-LEVEL]",
    "member": "[MEMBER-LEVEL]",
    "unit": "[UNIT-LEVEL]",
}


def _render_goal_line(
    conn: sqlite3.Connection, row: sqlite3.Row, now: datetime
) -> str:
    parent_name = (
        resolve_entity_name(conn, row["parent_entity_type"], row["parent_entity_id"])
        or "(parent missing)"
    )
    header = (
        f'  - [{row["goal_id"]}] {row["target"] or "(no target set)"} '
        f'(parent: {row["parent_entity_type"]} "{parent_name}")'
    )
    body_lines: list[str] = []
    metric = _parse_metric(row["metric_json"])
    if metric is not None:
        m_type = metric.get("type") or "(no type)"
        m_val = metric.get("value")
        m_unit = metric.get("unit") or ""
        m_cur = metric.get("current")
        val_str = "null" if m_val is None else str(m_val)
        cur_str = "not-yet-baselined" if m_cur is None else str(m_cur)
        unit_str = f" {m_unit}" if m_unit else ""
        body_lines.append(
            f"    Metric: {m_type} — target {val_str}{unit_str}, current {cur_str}"
        )
        deadline = metric.get("deadline")
        if deadline:
            body_lines.append(f"    Deadline {deadline} — {_deadline_note(deadline, now)}")
        trend = metric.get("trend")
        if trend:
            body_lines.append(f"    Trend {trend}")
    body_lines.append(f"    Last metric update: {time_ago(row['updated_at'], now=now)}")
    return "\n".join([header, *body_lines])


def _parse_metric(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        m = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return m if isinstance(m, dict) else None


def _deadline_note(deadline_iso: str, now: datetime) -> str:
    try:
        dl = datetime.fromisoformat(deadline_iso)
    except ValueError:
        return "unparseable deadline"
    if dl < now:
        days_past = (now - dl).days
        if days_past == 0:
            return "OVERDUE (today)"
        return f"OVERDUE ({days_past}d past)"
    days_left = (dl - now).days
    if days_left == 0:
        return "DUE TODAY"
    return f"DUE IN {days_left}d"


def render_goal_health(
    conn: sqlite3.Connection,
    firm_id: str,
    now: datetime | None = None,
) -> str | None:
    """Render the ``<goal-health>`` block. Silent (None) when zero active Goals."""
    rows = conn.execute(_GOAL_HEALTH_SQL, (firm_id,)).fetchall()
    if not rows:
        return None

    from .render import _utcnow_naive
    ref = now if now is not None else _utcnow_naive()

    lines: list[str] = [f'<goal-health goals="{len(rows)}">']
    current_level: str | None = None
    for r in rows:
        level = r["level"] or "other"
        header = _LEVEL_HEADERS.get(level, f"[{level.upper()}-LEVEL]" if level else "[UNSCOPED]")
        if level != current_level:
            lines.append(header)
            current_level = level
        lines.append(_render_goal_line(conn, r, now=ref))

    lines.append("")
    lines.append(_GOAL_BEHAVIOR)
    lines.append("</goal-health>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# <budget-health>
# ---------------------------------------------------------------------------

_BUDGET_HEALTH_SQL = """
SELECT
  m.id              AS member_id,
  m.name            AS member_name,
  bp.run_count      AS run_count,
  bp.total_cost_usd AS total_cost_usd,
  c.budget_config   AS budget_config_json
FROM budget_period bp
JOIN member m ON m.id = bp.member_id
LEFT JOIN contract c ON c.id = m.contract_id
WHERE bp.firm_id = ? AND bp.status = 'active'
ORDER BY m.id
"""

_BUDGET_BEHAVIOR = (
    "BEHAVIOR: This context is PASSIVE AWARENESS ONLY.\n"
    "Do NOT proactively mention budget health unless the user asks about budget\n"
    "OR a Member is at limit_reached status."
)


def render_budget_health(
    conn: sqlite3.Connection,
    firm_id: str,
) -> str | None:
    """Render the ``<budget-health>`` block. Silent when no Members are near limits."""
    rows = conn.execute(_BUDGET_HEALTH_SQL, (firm_id,)).fetchall()
    if not rows:
        return None

    warning_lines: list[str] = []
    for r in rows:
        bc_raw = r["budget_config_json"]
        if not bc_raw:
            continue
        try:
            bc = json.loads(bc_raw) if isinstance(bc_raw, str) else bc_raw
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(bc, dict):
            continue

        limits = bc.get("limits", {})
        if not isinstance(limits, dict):
            continue

        alerts: list[str] = []
        max_runs = limits.get("max_runs_per_period")
        if max_runs and max_runs > 0:
            pct = (r["run_count"] or 0) / max_runs * 100
            if pct >= 80:
                alerts.append(f"runs {r['run_count']}/{max_runs} ({pct:.0f}%)")

        max_cost = limits.get("max_total_cost_per_period_usd")
        if max_cost and max_cost > 0:
            pct = (r["total_cost_usd"] or 0.0) / max_cost * 100
            if pct >= 80:
                alerts.append(f"cost ${r['total_cost_usd']:.2f}/${max_cost:.2f} ({pct:.0f}%)")

        if alerts:
            warning_lines.append(
                f"  - [{r['member_id']}] {r['member_name']}: {', '.join(alerts)}"
            )

    if not warning_lines:
        return None

    lines = [f'<budget-health warnings="{len(warning_lines)}">']
    lines.extend(warning_lines)
    lines.append("")
    lines.append(_BUDGET_BEHAVIOR)
    lines.append("</budget-health>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def render(
    conn: sqlite3.Connection,
    firm_id: str,
    now: datetime | None = None,
) -> str:
    """Render all four tags. Empty string when all are silent.

    Non-None results are joined with a single blank line between them so the
    entrypoint can ``print(render(...))`` unconditionally — an empty string
    produces a single newline which Claude Code treats as no injection.
    """
    parts: list[str] = []
    roster = render_active_roster(conn, firm_id)
    if roster:
        parts.append(roster)
    gates = render_pending_gates(conn, firm_id, now=now)
    if gates:
        parts.append(gates)
    goals = render_goal_health(conn, firm_id, now=now)
    if goals:
        parts.append(goals)
    budget = render_budget_health(conn, firm_id)
    if budget:
        parts.append(budget)
    return "\n\n".join(parts)
