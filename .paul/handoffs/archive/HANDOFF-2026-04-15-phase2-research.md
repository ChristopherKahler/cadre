# PAUL Session Handoff — Phase 2 Research Complete

**Session:** 2026-04-15, ~15:28–16:00 CDT (~30min active)
**Satellite:** `apps/agent-company-architecture/`
**Phase:** 2 — Hook Layer (1 of 4 plans complete)
**Context:** Research plan (02-01) ran full PLAN→APPLY→UNIFY loop. Brief locks injection format + hook contracts before any hook code is written.

---

## Session Accomplishments

- Entered PLAN for Phase 2 with scope classified as **complex → research-first (Option A)**
- Created and approved `.paul/phases/02-hook-layer/02-01-PLAN.md` (research type, 4 tasks, autonomous)
- Completed all 4 tasks in APPLY, all verifies PASS:
  - Task 1: Context deep-read (ENTITY-DESIGN, MEMBERS-DESIGN, migrations, repo.py, units.py, db.py) → 8 citations
  - Task 2: Paperclip reference mining (agents-runtime, execution-policy, adapters/overview, activity-log.ts, costs.ts, heartbeat-run-summary.ts) → 16 KEEP/ADAPT/REJECT decisions
  - Task 3: BASE/CARL injection precedent audit (live session payload + `.base/hooks/*.py` + `.claude/hooks/carl-hook.py`) → 26 tag citations
  - Task 4: Synthesized `.paul/phases/02-hook-layer/02-01-BRIEF.md` (414 lines, 7 sections)
- Ran UNIFY to close the loop:
  - Rewrote `02-01-SUMMARY.md` to full UNIFY template (AC table, verify outputs, deviations, patterns, next)
  - Updated `STATE.md` — loop ✓✓✓, decisions rollup, session continuity points to 02-02
  - Updated `paul.json` — `phase.status: in_progress`, timestamp refreshed, id preserved
  - Updated `ROADMAP.md` — 02-01 ✅, 02-02/03/04 outlines with dependencies
- Archived intermediate notes to `.paul/phases/02-hook-layer/_notes/`

---

## Key Data Points from Research

### Hook triggers + delivery model (locked)

| Hook | v1 Trigger | v2 Trigger | Notes |
|------|-----------|-----------|-------|
| `session-pulse` | `SessionStart:startup` hook | Same | Fires once per session; renders 3 injection tags from SQLite |
| `unit-completion` | Manual via callable `firm.hooks.unit_completion.on_unit_done()` | `PostToolUse` on `base_update_unit` (Phase 6 MCP) | Scope-tightens v1 — no auto-hook until MCP surface exists |
| `run-record` | Manual via callable `firm.hooks.run_record.on_run_end()`, wrapped by `/member:run` | Same wrapper pattern, MCP-driven | `Stop` event rejected (fires on every session, not only Member Runs) |

### Injection tag source tables (SQL shapes in BRIEF §2)

- `<active-roster>` — `member` JOIN `contract` (for entry_command) LEFT JOIN `unit ON u.claimed_by = m.id` (for "currently on" annotation)
- `<pending-gates>` — `gate WHERE status='pending'` + per-row polymorphic name resolution against target table
- `<goal-health>` — `goal WHERE status='active'` ORDER BY level hierarchy; render-only (no metric computation v1)

### Paperclip borrow highlights (full 16-row table in BRIEF §4)

**KEEP:**
- `LogActivityInput` shape — already matches our `records` schema (validated Phase 1 design)
- Per-Run cost event granularity — our `usage_event` is richer than Paperclip's per-call events
- Run outputs rollup to parent Unit on completion
- Wakeup coalesce ("one active Run per Member") — enforce in Phase 4+ `/member:run` wrapper

