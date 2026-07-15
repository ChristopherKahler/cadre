-- 011_gen_spend_run_id: link generative spend to the member run that caused it.
--
-- gen_spend recorded member_id (optional) but never the run — so a firm could
-- not answer "which run produced this image/audio", and any tool that logged a
-- generation without threading the acting member left the row unattributed
-- (the-table: 55 of 56 nano-banana rows had member_id NULL, 0 tied to a run).
--
-- Additive + nullable. The framework now stamps member_id + run_id from the run
-- context — gen_spend.record() falls back to CADRE_MEMBER_ID / CADRE_RUN_ID,
-- which the pulse spawn exports — so EVERY firm attributes generations produced
-- inside a Member run without editing its own tools. Out-of-run generations (a
-- manual CLI call, a Board-side script) legitimately stay NULL: that is honest
-- attribution, not a gap.

ALTER TABLE gen_spend ADD COLUMN run_id TEXT;
-- The member_run this generation happened during. NULL = produced outside any
-- Member run.
