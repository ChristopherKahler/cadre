# Phase 2 Research Brief — Hook Layer Architecture

**Plan:** 02-01 (research)
**Produced:** 2026-04-15
**Supersedes:** Open questions in ROADMAP §Phase 2, PROJECT.md §Open Questions #2
**Sources:** Full citations in `_notes/02-01-notes-context.md`, `_notes/02-01-notes-paperclip.md`, `_notes/02-01-notes-precedent.md`

---

## 1. Executive Summary

1. **Trigger events locked.** `session-pulse` → `SessionStart:startup` (v1). `unit-completion` + `run-record` → **manual-invoke wrappers in v1**; auto-hook conversion deferred to Phase 6 (MCP). This scope-tightens Phase 2 but matches the reality that v1 doesn't yet have the slash commands or tool-call surface to trigger them automatically.
2. **Three injection tags, zero new entities.** `<active-roster>`, `<pending-gates>`, `<goal-health>` all derive from Phase 1's SQLite schema. No migrations needed. Null-metric Goals (GOAL-002/003 live state) drive a format accommodation, not a schema gap.
3. **Install path: workspace-scoped.** Hooks land at `<workspace>/.claude/hooks/firm-*.py`, registered in `.claude/settings.json`. Matches BASE satellite pattern; firm data is workspace-scoped, hooks should be too.
4. **Firm ID resolution: env var with hardcoded default.** `FIRM_ID` env var, defaulting to `"chrisai"`. No new config file. Migration to multi-Firm is the 1-2 hr path already documented in ENTITY-DESIGN.
5. **Sixteen Paperclip patterns triaged** (see §4). Key carries: `logActivity` shape (already matches our `records`), per-Run cost granularity, Run-outputs-to-Unit rollup. Key rejects: heartbeat taxonomy, required-comment invariant, live budget enforcement.

---

## 2. Injection Tag Specs

### 2.1 `<active-roster>` — Who's on the team right now

**Purpose:** One-line-per-Member view of the Firm's active roster with reporting chain and current Unit claim.

**SQL (single query, left-joins for optional data):**

```sql
SELECT
  m.id, m.name, m.role, m.status, m.reports_to_member_id,
  c.runtime_type,
  json_extract(c.runtime_config, '$.entry_command') AS entry_command,
  u.id AS claimed_unit_id, u.name AS claimed_unit_name, u.status AS claimed_unit_status
FROM member m
LEFT JOIN contract c ON c.id = m.contract_id
LEFT JOIN unit u ON u.claimed_by = m.id AND u.firm_id = m.firm_id
WHERE m.firm_id = :firm_id AND m.status = 'active'
ORDER BY
  CASE WHEN m.reports_to_member_id IS NULL THEN 0 ELSE 1 END,
  m.reports_to_member_id,
  m.id;
```

**Rendered schema:**

```
<active-roster members="N">
[BOARD] — {operator.name} ({operator.role})
[MANAGERS] — Members with reports_to = NULL
  - [MEM-XXX] Name (Role) — {entry_command}
    {optional: CURRENTLY ON: [UNIT-YYY] name (status)}
[INDIVIDUAL CONTRIBUTORS] — Members with reports_to set
  - [MEM-XXX] Name (Role) reports to Name — {entry_command}
    {optional: CURRENTLY ON: [UNIT-YYY] name (status)}

BEHAVIOR: This context is PASSIVE AWARENESS ONLY.
Do NOT proactively mention roster state unless the user asks who's working on what
or a Member's current Unit is blocked.
</active-roster>
```

**Worked example (ChrisAI Firm from MEMBERS-DESIGN.md):**

```
<active-roster members="3">
[BOARD] — Chris Kahler (Board / Founder)
[MANAGERS]
  - [MEM-002] Sterling (CMO) — (no contract wired yet)
[INDIVIDUAL CONTRIBUTORS]
  - [MEM-001] Quill (Blog Author) reports to Sterling — /quill:run
  - [MEM-003] Sage (Content Strategist) reports to Sterling — (no contract wired yet)

BEHAVIOR: ...
</active-roster>
```

