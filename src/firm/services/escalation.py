"""Escalation entity service — raise, list, view, acknowledge, resolve.

Escalations are the Members' direct line to the Board: attention items that
are NOT approval requests (those are Gates). Raising one notifies the Board
immediately (Slack DM / webhook via firm.notify) — deterministically, from
the service layer, so the channel is structural rather than discretionary.

Dedup is first-class: every escalation carries a ``dedupe_key`` (derived
from target + normalized title unless the caller supplies one). Re-raising
an issue whose escalation is still open does NOT create a second row and
only re-notifies the Board once the firm's reminder window (default 24h)
has elapsed — the Board never gets pinged hourly about the same thing.
A resolved escalation's key becomes reusable: if the issue resurfaces after
resolution, that is genuinely new signal and notifies again.

ID prefix: ESC-NNN
Records events: escalation.raised, escalation.reminded, escalation.deduped,
                escalation.acknowledged, escalation.resolved
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from firm import notify
from firm.core import repo
from firm.services._id import next_id
from firm.services._records import log_event
from firm.services._validate import require_exists, validate_parent_ref

ESCALATION_STATUSES = ["open", "acknowledged", "resolved"]
ESCALATION_SEVERITIES = ["low", "normal", "high", "critical"]


def derive_dedupe_key(
    title: str,
    target_entity_type: str | None = None,
    target_entity_id: str | None = None,
) -> str:
    """Stable key for "the same issue": target ref + normalized title."""
    norm = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"{target_entity_type or ''}:{target_entity_id or ''}:{norm}"


def _hours_since(iso_ts: str | None, ref: datetime) -> float:
    if not iso_ts:
        return float("inf")
    try:
        ts = datetime.fromisoformat(iso_ts)
    except (TypeError, ValueError):
        return float("inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (ref - ts).total_seconds() / 3600


def _render_dm(esc: dict[str, Any], member: dict[str, Any], *, reminder_no: int | None) -> str:
    head = (
        f"🔔 Reminder #{reminder_no} — {esc['id']}"
        if reminder_no
        else f"🚨 New escalation {esc['id']}"
    )
    lines = [
        f"{head} [{esc['severity']}] from {member['name']} ({member['id']})",
        f"*{esc['title']}*",
    ]
    if esc.get("target_entity_id"):
        lines.append(f"On: {esc['target_entity_type']} {esc['target_entity_id']}")
    if esc.get("body"):
        lines.append(str(esc["body"])[:500])
    return "\n".join(lines)


def raise_escalation(
    conn: sqlite3.Connection,
    firm_id: str,
    data: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Raise an escalation to the Board (dedup-aware, notifies immediately).

    Args:
        conn: SQLite connection.
        firm_id: Firm scope.
        data: Must include 'raised_by_member_id', 'title'. Optional: body,
              severity, target_entity_type + target_entity_id, dedupe_key.
        now: Datetime override for deterministic tests.

    Returns:
        The escalation row plus notification outcome:
        {"escalation": row, "deduped": bool, "notified": bool, "notify_reason": str}

    Raises:
        ValueError: If required fields missing or references invalid.
    """
    for required in ("raised_by_member_id", "title"):
        if required not in data:
            raise ValueError(f"'{required}' is required to raise an escalation")

    member = require_exists(conn, "member", data["raised_by_member_id"])

    target_type = data.get("target_entity_type")
    target_id = data.get("target_entity_id")
    if target_type or target_id:
        if not (target_type and target_id):
            raise ValueError(
                "target_entity_type and target_entity_id must be provided together"
            )
        validate_parent_ref(conn, target_type, target_id)

    severity = data.get("severity", "normal")
    if severity not in ESCALATION_SEVERITIES:
        raise ValueError(
            f"Invalid severity {severity!r} — must be one of {ESCALATION_SEVERITIES}"
        )

    ref = now or datetime.now(tz=timezone.utc)
    dedupe_key = data.get("dedupe_key") or derive_dedupe_key(
        data["title"], target_type, target_id
    )

    # Dedup: an existing non-resolved escalation with the same key absorbs
    # this raise instead of creating a duplicate row.
    existing = [
        e for e in repo.find(conn, "escalation", firm_id=firm_id, dedupe_key=dedupe_key)
        if e.get("status") in ("open", "acknowledged")
    ]
    if existing:
        esc = existing[0]
        cfg = notify.get_notify_config(conn, firm_id)
        window = notify.remind_interval_hours(cfg)
        if _hours_since(esc.get("last_notified_at"), ref) >= window:
            reminder_no = int(esc.get("notify_count") or 0)  # count includes the original
            outcome = notify.send_board_dm(
                conn, firm_id, _render_dm(esc, member, reminder_no=reminder_no)
            )
            if outcome["sent"]:
                esc = repo.update(conn, "escalation", esc["id"], {
                    "notify_count": (esc.get("notify_count") or 0) + 1,
                    "last_notified_at": ref.isoformat(),
                })
            log_event(
                conn, firm_id=firm_id, event_type="escalation.reminded",
                actor={"type": "member", "id": member["id"]},
                target_ref={"type": "escalation", "id": esc["id"]},
                details={"notified": outcome["sent"], "notify_reason": outcome["reason"]},
            )
            return {
                "escalation": esc, "deduped": True,
                "notified": outcome["sent"], "notify_reason": outcome["reason"],
            }
        log_event(
            conn, firm_id=firm_id, event_type="escalation.deduped",
            actor={"type": "member", "id": member["id"]},
            target_ref={"type": "escalation", "id": esc["id"]},
            details={"within_window_hours": window},
        )
        return {
            "escalation": esc, "deduped": True, "notified": False,
            "notify_reason": f"already notified within the {window}h reminder window",
        }

    esc_id = next_id(conn, "escalation", firm_id)
    row_data: dict[str, Any] = {
        "id": esc_id,
        "firm_id": firm_id,
        "raised_by_member_id": member["id"],
        "severity": severity,
        "title": data["title"],
        "dedupe_key": dedupe_key,
        "status": "open",
    }
    if data.get("body"):
        row_data["body"] = data["body"]
    if target_type:
        row_data["target_entity_type"] = target_type
        row_data["target_entity_id"] = target_id
    created = repo.create(conn, "escalation", row_data)

    outcome = notify.send_board_dm(
        conn, firm_id, _render_dm(created, member, reminder_no=None)
    )
    if outcome["sent"]:
        created = repo.update(conn, "escalation", esc_id, {
            "notify_count": 1,
            "last_notified_at": ref.isoformat(),
        })

    log_event(
        conn, firm_id=firm_id, event_type="escalation.raised",
        actor={"type": "member", "id": member["id"]},
        target_ref={"type": "escalation", "id": esc_id},
        details={
            "severity": severity, "dedupe_key": dedupe_key,
            "notified": outcome["sent"], "notify_reason": outcome["reason"],
        },
    )
    return {
        "escalation": created, "deduped": False,
        "notified": outcome["sent"], "notify_reason": outcome["reason"],
    }


