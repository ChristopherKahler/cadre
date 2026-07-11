<purpose>
Elite-firm audit of an EXISTING firm: score it against the scaffold checklist, grade the parts a checklist can't see (team comp, containment, enforcement), and deliver ranked findings + a remediation plan. Read-only — remediation executes only on the operator's go.
</purpose>

<context>
@~/.base-frameworks/cadre-framework/checklists/scaffold-checklist.md (the rubric — always load)
@~/.base-frameworks/cadre-framework/frameworks/org-design.md (grading standards for roster/contracts/routing)
@~/.base-frameworks/cadre-framework/frameworks/gotchas.md (load per finding to name the failure pattern)
</context>

<steps>

<step name="evidence-pull">
Read-only, via the hub API first (`/api/hub`, `/f/<id>/api/state`, `/f/<id>/api/member/<MEM-ID>`), DB second (venv python + sqlite3 module, SELECTs only). Collect: firm row (north_star/values/notify), contracts (model/timeout/budget/validation/loadout), roster (reporting lines, can_self_assign, descriptions), units (assignment, chains, acceptance criteria), goals, runs/spend history, workspace scaffolding (charter, .mcp.json, .venv, hooks, git), protocols dir, prompt previews per contract.
</step>

<step name="score">
March the scaffold checklist — verdict + evidence per item. Then grade beyond it:
- **Containment:** do the charter NEVERs cover what members can actually touch (external services, money, production data)? Structural (loadout/validator/chain) or merely behavioral (a prompt sentence)?
- **Enforcement:** are review/ship gates chained via `depends_on`, or just described? `validation_config: None` on a contract that produces artifacts is a finding (vacuous completion).
- **Specialization:** do contracts differentiate members (duties/policies), or is the org chart cosmetic — identical blank contracts with different name tags?
- **Economics:** budgets bounded per period? model tiers matched to work value? timeouts matched to real run lengths?
- **Routing:** can the firm actually start — assigned units or a self-assign triage role? Any member with load but repeated failed runs?
</step>

<step name="report">
Deliver: verdict line (elite / solid / shell), what's genuinely right, findings in severity order (each: what, evidence, which gotcha/failure it maps to), then a single remediation commission the operator can approve in one word. Do not fix anything before the go.
</step>

</steps>

<output>
Audit report with per-item verdicts, ranked findings, and an approvable remediation plan.
</output>

<acceptance-criteria>
- [ ] Every checklist item verdicted with evidence (no vibes)
- [ ] Every finding names its failure pattern and its structural fix
- [ ] Zero writes performed during the audit
</acceptance-criteria>
