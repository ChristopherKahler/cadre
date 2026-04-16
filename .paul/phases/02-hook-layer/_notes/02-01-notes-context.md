# Task 1 Notes — Project Context Deep-Read

**Source:** ENTITY-DESIGN.md, MEMBERS-DESIGN.md, src/firm/migrations/002_entities.sql, src/firm/core/{db,repo,units}.py
**Purpose:** Facts the 3 hooks will need to read/write. Grounded in real field names, not paraphrase.

---

## 1. Data Substrate (Phase 1 output)

- **DB location:** `<workspace>/.firm/firm.db` — resolved via `firm.core.db.get_db_path(workspace)`
- **Connection pattern:** `firm.core.db.connect(db_path)` — sets `foreign_keys = ON`, `row_factory = sqlite3.Row`
- **Context manager:** `firm.core.db.db_connection(workspace)` — commits on clean exit, rolls back on exception
- **CRUD API:** `firm.core.repo.{create, get, update, find, delete}` — hooks READ via `find`/`get`, WRITE via `create` on immutable tables (records, comment, usage_event) or `update` on mutable ones
- **Immutable tables:** `comment`, `records`, `usage_event` — trigger-enforced at DB level. `member_run` is MUTABLE (has `running → completed` lifecycle)
- **JSON columns auto-serialize** via `repo.py` — hook code uses native list/dict, repo handles `json.dumps`/`loads`

---

## 2. Injection Tag Source Tables

### 2.1 `<active-roster>` — driven by `member` table

Fields hooks will surface (ENTITY-DESIGN §Entity 2, 002_entities.sql lines 59-74):
- `id` (MEM-*), `name`, `role`
- `status` CHECK IN ('active', 'paused', 'retired') — filter `status='active'`
- `reports_to_member_id` — enables hierarchy rendering (Board → Sterling → Sage/Quill)
- `contract_id` — join to `contract` for runtime/entry_command display
- `suggested_skills`, `suggested_domains` (JSON arrays) — shows loadout hint

**Concrete roster (MEMBERS-DESIGN.md):**
- MEM-001 Quill (Blog Author) → reports to MEM-002 → CON-001 `/quill:run`
- MEM-002 Sterling (CMO) → reports to Board (null) → CON-002 (TBD)
- MEM-003 Sage (Content Strategist) → reports to MEM-002 → CON-003 (TBD)

**Join shape for roster render:**
```sql
SELECT m.id, m.name, m.role, m.reports_to_member_id,
       c.runtime_config  -- extract entry_command
FROM member m
LEFT JOIN contract c ON c.id = m.contract_id
WHERE m.firm_id = ? AND m.status = 'active'
ORDER BY m.reports_to_member_id NULLS FIRST, m.id
```

**Active work annotation** — each Member may have a claimed Unit (002_entities.sql line 176):
```sql
SELECT u.id, u.name, u.status, u.claimed_at
FROM unit u
WHERE u.claimed_by = ? AND u.firm_id = ?
```
This surfaces "Quill is currently on UNIT-003".

### 2.2 `<pending-gates>` — driven by `gate` table

Fields (ENTITY-DESIGN §Entity 10, 002_entities.sql lines 298-322):
- `id` (GATE-*), `action`, `context`, `status`
- `requesting_member_id` → join `member(name)`
- `target_entity_type` + `target_entity_id` (polymorphic; CHECK allowed types: firm, member, operation, project, unit, goal, document, firm_secret, contract)
- `expires_at` — if non-null, render urgency

**Filter:** `status = 'pending'` only. Skip `approved | rejected | expired | revoked`.

```sql
SELECT g.id, g.action, g.context, g.target_entity_type, g.target_entity_id,
       g.expires_at, g.created_at, m.name AS requesting_member_name
FROM gate g
JOIN member m ON m.id = g.requesting_member_id
WHERE g.firm_id = ? AND g.status = 'pending'
ORDER BY g.created_at ASC
```

