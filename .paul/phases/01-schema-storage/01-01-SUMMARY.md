---
phase: 01-schema-storage
plan: 01
subsystem: infra
tags: [python, sqlite, migrations, package-scaffold, cli]

requires:
  - phase: (none — first plan in project)
    provides: (n/a)
provides:
  - Python package (`firm`) with src-layout
  - SQLite connection helpers (`firm.core.db`)
  - Transactional migration runner (`firm.core.migrate`)
  - `firm init <workspace>` CLI command
  - Migration-tracking table (`_migrations`) bootstrap
affects: [01-schema-storage/01-02, 01-schema-storage/01-03, all future phases]

tech-stack:
  added: [pytest (dev only)]
  patterns:
    - src-layout Python package with pyproject.toml
    - Numbered SQL migration files (NNN_name.sql) under `src/firm/migrations/`
    - Per-migration explicit transaction boundaries (BEGIN/COMMIT/ROLLBACK)
    - Manual isolation_level=None within apply_migrations to sidestep Python sqlite3 DDL quirks
    - argparse-based CLI with subcommands dispatched from `firm.__main__:main`

key-files:
  created:
    - apps/agent-company-architecture/pyproject.toml
    - apps/agent-company-architecture/src/firm/__init__.py
    - apps/agent-company-architecture/src/firm/__main__.py
    - apps/agent-company-architecture/src/firm/core/db.py
    - apps/agent-company-architecture/src/firm/core/migrate.py
    - apps/agent-company-architecture/src/firm/cli/init.py
    - apps/agent-company-architecture/src/firm/migrations/001_init.sql
  modified:
    - apps/agent-company-architecture/README.md (Stack line + Install section)

key-decisions:
  - "Package name = `firm` internally; subject to rename at Phase 8 public release"
  - "Manual transaction control in apply_migrations to work around Python sqlite3 DDL/deferred-isolation quirk"
  - "Migrations are plain numbered .sql files — no ORM, stdlib sqlite3 only"
  - "Preserve substantive existing README rather than overwrite with minimal scaffold"

patterns-established:
  - "Migration files: NNN_snake_case.sql in src/firm/migrations/"
  - "Each migration runs in its own transaction; failure rolls back that migration only"
  - "CLI subcommands live in src/firm/cli/{command}.py, return int exit codes"
  - "DB connections via firm.core.db.connect() (PRAGMA foreign_keys=ON, row_factory=Row)"

duration: ~30min
started: 2026-04-15T09:00:00-05:00
completed: 2026-04-15T09:32:00-05:00
---

# Phase 1 Plan 1: Schema + Storage Layer — Package Scaffold + SQLite Foundation

**Python package (`firm`) installs editable, `firm init <workspace>` creates a SQLite `.firm/firm.db` with a transactional migration runner; 19/19 tests green.**

## Performance

| Metric | Value |
|--------|-------|
| Duration | ~30 minutes |
| Started | 2026-04-15T09:00:00-05:00 |
| Completed | 2026-04-15T09:32:00-05:00 |
| Tasks | 3 of 3 completed |
| Tests | 19 passing, 0 failing |
| Files created | 14 |
| Files modified | 1 (README.md) |

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: Package installs and is CLI-invokable | PASS | `pip install -e ".[dev]"` OK; `python -m firm --help` / `--version` / `import firm` all work |
| AC-2: `firm init` creates SQLite DB with migrations applied | PASS | Verified on /tmp/test-firm-apply; `_migrations` row for `001_init` present |
| AC-3: Re-running init is idempotent | PASS | Second run prints "Already initialized" and does not modify DB |
| AC-4: Migration runner applies migrations in numeric order, transactionally | PASS | `test_migration_failure_rolls_back` verifies mid-migration SQL failure rolls back prior statements in the same migration |

## Accomplishments

