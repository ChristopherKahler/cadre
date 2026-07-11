<overview>
The Cadre entity model and how a member run actually composes. Operational distillation — canonical depth lives in the cadre checkout's `docs/FIRM-SCAFFOLDING-GUIDE.md` and `docs/ENGINEERING.md`.
</overview>

<workspace-layout>
```
~/firms/<folder>/            # folder name ≠ firm id (id lives in the DB firm row)
├── CLAUDE.md                # charter — binds every session in the workspace
├── .venv/                   # WSL-only; editable install of the cadre framework
├── .mcp.json                # the ONLY MCP servers members get (--strict-mcp-config)
├── .claude/                 # session-pulse hooks + /pulse command
├── .firm/
│   ├── firm.db              # ALL entities (SQLite WAL); WSL-only access
│   ├── protocols/*.md       # render into EVERY member's prompt
│   ├── protocols/_member/<MEM-ID>/*.md   # per-member fragments
│   ├── templates/<family>/  # staged discipline packs
│   └── dashboard/views.json # optional custom boardroom views
├── scripts/seed_*.py        # idempotent firm definition
└── reports/                 # member run reports (convention)
```
</workspace-layout>

<entity-model>
```
Firm ─┬─ Operation (owner_member_id) ─ Project (due_date NOT NULL) ─ Unit
      ├─ Member ── Contract (pulse_config · budget_config · validation_config · skill_loadout)
      ├─ Goal (target/metric JSON on firm|operation|project)
      ├─ Gate / Escalation (Board decision surface; Slack-notified via firm.notify_config)
      └─ Records / Documents / Comments / member_run / usage_event (immutable audit + deliverables)
```
- IDs: `PREFIX-NNN` minted by services, or explicit custom ids in seeds.
- **Writes go through the service layer only**: members via firm MCP/CLI, Board via dashboard `perform_action`, seeds via `firm.core.repo` (column-validated). Never raw UPDATE.
- `repo.get` auto-deserializes JSON columns (loadouts come back as dicts); `repo.update` serializes.
- Seam-4: the harness flips a unit done after a validated run — member prose never does. With `require_written`, seam-4 also auto-registers written files as Documents (Board-visible).
</entity-model>

<contract-fields>
| Field | Shape | Omission cost |
|---|---|---|
| `pulse_config` | `{"timeout_sec": 900, "model": "sonnet"}` | unset model silently runs account default |
| `budget_config` | `{"limits": {"max_runs_per_period": N, "max_total_cost_per_period_usd": X}}` | unbounded spend |
| `validation_config` | `{"enabled": true, "max_retries": 1, "validators": [{"name":"file_exists","require_written":true} \| {"name":"sql_guard","query":...,"expect":...,"message":...}]}` | vacuous completion — refusals close units |
| `skill_loadout` | dict; ONLY `stages`/`tools`/`duties`/`policies` render into prompts | member improvises from a one-line identity |
</contract-fields>

<prompt-composition>
A member run's prompt = system context → identity (name/role/description/reports-to) → contract section (name + rendered loadout keys) → operational context → unit briefing → execution directive → `.firm/protocols/*.md` → `.firm/protocols/_member/<id>/*.md`. Dashboard `prompt_preview` shows a subset; `firm.pulse.prompt.assemble_prompt(conn, firm_id, member_id, unit_id, cwd=...)` shows the whole thing.
</prompt-composition>

<pulse>
```
reap stale runs → business-hours gate → filter (active, load>0, frequency)
→ [--only MEM-ID] → topo-sort by unit depends_on → sequential spawn loop
```
- `load>0` = claimed OR assigned pending units. All-members `load=0` = the routing deadlock.
- Spawn: headless `claude --print --dangerously-skip-permissions --strict-mcp-config --mcp-config <ws>/.mcp.json [--model X]`.
- Live pulses hold `pulse.lock` and block for the slowest member — detach with `systemd-run --user --collect` only.
- Chained units can complete within one pulse (topo order runs upstream first).
</pulse>