**Polymorphic target resolution** — hook needs a tiny dispatcher to look up `target_entity_type` → fetch name from that table (e.g. `unit.name`, `project.name`).

### 2.3 `<goal-health>` — driven by `goal` table + parent entity

Fields (ENTITY-DESIGN §Entity 3, 002_entities.sql lines 82-96):
- `id` (GOAL-*), `target`, `status`
- `parent_entity_type` + `parent_entity_id` (polymorphic; CHECK: firm, member, operation, project, unit)
- `level` (optional hint: firm|operation|project|unit|member)
- `metric` (JSON object) — shape `{type, value, unit, deadline, current, trend?}`

**Health calculation** (v1 manual, per ENTITY-DESIGN):
- Goal status = `active` → include
- If `metric.deadline` present and past due → flag as "overdue"
- If `metric.value` and `metric.current` present → compute `(current / value) * 100` progress
- If `metric.trend` present (e.g., `growing | stable_or_growing`) → render trend indicator

**Real example (MEMBERS-DESIGN §OPS-001):**
- GOAL-001 Throughput: `{type: publish_rate, value: 2, unit: posts_per_week}` — needs external query to count publishes
- GOAL-002 Reach: `{type: unique_visitors, value: null, unit: per_month, trend: growing}` — null-baseline, just render target + trend
- GOAL-003 Quality: `{type: conversion_ratio, value: null, unit: subs_per_unique, trend: stable_or_growing}` — same

**Implication:** v1 goal-health can NOT compute most metrics — `current` is manually updated. Render target + status + deadline + last-update staleness. Don't fake a computation.

```sql
SELECT g.id, g.level, g.parent_entity_type, g.parent_entity_id,
       g.target, g.metric, g.status, g.updated_at
FROM goal g
WHERE g.firm_id = ? AND g.status = 'active'
ORDER BY
  CASE g.level
    WHEN 'firm' THEN 1 WHEN 'operation' THEN 2
    WHEN 'project' THEN 3 WHEN 'member' THEN 4 WHEN 'unit' THEN 5
    ELSE 6 END,
  g.created_at
```

**Parent name resolution** — same polymorphic dispatcher as gates.

---

## 3. Hook Write Contracts

### 3.1 `unit-completion` hook writes

When a Unit transitions to `done`:

**Write 1: Records row (immutable)** (002_entities.sql lines 327-361):
```python
repo.create(conn, "records", {
    "id": f"LOG-{next_id}",
    "firm_id": firm_id,
    "event_type": "unit.status_transition",
    "actor_type": "member",           # or "board" or "system"
    "actor_id": member_id,
    "target_entity_type": "unit",
    "target_entity_id": unit_id,
    "details": {"from": prior_status, "to": "done"},
    "run_id": active_run_id,          # nullable
    "timestamp": iso_utc_now,
})
```

**Write 2: Update Project.acceptance_criteria** — mark matching AC resolved.
- Project.acceptance_criteria is a JSON array of `{id, condition, resolved, resolved_by}`.
- Pattern: if AC has `resolved_by: "UNIT-XXX"` matching the completed unit, flip `resolved: true`.
- Read Project JSON, mutate, write back via `repo.update(conn, "project", pid, {"acceptance_criteria": ...})`.
- Example in MEMBERS-DESIGN §PROJ-001 — AC-3 has `resolved_by: "UNIT-012"`.

### 3.2 `run-record` hook writes

When a Member Run ends (any of: completed, failed, cancelled, timed_out):

**Write 1: Update member_run** (MUTABLE — 002_entities.sql line 230-246):
```python
repo.update(conn, "member_run", run_id, {
    "status": final_status,
    "ended_at": iso_utc_now,
    "outputs": outputs_list,          # JSON array
    "error": error_obj,               # JSON or None
    "notes": notes,
})
```