- Python package `firm` scaffolded with src-layout, pyproject.toml, console_scripts entry, zero runtime deps
- SQLite foundation with connection helpers, context manager, and migration runner that correctly rolls back on mid-migration SQL failure
- `firm init <workspace>` CLI end-to-end: creates `.firm/` dir, initializes SQLite DB, applies all pending migrations, idempotent re-runs
- Full test coverage (19 tests) for db helpers, migration runner, and init command — all passing
- Migration pattern established: numbered `NNN_name.sql` files, per-migration transactions, `_migrations` tracking table

## Task Commits

(Not committed to git yet — commit deferred to transition step per PAUL convention.)

| Task | Type | Description |
|------|------|-------------|
| Task 1: Python package scaffold | feat | pyproject, src-layout package markers, CLI dispatcher |
| Task 2: SQLite + migration runner | feat | connect/db_connection helpers, transactional apply_migrations, 001_init bootstrap |
| Task 3: `firm init` command | feat | run_init wires scaffold + migrations end-to-end with idempotency and error handling |

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `apps/agent-company-architecture/pyproject.toml` | Created | Build config, console_scripts entry, pytest config |
| `apps/agent-company-architecture/.gitignore` | Created | Python + .firm/ ignore patterns |
| `apps/agent-company-architecture/README.md` | Modified | Stack line reflects SQLite pivot; Install section added |
| `apps/agent-company-architecture/src/firm/__init__.py` | Created | `__version__ = "0.1.0"` |
| `apps/agent-company-architecture/src/firm/__main__.py` | Created | argparse CLI dispatcher; `--version`, `--help`, `init` subcommand |
| `apps/agent-company-architecture/src/firm/core/__init__.py` | Created | Package marker |
| `apps/agent-company-architecture/src/firm/core/db.py` | Created | `get_db_path`, `connect`, `db_connection` context manager |
| `apps/agent-company-architecture/src/firm/core/migrate.py` | Created | Migration discovery, `_migrations` tracking, transactional `apply_migrations` |
| `apps/agent-company-architecture/src/firm/cli/__init__.py` | Created | Package marker |
| `apps/agent-company-architecture/src/firm/cli/init.py` | Created | `run_init(workspace, force)` — workspace validation, idempotent init, migration apply |
| `apps/agent-company-architecture/src/firm/migrations/001_init.sql` | Created | Bootstrap: `_migrations` tracking table (IF NOT EXISTS) |
| `apps/agent-company-architecture/tests/__init__.py` | Created | Package marker |
| `apps/agent-company-architecture/tests/test_db.py` | Created | 6 tests for db helpers |
| `apps/agent-company-architecture/tests/test_migrate.py` | Created | 8 tests for migration runner (including rollback) |
| `apps/agent-company-architecture/tests/test_init.py` | Created | 5 tests for init command |

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Package name = `firm` internally | Matches `.firm/` data directory; short, memorable; ships well with data layout convention | Import paths are `firm.core.*`, `firm.cli.*`. Final public name may differ — decided at Phase 8. |
| Manual transaction control in `apply_migrations` via `isolation_level=None` + explicit BEGIN/COMMIT/ROLLBACK | Python sqlite3's default deferred isolation does not wrap DDL statements reliably; CREATE TABLE can implicit-commit a pending transaction, breaking rollback of subsequent failing statements. Verified by test | Migration runner has correct all-or-nothing semantics per migration file. Connection isolation is saved/restored so other code isn't affected. |
| Preserve existing README, add Install section | Existing README had substantive framework overview (entity table, architecture context) — overwriting with a "minimal scaffold" README would destroy reader value | Stack line updated for SQLite pivot, Install section added below. No detail lost. |
| Wire `__main__.py` fully in Task 1 | Stub-then-replace in Task 3 adds no value in non-collaborative single-pass execution | Task 3 only needed to create `cli/init.py` and tests. End state identical to plan intent. |
| Added 5th test `test_init_creates_firm_dir` | Directly exercises the `.firm/` directory creation side effect, which is subtle (happens inside `connect()` via `mkdir(parents=True)`) | Extra coverage, no scope change |

