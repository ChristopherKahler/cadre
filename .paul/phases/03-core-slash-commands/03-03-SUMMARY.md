---
phase: 03-core-slash-commands
plan: 03
subsystem: services
tags: [python, services, id-generation, validation, records, audit-trail, sqlite]

requires:
  - phase: 01-foundation
    provides: firm.core.repo (CRUD), firm.core.migrate (schema), firm.core.db (connection)
provides:
  - firm.services._id.next_id() — unified ID generation for all entities
  - firm.services._validate.require_exists(), validate_status(), validate_parent_ref(), validate_fk()
  - firm.services._records.log_event() — immutable Records auto-entry
affects: [03-04, 03-05, 03-06, 03-07]

tech-stack:
  added: []
  patterns:
    - "Global COUNT(*) for ID generation (not firm-scoped) since id is PRIMARY KEY"
    - "Validation helpers raise ValueError with entity-aware messages"
    - "log_event uses repo.create for standalone immutable writes"

key-files:
  created:
    - src/firm/services/__init__.py
    - src/firm/services/_id.py
    - src/firm/services/_validate.py
    - src/firm/services/_records.py
    - tests/services/__init__.py
    - tests/services/test_id.py
    - tests/services/test_validate.py
    - tests/services/test_records.py
  modified: []

key-decisions:
  - "next_id uses global COUNT(*) not firm-scoped — id is PRIMARY KEY, must be globally unique. firm_id param kept for forward-compat."
  - "validate_parent_ref checks type against POLYMORPHIC_TABLES superset, then require_exists on target"
  - "log_event uses repo.create (not raw SQL) since each Records write is a standalone commit, not part of multi-table transaction"

patterns-established:
  - "Service infrastructure modules prefixed with underscore (_id, _validate, _records) — internal, not for direct import by skill tasks"
  - "All validators raise ValueError (not custom exceptions) — consistent with repo pattern"

duration: ~10min
started: 2026-04-15T21:14:00-05:00
completed: 2026-04-15T21:25:00-05:00
---

# Phase 3 Plan 03: Service Infrastructure Summary

**Three cross-cutting service modules shipped: unified ID generation (next_id for 12 prefixes), shared validation (require_exists, validate_status, validate_parent_ref, validate_fk), and Records auto-entry (log_event). 181/181 tests green (27 new + 154 prior).**

## Performance

| Metric | Value |
|--------|-------|
| Duration | ~10min |
| Tasks | 2 completed |
| Files created | 8 (4 source + 4 test) |
| Tests added | 27 (8 id + 12 validate + 7 records) |
| Total tests | 181/181 green |

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: next_id generates correct prefixed sequential IDs | Pass | 8 tests: first ID, sequential, global uniqueness, all prefixes, sub-unit, zero-padding |
| AC-2: Validation helpers catch invalid references | Pass | 12 tests: require_exists hit/miss, validate_status valid/invalid, validate_parent_ref type/existence, validate_fk nil/valid/missing |
| AC-3: log_event writes immutable Records entries | Pass | 7 tests: basic create, sequential IDs, details JSON, run_id FK, member actor, global uniqueness, persistence |

## Deviations from BRIEF

| Deviation | Rationale |
|-----------|-----------|
| next_id uses global COUNT(*) instead of firm-scoped | BRIEF designed firm-scoped counts, but id is a PRIMARY KEY (globally unique). Firm-scoped counts cause PK collisions with multiple firms. Fixed during test verification. |

## Next Phase Readiness

**Ready:** Wave 2 (03-04 through 03-07) can all proceed. Each entity service module imports from _id, _validate, _records.

**Blockers:** None

---
*Phase: 03-core-slash-commands, Plan: 03*
*Completed: 2026-04-15*
