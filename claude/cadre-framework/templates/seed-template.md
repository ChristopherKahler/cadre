# Seed Template — idempotent firm definition skeleton

Fill `{placeholders}`; keep the guard-every-create idiom. Runs with `.venv/bin/python scripts/seed_{firm}.py`; running twice must change nothing.

---

```template
"""Seed the {firm-id} firm — [one line: what this firm is].

Idempotent: guarded creates, targeted updates. Service layer (firm.core.repo) only.
"""
from __future__ import annotations

import json
import sqlite3

from firm.core import repo

FIRM_ID = "{firm-id}"
DB_PATH = "{workspace-abs-path}/.firm/firm.db"

# Name the firm's laws once; reuse in loadout policies.
[LAW_CONSTANTS = "..." — sandbox / money / ship / publish laws as applicable]


def run(conn: sqlite3.Connection) -> dict[str, str]:
    out: dict[str, str] = {}

    # --- Firm identity (ALL of it — notify_config makes gates reach the Board) ---
    if not repo.get(conn, "firm", FIRM_ID):
        repo.create(conn, "firm", {
            "id": FIRM_ID, "name": "{Firm Name}",
            "description": "[what it produces]", "operator": "{operator}",
            "north_star": "[one sentence the whole firm optimizes for]",
            "core_values": json.dumps(["...", "..."]),
            "vision": "[where this goes]",
            "notify_config": json.dumps({"provider": "slack", "slack_user_id": "{U-ID}",
                                         "token_env": "CADRE_SLACK_TOKEN", "remind_hours": 24}),
        })

    # --- Contracts (model + timeout + budget + validation + loadout: ALL required decisions) ---
    if not repo.get(conn, "contract", "CON-LEAD"):
        repo.create(conn, "contract", {
            "id": "CON-LEAD", "firm_id": FIRM_ID, "name": "{Firm} Lead Contract",
            "runtime_type": "claude_code", "runtime_config": json.dumps({"cwd": "."}),
            "pulse_config": json.dumps({"timeout_sec": 900, "model": "sonnet"}),
            "budget_config": json.dumps({"limits": {"max_runs_per_period": 30, "max_total_cost_per_period_usd": 10}}),
            "skill_loadout": json.dumps({
                "duties": ["[name-prefixed duty lines per lead]"],
                "policies": ["[firm laws]"],
            }),
        })
    # CON-ENG etc: engineers add validation_config:
    #   json.dumps({"enabled": True, "max_retries": 1,
    #               "validators": [{"name": "file_exists", "require_written": True}]})

    # --- Members (reporting lines; exactly ONE can_self_assign triage role) ---
    roster = [
        ("MEM-DIR",  "{Name}", "[Director role]", None,        "CON-LEAD", 1),
        ("MEM-LEAD", "{Name}", "[Lead role]",     "MEM-DIR",   "CON-LEAD", 0),
        ("MEM-A",    "{Name}", "[IC role]",       "MEM-LEAD",  "CON-ENG",  0),
    ]
    for mid, name, role, boss, cid, self_assign in roster:
        if not repo.get(conn, "member", mid):
            repo.create(conn, "member", {
                "id": mid, "firm_id": FIRM_ID, "name": name, "role": role,
                "description": "[2-4 specific sentences — this IS the member's prompt identity]",
                "reports_to_member_id": boss, "contract_id": cid,
                "status": "active", "can_self_assign": self_assign,
            })

    # --- Operation → Project (due_date NOT NULL) → Units (assigned + chained) ---
    if not repo.get(conn, "operation", "OP-001"):
        repo.create(conn, "operation", {"id": "OP-001", "firm_id": FIRM_ID,
                                        "name": "[dept]", "owner_member_id": "MEM-LEAD", "status": "active"})
    if not repo.get(conn, "project", "PRJ-001"):
        repo.create(conn, "project", {"id": "PRJ-001", "firm_id": FIRM_ID, "operation_id": "OP-001",
                                      "name": "[project]", "status": "in_progress", "due_date": "{YYYY-MM-DD}"})
    units = [
        ("UNT-BUILD", "[one-run-sized task]", "MEM-A", [],
         ["[verifiable criterion]", "[evidence requirement — report to reports/]"]),
        ("UNT-SHIP", "[ship-gate audit of UNT-BUILD's output]", "MEM-{ship-role}", ["UNT-BUILD"],
         ["[audit evidence]", "[failures filed as follow-up units, not glossed]"]),
    ]
    for uid, name, assignee, deps, acs in units:
        if not repo.get(conn, "unit", uid):
            repo.create(conn, "unit", {
                "id": uid, "firm_id": FIRM_ID, "project_id": "PRJ-001", "name": name,
                "assignee_member_id": assignee, "status": "pending", "priority": "medium",
                "depends_on": json.dumps(deps), "acceptance_criteria": json.dumps(acs),
            })

    # --- Goals ---
    if not repo.get(conn, "goal", "GL-001"):
        repo.create(conn, "goal", {
            "id": "GL-001", "firm_id": FIRM_ID, "level": "operation",
            "parent_entity_type": "operation", "parent_entity_id": "OP-001",
            "metric": json.dumps({"type": "[metric_slug]", "current": 0}),
            "target": json.dumps({"type": "count", "value": 1, "unit": "[unit]", "current": 0}),
            "status": "active",
        })

    out["seeded"] = "firm + contracts + roster + chain + goal"
    return out


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    result = run(conn)
    conn.commit()
    print(json.dumps(result, indent=2))
```

---

After seeding: `cadre templates apply discipline --map lead=CON-LEAD --map dev=CON-ENG` attaches the role packs (the discipline protocol itself was installed by `cadre init`).
