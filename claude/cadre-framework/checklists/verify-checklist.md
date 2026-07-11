# Verify Checklist — proof without spend

Every item gets a verdict + the evidence line. Any FAIL routes to `frameworks/gotchas.md`.

- [ ] `FIRM_ID=<id> .venv/bin/firm pulse --dry-run` → `errors: 0`
- [ ] `skip_reasons` explained: `load=0` only for members with intentionally no work — NOT all 13/n members (that's the routing deadlock: assign units or set the triage role's `can_self_assign`)
- [ ] `ran_details` lists the expected members in dependency order
- [ ] One member per contract: prompt healthy — `prompt_preview` ≥ ~2k chars with Duties + "Binding policies" sections (≈350 chars = empty loadout)
- [ ] Full `assemble_prompt` shows the protocol markers ("Evidence before claims", "Deviation rules") for any member; dev-pack markers ("TDD heuristic") only where mapped
- [ ] `/f/<id>/api/state` → `notify_configured: true`
- [ ] Hub card (`/api/hub`) shows the firm with correct member counts
- [ ] No `stale: true` runs; no leftover `pulse.lock` without a live pulse process
- [ ] Boardroom folder + `PULSE-LOG.md` exist (Windows side)
- [ ] Session hook renders roster on a fresh session (or explicitly reported unverified)
- [ ] NO live pulse fired during verification
