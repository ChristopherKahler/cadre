-- 010_run_scoring: Operator run-scoring & calibration.
-- Spec source: fork cadre-calibration-run-scoring (2026-07-15).
--
-- The Board rates a completed Member run 1-5 (+ optional note). The score is
-- the heavily-weighted input the Calibration Ladder (separate fork) will later
-- consume for tier progression; here it only feeds Floor stats.
--
-- All changes are additive and nullable — no destructive operations, no data
-- migration. The core enabler holds: there is NO stored game state. XP, levels,
-- and every score aggregate are recomputed at read time in dashboard/server.py,
-- so a retroactive rescore is a single-column UPDATE and every dependent stat
-- recomputes on the next read. Do NOT add a cached aggregate table — it would
-- forfeit that property.
--
-- run_score is BOARD evaluation, never member-authored and never member-read
-- (Invariant #5, enforced structurally: it appears in no MCP read tool and no
-- pulse/prompt renderer). A Member that sees its score games the score.
--
-- "Reviewed" is a timestamp column, never a member_run.status value — the
-- status CHECK is a closed set SQLite cannot extend without a table rebuild.

-----------------------------------------------------------------------------
-- member_run: Board quality score, note, and review provenance
-----------------------------------------------------------------------------

ALTER TABLE member_run ADD COLUMN run_score INTEGER;
-- Board quality score, 1-5. NULL = not yet rated.

ALTER TABLE member_run ADD COLUMN run_score_notes TEXT;
-- Optional Board note explaining the score. NULL = none.

ALTER TABLE member_run ADD COLUMN reviewed_at TEXT;
-- ISO timestamp of the most recent scoring/rescoring. NULL = never reviewed.

ALTER TABLE member_run ADD COLUMN reviewed_by TEXT;
-- Actor id that scored the run (NULL for the Board default actor).
