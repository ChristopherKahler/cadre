"""Seed the ChrisAI firm with leadership hierarchy and supporting entities.

Creates the full ChrisAI firm entity set:
  Firm "chrisai"
  Contracts: CON-001 (Quill), CON-002 (Sterling), CON-003 (Sage)
  Members:   MEM-002 (Sterling, CMO) → MEM-001 (Quill) + MEM-003 (Sage)
  Operation: OP-001 (Content Pipeline)
  Project:   PRJ-001 (Blog v1)
  Unit:      UNT-001 (first blog post, claimed by Quill)

Hierarchy:
  Board
  └── Sterling (CMO, MEM-002)
      ├── Quill (Blog Author, MEM-001)
      └── Sage (Content Strategist, MEM-003)
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

STERLING_SKILL_LOADOUT = {
    "stages": {
        "audit": "/sterling:audit",
        "queue": "/sterling:queue",
        "review": "/sterling:review",
    },
}

SAGE_SKILL_LOADOUT = {
    "stages": {
        "surface": "/sage:surface",
        "analyze": "/sage:analyze",
        "recommend": "/sage:recommend",
    },
}


def seed_chrisai(conn: sqlite3.Connection) -> dict[str, Any]:
    """Seed the ChrisAI firm. Idempotent — skips entities that already exist.

    For existing databases where MEM-001 was created without reports_to,
    updates the reports_to chain to point to Sterling (MEM-002).

    Returns:
        Dict of created/existing entity IDs.
    """
    ids: dict[str, str] = {}

    # Firm
    if not repo.get(conn, "firm", "chrisai"):
        repo.create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    ids["firm"] = "chrisai"

    # --- Contracts (before Members, since Member.contract_id references them) ---

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
    ids["contract_quill"] = "CON-001"

    if not repo.get(conn, "contract", "CON-002"):
        repo.create(conn, "contract", {
            "id": "CON-002",
            "firm_id": "chrisai",
            "name": "Sterling CMO Contract",
            "runtime_type": "claude_code",
            "skill_loadout": json.dumps(STERLING_SKILL_LOADOUT),
            "runtime_config": json.dumps({"cwd": "."}),
            "pulse_config": json.dumps({"timeout_sec": 300}),
        })
    ids["contract_sterling"] = "CON-002"

    if not repo.get(conn, "contract", "CON-003"):
        repo.create(conn, "contract", {
            "id": "CON-003",
            "firm_id": "chrisai",
            "name": "Sage Content Strategist Contract",
            "runtime_type": "claude_code",
            "skill_loadout": json.dumps(SAGE_SKILL_LOADOUT),
            "runtime_config": json.dumps({"cwd": "."}),
            "pulse_config": json.dumps({"timeout_sec": 300}),
        })
    ids["contract_sage"] = "CON-003"

    # --- Members (Sterling first — no reports_to dependency) ---

    if not repo.get(conn, "member", "MEM-002"):
        repo.create(conn, "member", {
            "id": "MEM-002",
            "firm_id": "chrisai",
            "name": "Sterling",
            "role": "Chief Marketing Officer",
            "description": "Owns content strategy, queues work for Quill, reviews output quality",
            "status": "active",
            "contract_id": "CON-002",
        })
    ids["member_sterling"] = "MEM-002"

    if not repo.get(conn, "member", "MEM-001"):
        repo.create(conn, "member", {
            "id": "MEM-001",
            "firm_id": "chrisai",
            "name": "Quill",
            "role": "Blog Author",
            "description": "Produces research-driven blog posts through the blog-post-master pipeline",
            "status": "active",
            "contract_id": "CON-001",
            "reports_to_member_id": "MEM-002",
        })
    else:
        # Upgrade path: existing Quill without reports_to → set to Sterling
        quill = repo.get(conn, "member", "MEM-001")
        if quill and not quill.get("reports_to_member_id"):
            repo.update(conn, "member", "MEM-001", {
                "reports_to_member_id": "MEM-002",
            })
    ids["member_quill"] = "MEM-001"

    if not repo.get(conn, "member", "MEM-003"):
        repo.create(conn, "member", {
            "id": "MEM-003",
            "firm_id": "chrisai",
            "name": "Sage",
            "role": "Content Strategist",
            "description": "Surfaces content pillar opportunities and recommends topics aligned with business goals",
            "status": "active",
            "contract_id": "CON-003",
            "reports_to_member_id": "MEM-002",
        })
    ids["member_sage"] = "MEM-003"

    # --- Operation + Project + Unit (unchanged from Phase 4) ---

    if not repo.get(conn, "operation", "OP-001"):
        repo.create(conn, "operation", {
            "id": "OP-001",
            "firm_id": "chrisai",
            "name": "Content Pipeline",
            "status": "active",
        })
    ids["operation"] = "OP-001"

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

    # Backward-compatible aliases (Phase 4 tests use these keys)
    ids["member"] = "MEM-001"
    ids["contract"] = "CON-001"

    return ids