def list_escalations(
    conn: sqlite3.Connection,
    firm_id: str,
    *,
    status: str | None = None,
    raised_by: str | None = None,
) -> list[dict[str, Any]]:
    """List escalations with optional status / raiser filters."""
    filters: dict[str, Any] = {"firm_id": firm_id}
    if status is not None:
        filters["status"] = status
    if raised_by is not None:
        filters["raised_by_member_id"] = raised_by
    return repo.find(conn, "escalation", **filters)


def view_escalation(conn: sqlite3.Connection, escalation_id: str) -> dict[str, Any]:
    """View an escalation by ID. Raises ValueError if not found."""
    return require_exists(conn, "escalation", escalation_id)


def resolve_escalation(
    conn: sqlite3.Connection,
    escalation_id: str,
    *,
    status: str = "resolved",
    resolution: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Move an escalation to acknowledged/resolved (Board action).

    Raises:
        ValueError: If not found or invalid status.
    """
    existing = require_exists(conn, "escalation", escalation_id)
    if status not in ("acknowledged", "resolved"):
        raise ValueError("status must be 'acknowledged' or 'resolved'")

    data: dict[str, Any] = {"status": status}
    if resolution:
        data["resolution"] = resolution
    updated = repo.update(conn, "escalation", escalation_id, data)

    log_event(
        conn, firm_id=existing["firm_id"],
        event_type=f"escalation.{status}",
        actor=actor or {"type": "board", "id": None},
        target_ref={"type": "escalation", "id": escalation_id},
        details={"resolution": resolution},
    )
    return updated
