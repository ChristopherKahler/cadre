-- 007: firm_rev — monotonically increasing write counter.
--
-- Change-signal fallback for remote backends that refuse PRAGMA data_version
-- (Turso cloud). Every meaningful write path bumps n (see core.db.bump_rev);
-- the dashboard SSE watcher polls it when data_version is unavailable.

CREATE TABLE IF NOT EXISTS firm_rev (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    n  INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO firm_rev (id, n) VALUES (1, 0);
