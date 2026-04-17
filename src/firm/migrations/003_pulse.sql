-- 003_pulse: Schema additions for the CADRE PULSE activation model.
-- Spec source: PULSE-SPEC.md (2026-04-15), discuss-phase decisions (2026-04-16).
--
-- PULSE = stateless function (`firm pulse`), not a persistent server.
-- Any trigger source (CLI, webhook, session-start hook, cron) fires the
-- same handler. Members activate based on computed load, frequency gating,
-- and dependency-based sequencing.
--
-- All changes are additive — no destructive operations, no data migration.
-- New nullable columns on member, contract, member_run, firm.
-- New budget_period table for rolling budget tracking.
--
-- Budget data migrates conceptually from member.budget → contract.budget_config
-- but the old column is NOT dropped here. member.budget remains as legacy/fallback
-- until application code is fully cutover, then a future migration drops it.

-----------------------------------------------------------------------------
-- member: PULSE activation columns
-- Frequency and cadence are Member-level concerns (identity, not runtime).
-- Contract defines the runtime binding only — HOW a Member executes,
-- not WHAT they do or WHEN.
-----------------------------------------------------------------------------

ALTER TABLE member ADD COLUMN frequency INTEGER;
-- Minimum seconds between activations. NULL = no throttle (fires every pulse).
-- Example: 3600 = at most once per hour. 86400 = at most once per day.

ALTER TABLE member ADD COLUMN last_activated TEXT;
-- ISO timestamp of last PULSE activation. NULL = never activated.
-- Updated by the PULSE handler after each successful activation.

ALTER TABLE member ADD COLUMN can_self_assign INTEGER NOT NULL DEFAULT 0;
-- Boolean: 0 = assignments from superiors only, 1 = can self-assign tasks.
-- Quality control gate: determines whether a Member can create/assign
-- Units to themselves during a run, or must receive assignments from
-- a superior Member (via reports_to chain).

-----------------------------------------------------------------------------
-- contract: pulse, validation, and budget configuration
-- These are runtime-scoped: changing a Member's runtime may change
-- budget parameters, validation rules, or execution config.
-----------------------------------------------------------------------------

ALTER TABLE contract ADD COLUMN pulse_config TEXT;
-- JSON shape:
-- {
--   "timeout_sec": 300,
--   "grace_sec": 30,
--   "model": "claude-sonnet-4-6",
--   "max_turns": 25,
--   "cwd": "/absolute/path/to/workspace",
--   "instructions_file": ".firm/instructions/MEM-001.md",
--   "extra_args": [],
--   "env": {}
-- }

ALTER TABLE contract ADD COLUMN validation_config TEXT;
-- JSON shape:
-- {
--   "enabled": true,
--   "max_retries": 1,
--   "validators": ["file_exists", "min_word_count", "ac_self_report"],
--   "on_final_failure": "flag_for_review"
-- }

ALTER TABLE contract ADD COLUMN budget_config TEXT;
-- JSON shape:
-- {
--   "enforcement": "hard",
--   "period": "monthly",
--   "limits": {
--     "max_runs_per_period": 60,
--     "max_input_tokens_per_run": 200000,
--     "max_output_tokens_per_run": 16000,
--     "max_total_cost_per_period_usd": 50.00
--   },
--   "on_limit": "pause_member",
--   "alert_threshold_pct": 80
-- }

-----------------------------------------------------------------------------
-- member_run: invocation tracking, retry lineage, prompt audit, validation
-----------------------------------------------------------------------------

ALTER TABLE member_run ADD COLUMN retry_of_run_id TEXT REFERENCES member_run(id);

ALTER TABLE member_run ADD COLUMN invocation_source TEXT DEFAULT 'manual'
    CHECK (invocation_source IN ('manual', 'pulse', 'supervisor', 'retry'));

ALTER TABLE member_run ADD COLUMN prompt_snapshot TEXT;

ALTER TABLE member_run ADD COLUMN validation_result TEXT;
-- JSON shape:
-- {
--   "passed": true,
--   "errors": [],
--   "validators_run": ["file_exists", "ac_self_report"],
--   "retry_triggered": false
-- }

-----------------------------------------------------------------------------
-- firm: business-hours schedule
-----------------------------------------------------------------------------

ALTER TABLE firm ADD COLUMN schedule TEXT;
-- JSON shape:
-- {
--   "timezone": "America/Chicago",
--   "business_hours": {
--     "start": "07:00",
--     "end": "17:00",
--     "days": ["mon", "tue", "wed", "thu", "fri"]
--   },
--   "override_open": false
-- }

-----------------------------------------------------------------------------
-- budget_period: rolling budget tracking per Member per period
-----------------------------------------------------------------------------

CREATE TABLE budget_period (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    member_id TEXT NOT NULL REFERENCES member(id) ON DELETE CASCADE,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    run_count INTEGER NOT NULL DEFAULT 0,
    total_input_tokens INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'closed', 'limit_reached')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX idx_budget_period_member ON budget_period(member_id, period_start);
CREATE INDEX idx_budget_period_firm_id ON budget_period(firm_id);
CREATE INDEX idx_budget_period_status ON budget_period(status);

-- Immutability is NOT enforced on budget_period — it's a running counter
-- that gets updated on every run. The usage_event table (immutable) is the
-- audit trail; budget_period is the derived rollup for fast pre-flight checks.
