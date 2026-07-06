-- 005: DB-row pulse lock + turn-request queue (multiplayer shared state).
--
-- pulse_lock replaces the machine-local .firm/pulse.lock flock: one pulse
-- per firm across ALL machines pointed at the same database. A holder that
-- stops heartbeating is presumed dead and its lock is stealable.
--
-- pulse_request is the turn queue: a submitted turn never silently fizzles
-- on the lock — it lands as a row, and a claimer drains the queue when the
-- table frees up (`cadre pulse --drain-queue`).

CREATE TABLE IF NOT EXISTS pulse_lock (
    firm_id      TEXT PRIMARY KEY,
    holder       TEXT NOT NULL,
    acquired_at  TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pulse_request (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id      TEXT NOT NULL,
    requested_by TEXT,
    note         TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | claimed | done | abandoned
    requested_at TEXT NOT NULL,
    claimed_by   TEXT,
    claimed_at   TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_pulse_request_pending
    ON pulse_request (firm_id, status);
