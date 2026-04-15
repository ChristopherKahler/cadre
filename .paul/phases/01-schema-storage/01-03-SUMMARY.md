---
phase: 01-schema-storage
plan: 03
subsystem: database
tags: [sqlite, crud, repository, atomic-checkout, cycle-detection, json]

requires:
  - phase: 01-schema-storage
    provides: 14 entity tables with enforced constraints, immutability triggers, and indexes
provides:
  - Generic CRUD repository (`firm.core.repo`) for all 14 tables
  - JSON column auto-serialization (10 tables with JSON-shaped columns)
  - Auto `updated_at` touch on update
  - Table/column allow-list guards (SQL-injection resistant)
  - Atomic Unit checkout (`firm.core.units.checkout`)
  - Unit release primitive
  - Dependency cycle detection (`validate_no_cycle`, `CycleError`)
  - Convenience wrappers: `create_with_deps`, `set_dependencies`
affects: [02-hook-layer, 03-core-slash-commands, 04-quill-end-to-end, 06-mcp-server]

tech-stack:
  added: []
  patterns:
    - "Generic repository over raw SQL — no ORM, stdlib sqlite3 only"
    - "Table + column allow-lists hardcoded; unknown names → ValueError"
    - "JSON columns registered per-table; list/dict round-trip via json.dumps/loads"
    - "Immutable tables short-circuit at application layer (ImmutableTableError) before trigger fires"
    - "Atomic ops via SQL WHERE clauses + RETURNING (SQLite 3.35+)"
    - "Cycle detection via Python-side BFS with visited-set pruning"

key-files:
  created:
    - apps/agent-company-architecture/src/firm/core/repo.py
    - apps/agent-company-architecture/src/firm/core/units.py
    - apps/agent-company-architecture/tests/test_repo.py
    - apps/agent-company-architecture/tests/test_units.py

key-decisions:
  - "Rename plan's `repo.list` → `repo.find` to avoid shadowing the Python `list` builtin"
  - "ImmutableTableError raised at application layer (nicer error than DB trigger fires generic IntegrityError)"
  - "Cycle detection tolerates soft refs to nonexistent Units — the FK-less graph is intentional per ENTITY-DESIGN"
  - "No ORM, no Pydantic — stdlib sqlite3 only; runtime deps stay at zero"
  - "Status transition on checkout: pending → in_progress only; other statuses preserved"

patterns-established:
  - "CRUD signature: (conn, table, [id,] [data | **filters]) → dict | list | int"
  - "JSON column handling via JSON_COLUMNS registry — single place to declare JSON-shaped fields per table"
  - "Atomic claim pattern: UPDATE ... WHERE claim_col IS NULL RETURNING *"

duration: ~22min
started: 2026-04-15T11:00:00-05:00
completed: 2026-04-15T11:22:00-05:00
---

# Phase 1 Plan 3: Typed CRUD + Unit Domain Logic

**Generic CRUD repository (`firm.core.repo`) shipped over all 14 entity tables with JSON auto-serialization, auto `updated_at`, and injection-safe guards. Unit-specific atomic checkout + cycle detection (`firm.core.units`) wire the DB-level primitives to domain semantics. 76/76 tests green — Phase 1 data substrate complete.**

## Performance

| Metric | Value |
|--------|-------|
| Duration | ~22 minutes |
| Tasks | 2 of 2 |
| Tests | 76 total (35 new + 41 prior) |
| Files created | 4 |

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: Generic CRUD works on all 14 tables | PASS | Roundtrip + list filter + ordering tested |
| AC-2: JSON columns round-trip transparently | PASS | list, dict, and null paths all verified |
| AC-3: updated_at auto-touches; immutable tables reject updates | PASS | Sleep-based timestamp check + ImmutableTableError + trigger enforces DELETE |
| AC-4: Atomic Unit checkout resolves deterministically | PASS | Happy path, collision, preserved status, missing unit, invalid member_id all tested |
| AC-5: Cycle detection catches direct, indirect, self-loop | PASS | + tolerates missing Units (soft ref); create_with_deps blocks before insert |

