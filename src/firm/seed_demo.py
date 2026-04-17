"""Generic demo firm seed — the public example.

Separate from `seed.py` (chrisai-specific). `seed_demo` stands alone so
users who run `cadre init . --demo` get a working, non-chrisai Firm they
can inspect and learn from. Deliberately seeds UNT-001 unclaimed so
`detect_gaps` has something to surface on first run.

Idempotent — safe to re-run; skips entities that already exist.
"""

from __future__ import annotations

import json
import sqlite3

from firm.core import repo

_WRITER_LOADOUT = {
    "stages": {
        "draft": "/demo:draft",
        "review": "/demo:review",
        "publish": "/demo:publish",
    },
}

_EDITOR_LOADOUT = {
    "stages": {
        "audit": "/demo:audit",
        "queue": "/demo:queue",
    },
}


def seed_demo(conn: sqlite3.Connection) -> dict[str, str]:
    """Seed the `demo` Firm. Returns dict of entity IDs."""
    ids: dict[str, str] = {}

    if not repo.get(conn, "firm", "demo"):
        repo.create(conn, "firm", {"id": "demo", "name": "Demo Firm"})
    ids["firm"] = "demo"

    if not repo.get(conn, "contract", "CON-001"):
        repo.create(conn, "contract", {
            "id": "CON-001",
            "firm_id": "demo",
            "name": "Demo Writer Contract",
            "runtime_type": "claude_code",
            "skill_loadout": json.dumps(_WRITER_LOADOUT),
            "runtime_config": json.dumps({"cwd": "."}),
            "pulse_config": json.dumps({"timeout_sec": 300}),
        })
    ids["contract_writer"] = "CON-001"

    if not repo.get(conn, "contract", "CON-002"):
        repo.create(conn, "contract", {
            "id": "CON-002",
            "firm_id": "demo",
            "name": "Demo Editor Contract",
            "runtime_type": "claude_code",
            "skill_loadout": json.dumps(_EDITOR_LOADOUT),
            "runtime_config": json.dumps({"cwd": "."}),
            "pulse_config": json.dumps({"timeout_sec": 300}),
        })
    ids["contract_editor"] = "CON-002"

    # Editor first (no reports_to), then Writer reports to Editor.
    if not repo.get(conn, "member", "MEM-002"):
        repo.create(conn, "member", {
            "id": "MEM-002",
            "firm_id": "demo",
            "name": "Edit",
            "role": "Editor",
            "description": "Reviews drafts, queues work for writers, ships final copy.",
            "status": "active",
            "contract_id": "CON-002",
        })
    ids["member_editor"] = "MEM-002"

    if not repo.get(conn, "member", "MEM-001"):
        repo.create(conn, "member", {
            "id": "MEM-001",
            "firm_id": "demo",
            "name": "Pen",
            "role": "Writer",
            "description": "Drafts blog posts and articles on assigned topics.",
            "status": "active",
            "contract_id": "CON-001",
            "reports_to_member_id": "MEM-002",
        })
    ids["member_writer"] = "MEM-001"

    if not repo.get(conn, "operation", "OP-001"):
        repo.create(conn, "operation", {
            "id": "OP-001",
            "firm_id": "demo",
            "name": "Blog Pipeline",
            "status": "active",
        })
    ids["operation"] = "OP-001"

    if not repo.get(conn, "project", "PRJ-001"):
        repo.create(conn, "project", {
            "id": "PRJ-001",
            "firm_id": "demo",
            "operation_id": "OP-001",
            "name": "First post",
            "status": "in_progress",
            "due_date": "2026-12-31",
        })
    ids["project"] = "PRJ-001"

    # Intentionally unclaimed — demonstrates detect_gaps on first run.
    if not repo.get(conn, "unit", "UNT-001"):
        repo.create(conn, "unit", {
            "id": "UNT-001",
            "firm_id": "demo",
            "project_id": "PRJ-001",
            "name": "Write the welcome post",
            "status": "pending",
            "depends_on": json.dumps([]),
            "acceptance_criteria": json.dumps([
                "Post explains what Cadre does in one paragraph",
                "Post includes a code block showing `cadre init`",
            ]),
        })
    ids["unit"] = "UNT-001"

    return ids


def summary_line(conn: sqlite3.Connection, firm_id: str = "demo") -> str:
    """Human-readable one-liner for installer output."""
    members = repo.find(conn, "member", firm_id=firm_id)
    ops = repo.find(conn, "operation", firm_id=firm_id)
    projects = repo.find(conn, "project", firm_id=firm_id)
    units = repo.find(conn, "unit", firm_id=firm_id)
    unclaimed = sum(1 for u in units if u.get("claimed_by") is None)
    return (
        f"demo firm seeded: {len(members)} members, {len(ops)} operations, "
        f"{len(projects)} projects, {len(units)} units ({unclaimed} unclaimed)"
    )