**Write 2: Create usage_event (IMMUTABLE)** (002_entities.sql lines 256-273):
```python
repo.create(conn, "usage_event", {
    "id": f"USG-{next_id}",
    "firm_id": firm_id,
    "member_id": member_id,
    "run_id": run_id,
    "unit_id": unit_id,               # may be None if Run not Unit-scoped
    "timestamp": iso_utc_now,
    "plan": "claude_pro_100",         # or claude_pro_200 | api | custom (CHECK)
    "model": model_string,
    "tokens_in": ..., "tokens_out": ...,
    "cache_read_tokens": ..., "cache_create_tokens": ...,
    "dollar_equivalent": float_or_none,
    "window_percent_consumed": float_or_none,
    "window_id": iso_block_start_or_none,
})
```

**Source of usage data:** ccusage integration is PARKING LOT (ENTITY-DESIGN §Parking Lot). v1 likely writes partial/null metrics; wire ccusage JSONL parser later.

### 3.3 `session-pulse` hook writes

Possibly writes a Records entry for "session started" — low priority, may skip in v1. Primary output is tag injection to stdout.

Otherwise READ-ONLY.

---

## 4. Schema Gaps Surfaced by Hook Planning

- **No `current_run_id` on member.** To know "Quill is currently running," hook must query `member_run WHERE member_id=? AND status='running'`. Doable, but a denormalized pointer would speed the roster render. Defer; SQL query is fast enough.
- **No `active_goal_current` auto-refresh.** Per §3 above, goal-health can't compute most v1 metrics. Brief must call this out explicitly — don't promise math we can't do.
- **No firm selector for multi-Firm.** v1 hardcodes `firm_id='chrisai'`. Hooks read it from config/env. Brief should specify the lookup mechanism (env var? `.firm/config.json`? workspace-relative default?).
- **Goal inheritance** is computed at read time (ENTITY-DESIGN §Entity 3). If goal-health surfaces inherited goals, SQL becomes a recursive walk of parent_ref chain. v1 recommendation: surface OWN goals only, defer inheritance display to v2.

---

## 5. Firm ID Resolution for Hooks

ENTITY-DESIGN explicitly: `firm_id` defaulted to `"chrisai"` in v1. Migration cost to multi-Firm = 1-2 hr.

**Hook must resolve firm_id from:**
1. Environment variable (proposed): `FIRM_ID` with default `"chrisai"`
2. OR: `.firm/config.json` — doesn't exist yet, would need schema decision
3. OR: hardcoded v1 default `"chrisai"` with TODO for v2

**Recommendation for BRIEF:** Env var with default. Matches pulse-activation simplicity. No new config file needed.

---

## 6. Citation Index

- ENTITY-DESIGN.md §Entity 2 Member (lines 82-138)
- ENTITY-DESIGN.md §Entity 3 Goal (lines 141-216)
- ENTITY-DESIGN.md §Entity 8 Member Run (lines 408-450)
- ENTITY-DESIGN.md §Entity 9 Usage Event (lines 453-496)
- ENTITY-DESIGN.md §Entity 10 Gate (lines 499-538)
- ENTITY-DESIGN.md §Entity 11 Records (lines 541-583)
- ENTITY-DESIGN.md §Parking Lot (line 728-737)
- MEMBERS-DESIGN.md §Active Roster (lines 25-116)
- MEMBERS-DESIGN.md §OPS-001 Goals (lines 149-192)
- MEMBERS-DESIGN.md §PROJ-001 (lines 197-228)
- 002_entities.sql `unit.claimed_by` (line 176)
- 002_entities.sql `gate` table (lines 298-322)
- 002_entities.sql `records` table (lines 327-361)
- 002_entities.sql `usage_event` immutable triggers (lines 281-293)
- repo.py `IMMUTABLE_TABLES` (line 36)
- repo.py `JSON_COLUMNS` map (lines 42-53)
- db.py `get_db_path` (line 11) and `connect` (line 16)
- units.py `checkout` atomic claim pattern (lines 25-61)
