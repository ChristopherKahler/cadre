-- 014_loadout_posture: per-member + firm-default loadout trust posture.
-- Spec source: fork cadre-loadout-posture-controls (2026-07-16).
--
-- LEAN  = spawn with --strict-mcp-config: a Member's MCP surface is EXACTLY
--         the firm's .mcp.json armory. The loadout is the law.
-- FULL  = spawn WITHOUT it: the Member additionally inherits the operator's
--         entire user-scope/plugin connector fleet (Gmail, Calendar, Drive,
--         Canva, DocuSeal, Monday, Zoom, FirstPromoter...) with its full
--         write/send/publish/e-sign power, under --dangerously-skip-permissions.
--         The firm's loadout no longer bounds what that Member can do.
--
-- Why this exists: the posture mechanism shipped firm-only, invisible, and as
-- a quiet founding file (.firm/spawn.json {"full": true}). chief-of-staff was
-- founded full-load, so every Member silently inherited 8+ uncovered write/send
-- connectors — ESC-086..093. A dangerous posture with no UI and no per-member
-- granularity is a posture nobody chose; this migration gives it a home the
-- Board can see and set.
--
-- Both columns are additive + nullable, and NULL is the SAFE direction at every
-- tier (Invariant: LEAN is the default and the safe path):
--   member.loadout_posture NULL -> inherit the firm default
--   firm.loadout_posture   NULL -> no explicit firm choice; the resolver falls
--                                  back to legacy .firm/spawn.json, then LEAN.
--
-- The firm default lives HERE and not in .firm/spawn.json because the Board
-- must be shown the EFFECTIVE posture: assemble_state() reads the DB without a
-- workspace, and a Settings page that renders "Lean" for a firm whose spawn.json
-- says full is the ESC-021 failure — a control that reports green while aiming
-- at nothing. spawn.json survives as a READ-ONLY back-compat fallback (existing
-- full-load firms keep working); once the Board sets a posture here, the DB is
-- authoritative and the file is shadowed. One source of truth, legacy honored.
--
-- Written ONLY via services/posture.py (Invariant #2). Board-facing config —
-- it reaches no prompt renderer and no MCP tool (Invariant #5).

ALTER TABLE firm ADD COLUMN loadout_posture TEXT
    CHECK (loadout_posture IN ('lean', 'full'));
-- NULL = unset; resolver falls back to .firm/spawn.json, then LEAN.

ALTER TABLE member ADD COLUMN loadout_posture TEXT
    CHECK (loadout_posture IN ('lean', 'full'));
-- NULL = inherit the firm default (the common case).
