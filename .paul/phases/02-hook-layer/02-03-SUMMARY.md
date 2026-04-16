---
phase: 02-hook-layer
plan: 03
subsystem: hooks
tags: [sqlite, records, transactions, cli, argparse, python, audit-trail]

requires:
  - phase: 01-foundation
    provides: firm.core.repo (CRUD), firm.core.db (connection), firm.core.migrate (schema), records/project/unit tables
  - phase: 02-hook-layer (plan 02-01)
    provides: hook contracts, on_unit_done input payload + failure modes (BRIEF §3.2, §5.2)
  - phase: 02-hook-layer (plan 02-02)
    provides: firm/hooks/ package layout, module conventions, test fixture patterns
provides:
  - firm.hooks.unit_completion.on_unit_done() — transactional records write + AC flip
  - python -m firm unit complete <id> CLI verb (+ --dry-run)
  - argparse nested subcommand pattern (command → subcommand → flags)
affects: [02-04 run-record, 03-slash-commands, 06-mcp-layer]

tech-stack:
  added: []
  patterns:
    - Raw-SQL manual transactions when atomicity is required across multiple writes
    - Nested argparse subparsers for grouped command verbs
    - CLI dry-run semantics: read-only preview of the planned mutation

key-files:
  created:
    - src/firm/hooks/unit_completion.py
    - src/firm/cli/unit.py
    - tests/hooks/test_unit_completion.py
    - tests/cli/__init__.py
    - tests/cli/test_unit.py
  modified:
    - src/firm/hooks/__init__.py
    - src/firm/__main__.py

key-decisions:
  - "Raw SQL + manual transaction for records INSERT + project UPDATE (repo.* commits internally, breaks AC-4 atomicity)"
  - "LOG-NNN record ids generated sequentially via COUNT(*) scoped to firm_id"
  - "AC flip matching: entry.resolved_by == unit_id AND entry.resolved is not True (idempotent)"
  - "dry-run does reads only, never opens a write transaction — preview ids come from a separate _preview_resolved_acs helper"
  - "Caller owns unit.status mutation; on_unit_done records the transition but does not flip status itself"

patterns-established:
  - "Hook callable returns {ok: True/False, reason?, ...payload} — structured errors not exceptions for business-logic failures; exceptions only for genuine infrastructure failures (DB down, FK violation, trigger abort)"
  - "DB-level trigger as test fixture for forcing mid-transaction failures (safer than monkeypatching sqlite3.Connection.execute, which is read-only)"

duration: ~45min
started: 2026-04-15T17:05:00-05:00
completed: 2026-04-15T17:20:00-05:00
---

# Phase 2 Plan 03: Unit-Completion Handler Summary

**Callable `on_unit_done()` ships the records-write + AC-rollup half of the Unit lifecycle, exposed via `firm unit complete` CLI verb with atomic two-write transactions and dry-run preview.**

## Performance

| Metric | Value |
|--------|-------|
| Duration | ~45 min |
| Started | 2026-04-15T17:05:00-05:00 |
| Completed | 2026-04-15T17:20:00-05:00 |
| Tasks | 2/2 PASS |
| Files created | 5 |
| Files modified | 2 |
| New tests | 22 (12 unit + 10 CLI) |
| Full suite | 129/129 green |

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: records row written + matching AC flipped | PASS | `test_happy_path_flips_matching_ac` + `test_already_resolved_ac_is_idempotent` |
| AC-2: no-match AC list is a no-op on project | PASS | `test_no_matching_ac_writes_records_only` + `test_empty_acceptance_criteria_list` + `test_null_acceptance_criteria` |
| AC-3: unit-not-found returns structured error, writes nothing | PASS | `test_unit_not_found_returns_structured_err` + `test_unit_not_found_writes_nothing` + CLI path via `test_unit_not_found_exits_nonzero` |
| AC-4: transactional atomicity on project-update failure | PASS | `test_project_missing_returns_structured_err` (pre-write detection) + `test_transaction_rolls_back_on_mid_failure` (DB-trigger-forced rollback) |
| AC-5: CLI `firm unit complete` succeeds | PASS | `test_complete_happy_path_exits_zero` + `test_complete_writes_records_row` |
| AC-6: CLI `--dry-run` prints plan without writing | PASS | `test_dry_run_prints_plan_without_writing` (row-count + AC-bytes snapshot pre/post identical) |

