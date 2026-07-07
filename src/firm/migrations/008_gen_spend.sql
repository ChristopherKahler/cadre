-- 008_gen_spend.sql — universal generative-spend ledger.
--
-- Every firm incurs API cost the moment it inherits a tool that generates
-- (voice, images, video, …). This table is the firm-agnostic record of that
-- spend: one row per generation, normalized by a per-platform adapter
-- (firm.services.gen_adapters) into a common shape the boardroom reports on.
-- Adding a platform is just registering an adapter — the ledger and the
-- boardroom panel need no schema change.

CREATE TABLE IF NOT EXISTS gen_spend (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id     TEXT NOT NULL,
    platform    TEXT NOT NULL,                 -- 'elevenlabs' | 'nano-banana' | …
    kind        TEXT NOT NULL,                 -- 'tts' | 'image' | 'video' | …
    units       REAL NOT NULL DEFAULT 0,       -- chars, images, seconds …
    unit_label  TEXT,                          -- 'chars' | 'images' …
    cost_usd    REAL NOT NULL DEFAULT 0,       -- adapter-derived $ estimate
    asset_path  TEXT,                          -- firm-relative path to the generated asset
    member_id   TEXT,                          -- roster attribution (who spent it)
    ref         TEXT,                          -- source entity (story entry, unit, …)
    meta        TEXT,                          -- JSON extras (voice_id, model, prompt …)
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_gen_spend_firm ON gen_spend(firm_id, platform, created_at);
