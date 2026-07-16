"""``firm unit create`` / ``firm unit complete`` — the Member's Unit surface.

The firm's execution rules tell every Member to queue follow-up work as it goes
("queued work is throughput, hoarded work is a bottleneck") and to register a
deliverable before its Unit closes. Neither was executable: ``firm unit``
exposed only ``complete``, so ~15-20 pieces of follow-up work went into BASE
notes where no queue, no assignee, and no pulse could reach them (ESC-041), and
nothing registered a deliverable at all (ESC-026).

Both verbs are thin wrappers — the unit and document services own the logic, and
route Board and Member writes through identical audit behavior.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.core.db import connect, get_db_path, resolve_firm_id
from firm.services.authority import MEMBER_ID_ENV
from firm.services.document import register_deliverable
from firm.services.unit import complete_unit, create_unit


def caller_member_id() -> str | None:
    """The Member running this command, from the env the spawn stamps.

    A Member's Bash subshell inherits ``CADRE_MEMBER_ID``, so the caller never
    has to name itself. Empty/whitespace reads as absent: an env var set to ""
    is still "set", and a default that only fires on a missing key would let a
    blank value through as an identity.
    """
    return os.environ.get(MEMBER_ID_ENV, "").strip() or None


def _open_workspace_db(workspace: Path) -> Path | None:
    """Resolve the workspace's firm.db, printing the standard error if absent."""
    db_path = get_db_path(workspace)
    if not db_path.exists():
        print(
            f"Error: .firm/firm.db not found at {db_path}. "
            f"Run 'firm init {workspace}' first.",
            file=sys.stderr,
        )
        return None
    return db_path


def run_unit_create(
    workspace: Path,
    *,
    name: str,
    project_id: str,
    description: str = "",
    assignee: str | None = None,
    priority: str = "medium",
    depends_on: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    dry_run: bool = False,
    firm_id: str | None = None,
) -> int:
    """Create a Unit in the workspace firm DB. JSON to stdout, errors to stderr.

    Deliberately NOT gated on the authority key. ``unit.complete`` is gated
    because a Member marking its own work done is the self-govern overreach the
    key exists to stop; queueing follow-up work is the opposite — it is the
    behavior the rules ask for, and gating it would re-close the hole ESC-041
    opened.
    """
    workspace = workspace.expanduser().resolve()
    db_path = _open_workspace_db(workspace)
    if db_path is None:
        return 1

    assignee = assignee or caller_member_id()
    conn = connect(db_path)
    try:
        try:
            firm_id = resolve_firm_id(conn, firm_id)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        data: dict[str, Any] = {
            "name": name,
            "project_id": project_id,
            "status": "pending",
            "priority": priority,
        }
        if description:
            data["description"] = description
        if assignee:
            data["assignee_member_id"] = assignee
        if depends_on:
            data["depends_on"] = list(depends_on)
        if acceptance_criteria:
            data["acceptance_criteria"] = list(acceptance_criteria)

        if dry_run:
            print(f"[dry-run] would create unit {name!r} in {project_id}")
            print(f"[dry-run] assignee: {assignee or '(unassigned)'}")
            print(f"[dry-run] priority: {priority}, depends_on: "
                  f"{', '.join(depends_on or []) or '(none)'}")
            print(f"[dry-run] would write records row: event_type=unit.created, "
                  f"actor={assignee or 'board'}")
            return 0

        # The Member queueing the work is the actor — crediting it to the Board
        # would erase who is actually filling the queue.
        actor = ({"type": "member", "id": assignee} if assignee
                 else {"type": "board", "id": None})
        try:
            unit = create_unit(conn, firm_id, data, actor=actor)
        except Exception as exc:  # service raises ValueError/CycleError on bad input
            print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 1
        conn.commit()

        print(json.dumps({"ok": True, "unit": dict(unit)}, default=str))
        return 0
    finally:
        conn.close()


def _preview_resolved_acs(
    project: dict[str, Any] | None, unit_id: str,
) -> list[str]:
    """Return the ids of acceptance_criteria that *would* flip for *unit_id*."""
    if project is None:
        return []
    ac_list = project.get("acceptance_criteria") or []
    if not isinstance(ac_list, list):
        return []
    out: list[str] = []
    for entry in ac_list:
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("resolved_by") == unit_id
            and entry.get("resolved") is not True
            and entry.get("id")
        ):
            out.append(str(entry["id"]))
    return out


