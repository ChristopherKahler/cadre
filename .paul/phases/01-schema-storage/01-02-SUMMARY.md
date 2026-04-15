---
phase: 01-schema-storage
plan: 02
subsystem: database
tags: [sqlite, migrations, schema, ddl, triggers, foreign-keys, indexes]

requires:
  - phase: 01-schema-storage
    provides: Python package, SQLite connection helpers, migration runner, `firm init` CLI
provides:
  - 14 entity tables (firm, contract, member, goal, operation, project, unit, comment, member_run, usage_event, gate, records, firm_secret, document)
  - Firm-scoped foreign keys with ON DELETE CASCADE
  - Immutability triggers on comment, records, usage_event
  - CHECK constraints on enum-like status fields
  - 46 indexes (firm_id on scoped tables, polymorphic parent refs, work-filter fields)
  - Upgraded SQL splitter (BEGIN/END + inline `--` comments)
affects: [01-schema-storage/01-03, 02-hook-layer, 03-core-slash-commands, 04-quill-end-to-end, 06-mcp-server]

tech-stack:
  added: []
  patterns:
    - "One migration = one `NNN_name.sql` file; no rollback scripts in v1"
    - "Firm-scoped entities use `firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE`"
    - "Status/enum fields enforced with `CHECK (col IN (...))`"
    - "Array/object payloads stored as TEXT (JSON); shape validated in application layer"
    - "Polymorphic refs stored as `{relation}_entity_type + {relation}_entity_id` with CHECK on type column (no FK on id)"
    - "Immutability enforced via BEFORE UPDATE/DELETE triggers raising ABORT"
    - "Mutable entities get `created_at` + `updated_at`; immutable entities get `created_at` only"

key-files:
  created:
    - apps/agent-company-architecture/src/firm/migrations/002_entities.sql
    - apps/agent-company-architecture/tests/test_schema.py
  modified:
    - apps/agent-company-architecture/src/firm/core/migrate.py (splitter upgrade)
    - apps/agent-company-architecture/tests/test_migrate.py (count-agnostic)
    - apps/agent-company-architecture/tests/test_init.py (count-agnostic)

key-decisions:
  - "member_run is NOT immutable — it has a running→completed lifecycle and must remain UPDATE-able. Corrected plan spec."
  - "firm.values renamed to firm.core_values (SQL keyword avoidance)"
  - "member.reports_to renamed to reports_to_member_id (convention: *_member_id)"
  - "contract.member_id has no FK constraint (would create circular dep with member.contract_id)"
  - "Polymorphic refs split into two columns with CHECK on type; no composite FK on id side"
  - "Naive SQL splitter upgraded with line-comment stripping + BEGIN/END depth tracking"

patterns-established:
  - "Entity table structure: id (prefixed) + firm_id FK + domain fields + status CHECK + timestamps"
  - "Immutability via paired BEFORE UPDATE + BEFORE DELETE triggers with RAISE(ABORT, 'table is immutable')"
  - "Claimable entities (unit) have claimed_by (FK to member) + claimed_at — atomic checkout goes in 01-03"

duration: ~75min (inc. splitter fix cycle)
started: 2026-04-15T09:55:00-05:00
completed: 2026-04-15T10:55:00-05:00
---

# Phase 1 Plan 2: Schema + Storage Layer — 14 Entity Tables

**Migration 002 ships all 14 entity tables per ENTITY-DESIGN.md, with firm-scoped FKs, enum CHECK constraints, polymorphic parent refs, immutability triggers on comment/records/usage_event, and 46 indexes. 22 schema tests + 41-test suite all green. Naive SQL splitter upgraded to handle trigger bodies and inline `--` comments.**

## Performance

| Metric | Value |
|--------|-------|
| Duration | ~75 minutes (incl. one splitter-fix iteration) |
| Started | 2026-04-15T09:55:00-05:00 |
| Completed | 2026-04-15T10:55:00-05:00 |
| Tasks | 2 of 2 completed |
| Tests | 41 passing total (22 new schema tests + 19 from 01-01) |
| Files created | 2 |
| Files modified | 3 |

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: All 14 entity tables exist after migration | PASS | `test_all_14_entity_tables_exist` asserts exact set match |
| AC-2: Firm-scoped FKs enforce with ON DELETE CASCADE | PASS | Unknown-FK insert raises; firm-delete cascades to member/operation/project/unit |
| AC-3: Immutable entities reject UPDATE and DELETE | PASS | Verified for comment, records, usage_event (paired triggers each). `test_member_run_is_mutable` pins the correct non-immutable behavior of member_run. |
| AC-4: CHECK constraints reject invalid status values | PASS | unit, member, project, gate all smoke-tested; unit status values parametrized over the full lifecycle set |
| AC-5: Required indexes exist | PASS | `test_required_indexes_exist` checks 22 named indexes; 46 total indexes created |

