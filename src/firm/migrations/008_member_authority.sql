-- 008_member_authority.sql — the authority key on the Member record.
--
-- Any spawned Member could previously call the management tools with zero
-- identity check: mark its own Unit done, resolve the Escalation raised about
-- its own work, rewrite Goals. The key gates those tools on the Member record
-- itself (services.authority), so every caller path — MCP, CLI, dashboard —
-- reads one source of truth.
--
-- Shape follows `budget`: a JSON object column registered in
-- repo.JSON_COLUMNS['member'], which is how this table already carries
-- extensible per-member attrs. NULL = no autonomy attrs = no authority.
-- Fresh firms ship zero grants; nothing pre-grants this.

ALTER TABLE member ADD COLUMN autonomy TEXT;  -- JSON object, e.g. {"authority": true}; NULL = no grants