def run_unit_complete(
    workspace: Path,
    unit_id: str,
    member_id: str,
    *,
    run_id: str | None = None,
    outputs: list[str] | None = None,
    dry_run: bool = False,
    firm_id: str | None = None,
) -> int:
    """Complete *unit_id* in the workspace firm DB.

    ``--outputs`` registers each produced file as a Document parented to the
    Unit and lands it on ``unit.outputs``, satisfying "the artifact must exist
    and be REGISTERED before the Unit closes" in one call. Registration runs
    BEFORE the completion: a deliverable that is not on disk aborts with the
    Unit still open, because a Unit closed with nothing to show is the precise
    state that rule exists to prevent.

    ``dry-run`` performs the reads and prints the planned changes without
    opening a write transaction. On success returns 0; structured failures
    (unit not found, project missing) return 1 and print to stderr.
    """
    workspace = workspace.expanduser().resolve()
    db_path = _open_workspace_db(workspace)
    if db_path is None:
        return 1

    conn = connect(db_path)
    try:
        try:
            firm_id = resolve_firm_id(conn, firm_id)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        unit = repo.get(conn, "unit", unit_id)
        if unit is None:
            print(
                f"Error: unit-not-found: {unit_id}",
                file=sys.stderr,
            )
            return 1
        prior_status = unit.get("status", "unknown")
        project_id = unit["project_id"]
        project = repo.get(conn, "project", project_id)

        if dry_run:
            planned_ac = _preview_resolved_acs(project, unit_id)
            print(f"[dry-run] would complete {unit_id} (prior status: {prior_status})")
            print(f"[dry-run] project: {project_id}")
            print(f"[dry-run] would write records row: event_type=unit.status_transition, "
                  f"actor={member_id}")
            ac_display = ", ".join(planned_ac) if planned_ac else "(none)"
            print(f"[dry-run] would resolve AC: {ac_display}")
            for path in outputs or []:
                exists = "" if os.path.isfile(os.path.expanduser(path)) else " (MISSING)"
                print(f"[dry-run] would register deliverable: {path}{exists}")
            if project is None:
                print("[dry-run] WARNING: project row missing; live run would return "
                      "project-missing error")
            return 0

        # Register first: a deliverable that isn't on disk must not close the
        # Unit. Completing and then failing to register would leave exactly the
        # done-with-no-artifact state the rule forbids.
        registered: list[dict[str, Any]] = []
        for path in outputs or []:
            try:
                result = register_deliverable(
                    conn, firm_id, unit_id, path, member_id=member_id,
                    cwd=str(workspace),
                )
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if result["action"] == "conflict":
                owner = result["document"]
                print(
                    f"Error: deliverable-conflict: {result['content_path']} is "
                    f"already registered as {owner['id']} against "
                    f"{owner['parent_entity_id']} — a deliverable belongs to the "
                    "Unit that produced it; register a copy under this Unit or "
                    "revise the existing document.",
                    file=sys.stderr,
                )
                return 1
            registered.append(result)

        # Route through the service so the status flip, audit record, and AC
        # rollup stay one transaction — calling on_unit_done directly left
        # unit.status untouched and pulses re-dispatched finished work.
        result = complete_unit(
            conn,
            firm_id,
            unit_id,
            member_id,
            run_id=run_id,
        )
        if not result.get("ok"):
            reason = result.get("reason", "unknown")
            print(f"Error: {reason} — {json.dumps(result)}", file=sys.stderr)
            return 1

        conn.commit()

        resolved = result.get("resolved_ac_ids") or []
        ac_display = ", ".join(resolved) if resolved else "(none)"
        print(f"completed {unit_id} (status: {prior_status} -> done) — resolved AC: {ac_display}")
        print(f"records: {result['records_id']}")
        for reg in registered:
            print(f"deliverable: {reg['document']['id']} {reg['action']} "
                  f"— {reg['content_path']}")
        return 0
    finally:
        conn.close()


def run_doc_register(
    workspace: Path,
    *,
    unit_id: str,
    path: str,
    member_id: str | None = None,
    name: str | None = None,
    doc_type: str = "draft",
    firm_id: str | None = None,
) -> int:
    """Register a produced file as *unit_id*'s deliverable. JSON to stdout.

    The Member-facing half of the §2 rule. Ungated on purpose: registering the
    evidence of your own work is not the self-govern overreach the authority key
    guards — and because ``unit.complete`` IS gated, hanging registration solely
    off that verb would leave a keyless Member unable to register at all, which
    is the ESC-026 hole rebuilt one level down.
    """
    workspace = workspace.expanduser().resolve()
    db_path = _open_workspace_db(workspace)
    if db_path is None:
        return 1

    member_id = member_id or caller_member_id()
    if not member_id:
        print(
            "Error: no acting member — pass --member MEM-00N, or run where "
            f"{MEMBER_ID_ENV} is set. Records must carry who produced the "
            "deliverable.",
            file=sys.stderr,
        )
        return 1

    conn = connect(db_path)
    try:
        try:
            firm_id = resolve_firm_id(conn, firm_id)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        try:
            result = register_deliverable(
                conn, firm_id, unit_id, path, member_id=member_id,
                name=name, doc_type=doc_type, cwd=str(workspace),
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        if result["action"] == "conflict":
            owner = result["document"]
            print(
                f"Error: deliverable-conflict: {result['content_path']} is "
                f"already registered as {owner['id']} against "
                f"{owner['parent_entity_id']} — a deliverable belongs to the "
                "Unit that produced it; register a copy under this Unit or "
                "revise the existing document.",
                file=sys.stderr,
            )
            return 1
        conn.commit()

        print(json.dumps({
            "ok": True,
            "action": result["action"],
            "document": dict(result["document"]),
            "content_path": result["content_path"],
        }, default=str))
        return 0
    finally:
        conn.close()
