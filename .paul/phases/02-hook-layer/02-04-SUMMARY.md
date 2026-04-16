---
phase: 02-hook-layer
plan: 04
subsystem: hooks
tags: [sqlite, records, transactions, cli, argparse, python, redaction, audit-trail, usage-event]

requires:
  - phase: 01-foundation
    provides: firm.core.repo (CRUD), firm.core.db (connection), firm.core.migrate (schema), records/member_run/usage_event/unit tables
  - phase: 02-hook-layer (plan 02-01)
    provides: hook contracts, on_run_end input payload + failure modes (BRIEF §3.3, §5.3, §6)
  - phase: 02-hook-layer (plan 02-02)
    provides: firm/hooks/ package layout, FIRM_NOW_OVERRIDE test pattern, polymorphic dispatcher
  - phase: 02-hook-layer (plan 02-03)
    provides: raw-SQL transaction pattern, _next_records_id, CLI verb pattern, DB-trigger test fixture
provides:
  - firm.hooks.run_record.on_run_end() — 4-row atomic transaction (member_run + usage_event + unit + records)
  - firm.hooks._redact.redact() — credential-pattern stripping for dicts, strings, lists
  - python -m firm run end <id> CLI verb (+ --dry-run, JSON output)
  - PROJECT.md Key Decisions table with 15 accumulated Phase 1+2 decisions
affects: [03-slash-commands, 04-quill-e2e, 06-mcp-layer]

tech-stack:
  added: []
  patterns:
    - "4-row atomic transaction via raw SQL (member_run UPDATE + usage_event INSERT + optional unit UPDATE + records INSERT)"
    - "Credential-regex redaction utility as pure function (never mutates input)"
    - "CLI returns JSON to stdout with exit 0 for all structured results (including run-not-found)"
    - "USG-NNN id scheme for usage_event (mirrors LOG-NNN from records)"

key-files:
  created:
    - src/firm/hooks/_redact.py
    - src/firm/hooks/run_record.py
    - src/firm/cli/run.py
    - tests/hooks/test_run_record.py
    - tests/cli/test_run.py
  modified:
    - src/firm/hooks/__init__.py
    - src/firm/__main__.py
    - .paul/PROJECT.md

key-decisions:
  - "on_run_end takes (conn, *, firm_id, ...) matching on_unit_done pattern, not (workspace, ...) as BRIEF sketch implied — codebase consistency"
  - "CLI outputs JSON to stdout and returns exit 0 for all structured results (run-not-found, db-not-found) — machine-friendly vs unit CLI's stderr/exit-1 pattern"
  - "unit.outputs merge is unconditional when unit_id present — doesn't gate on final_status per BRIEF §3.3"
  - "_redact is a separate module (not inline) for reuse in Phase 6 MCP and future hooks"

patterns-established:
  - "CLI JSON output pattern: all structured results on stdout, exit 0; exit 1 only for unhandled exceptions"
  - "Credential redaction as a pre-write gate on immutable tables (records, usage_event) — redact() before any INSERT/UPDATE"
  - "_next_*_id cross-module import — hooks can reuse each other's id generators via package-internal imports"

duration: ~20min
started: 2026-04-15T20:13:00-05:00
completed: 2026-04-15T20:28:00-05:00
---

# Phase 2 Plan 04: Run-Record Handler Summary

**Callable `on_run_end()` ships the Member-Run finalization handler with 4-row atomic transactions, credential redaction, and `firm run end` CLI verb — plus 15 accumulated Phase 1+2 decisions durably appended to PROJECT.md. 154/154 tests green.**

## Performance

| Metric | Value |
|--------|-------|
| Duration | ~20min |
| Started | 2026-04-15T20:13:00-05:00 |
| Completed | 2026-04-15T20:28:00-05:00 |
| Tasks | 3 completed |
| Files created | 5 |
| Files modified | 3 |
| Tests added | 25 (15 unit + 10 CLI) |
| Total tests | 154/154 green |

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: on_run_end writes member_run + usage_event + records atomically | Pass | 4-row transaction verified; test_completes_run_with_full_usage confirms all writes |
| AC-2: unit.outputs merged when unit_id present; skipped when absent | Pass | test_unit_outputs_merged_not_replaced (append semantics); test_completes_run_without_unit_id (3-write path) |
| AC-3: Partial failure rolls back all writes | Pass | DB-trigger fixture on usage_event INSERT; member_run.status stays 'running', no orphan rows |
| AC-4: Credential redaction strips secret-shaped keys | Pass | api_key, auth_token both redacted; caller's original dict unchanged |
| AC-5: firm run end CLI with full flag parity | Pass | 8 flags verified in help; JSON output; --dry-run; run-not-found exit 0; db-missing exit 0 |
| AC-6: PROJECT.md Key Decisions table has 15 new rows | Pass | All 15 distinctive phrases grep-verified; 34 total rows (19 existing + 15 new) |

