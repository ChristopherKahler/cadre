<purpose>
Prove a firm is ready without spending a cent: dry-run the pulse, inspect real member prompts, check the hub, and hunt the known dead-on-arrival states.
</purpose>

<context>
@~/.base-frameworks/cadre-framework/checklists/verify-checklist.md (the exit gate — load at the end)
@~/.base-frameworks/cadre-framework/frameworks/gotchas.md (load when a check fails and you need the failure catalog)
</context>

<steps>

<step name="dry-run">
```bash
cd <workspace> && FIRM_ID=<id> .venv/bin/firm pulse --dry-run
```
Expect `errors: 0`. Read `skip_reasons` — `load=0` for members without assigned work is CORRECT; `load=0` for **everyone** is the deadlock (units unassigned + nobody `can_self_assign`). `ran` lists members in topo order; chained units running in-sequence within one pulse is by design.
</step>

<step name="prompts">
For each distinct contract, pull one member's composed prompt — the dashboard preview (`GET /f/<id>/api/member/<MEM-ID>` → `prompt_preview`) shows identity/notes/contract; a full `assemble_prompt` call (see the disciplines task) shows everything including protocols. A ~350-char preview = empty loadout = the member will improvise. Healthy: 2k+ with Duties and Binding policies sections.
</step>

<step name="surfaces">
- Hub card appears automatically: `curl -s http://127.0.0.1:8484/api/hub` → firm listed with correct member counts.
- `notify_configured: true` in `/f/<id>/api/state` — otherwise gates/escalations never reach the Board.
- Session hook renders roster on a fresh session in the workspace (or note as unverified).
- Boardroom folder exists with PULSE-LOG.md.
</step>

<step name="verdicts">
Load the verify checklist; give every item a verdict with its evidence line. Anything failing routes to gotchas.md for diagnosis. Do NOT fire a live pulse to "double-check" — that's the Board's first-spend decision.
</step>

</steps>

<output>
A verdicted verify-checklist with evidence per item, delivered to the operator.
</output>

<acceptance-criteria>
- [ ] Dry-run 0 errors with explained skips
- [ ] One prompt inspected per contract, all healthy
- [ ] notify + hub + boardroom folder confirmed
- [ ] No live pulse fired
</acceptance-criteria>
