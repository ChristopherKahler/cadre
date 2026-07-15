-- 013_entity_business: business/domain tag on firm entities.
-- Spec source: fork cadre-entity-business-tags (2026-07-15).
--
-- An operational firm serves several businesses at once (chief-of-staff runs
-- Caddy / Extendly / ChrisAI). Escalations, gates, and units carried no signal
-- for WHICH business an item is for — ESC-012 was mixed (Meet Caddy prospects +
-- one ChrisAI/CADRE sub-item) and the Board had to hand-annotate the tag in the
-- resolution text because the field did not exist.
--
-- Additive + nullable: existing rows keep NULL (untagged). Board-authored via
-- the service layer; a calendar/platform-sourced entity can inherit its source's
-- business at the derivation point.
--
-- (Migration 012 is reserved for the concurrently-built Calibration Ladder;
--  this fork takes 013 to avoid a collision — a numbering gap is harmless, the
--  runner applies pending migrations in numeric order.)

ALTER TABLE escalation ADD COLUMN business TEXT;
ALTER TABLE gate ADD COLUMN business TEXT;
ALTER TABLE unit ADD COLUMN business TEXT;