- **Token budget:** <300 tokens for up to 10 Members. Scales linearly; split into multiple tags if Firm exceeds 30.
- **Inclusion rules:** `status='active'` only. Paused/retired Members hidden.
- **Dedup:** v1 skip (SessionStart trigger fires once per session). Add signature if moved to UserPromptSubmit.
- **Contract rendering:** `entry_command` from `runtime_config.entry_command`. If NULL, render `(no contract wired yet)` — common in v1 since only CON-001 is defined.

### 2.2 `<pending-gates>` — What needs Board decision

**Purpose:** Surface pending Gate requests so the Board (Chris) can decide or delegate at session start.

**SQL (two-step: gates + polymorphic target lookup):**

```sql
-- Step 1: pending gates joined with requesting member
SELECT
  g.id, g.action, g.context,
  g.target_entity_type, g.target_entity_id,
  g.expires_at, g.created_at,
  m.name AS requesting_member_name
FROM gate g
JOIN member m ON m.id = g.requesting_member_id
WHERE g.firm_id = :firm_id AND g.status = 'pending'
ORDER BY
  CASE WHEN g.expires_at IS NOT NULL AND g.expires_at < datetime('now') THEN 0 ELSE 1 END,
  g.expires_at NULLS LAST,
  g.created_at ASC;

-- Step 2: per-row name resolution (polymorphic dispatcher)
-- For each row, SELECT name FROM <target_entity_type> WHERE id = :target_entity_id
-- Allowed target types (CHECK constraint in 002_entities.sql line 303):
--   firm, member, operation, project, unit, goal, document, firm_secret, contract
```

**Rendered schema:**

```
<pending-gates count="N">
[EXPIRED] — Overdue, still undecided
  - [GATE-XXX] {action} on {target_type} "{target_name}" (requested by {member_name}, expired {time_ago})
    Context: {context}

[URGENT] — Expires within 24h
  - [GATE-XXX] ...

[STANDARD]
  - [GATE-XXX] ...

BEHAVIOR: This context is PASSIVE AWARENESS ONLY.
Do NOT proactively mention pending gates unless the user asks about approvals
OR a gate is expired and unacknowledged this session.
Use /gate:decide {id} approve|reject "{comment}" to act.
</pending-gates>
```

**Worked example (hypothetical — no Gates exist in ChrisAI v1 data yet):**

```
<pending-gates count="1">
[URGENT]
  - [GATE-001] publish_post on unit "Blog post #14 draft" (requested by Quill, expires in 3h)
    Context: Blog post #14 draft complete, AC resolved, ready for publish.

BEHAVIOR: ...
</pending-gates>
```

- **Silent-when-empty:** If no pending Gates, hook prints NOTHING. No empty tag. Matches base-pulse precedent.
- **Inclusion rules:** `status='pending'` only. Decided/expired/revoked Gates hidden unless explicitly queryable.
- **Expiry classification:**
  - `expires_at < now` → [EXPIRED]
  - `expires_at < now + 24h` → [URGENT]
  - else → [STANDARD]
- **Polymorphic safety:** Target-type dispatch table in hook code, one `SELECT name FROM <table>` per row. If target row deleted (FK ON DELETE SET NULL is NOT set on gate.target_entity_id), render `(target missing)` fallback.

### 2.3 `<goal-health>` — Are we on pace?

**Purpose:** Status of active Goals with trend indicators. v1 is **read-only**: `metric.current` is manually updated (ENTITY-DESIGN §Entity 3). Hook does NOT compute metrics.

**SQL:**

```sql
SELECT
  g.id, g.level, g.target, g.metric, g.status, g.updated_at,
  g.parent_entity_type, g.parent_entity_id
FROM goal g
WHERE g.firm_id = :firm_id AND g.status = 'active'
ORDER BY
  CASE g.level
    WHEN 'firm'      THEN 1
    WHEN 'operation' THEN 2
    WHEN 'project'   THEN 3
    WHEN 'member'    THEN 4
    WHEN 'unit'      THEN 5
    ELSE 6
  END,
  g.created_at;
-- Polymorphic parent name resolution per row (same dispatcher as §2.2)
```

**Rendered schema:**