## Accomplishments

- Generic CRUD shipped with zero ORM dependencies — just stdlib sqlite3 + careful allow-lists
- Injection-safe by construction: table names from hardcoded allow-list, column names validated against PRAGMA table_info on first use (cached)
- JSON column handling is transparent at the API — callers pass Python lists/dicts, get them back
- Atomic Unit checkout in a single SQL statement with `UPDATE ... WHERE claimed_by IS NULL RETURNING *` — no race window, no application-layer locking
- Cycle detection works on the soft-ref graph (depends_on has no FK, so BFS tolerates missing targets gracefully)
- 35 new tests pinning behavior: 18 repo + 17 units

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `apps/agent-company-architecture/src/firm/core/repo.py` | Created | Generic CRUD + JSON handling + allow-list guards |
| `apps/agent-company-architecture/src/firm/core/units.py` | Created | Atomic checkout + release + cycle detection |
| `apps/agent-company-architecture/tests/test_repo.py` | Created | 18 tests covering AC-1..AC-3 + injection guards |
| `apps/agent-company-architecture/tests/test_units.py` | Created | 17 tests covering AC-4..AC-5 + release + reclaim |

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Rename `repo.list` → `repo.find` | `list` shadows Python builtin; confuses static analyzers and list comprehension syntax inside the function body | Clearer API, no type-checker noise. Plan's spec said `list`; deviation documented. |
| `ImmutableTableError` at application layer | Cleaner error message than generic `sqlite3.IntegrityError` from trigger | App code can distinguish "wrong table for update" from other integrity issues. Triggers still fire on DELETE as a belt-and-suspenders DB-level invariant |
| BFS over recursive CTE for cycle detection | Python-side BFS is readable, handles missing-unit gracefully, and v1 scale (hundreds of Units) is trivial | Trade: CTE would be faster at 10k+ Units. Revisit at scale. |
| Status transition on checkout limited to `pending → in_progress` | Claiming a `blocked` Unit shouldn't silently unblock it. Explicit status changes stay separate from claim | Caller must manage status transitions for non-pending Units |

## Deviations from Plan

### Summary

| Type | Count | Impact |
|------|-------|--------|
| Auto-fixed | 0 | — |
| Scope additions | 1 | Minor: `test_reclaim_after_release_works` (reclaim after release chain) |
| Renames | 1 | `list` → `find` (static analysis + syntax clarity) |
| Deferred | 0 | — |

### Scope Additions

**`test_reclaim_after_release_works`** — Exercises claim → release → claim-by-different-member. Not explicitly called out in plan's test list but natural coverage for the lifecycle.

### Renames

**`repo.list` → `repo.find`** — Plan text used `list` in signatures. Python has `list` as a builtin; shadowing it at module level causes pyright to misinterpret list comprehension syntax inside the function body as calls to the outer function. `find` is concise, unambiguous, matches common ORM conventions (find, findOne, findMany). Plan spec intent preserved — just a different name.

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| `list` function name shadowing the builtin | Renamed to `find`; tests updated |

## Next Phase Readiness

**Ready:**
- Phase 2 (Hook Layer) has a clean data API: `repo.find`, `repo.get`, etc. No SQL in hook code
- Quill (Phase 4) can consume Units via `checkout`/`release`, create Records via `repo.create("records", ...)`
- MCP server (Phase 6) can expose CRUD directly — each repo function maps to one or two MCP tools
- Cycle detection ready for UI/command-level validation when new Unit dependencies are proposed

**Concerns:**
- No concurrency test (multi-process/thread); SQLite's transactions are the guarantee, but real concurrency under the hook ecosystem may surface edge cases
- `datetime('now')` is second-resolution UTC. If hooks write rapidly, created_at ties will happen — `list` falls back to `id` as secondary sort for deterministic ordering
- JSON columns rely on application-layer shape correctness. A bad write produces a bad read. Consider JSON1 validators in a follow-up if shape drift appears

**Blockers:** None.

---
*Phase: 01-schema-storage, Plan: 03*
*Completed: 2026-04-15*
