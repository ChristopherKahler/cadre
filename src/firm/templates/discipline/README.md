# Discipline Templates

Quality constraints for Cadre firm Members — the execution discipline that makes "done" mean done, extracted from the PAUL framework (Plan-Apply-Unify Loop) and re-shaped for autonomous, headless member runs. PAUL's session ceremony (satellite state files, approval menus, context budgeting) is deliberately left out: Cadre's own entities — Units, Gates, seam-4 validation, Documents — already own those seams.

**New firms get this family automatically at `cadre init`.** Retrofit an existing firm and attach the role packs with:

```bash
cadre templates install discipline --workspace <your-firm>   # retrofit (init does this for new firms)
cadre templates apply discipline --map lead=CON-LEAD --map dev=CON-ENG
```

See `SETUP.md` for the full walkthrough.

## What's in the family

| File | Tier | Delivered as |
| :--- | :--- | :--- |
| `15-execution-discipline.md` | **Universal** — every member of every firm type | Firm protocol (`.firm/protocols/`) — renders into every member run automatically |
| `lead-unit-authoring.json` | **Leads/coordinators** — any member whose duty is decomposing work into units (editor-in-chief, dungeon master, lab director, dev lead) | Loadout pack — merge `duties` into the lead contract's `skill_loadout` |
| `dev-discipline.json` | **Dev/engineering firms** | Loadout pack — merge `duties` + `policies` into engineer contracts' `skill_loadout` |

## What each pack enforces

**Execution discipline (universal):** the evidence-before-claims chain (run the verify fresh, read the output, compare to the acceptance criteria word by word — confidence is not evidence), the five deviation tiers (fix bugs/security/blockers in-scope, escalate architectural changes, log nice-to-haves as follow-ups instead of building them), and report-as-reconciliation (planned vs actual, evidence per criterion, deviations tiered, unknowns named — never invented).

**Unit authoring (leads):** every unit specifies Files / Action / Verify / Done or it's too vague to assign; acceptance criteria in Given/When/Then with error states; explicit DO-NOT-CHANGE boundaries; one-run sizing with vertical slices and genuine-only `depends_on`.

**Dev discipline (engineers):** the TDD heuristic (testable-behavior-first work runs red-green-refactor with the failing test shown as evidence; UI/config/glue/scripts skip it), test quality law (behavior over implementation, one concept per test), atomic unit-tagged commits, and the ship gate — no dev deliverable is done until the firm's security/ship role passes an AEGIS diagnostic audit + MIDAS standards check, enforced as the final `depends_on` link of every dev chain.

## Why loadout packs are JSON, not protocol files

Protocols render into **every** member's prompt — right for universal law, wrong for role law (a demo-data engineer doesn't need TDD rules taxing every run). Contract `skill_loadout` scopes precisely: only `stages` / `tools` / `duties` / `policies` keys render into member prompts, so the packs ship as arrays you merge into exactly the contracts that need them.