## Accomplishments

- `firm.hooks.unit_completion.on_unit_done()` ships the immutable audit-record write + AC rollup in one atomic transaction, including the genuine-rollback invariant (AC-4 forces a DB-level ABORT mid-transaction and verifies neither write commits).
- `python -m firm unit complete <id> --member <id>` is a complete CLI verb with `--dry-run`, `--run-id`, `--workspace`, and `--firm-id` (with `$FIRM_ID` env fallback); `test_complete_help_includes_all_flags` locks the surface.
- Full test suite grew from 107 → 129 (22 new) with zero regressions; manual dry-run against the live `.firm/firm.db` at the workspace root returns `unit-not-found` for a fake id without writes (read-only confirmed).

## Task Commits

Commits deferred to a bundle at 02-03 UNIFY boundary per handoff Open Q#1. Pending commit:
`feat(firm): hook layer through 02-03 — session-pulse + unit-completion + CLI`

| Task | Commit | Type | Description |
|------|--------|------|-------------|
| Task 1: on_unit_done handler + 12 unit tests | (pending) | feat | Transactional records INSERT + project AC flip; structured errors for unit-not-found / project-missing |
| Task 2: CLI verb + 10 CLI tests | (pending) | feat | `firm unit complete` argparse surface with dry-run preview and FIRM_ID env override |

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/firm/hooks/unit_completion.py` | Created | `on_unit_done()` entrypoint + `_next_records_id` + `_compute_ac_flips` |
| `src/firm/cli/unit.py` | Created | `run_unit_complete` wrapper — resolves workspace DB, branches dry-run vs live write, handles structured failures |
| `tests/hooks/test_unit_completion.py` | Created | 12 tests covering AC-1..AC-4 + run_id threading + deterministic `now` override + details payload shape |
| `tests/cli/__init__.py` | Created | Package marker for CLI test dir |
| `tests/cli/test_unit.py` | Created | 10 subprocess-based CLI tests (happy path, dry-run, error paths, help surface, FIRM_ID env) |
| `src/firm/hooks/__init__.py` | Modified | Export `on_unit_done` for `from firm.hooks import on_unit_done` |
| `src/firm/__main__.py` | Modified | Nested `unit` subparser with `complete` verb; $FIRM_ID env fallback at dispatch |

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Raw SQL + manual `try/commit/except+rollback` instead of `repo.create` / `repo.update` for the records INSERT + project UPDATE | `repo.*` calls `conn.commit()` internally. Inside a `with conn:` or try/except block, an internal commit defeats rollback — the records row would persist even if the subsequent project UPDATE raised. AC-4 requires both-or-neither. | Future hook/wrapper authors writing multi-row transactions should use raw SQL (or a `repo.*_no_commit` variant) until the repo layer grows a transaction-aware surface. Noted for 02-04 run-record (which writes 4 rows atomically). |
| Sequential `LOG-NNN` ids from `SELECT COUNT(*) FROM records WHERE firm_id = ?` | Simplest id scheme that reads well in rendered audit trails; safe because `records` is immutable — no deletes shrink the count. | 02-04 run-record will reuse this pattern. At high concurrency the scheme would race (two concurrent inserts → same `COUNT(*)+1`); v1 is single-operator so not a concern. Flag if the framework grows concurrent writers. |
| AC entries without an `id` field still flip `resolved: true` but are omitted from `resolved_ac_ids` | Spec (BRIEF §3.2) keyed matching on `resolved_by`, not on `id`. Entries that happen to lack an id shouldn't block the mutation, but they can't be reported back to the caller either. | Consumers that rely on `resolved_ac_ids` being comprehensive need to ensure project AC rows carry ids. 02-04 may tighten this if it surfaces as a gap. |
| DB-level trigger as AC-4 rollback-test fixture, not monkeypatching | `sqlite3.Connection.execute` is read-only in CPython (can't `patch.object(conn, "execute", ...)`). A `BEFORE UPDATE ON project BEGIN SELECT RAISE(ABORT, ...) END` trigger fires cleanly during the real UPDATE. | Repeatable pattern for any future test that needs to force a genuine mid-transaction SQL failure. |
| Caller owns `unit.status` mutation; `on_unit_done` records the transition but does not flip the column | Keeps the handler pure-function and decoupled from caller flow (Phase 3 slash command will want to sequence status update, handler call, and any downstream messaging explicitly). Prevents double-writes if callers already updated status before calling. | CLI `firm unit complete` does NOT currently flip `unit.status` either — that's a Phase 3 concern when the slash command orchestrates the full flow. Users running the CLI directly today get the records + AC effects, not the status transition. Noted in the CLI docstring. |

## Deviations from Plan

### Summary

| Type | Count | Impact |
|------|-------|--------|
| Spec refinements (implementation required to meet AC) | 1 | Essential — AC-4 unachievable otherwise |
| Scope additions | 1 | Small — help-surface regression guard test |
| Deferred | 0 | n/a |

**Total impact:** One essential implementation refinement (repo.* → raw SQL) to satisfy AC-4 atomicity. One small scope addition (CLI help surface test) as a lightweight guard against future flag drift.

### Auto-fixed Issues

**1. [Spec refinement] PLAN said "Call `repo.create(conn, 'records', {...})` inside `with conn:`" — but repo.create commits internally**
- **Found during:** Task 1 qualify (writing test_transaction_rolls_back_on_mid_failure)
- **Issue:** `repo.create` and `repo.update` both call `conn.commit()` at the end of each call. A `with conn:` transaction boundary becomes meaningless when the inner calls have already committed — AC-4's "both or neither" requirement is unachievable through repo.
- **Fix:** Switched to raw SQL inside `try: conn.execute(INSERT) → conn.execute(UPDATE) → conn.commit() except: conn.rollback(); raise`. Records INSERT and project UPDATE now share a single transaction; either both commit or both roll back.
- **Files:** `src/firm/hooks/unit_completion.py`
- **Verification:** `test_transaction_rolls_back_on_mid_failure` forces an UPDATE failure via a DB-level trigger and asserts the records row did not persist.
- **Commit:** Pending in 02-03 bundle.

### Scope Additions

**2. [Test] `test_complete_help_includes_all_flags`**
- **Not in plan's 6 required cases**, but added as a lightweight guard: any future PR that accidentally drops `--dry-run` or renames `--member` trips this test.
- **Files:** `tests/cli/test_unit.py`
- **Cost:** ~15 lines; runs in <200ms via subprocess.

### Deferred Items

None.

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| `patch.object(conn, "execute", ...)` fails with `AttributeError: 'sqlite3.Connection' object attribute 'execute' is read-only` | Pivoted to a DB-level `BEFORE UPDATE` trigger that calls `RAISE(ABORT, ...)` — this fires inside the real SQL execution and produces a genuine `sqlite3.IntegrityError`, which is a better simulation of real mid-transaction failure anyway. |
| First run of `test_run_id_threads_through` failed with `NOT NULL constraint failed: member_run.started_at` | Added `"started_at": "2026-04-15 16:00:00"` to the member_run seed dict. Schema validation caught a missing required field before it could hide as a silent bug. |
| Pyright reports `Import "firm.hooks.unit_completion" could not be resolved` | Pre-existing src-layout config gap documented in 02-02 handoff Gap 1. Runtime is clean (129/129 tests pass). Still deferred. |

## Next Phase Readiness

**Ready:**
- Phase 2 Plan 02-04 (run-record handler + PROJECT.md decisions append) — all substrate present: the hooks package is established, transactional write pattern is proven, CLI nested-subcommand pattern is proven (reuse for `firm run end`), records id generation pattern is reusable.
- Three phase-2 decisions ready for the PROJECT.md Key Decisions table append in 02-04 Task 3 (raw-SQL transaction pattern, LOG-NNN id scheme, DB-trigger test fixture pattern).

**Concerns:**
- The repo layer's internal-commit behavior will bite again in 02-04 (run-record writes 4 rows atomically). 02-04 plan author: default to raw SQL from the outset, don't re-discover this.
- `LOG-NNN` count-based id generation is not concurrency-safe. v1 is single-operator; note if/when concurrency enters scope.
- Phase 1 + 02-01 + 02-02 + 02-03 are all in one pending commit bundle. Size is still manageable but growing; commit at UNIFY boundary here (per handoff Open Q#1 default).

**Blockers:**
- None.

---
*Phase: 02-hook-layer, Plan: 03*
*Completed: 2026-04-15*
