-- 016: the pulse ledger — one row per pulse PROCESS (#128 D3).
--
-- A firm's own durable record that it pulsed. `.firm/last-pulse.json` is
-- written by exactly one launcher, the hub's `_fire_pulse`, so a firm whose
-- pulses come from its timer has no record at all: `heartbeat status` shows
-- nothing, or a stale Board pulse, and the doctor has nothing to read a gap
-- from. This table is that record.
--
-- 015 is `015_pulse_interval.sql` (#134, PR #140). The D3 design drafted this
-- table as 015 before that landed; it is 016 and only 016.
--
-- `holder` is `dblock.make_holder_id()` — `host:pid:nonce`, never a bare pid,
-- because several machines can pulse against one shared database
-- (`core/db.py`, CADRE_DB_URL). A close-out that read the pid alone would
-- judge another machine's pulse against its own process table.
--
-- `ended_at` stays NULL until the pulse finishes. A pulse that was killed
-- never reaches its exit function and so never closes its own row; the next
-- pulse ON THE SAME HOST closes it as `unclosed`. Not `died`: a pulse whose
-- closing write failed leaves the identical open row and dead pid, so `died`
-- would name a crash nobody read (osprey's verdict, item 5).

CREATE TABLE IF NOT EXISTS pulse_run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id     TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,                            -- NULL while the pulse runs
    source      TEXT NOT NULL DEFAULT 'unset',   -- heartbeat|board|cli|queue|unset
    holder      TEXT NOT NULL,                   -- host:pid:nonce
    outcome     TEXT,                            -- ok | the JSON reason | unclosed
    ok          INTEGER,
    ran         INTEGER,
    errors      INTEGER,
    skipped     INTEGER
);

-- The two reads this table exists for: the newest start for a firm
-- (`heartbeat status` last_pulse, and the doctor's gap card walking starts in
-- order), and the open rows a close-out has to find.
CREATE INDEX IF NOT EXISTS idx_pulse_run_firm_started
    ON pulse_run (firm_id, started_at);

CREATE INDEX IF NOT EXISTS idx_pulse_run_open
    ON pulse_run (firm_id, ended_at);
