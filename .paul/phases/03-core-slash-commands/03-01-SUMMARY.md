---
phase: 03-core-slash-commands
plan: 01
subsystem: commands
tags: [skillsmith, slash-commands, service-layer, entity-lifecycle, python, skill-spec]

requires:
  - phase: 01-foundation
    provides: firm.core.repo (CRUD), firm.core.db (connection), firm.core.units (checkout, cycle detection), 14 entity tables
  - phase: 02-hook-layer
    provides: on_unit_done, on_run_end, _redact, session-pulse hook, CLI verbs (unit complete, run end)
provides:
  - 03-01-BRIEF.md with complete command surface, Skillsmith skill spec, service layer design, 9-plan breakdown
affects: [03-02 through 03-10, skill installation, Phase 4 member dispatch]

tech-stack:
  added: []
  patterns:
    - "Skillsmith-compliant skill architecture (suite type, 11 tasks, 2 frameworks, 1 context, 1 checklist)"
    - "Python service layer bridging skill tasks to repo CRUD (firm.services.*)"
    - "Unified ID generation via next_id(conn, table, prefix, firm_id)"
    - "Records auto-entry on significant operations (services own audit trail)"

key-files:
  created:
    - .paul/phases/03-core-slash-commands/03-01-BRIEF.md
  modified: []

key-decisions:
  - "Skillsmith-compliant skill architecture for all slash commands (operator mandate)"
  - "firm.services.* as bridge between skill tasks and firm.core.repo"
  - "10 entities get Phase 3 commands; 4 deferred to Phase 6 MCP"
  - "Unified ID generation via services._id.next_id() — COUNT(*)-based, matches Phase 2 pattern"
  - "Records auto-entry owned by services, not skill tasks"
  - "Skill source at src/firm/commands/firm/, install to <workspace>/.claude/commands/firm/"
  - "v1 tasks invoke services via subprocess (python -m firm <verb>)"
  - "Board is default author/approver in v1"

patterns-established:
  - "Service function signature: (conn, *, firm_id, ...) -> dict — matches Phase 2 hook handler pattern"
  - "Multiplexed sub-action routing in task files (e.g., /firm:member create|list|view|update)"
  - "Polymorphic parent_ref validation via services._validate.validate_parent_ref()"

duration: ~15min
started: 2026-04-15T20:44:00-05:00
completed: 2026-04-15T20:55:00-05:00
---

# Phase 3 Plan 01: Skill Discovery + Command Surface Design Summary

**Research BRIEF locks the complete slash command surface, Skillsmith skill spec, Python service layer design, and 9-plan execute breakdown for Phase 3 (Core Slash Commands). 10 entities get commands, 4 deferred. 16-file skill architecture follows Skillsmith conventions.**

## Performance

| Metric | Value |
|--------|-------|
| Duration | ~15min |
| Started | 2026-04-15T20:44:00-05:00 |
| Completed | 2026-04-15T20:55:00-05:00 |
| Tasks | 3 completed |
| Files created | 1 (BRIEF.md) |

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: Command surface complete and unambiguous | Pass | 14 entities mapped, 10 with commands (sub-actions, args, read/write profiles), 4 deferred with rationale |
| AC-2: Skillsmith skill spec scaffold-ready | Pass | Full skill-spec template: identity, persona, activation, 11 commands, content architecture (11 tasks, 2 frameworks, 1 context, 1 checklist) |
| AC-3: Service layer bridges skills to repo | Pass | 14 modules designed, typed signatures, next_id() for all prefixes, _validate helpers, _records.log_event() |
| AC-4: Execute plan breakdown scoped and ordered | Pass | 9 plans (03-02 to 03-10), 4 waves, 2-3 tasks each, genuine dependencies only |

## Accomplishments

- Complete command surface map for 10 entity types covering 37 sub-actions (create/list/view/update/checkout/complete/approve/reject/request/add)
- Skillsmith-compliant skill spec ready to hand to `/skillsmith scaffold` (16 files total)
- Python service layer design with uniform function signatures, shared infrastructure (_id, _validate, _records), and Records auto-entry
- 9-plan execute breakdown with 4-wave parallel execution graph
- 8 decision log entries drafted for PROJECT.md

## Key Metrics

| Metric | Value |
|--------|-------|
| Phase 3 entities | 10 |
| Deferred entities | 4 |
| Total sub-actions | 37 |
| Skill files | 16 |
| Service modules | 14 (10 entity + 3 infrastructure + 1 __init__) |
| Execute plans | 9 (03-02 through 03-10) |
| Execution waves | 4 |

## Next Phase Readiness

**Ready:**
- BRIEF is self-contained reference for all 9 execute plans
- Skill spec can be handed to 03-02 (scaffold)
- Service layer design can be handed to 03-03 (infrastructure) and 03-04 through 03-07 (entity services)

**Concerns:**
- Phase 1 + Phase 2 still uncommitted (bundle commit deferred). Should fire before 03-02 APPLY to establish clean git baseline.
- 16 skill files + 14 service modules + tests = significant code volume. Each plan is sized to ~50% context but total Phase 3 will span many sessions.

**Blockers:** None

---
*Phase: 03-core-slash-commands, Plan: 01*
*Completed: 2026-04-15*