```
<goal-health goals="N">
[OPERATION-LEVEL]
  - [GOAL-XXX] {target} (parent: {parent_type} "{parent_name}")
    Metric: {metric.type} — target {metric.value} {metric.unit}, current {metric.current or "not-yet-baselined"}
    {optional: Deadline {metric.deadline} — OVERDUE|DUE IN Nd|N days past|on track}
    {optional: Trend {metric.trend}}
    Last metric update: {g.updated_at relative}

[PROJECT-LEVEL]
  - [GOAL-XXX] ...

BEHAVIOR: This context is PASSIVE AWARENESS ONLY.
v1 metrics are manually refreshed (no auto-polling). Stale metric.current reflects
last manual update; do not infer actual progress from injection alone. Use
/goal:update {id} {value} to refresh.
</goal-health>
```

**Worked example (ChrisAI Firm — OPS-001 Content Publishing from MEMBERS-DESIGN.md):**

```
<goal-health goals="3">
[OPERATION-LEVEL]
  - [GOAL-001] Publish 2 longform blog posts per week (parent: operation "Content Publishing")
    Metric: publish_rate — target 2 posts_per_week, current not-yet-baselined
    Last metric update: 1d ago (2026-04-14)
  - [GOAL-002] Monthly unique visitors trending upward (parent: operation "Content Publishing")
    Metric: unique_visitors — target null per_month, current not-yet-baselined
    Trend: growing
    Last metric update: 1d ago
  - [GOAL-003] Unique-visitor-to-subscriber ratio held or growing (parent: operation "Content Publishing")
    Metric: conversion_ratio — target null subs_per_unique, current not-yet-baselined
    Trend: stable_or_growing
    Last metric update: 1d ago

BEHAVIOR: ...
</goal-health>
```

- **Null-metric tolerance:** GOAL-002 and GOAL-003 have `metric.value: null` pending analytics baseline. Hook MUST render "target null {unit}" and "current not-yet-baselined" without crashing. Trend still meaningful.
- **Inclusion rules:** `status='active'` only. `achieved` and `abandoned` hidden.
- **Inheritance:** v1 surfaces own-Goals only. Inherited Goal rendering (walking `parent_ref` chain) is v2.
- **Overdue calc:** only when `metric.deadline` present. Null deadline → no overdue line.

---

## 3. Hook Contracts

### 3.1 `session-pulse` hook