## Deviations from Plan

### Summary

| Type | Count | Impact |
|------|-------|--------|
| Auto-fixed | 1 | DDL transaction bug — spec-level issue caught and fixed in-plan |
| Scope additions | 2 | Minor: README preserved, extra test added |
| Scope omissions | 1 | Minor: Task 3's plan said "update __main__.py stub" but it was already final form |
| Deferred | 0 | — |

**Total impact:** Plan shipped as intended. One genuine bug (DDL rollback) caught by the plan's own rollback test and fixed before APPLY completed.

### Auto-fixed Issues

**1. [Correctness] Python sqlite3 DDL does not participate in `with conn:` transactions**
- **Found during:** Task 2 qualify (running `test_migration_failure_rolls_back`)
- **Issue:** Original implementation wrapped each migration in `with conn:`. When a migration file contained `CREATE TABLE X; INVALID SQL;`, the CREATE TABLE was implicit-committed by Python's sqlite3 driver before the INVALID SQL failure reached the exception handler, so `bad_table` persisted despite the rollback. The plan's AC-4 explicitly requires mid-migration failures to roll back their transactions.
- **Fix:** Switched apply_migrations to explicit transaction control: save original `isolation_level`, set to None (manual mode), drive `BEGIN` / `COMMIT` / `ROLLBACK` explicitly for each migration, then restore the saved isolation level in a `finally`. `ensure_migrations_table` switched from `with conn:` to a simple execute + `conn.commit()` (idempotent CREATE TABLE IF NOT EXISTS, safe outside a manually-managed transaction).
- **Files:** `src/firm/core/migrate.py`
- **Verification:** `test_migration_failure_rolls_back` now passes. Full suite (19 tests) green.

### Scope Changes (Minor)

**README preservation** — Task 1 spec said "minimal scaffold README" but an existing 200-line README with framework overview, entity table, and architecture context already existed. Overwriting would destroy value. Updated in-place: Stack line pivoted to SQLite, Install section added.

**`__main__.py` fully wired in Task 1** — Spec staged this: Task 1 creates a stub, Task 3 replaces it with real dispatch. In a single-pass execution, the stub-then-replace has no value. Wrote final form in Task 1; Task 3 only needed to create `firm/cli/init.py` and tests.

**Extra init test** — Added `test_init_creates_firm_dir` to exercise the `.firm/` directory creation side effect (triggered inside `connect()` via `mkdir(parents=True)`). Not in the plan; minor added coverage.

### Deferred Items

None.

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| `pip install -e .` blocked by Debian/Ubuntu PEP 668 "externally managed environment" | Created a venv at `apps/agent-company-architecture/.venv` and installed the package there. Already in `.gitignore`. |
| `sqlite3` CLI not installed on the machine | Used Python stdlib `sqlite3` module for manual DB inspection. No impact on plan correctness. |

## Next Phase Readiness

**Ready:**
- Python package scaffolded and importable — Plan 01-02 can add migration files for the 14 entity tables without any additional setup
- Migration runner transactional and tested — new migrations drop straight into `src/firm/migrations/NNN_name.sql` and work
- `firm init` is dogfood-ready — can install into workspace-root for end-to-end testing when Plan 01-03 adds CRUD
- pytest harness in place — new tests go under `tests/`

**Concerns:**
- `_split_sql` is a naive semicolon-splitter; will not correctly parse migrations with embedded semicolons (string literals, triggers). Current migrations don't need this, but flag for Plan 01-02 if any entity table uses CHECK constraints with string literals containing `;`.
- Package name `firm` is a placeholder for the public release name; import paths will move during Phase 8 rename. Not a problem until then.
- Pyright (running outside the venv) reports unresolved imports for `firm.*` — cosmetic, not a runtime issue; pytest via the venv resolves everything.

**Blockers:** None.

---
*Phase: 01-schema-storage, Plan: 01*
*Completed: 2026-04-15*