**ADAPT (v2+):**
- Redaction of activity details on write → borrow for `run-record` v1 (16-row #16)
- `billingType` enum (metered/subscription) → schema addition post-v1
- Execution policy review/approval stages → multi-stage Gate for later

**REJECT:**
- Four-way wakeup taxonomy (`timer/assignment/on_demand/automation`) — pulse-only locked
- Required-issue-comment invariant — ceremony we don't need v1
- Live budget-enforcement flow — budget scaffolded off v1
- Session-resume via adapter session IDs — Claude Code handles internally
- `execute/parse/test` naming — our `invoke/status/cancel` is canonical

### Phase 1 schema gaps surfaced (none blocking)

- No denormalized `current_run_id` on `member` — SQL query is fast enough; skip
- No auto-refresh for `goal.metric.current` — by design; v1 is manual
- No firm-selector config file — resolved via env var decision
- Goal inheritance computed at read time — v1 defers recursive rendering to v2

---

## Decisions Made (Plan 02-01)

These 8 live in `.paul/phases/02-hook-layer/02-01-BRIEF.md` §6 and will be appended to `.paul/PROJECT.md` Key Decisions during Plan 02-04 Task 3. Not yet in PROJECT.md — intentional to avoid premature mutation during research.

| # | Decision | Rationale | Impact |
|---|----------|-----------|--------|
| 1 | `session-pulse` triggers on `SessionStart:startup`, not `UserPromptSubmit` | One injection per session matches pulse-activation principle; avoids v1 dedup complexity | Hook is simpler; dedup deferred to v2 if UserPromptSubmit variant added |
| 2 | `unit-completion` + `run-record` ship as callable Python functions in v1 | No slash commands or MCP surface yet to auto-trigger them; Phase 6 is the right layer | Phase 2 deliverable scope-tightens; CLI verbs + Phase 3/4 wrappers invoke them |
| 3 | Hook install path: `<workspace>/.claude/hooks/firm-*.py` | Firm data is workspace-scoped (`.firm/`); hooks must be too; matches BASE satellite precedent | Installer script copies + patches `.claude/settings.json` idempotently |
| 4 | Firm ID via `FIRM_ID` env var, default `"chrisai"` | No new config file; multi-Firm migration path already documented at 1-2hr cost | Hooks portable across workspaces; easy to test with env override |
| 5 | Goal health is read-only v1 — no metric computation in hook | `metric.current` is manually updated; GOAL-002/003 have null baselines by design | Hook renders target + status + staleness; real computation lives in data sources v2 |
| 6 | `<pending-gates>` renders silent-when-empty | Matches `<base-pulse>` precedent; avoids empty-tag noise | Hook `sys.exit(0)` silently when no pending rows |
| 7 | Regex-based credential redaction on `run-record` error/notes before immutable write | Records are immutable; accidental secret-logging can't be undone | `_redact.py` utility strips `/token\|key\|secret\|password/i` keys |
| 8 | Inheritance-via-parent-chain Goal rendering deferred to v2 | Walking `parent_ref` recursively is a perf + UX question worth deferring | v1 surfaces own-Goals only; inherited Goals not shown |

---

## Gap Analysis with Decisions

### Gap 1: ccusage integration for real token/cost data
**Status:** DEFER
**Notes:** Parking lot item (ENTITY-DESIGN.md §Parking Lot). `run-record` accepts partial usage dict; ccusage JSONL parser will land in a dedicated post-Phase-2 plan. Hook is designed to tolerate null usage fields.
**Effort:** ~2-4 hr once parking-lot item is unparked
**Reference:** `apps/ccusage/` (unread this session)

### Gap 2: Auto-hooking for unit-completion / run-record via tool events
**Status:** DEFER (to Phase 6)
**Notes:** Phase 6 MCP builds the `firm_update_unit`-style tool surface that PostToolUse can filter on. Trying to auto-hook before that surface exists is building on air. v1 callable + CLI verbs are the right scope.
**Effort:** Included in Phase 6 scope
**Reference:** `.paul/ROADMAP.md` §Phase 6

### Gap 3: Dedup signature for session-pulse
**Status:** DEFER (optional v2)
**Notes:** Not needed for SessionStart trigger (fires once per session). If Phase 3 adds a UserPromptSubmit variant for roster refresh, implement CARL-style signature match (`firm_id | active_member_count | pending_gate_count | active_goal_count | most_recent_record_ts`).
**Effort:** ~30min if ever needed
**Reference:** Precedent in `.claude/hooks/carl-hook.py` `compute_context_signature()` (line ~44)

### Gap 4: 8 decisions not yet in PROJECT.md
**Status:** CREATE (but in Plan 02-04, not now)
**Notes:** Intentional. Research plans should not mutate PROJECT.md mid-phase. Plan 02-04 Task 3 handles the append. Until then, decisions are durable in BRIEF §6.
**Effort:** ~5min when 02-04 runs
**Reference:** `.paul/phases/02-hook-layer/02-01-BRIEF.md` §6

### Gap 5: `.firm/` scaffold + `firm init` CLI not verified end-to-end in this session
**Status:** INTENTIONAL (Phase 1 completed separately; out of scope for research plan)
**Notes:** Brief assumes Phase 1's `python -m firm init` creates `.firm/firm.db`. Not re-verified this session. If 02-02 fixtures fail, root-cause Phase 1 before blaming hook code.
**Effort:** 5min smoke test when 02-02 starts
**Reference:** `src/firm/cli/init.py`, `tests/` (Phase 1 output)

---

## Open Questions

- **Should session-pulse also write a Records row for "session started"?** Brief notes this as low-priority — v1 may skip to stay read-only. Revisit during 02-02 implementation if operator wants session-attended-by tracking.
- **Fixture strategy for e2e test in Plan 02-02 Task 3:** Seed via direct CRUD calls or reuse Phase 1 test fixtures? Brief didn't commit; decide at plan time.
- **Where does the installer for `firm-*.py` hooks live?** Brief proposes `install/hook-installer.py` as a "small utility" but doesn't flesh out idempotent patching of `.claude/settings.json`. Spec this in 02-02 Task 2.

---

## Reference Files for Next Session

**Primary deliverable (read this first on resume):**
```
@.paul/phases/02-hook-layer/02-01-BRIEF.md
```

**Supporting artifacts:**
```
@.paul/phases/02-hook-layer/02-01-PLAN.md
@.paul/phases/02-hook-layer/02-01-SUMMARY.md
@.paul/phases/02-hook-layer/_notes/02-01-notes-context.md
@.paul/phases/02-hook-layer/_notes/02-01-notes-paperclip.md
@.paul/phases/02-hook-layer/_notes/02-01-notes-precedent.md
```

**Phase 1 code hooks will touch:**
```
@src/firm/core/db.py           # db_connection(workspace) context manager
@src/firm/core/repo.py         # find, get, create, update CRUD primitives
@src/firm/core/units.py        # atomic checkout patterns (reference for Phase 4+)
@src/firm/migrations/002_entities.sql  # canonical table shapes
```

**Precedent code to mirror (BASE/CARL hook patterns):**
```
@~/chris-ai-systems/.base/hooks/active-hook.py          # list-surface precedent
@~/chris-ai-systems/.base/hooks/base-pulse-check.py     # alert-surface precedent
@~/chris-ai-systems/.claude/hooks/carl-hook.py          # rule-injection + dedup precedent
```

**Project context (only re-read if revising core assumptions):**
```
@.paul/PROJECT.md
@.paul/ROADMAP.md
@ENTITY-DESIGN.md
@MEMBERS-DESIGN.md
```

---

## Prioritized Next Actions

| Priority | Action | Effort | Notes |
|----------|--------|--------|-------|
| 1 | Run `/paul:plan` for 02-02 (session-pulse execute) | ~10min plan creation | Scope already in BRIEF §5.1; 3 tasks (module, entrypoint, e2e test) with human-verify checkpoint after install |
| 2 | APPLY 02-02 | ~45-90min | Tasks: implement `firm.hooks.session_pulse` + render helpers, hook entrypoint script, e2e test with seeded ChrisAI roster fixture |
| 3 | Verify hook fires live (checkpoint) | ~5min | Fresh terminal session, confirm `<active-roster>` / `<pending-gates>` / `<goal-health>` appear in injection payload |
| 4 | UNIFY 02-02 | ~5min | SUMMARY + STATE update |
| 5 | `/paul:plan` for 02-03 (unit-completion) | Depends on 02-02 (shared hooks module) | 2 tasks, autonomous |
| 6 | `/paul:plan` for 02-04 (run-record + PROJECT.md append) | Depends on 02-02, 02-03 | 3 tasks including decisions append |

---

## State Summary

**Current position:**
- Milestone: v0.1 Initial Release (1 of 8 phases)
- Phase: 2 — Hook Layer (1 of 4 plans complete)
- Loop: `PLAN ✓ APPLY ✓ UNIFY ✓` for 02-01
- `paul.json`: `phase.status: in_progress`, `loop.position: IDLE`

**Next:**
- Run `/paul:plan` for 02-02 session-pulse execute track

**Resume:**
- `/paul:resume` in `apps/agent-company-architecture/` — will auto-detect this handoff
- Primary read: `.paul/phases/02-hook-layer/02-01-BRIEF.md` §5.1 (02-02 scope outline)

---

*Handoff created: 2026-04-15 ~16:03 CDT*
*Sizes: BRIEF 414 lines / SUMMARY 107 lines / notes ~550 lines combined*
