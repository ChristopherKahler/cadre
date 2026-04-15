-- 001_init: Bootstrap migration.
--
-- Creates the migration-tracking table. Idempotent via IF NOT EXISTS.
-- The runner also calls ensure_migrations_table() BEFORE applying any
-- migrations, so this statement is belt-and-suspenders — it mirrors
-- what the runner already set up so that the migration file is
-- self-contained and safe to re-run against a DB not managed by this
-- runner.
--
-- Entity tables land in Plan 01-02.

CREATE TABLE IF NOT EXISTS _migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