| Attribute | Value |
|-----------|-------|
| **Trigger event** | `SessionStart:startup` (once per session) |
| **Script path** | `<workspace>/.claude/hooks/firm-session-pulse.py` |
| **Settings hook** | `settings.json` `hooks.SessionStart[].hooks[]` block |
| **Input payload** | Claude Code passes JSON via stdin with `{session_id, cwd, ...}`. Hook reads `cwd` to locate `.firm/firm.db`. |
| **Firm ID resolution** | `os.environ.get("FIRM_ID", "chrisai")` |
| **SQLite reads** | `member`, `contract`, `unit`, `gate`, `goal` (SELECTs per §2.1–2.3) |
| **SQLite writes** | NONE. Read-only. |
| **Output** | stdout. Up to 3 tags: `<active-roster>`, `<pending-gates>`, `<goal-health>`. Silent on empty. |
| **Exit code** | `0` always. Malformed DB / missing `.firm/` → silent no-op. |
| **Failure modes (tolerated)** | Missing `.firm/firm.db` (pre-`/firm:init`), malformed JSON in columns, DB lock contention (SELECT retries not needed — reads don't block). |
| **Failure modes (fatal, v1)** | None. Hook never blocks session start. |

### 3.2 `unit-completion` wrapper (v1 manual invoke)

**v1 decision:** Ship as a **callable Python function** in `src/firm/hooks/unit_completion.py`, invoked by a slash command (Phase 3) or manually. Auto-hook via PostToolUse deferred to Phase 6.

| Attribute | Value |
|-----------|-------|
| **Trigger event (v1)** | Manual call: `from firm.hooks.unit_completion import on_unit_done; on_unit_done(workspace, unit_id, member_id, prior_status)` |
| **Trigger event (v2)** | `PostToolUse` filtered on `base_update_unit` (or firm MCP equivalent). Phase 6. |
| **Input payload (v1)** | `workspace: Path, unit_id: str, member_id: str, prior_status: str, run_id: str \| None` |
| **SQLite reads** | `unit` (fetch current status + project_id), `project` (for AC update) |
| **SQLite writes** | 1. `records` INSERT (immutable) for `unit.status_transition` event. 2. `project` UPDATE of `acceptance_criteria` JSON — flip `resolved: true` on AC rows with matching `resolved_by: <unit_id>`. |
| **Output** | No stdout. Returns a summary dict for CLI display. |
| **Failure modes (tolerated)** | Unit not found → return `{ok: false, reason: "unit-not-found"}`. Project AC mutation fails → warn, continue (records row already committed). |
| **Failure modes (fatal)** | DB unavailable → raise. Caller handles. |
| **Redaction** | None needed — event details are status transitions, no user content. |

### 3.3 `run-record` wrapper (v1 manual invoke)

**v1 decision:** Same as unit-completion — callable function, wired up to `/member:run` slash command in Phase 3. Auto-hook on `Stop` event rejected (fires on every session, not only Member Runs).

| Attribute | Value |
|-----------|-------|
| **Trigger event (v1)** | Manual call from `/member:run` wrapper at Run end |
| **Trigger event (v2)** | Phase 4+ when `/quill:run` / `/member:run` are slash commands that wrap Runs. |
| **Input payload** | `workspace: Path, run_id: str, final_status: str, outputs: list, error: dict \| None, usage: dict \| None, notes: str \| None` |
| **SQLite reads** | `member_run` (fetch unit_id, member_id, firm_id for FK cross-population on usage_event) |
| **SQLite writes** | 1. `member_run` UPDATE — set `status`, `ended_at`, `outputs`, `error`, `notes`. 2. `usage_event` INSERT (immutable) — all token fields, derived from `usage` dict (partial OK if ccusage not wired). 3. `unit` UPDATE (if `run_id → unit_id` present) — merge `run.outputs` into `unit.outputs`. 4. `records` INSERT for `member_run.ended` event. |
| **Output** | Returns summary dict. No stdout. |
| **Failure modes (tolerated)** | `usage` dict missing → write USG row with nulls for token fields. ccusage integration is parking lot. |
| **Failure modes (fatal)** | `member_run` row not found (should not happen if caller passes a valid run_id) → raise. |
| **Redaction** | Strip keys matching `/token\|key\|secret\|password/i` from `error` and `notes` before write. One-function pass. |

---

## 4. Paperclip Borrow Decisions

Condensed from `_notes/02-01-notes-paperclip.md`. 16 patterns triaged:

| # | Pattern | Decision | Applies to |
|---|---------|----------|------------|
| 1 | Wakeup coalesce (one Run per Member) | **KEEP** | `run-record` (Phase 4+) |
| 2 | `LogActivityInput` shape | **KEEP** (already matches) | `records` writes in all hooks |
| 3 | Four-way wakeup taxonomy (`timer/assignment/on_demand/automation`) | **REJECT** | N/A — pulse-only |
| 4 | Separate `agentId` + `actorId` on activity | **REJECT** | — |
| 5 | Live events / plugin bus on activity | **ADAPT** (v2) | Possible `<recent-activity>` tag |
| 6 | Per-Run cost event granularity | **KEEP** | `run-record` usage_event |
| 7 | `billingType` enum (metered/subscription) | **ADAPT** (post-v1) | Schema addition later |
| 8 | Live budget-enforcement flow | **REJECT** (v1) | Budget scaffolded off |
| 9 | Run outputs → Unit outputs rollup | **KEEP** | `run-record` merges into unit |
| 10 | Required-issue-comment invariant | **REJECT** (v1) | No comment-retry in v1 |
| 11 | Execution policy (review/approval stages) | **ADAPT** (v2+) | v1 Gate is yes/no only |
| 12 | `execute/parse/test` naming | **REJECT** (keep our `invoke/status/cancel`) | Contract interface |
| 13 | `test()` adapter diagnostic | **ADAPT** (Phase 6/8) | Installer concern |
| 14 | Session resume via adapter session IDs | **REJECT** (v1) | Claude Code handles |
| 15 | Plugin event bus | **REJECT** (Phase 2) | Phase 6 MCP is the layer |
| 16 | Redaction of activity details on write | **ADAPT** | Simple regex in `run-record` |

---

## 5. Recommended Plan Split for 02-02 / 02-03 / 02-04

### Plan 02-02 — session-pulse hook (execute)

**Scope:** Implement the read-only SessionStart hook that renders `<active-roster>`, `<pending-gates>`, `<goal-health>` into Claude Code sessions.

**Dependencies:** None (Phase 1 substrate suffices).

**Tasks (3):**

1. **Task 2-02-1: Implement `firm.hooks.session_pulse` module.**
   Files: `src/firm/hooks/__init__.py` (new), `src/firm/hooks/session_pulse.py`, `src/firm/hooks/render.py`
   Action: Implement SQL functions for each of 3 tags (pure functions taking conn + firm_id, returning rendered string or None). Implement polymorphic name-resolution dispatcher. Concatenate outputs; silent when all three None.
   Verify: `pytest tests/hooks/test_session_pulse.py` — unit tests per tag with seeded fixture data matching MEMBERS-DESIGN roster.
   Done: AC-1 from this plan's equivalent (tag format spec satisfied).

2. **Task 2-02-2: Implement the hook entrypoint script.**
   Files: `.claude/hooks/firm-session-pulse.py` (new), `install/hook-installer.py` (new, small utility)
   Action: Entrypoint reads stdin JSON, resolves `FIRM_ID` env / default, resolves workspace from cwd, opens DB via `firm.core.db.db_connection`, calls `firm.hooks.session_pulse.render(conn, firm_id)`, prints result, exits 0. Installer copies to `<workspace>/.claude/hooks/` and patches `.claude/settings.json` (idempotent).
   Verify: `python .claude/hooks/firm-session-pulse.py < test-input.json` returns expected tags for seeded DB.
   Done: Hook installs and fires at session start; visible in live session payload.

3. **Task 2-02-3: End-to-end integration test.**
   Files: `tests/hooks/test_session_pulse_e2e.py`
   Action: Seed DB with full ChrisAI roster (Quill/Sterling/Sage, OPS-001 + goals, one pending Gate). Invoke hook script as subprocess. Assert output matches golden file `tests/golden/session-pulse-chrisai.txt`.
   Verify: `pytest tests/hooks/test_session_pulse_e2e.py`
   Done: AC-4 satisfied (injection correctness gate from PROJECT.md Quality Gates).

**Checkpoint:** After Task 2-02-2 — `checkpoint:human-verify`. User runs a fresh terminal session, confirms hook output appears as expected in injection payload. Diagnostic classification (intent/spec/code) on any mismatch.

---

### Plan 02-03 — unit-completion handler (execute)

**Scope:** Implement the callable `firm.hooks.unit_completion.on_unit_done()` function that writes Records + updates Project AC.

**Dependencies:** `02-02` (for the shared `hooks/` module layout + rendering patterns — avoids duplicating setup).

**Tasks (2):**

1. **Task 2-03-1: Implement `on_unit_done` function.**
   Files: `src/firm/hooks/unit_completion.py` (new)
   Action: Signature per §3.2 input payload. Writes records row. Walks Project.acceptance_criteria JSON, flips AC rows with `resolved_by == unit_id`. Transaction-wrapped.
   Verify: `pytest tests/hooks/test_unit_completion.py` — fixtures covering: (a) normal completion with matching AC, (b) completion with no matching AC, (c) unit not found, (d) project missing.
   Done: Records write idempotent (retries safe via unique LOG-id), AC update idempotent (already-resolved AC unchanged).

2. **Task 2-03-2: CLI hook for Phase 3 integration.**
   Files: `src/firm/cli/unit.py` (new, `complete` subcommand stub)
   Action: Add `python -m firm unit complete <unit_id> --member <member_id>` CLI verb that calls `on_unit_done`. Phase 3 slash command `/unit:complete` will wrap this CLI.
   Verify: `python -m firm unit complete UNIT-000 --member MEM-001 --dry-run` prints what would change.
   Done: CLI verb tested; returns structured summary.

**Checkpoint:** None. Fully autonomous.

---

### Plan 02-04 — run-record handler (execute)

**Scope:** Implement the callable `firm.hooks.run_record.on_run_end()` function that finalizes member_run + writes usage_event + rolls outputs up to Unit.

**Dependencies:** `02-02` (shared hooks module), `02-03` (AC-update helper reused).

**Tasks (3):**

1. **Task 2-04-1: Implement `on_run_end` function.**
   Files: `src/firm/hooks/run_record.py` (new), `src/firm/hooks/_redact.py` (new, ~20 LOC utility)
   Action: Signature per §3.3. Writes member_run update (mutable), usage_event insert (immutable), unit output merge (if unit_id), records row. All in single transaction. `_redact.py` strips credential-shaped keys from error/notes.
   Verify: `pytest tests/hooks/test_run_record.py` — fixtures: (a) completed Run with full usage, (b) failed Run with error dict, (c) Run without unit_id (non-Unit-scoped), (d) redaction on notes containing "api_key: ...".
   Done: All 4 DB writes commit atomically; rollback on partial failure leaves DB clean.

2. **Task 2-04-2: CLI hook for Phase 4 integration.**
   Files: `src/firm/cli/run.py` (new, `end` subcommand stub)
   Action: `python -m firm run end <run_id> --status completed --outputs '[...]'` → calls `on_run_end`. `/member:run` (Phase 3) and `/quill:run` (Phase 4) wrap this.
   Verify: `python -m firm run end RUN-001 --status completed` writes expected rows.
   Done: CLI verb tested.

3. **Task 2-04-3: Update BRIEF's Decisions section as PROJECT.md rows.**
   Files: `.paul/PROJECT.md` (Key Decisions table append)
   Action: Pull the Decisions section from this brief (§6) into PROJECT.md.
   Verify: PROJECT.md grep for each decision's distinctive phrase returns a match.
   Done: Decisions durable; brief can be referenced or archived without loss.

**Checkpoint:** None. Fully autonomous.

---

## 6. Decisions to Record (for PROJECT.md Key Decisions table)

Paste as table rows into `.paul/PROJECT.md` §Key Decisions:

| Decision | Rationale | Date | Status |
|----------|-----------|------|--------|
| `session-pulse` triggers on `SessionStart:startup`, not `UserPromptSubmit` | One injection per session matches pulse-activation principle (PROJECT.md §8); UserPromptSubmit would spam tags and force dedup logic v1 doesn't need | 2026-04-15 | Active (02-01) |
| `unit-completion` and `run-record` ship as callable functions in v1, not Claude Code hooks | v1 has no slash commands or MCP surface to auto-trigger them; Phase 6 (MCP) is the right layer for auto-hooking; scope-tightens Phase 2 deliverable | 2026-04-15 | Active (02-01) |
| Hook install path is `<workspace>/.claude/hooks/firm-*.py` | Firm data is workspace-scoped (`.firm/`); hooks must be too; matches BASE satellite precedent | 2026-04-15 | Active (02-01) |
| Firm ID resolves via `FIRM_ID` env var with default `"chrisai"` | No new config file needed; multi-Firm migration path already documented (ENTITY-DESIGN §Entity 1) at 1-2hr cost | 2026-04-15 | Active (02-01) |
| Goal health is read-only in v1 — hook does NOT compute metrics | `metric.current` is manually updated (ENTITY-DESIGN §Entity 3); GOAL-002/003 have null baselines; v1 surfaces target + status + staleness only | 2026-04-15 | Active (02-01) |
| `<pending-gates>` renders silent-when-empty | Matches `<base-pulse>` precedent; avoids empty-tag noise | 2026-04-15 | Active (02-01) |
| Run-record applies regex-based credential redaction on `error` and `notes` before write | Records are immutable; accidental secret-logging can't be undone; matches Paperclip's `sanitizeRecord` pattern (borrow #16) | 2026-04-15 | Active (02-01) |
| Inheritance-via-parent-chain rendering (Goals cascade) is v2, not v1 | Walking `parent_ref` recursively is a performance + UX question worth deferring until live data demands it | 2026-04-15 | Deferred (02-01) |

---

## 7. Open Flags for Future Sessions

- **ccusage integration** — still parking-lot (ENTITY-DESIGN §Parking Lot). `run-record` accepts partial usage; wire ccusage JSONL parser in a dedicated post-Phase-2 plan.
- **Auto-hooking for unit-completion / run-record** — Phase 6 MCP concern. Revisit once firm MCP server exposes `base_update_unit` equivalent.
- **Dedup logic** — v1 skip. If Phase 3 adds UserPromptSubmit trigger variant for roster refresh, implement CARL-style signature match.
- **Inherited-Goal rendering** — v2 feature. Requires recursive SQL walk + clearer UI for own-vs-inherited distinction.
- **Multi-Firm** — still deferred. 1-2 hr migration when a second Firm is created (e.g., `cc-strategic`).

---

*Brief complete. Ready for UNIFY and subsequent plan creation for 02-02/03/04.*