## Accomplishments

- 14 entity tables shipped with consistent conventions (firm_id FK, prefixed TEXT id, created_at/updated_at, CHECK enums, JSON columns for array/object payloads)
- Immutability enforced at DB level for comment, records, usage_event via paired BEFORE UPDATE / BEFORE DELETE triggers
- Polymorphic parent refs (Goal, Comment, Gate target, Document) implemented as `{relation}_entity_type + {relation}_entity_id` with CHECK constraints on type
- Upgraded `_split_sql` — now handles BEGIN/END trigger bodies and inline `--` comments (the gap explicitly flagged in 01-01's SUMMARY)
- Full test suite green (41 tests) — 01-01 tests were updated to be count-agnostic so future migrations don't break them

## Task Commits

(Not committed to git yet — commit at phase transition per convention.)

| Task | Type | Description |
|------|------|-------------|
| Task 1: Migration 002_entities.sql | feat | 14 tables + FKs + CHECKs + triggers + indexes per ENTITY-DESIGN.md |
| Task 2: Schema tests | test | 22 schema tests covering AC-1 through AC-5 |
| (Side) Splitter fix | fix | `_split_sql` upgraded for BEGIN/END + inline `--` |
| (Side) Count-agnostic tests | refactor | 01-01 tests derive expected migration count from `discover_migrations()` |

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `apps/agent-company-architecture/src/firm/migrations/002_entities.sql` | Created | Creates all 14 entity tables, indexes, immutability triggers |
| `apps/agent-company-architecture/tests/test_schema.py` | Created | 22 schema tests: existence, FK enforcement, immutability, CHECK constraints, indexes |
| `apps/agent-company-architecture/src/firm/core/migrate.py` | Modified | Added `_strip_line_comments` and BEGIN/END-aware statement splitter |
| `apps/agent-company-architecture/tests/test_migrate.py` | Modified | Tests derive expected migration set from `discover_migrations()` |
| `apps/agent-company-architecture/tests/test_init.py` | Modified | Uses `_expected_bundled_count()` instead of hard-coded `1` |

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| `member_run` is not immutable | Has `running → completed` lifecycle requiring UPDATE; listing it as immutable in the plan was wrong | 3 immutable tables (comment, records, usage_event), not 4. `test_member_run_is_mutable` pins the behavior. |
| Rename `firm.values` → `firm.core_values` | `values` is a SQL keyword — safer to rename than quote everywhere | Applications reference `core_values` instead of `values`. Documented in migration header. |
| Rename `member.reports_to` → `reports_to_member_id` | Consistency with existing `owner_member_id`, `assignee_member_id` convention | Minor field-name drift from ENTITY-DESIGN.md |
| No FK on `contract.member_id` | member and contract form a circular reference (member.contract_id → contract; contract.member_id → member); only one side can hold the FK and `member → contract` is the authoritative direction | `contract.member_id` is a soft reference validated in application layer |
| Polymorphic refs split, no FK on id side | SQLite's FK system can't express "depends on one of N tables" | Application layer verifies target entity exists when inserting Goal/Comment/Gate/Document/etc |
| JSON columns as plain TEXT without `json_valid()` CHECK | v1 trusts application layer for JSON shape; CHECKs via JSON1 add complexity for marginal value at this stage | Application layer must parse and validate JSON fields |
| Upgrade splitter instead of switching to `executescript()` | Deterministic, surgical, keeps manual transaction control that fixed the DDL rollback bug in 01-01 | More robust parser that handles real-world migration files |

## Deviations from Plan

### Summary

| Type | Count | Impact |
|------|-------|--------|
| Auto-fixed | 2 | Splitter gap (from 01-01's concerns list) and immutable-set correction |
| Scope additions | 3 | Extra indexes, extra test, count-agnostic refactor of existing tests |
| Scope omissions | 0 | — |
| Deferred | 0 | — |

**Total impact:** Plan shipped as intended, with two spec corrections caught during apply (both pre-flagged risks). Splitter upgrade is a reusable win that every future migration will benefit from.

### Auto-fixed Issues

**1. [Correctness] `_split_sql` couldn't handle BEGIN/END trigger bodies or inline `--` comments**
- **Found during:** Task 1 qualify — `apply_migrations()` failed on 002 with "incomplete input" for the first CREATE TRIGGER statement; then for the `contract` CREATE TABLE
- **Root cause 1:** `BEGIN ... SELECT RAISE(ABORT, '...'); END;` contains a semicolon inside the BEGIN/END block. The naive splitter cut on that semicolon, producing a fragment
- **Root cause 2:** The `contract` table had an inline comment `-- soft ref to member(id); no FK (circular)` containing a semicolon. The splitter treated that as a statement terminator, truncating the CREATE TABLE mid-column-list
- **Fix:** Added `_strip_line_comments()` helper (removes `-- ...` from each line, inline or full-line) and upgraded the splitter to track depth through word-boundary BEGIN/END tokens so semicolons inside trigger bodies don't terminate the outer statement. Both paths covered by existing migrations and verified via full suite
- **Files:** `src/firm/core/migrate.py`
- **Verification:** Full suite 41/41 passes; migration 002 applies cleanly; 14 tables + 46 indexes + 6 triggers all created

**2. [Spec Correction] Plan 01-02 listed `member_run` as immutable, but the status lifecycle requires UPDATE**
- **Found during:** Task 1 schema design — writing immutability triggers for member_run and realizing the `running → completed` transition would be blocked
- **Fix:** Dropped member_run from the immutable set. Immutable triggers now only on comment, records, usage_event. Added `test_member_run_is_mutable` to pin the correct (mutable) behavior
- **Files:** `src/firm/migrations/002_entities.sql`, `tests/test_schema.py`
- **Impact on plan AC:** AC-3 restated: "Immutable entities (Comment, Records, Usage Event) reject UPDATE and DELETE" — 3 tables instead of 4

### Scope Additions

**Existing 01-01 tests made count-agnostic** — `test_fresh_db_applies_001_init` asserted `applied == ["001_init"]`, which broke once 002 was bundled. Refactored to derive expected migrations from `discover_migrations(_default_migrations_dir())`. Tests now robust to future additions.

**Extra indexes beyond plan's named list:** `idx_records_timestamp` (for time-range audit-log queries), `idx_records_actor`, `idx_unit_parent` (sub-unit lookups), `idx_gate_requester`, `idx_gate_target`, `idx_gate_status`, `idx_operation_owner`, `idx_operation_status`, `idx_project_operation`, `idx_project_owner`, `idx_project_status`, `idx_member_reports_to`, `idx_comment_author`, `idx_comment_reply_to`, `idx_member_run_unit`, `idx_member_run_status`, `idx_usage_event_unit`, `idx_firm_secret_name`, `idx_document_type`, `idx_document_status`. Many are obvious indexes for common query patterns (hooks, MCP lookups); cheap to create now, avoids retrofitting later.

**`test_member_run_is_mutable`** — pinned the non-immutable behavior of member_run so future changes can't accidentally add an immutability trigger to it.

### Deferred Items

None.

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| `_split_sql` fails on 002 with "incomplete input" (BEGIN/END + inline comment bugs) | Upgraded splitter with line-comment stripping and BEGIN/END depth tracking |
| Existing 01-01 tests hard-coded migration count | Refactored to derive from `discover_migrations()` |

## Next Phase Readiness

**Ready:**
- 14 entity tables with enforced constraints — Plan 01-03 (typed CRUD + atomic Unit checkout + cycle detection) has a known, enforced schema to operate against
- Polymorphic refs documented and indexed — hooks/MCP queries can use `parent_entity_type + parent_entity_id` lookups efficiently
- Unit.claimed_by column ready — 01-03 atomic checkout is one `UPDATE unit SET claimed_by = ? WHERE id = ? AND claimed_by IS NULL` query
- Splitter handles real-world SQL — future migrations (new columns, indexes, triggers) drop in without parser ceremony
- Immutability enforced at DB level — applications can't accidentally corrupt the audit trail (Records) or usage history

**Concerns:**
- No JSON shape validation at DB level — application layer must validate shape of `tags`, `goal_ids`, `acceptance_criteria`, `budget`, `runtime_config`, etc. Consider adding JSON1 `json_valid()` CHECKs in Phase 2 if we hit shape drift in practice
- `member.status` and `contract.runtime_type` enum values are baked into schema — changing them requires a migration
- Circular dependency between member and contract: only `member.contract_id` has a FK. `contract.member_id` is a soft ref. Application layer must maintain the invariant (or Plan 01-03 could add a BEFORE INSERT trigger)
- Timestamps stored as TEXT using `datetime('now')` — no timezone. All timestamps are UTC by convention. Application layer must not mix local times into these columns

**Blockers:** None.

---
*Phase: 01-schema-storage, Plan: 02*
*Completed: 2026-04-15*
