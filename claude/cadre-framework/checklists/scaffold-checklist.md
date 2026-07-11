# Scaffold Checklist — a firm is stood up when every item has evidence

Verdict each item PASS/FAIL/N-A with the command output that proves it. Mirrors the cadre checkout's `docs/ENGINEERING.md` §6 (canonical).

## Runtime
- [ ] `.venv` exists; `pip install -e <cadre-checkout>`; `.venv/bin/python -c "import firm, mcp"` clean
- [ ] `.firm/firm.db` exists, migrations applied (`cadre init .` output)
- [ ] `.mcp.json` present, NATIVE `bash -lc ... exec .venv/bin/python -m firm.mcp.server` launch (no `wsl.exe` hop), FIRM_ID/FIRM_WORKSPACE/CADRE_SLACK_TOKEN inline
- [ ] `.claude/` session-pulse hook trio + `commands/pulse.md`, paths sed'd to this firm
- [ ] `.gitignore` + `.gitattributes` (LF); git initialized and committed
- [ ] Boardroom folder exists on the Windows side with seeded `PULSE-LOG.md`

## Charter
- [ ] `CLAUDE.md` exists; §0 preface verbatim; Board-Proxy five rules; ≥3 firm-specific structural NEVERs; accuracy tiers; board-pack export target

## Seed (the DB is the firm)
- [ ] Firm row: north_star + core_values + vision + notify_config ALL set
- [ ] Every contract: `pulse_config.model` + `timeout_sec` + `budget_config.limits` + explicit validation decision + non-empty `skill_loadout` (duties/policies)
- [ ] Roster: reporting lines set; exactly one `can_self_assign=1` triage role
- [ ] Projects have `due_date`; every seeded unit assigned; chains via genuine `depends_on`; dev chains end in a ship-gate unit
- [ ] ≥1 goal with metric/target JSON
- [ ] Seed run twice — second run changed nothing

## Disciplines
- [ ] `15-execution-discipline.md` in `.firm/protocols/` (init installs it; verify present)
- [ ] Role packs applied to the right contracts (`cadre templates apply` output)

## Exit
- [ ] Verify task run (dry-run green, prompts healthy, hub card, notify true)
- [ ] NO live pulse fired — first spend is the Board's call
