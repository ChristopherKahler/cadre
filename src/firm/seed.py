"""Seed the ChrisAI firm with Quill and supporting entities.

Creates the minimum entity set required for Quill E2E:
  Firm "chrisai" → Operation "Content Pipeline" → Project "Blog v1"
  Member "Quill" (MEM-001) → Contract CON-001 (claude_code, blog skill_loadout)
  Unit UNT-001 (claimed by Quill)
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from firm.core import repo

QUILL_SKILL_LOADOUT = {
    "stages": {
        "init": "/blog:init",
        "strategy": "/blog:strategy",
        "surface": "/blog:surface",
        "ideate": "/blog:ideate",
        "research": "/blog:research",
        "write": "/blog:write",
        "audit": "/blog:audit",
        "chronicle": "/blog:chronicle",
        "publish": "/blog:publish",
        "repurpose": "/blog:repurpose",
        "full": "/blog:write",
    },
}


def seed_chrisai(conn: sqlite3.Connection) -> dict[str, Any]:
    """Seed the ChrisAI firm. Idempotent — skips entities that already exist.

    Returns:
        Dict of created/existing entity IDs.
    """
    ids: dict[str, str] = {}

    # Firm
    if not repo.get(conn, "firm", "chrisai"):
        repo.create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    ids["firm"] = "chrisai"

    # Contract (before Member, since Member.contract_id references it)
    if not repo.get(conn, "contract", "CON-001"):
        repo.create(conn, "contract", {
            "id": "CON-001",
            "firm_id": "chrisai",
            "name": "Quill Blog Author Contract",
            "runtime_type": "claude_code",
            "skill_loadout": json.dumps(QUILL_SKILL_LOADOUT),
            "runtime_config": json.dumps({"cwd": "."}),
            "pulse_config": json.dumps({"timeout_sec": 300}),
        })
    ids["contract"] = "CON-001"

    # Member
    if not repo.get(conn, "member", "MEM-001"):
        repo.create(conn, "member", {
            "id": "MEM-001",
            "firm_id": "chrisai",
            "name": "Quill",
            "role": "Blog Author",
            "description": "Produces research-driven blog posts through the blog-post-master pipeline",
            "status": "active",
            "contract_id": "CON-001",
        })
    ids["member"] = "MEM-001"

    # Operation
    if not repo.get(conn, "operation", "OP-001"):
        repo.create(conn, "operation", {
            "id": "OP-001",
            "firm_id": "chrisai",
            "name": "Content Pipeline",
            "status": "active",
        })
    ids["operation"] = "OP-001"

    # Project
    if not repo.get(conn, "project", "PRJ-001"):
        repo.create(conn, "project", {
            "id": "PRJ-001",
            "firm_id": "chrisai",
            "operation_id": "OP-001",
            "name": "Blog v1",
            "status": "in_progress",
            "due_date": "2026-12-31",
        })
    ids["project"] = "PRJ-001"

    # Unit
    if not repo.get(conn, "unit", "UNT-001"):
        repo.create(conn, "unit", {
            "id": "UNT-001",
            "firm_id": "chrisai",
            "project_id": "PRJ-001",
            "name": "First blog post — Claude Code workflow automation",
            "status": "pending",
            "claimed_by": "MEM-001",
            "depends_on": json.dumps([]),
            "acceptance_criteria": json.dumps([
                "Research brief produced with 3+ sources",
                "Draft passes humanizer gate",
                "Published to chrisai.cv/blog",
            ]),
        })
    ids["unit"] = "UNT-001"

    return ids