## Accomplishments

- `on_run_end()` handler with 4-row atomic transaction covering the full Member-Run lifecycle finalization (status, usage, outputs rollup, audit trail)
- `_redact.py` utility prevents immutable-table secret leakage via regex-based credential stripping (dicts, strings, lists — never mutates input)
- `firm run end` CLI verb with JSON output pattern for programmatic consumption (differs from unit CLI's stderr/exit-1 pattern — logged as intentional decision)
- PROJECT.md Key Decisions table now carries all 34 decisions from init through Phase 2 — SUMMARYs can be archived without losing decision context

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/firm/hooks/_redact.py` | Created | Credential-regex redaction utility (~40 LOC) |
| `src/firm/hooks/run_record.py` | Created | `on_run_end()` handler with 4-row atomic transaction (~170 LOC) |
| `src/firm/cli/run.py` | Created | `firm run end` CLI verb with JSON output + dry-run (~100 LOC) |
| `tests/hooks/test_run_record.py` | Created | 15 unit tests: happy path, redaction, rollback, edge cases |
| `tests/cli/test_run.py` | Created | 10 subprocess tests: flags, dry-run, structured failures |
| `src/firm/hooks/__init__.py` | Modified | Export `on_run_end` alongside `on_unit_done` |
| `src/firm/__main__.py` | Modified | `run` subparser with `end` subcommand, 8 flags |
| `.paul/PROJECT.md` | Modified | 15 rows appended to Key Decisions table |

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| `on_run_end` takes `(conn, *, firm_id, ...)` not `(workspace, ...)` | Matches `on_unit_done` signature; enables in-memory DB testing; CLI wrapper handles workspace→conn | Minor deviation from plan sketch — plan said "workspace" but "mirror 02-03 pattern" instruction took precedence |
| CLI outputs JSON to stdout, exit 0 for structured failures | Machine-friendly for Phase 3/4 slash command wrappers that parse output; run-not-found is a valid result, not an error | Different from unit CLI which uses stderr/exit-1; both patterns valid, this one better for automation |
| `unit.outputs` merge is unconditional on unit_id presence | BRIEF §3.3 doesn't condition on final_status; simple rule: if unit_id present AND unit row exists, merge | Means failed runs can still contribute outputs to the unit — caller decides whether that's appropriate |
| `_redact` as separate module, not inline in run_record | Phase 6 MCP will need redaction on programmatic writes; session-pulse may redact in future; reusable utility | Clean import boundary; ~40 LOC standalone |

## Deviations from Plan

### Summary

| Type | Count | Impact |
|------|-------|--------|
| Signature deviation | 1 | No impact — follows codebase pattern |
| Scope additions | 0 | N/A |
| Deferred | 0 | N/A |

**Total impact:** One minor signature deviation (conn vs workspace); plan's own "mirror 02-03 pattern" instruction supports the deviation.

### Signature Deviation

**on_run_end parameter: `conn` instead of `workspace`**
- **Plan said:** `on_run_end(workspace: Path, run_id: str, ...)`
- **Built:** `on_run_end(conn: sqlite3.Connection, *, firm_id: str, run_id: str, ...)`
- **Why:** Plan also said "mirror 02-03 pattern" and `on_unit_done` takes `conn`. Codebase consistency + in-memory DB testability > plan sketch.
- **Impact:** None. CLI wrapper handles workspace→conn. Tests use in-memory DB.

## Issues Encountered

None.

## Next Phase Readiness

**Ready:**
- Phase 2 (Hook Layer) fully delivered: session-pulse hook (live-installed), unit-completion handler + CLI, run-record handler + CLI
- 154 tests cover all Phase 1+2 code
- PROJECT.md Key Decisions table is current (34 decisions)
- Phase 3 (Core Slash Commands) has two handler primitives (`on_unit_done`, `on_run_end`) + two CLI verbs (`firm unit complete`, `firm run end`) to wrap

**Concerns:**
- Pyright can't resolve `firm.hooks.*` imports (src-layout config gap) — runtime + tests clean, fix is `pyproject.toml` extraPaths; deferred since Phase 1
- `LOG-NNN` / `USG-NNN` id generation is count-based, not concurrency-safe — v1 single-operator; tag for Phase 6 MCP
- `repo.create_no_commit` / `repo.update_no_commit` variants still deferred — raw-SQL pattern works but isn't ideal long-term

**Blockers:** None

---
*Phase: 02-hook-layer, Plan: 04*
*Completed: 2026-04-15*
