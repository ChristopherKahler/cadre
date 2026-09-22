"""Open Units no active Member can reach — the query two surfaces both need.

Read from ``compute_load``'s definition (``pulse/orchestrator.py``) from the
other side: a pending or in-progress Unit is workable only when an active
Member of the firm has claimed it, or has it assigned, unclaimed and pending.

It lives here rather than in either caller because both the pulse and the
doctor ask it (#128 C2), and a doctor that reached into a CLI module for a
private name would be one edit away from the two asking different questions.
"""

from __future__ import annotations

from typing import Any

from firm.core import repo


def stranded_units(conn: Any, firm_id: str) -> list[dict[str, Any]]:
    """Open Units no active Member's queue counts, so no pulse will ever run them.

    The definition is ``compute_load``'s (``pulse/orchestrator.py``), read from
    the other side: a pending or in-progress Unit is workable only when an
    active Member of the firm has claimed it, or has it assigned, unclaimed and
    pending. A firm whose every Member skips at ``load=0`` while Units like
    these sit on its board prints the same ``ok: true, ran: 0`` as a firm with
    nothing to do, and those need opposite responses (#128 C2). Read-only, so a
    dry run reports them too.
    """
    statuses = {m["id"]: m.get("status")
                for m in repo.find(conn, "member", firm_id=firm_id)}
    active = {member_id for member_id, s in statuses.items() if s == "active"}

    def who(member_id: str) -> str:
        name = member_id if member_id else repr(member_id)
        status = statuses.get(member_id)
        if status is None:
            return f"{name}, who is not a Member of this firm"
        return f"{name}, who is {status}"

    stranded: list[dict[str, Any]] = []
    for unit in repo.find(conn, "unit", firm_id=firm_id):
        status = unit.get("status")
        if status not in ("pending", "in_progress"):
            continue
        assignee, claimed = unit.get("assignee_member_id"), unit.get("claimed_by")
        # compute_load's two clauses exactly, never by truthiness: claimed_by
        # EQUALS an active Member's id, or claimed_by IS NULL while the Unit is
        # pending and assigned to one. An empty id is neither (osprey's G1).
        if claimed in active:
            continue
        if claimed is None and status == "pending" and assignee in active:
            continue
        if claimed is not None:
            reason = f"claimed by {who(claimed)}"
        elif status == "in_progress":
            reason = "in progress with no claim"
        elif assignee is not None:
            reason = f"assigned to {who(assignee)}"
        else:
            reason = "no assignee and no claim"
        stranded.append({"id": unit["id"], "status": status,
                         "assignee_member_id": assignee, "claimed_by": claimed,
                         "reason": reason})
    return sorted(stranded, key=lambda u: str(u["id"]))
