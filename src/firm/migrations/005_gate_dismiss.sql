-- 005_gate_dismiss.sql — notification-layer dismiss for gates.
--
-- Dismissing a gate clears it from the Board's attention surface (hub
-- needs_you / pending-gate badge) WITHOUT touching its decision status. A
-- dismissed gate stays exactly as it was — pending stays pending, fully
-- resolvable — it just stops nagging. Deliberately distinct from
-- approve/reject, which are DECISIONS.
--
-- Field report (2026-07-06): "make the notification go away" was only
-- expressible as gate-reject, so a dashboard dismiss button wired to reject
-- silently destroyed a live Board decision. This column + the gate-dismiss
-- action give dismiss its own verb that never touches the decision.

ALTER TABLE gate ADD COLUMN dismissed_at TEXT;  -- ISO8601 when dismissed; NULL = live on the attention surface
