<purpose>
Write (or extend) a firm's idempotent seed script — the single file that defines the firm: identity, contracts, roster, work chains, goals. Re-running it must always be safe.
</purpose>

<context>
@~/.base-frameworks/cadre-framework/templates/seed-template.md (the skeleton — always load)
@~/.base-frameworks/cadre-framework/frameworks/org-design.md (load when making roster/contract/routing decisions)
</context>

<steps>

<step name="entity-order">
Author in dependency order, every create guarded with `repo.get`:
firm row → contracts → members → operation(s) → project(s) → units → goals.
</step>

<step name="non-negotiables">
These are the fields firms forget, and each omission has a named failure:
- **Firm row:** `north_star`, `core_values`, `vision`, AND `notify_config` (`{"provider":"slack","slack_user_id":...,"token_env":"CADRE_SLACK_TOKEN","remind_hours":24}`). No notify = gates rot silently.
- **Every contract:** `pulse_config` (`model` AND `timeout_sec`), `budget_config.limits` (`max_runs_per_period`, `max_total_cost_per_period_usd`), `validation_config` (`file_exists`+`require_written` for file-producing roles; `sql_guard` for DB invariants), `skill_loadout` (only `stages`/`tools`/`duties`/`policies` render into prompts).
- **Roster:** `reports_to_member_id` hierarchy; exactly ONE triage role with `can_self_assign=1`.
- **Projects:** `due_date` is NOT NULL — it bites.
- **Units:** assign every seeded unit (`assignee_member_id`) or the firm deadlocks at `load=0`; chain with `depends_on` (genuine dependencies only); dev chains END in a ship-gate audit unit; `acceptance_criteria` as verifiable evidence requirements; JSON fields via `json.dumps`.
- **Goals:** `target` metric JSON (`{"type":"count","value":N,"unit":"...","current":0}`) on firm or operation.
</step>

<step name="run-and-verify">
```bash
.venv/bin/python scripts/seed_<name>.py     # prints its summary
.venv/bin/python scripts/seed_<name>.py     # second run: identical world, no dupes
```
Then spot-check counts read back from the DB (read-only) — never trust the script's own claim.
</step>

</steps>

<output>
`scripts/seed_<name>.py` — idempotent, service-layer (`firm.core.repo`) only, committed to the firm repo.
</output>

<acceptance-criteria>
- [ ] Second run changes nothing (idempotence proven, not assumed)
- [ ] Every contract has model, timeout, budget limits, and a validation decision (explicit None is a decision — say why)
- [ ] No unassigned seeded units unless intentionally parked; one `can_self_assign` triage role exists
- [ ] Dev chains terminate in a ship-gate unit
</acceptance-criteria>
