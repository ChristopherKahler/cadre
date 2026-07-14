-- 009_unit_model.sql — per-Unit model override (fork 004).
--
-- The Contract's pulse_config.model says who a Member IS; real roles carry
-- two tiers of work (Dalton: triage is mechanical, drafting carries the
-- operator's name). A Unit may now say what THIS piece of work is worth:
-- the dispatcher resolves unit.model ?? contract model ?? session default,
-- and the cheap/expensive split becomes visible in the work queue instead
-- of buried in a config table. Alias or full id, same as the contract knob.

ALTER TABLE unit ADD COLUMN model TEXT;
