-- 004_escalation.sql — first-class Board escalations + firm notify config.
--
-- Escalations are the Members' direct line to the Board: "this needs your
-- attention" items that are NOT approval requests (those are Gates).
-- The dedupe_key + notify ledger makes notification idempotent: re-raising
-- the same open issue does not re-ping the Board inside the reminder window.

ALTER TABLE firm ADD COLUMN notify_config TEXT;  -- JSON {slack_user_id, token_env, remind_hours}

CREATE TABLE escalation (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    raised_by_member_id TEXT NOT NULL REFERENCES member(id) ON DELETE RESTRICT,
    severity TEXT NOT NULL DEFAULT 'normal'
        CHECK (severity IN ('low', 'normal', 'high', 'critical')),
    title TEXT NOT NULL,
    body TEXT,
    target_entity_type TEXT,
    target_entity_id TEXT,
    dedupe_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'acknowledged', 'resolved')),
    resolution TEXT,
    notify_count INTEGER NOT NULL DEFAULT 0,
    last_notified_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_escalation_firm ON escalation(firm_id);
CREATE INDEX idx_escalation_status ON escalation(status);
CREATE INDEX idx_escalation_dedupe ON escalation(firm_id, dedupe_key);
