<purpose>
Author a firm's CLAUDE.md charter — the file that binds every session opened in that workspace: runtime law, Board-Proxy hard rules, and the firm's structural NEVERs.
</purpose>

<context>
@~/.base-frameworks/cadre-framework/templates/charter-template.md (the skeleton — always load)
</context>

<steps>

<step name="preface">
Copy the template's §0 RUNTIME PREFACE **verbatim**, substituting only the workspace path. It is host-detection law (WSL vs Windows-hosted), learned expensively — do not rephrase it.
</step>

<step name="firm-table">
Fill §1: firm id, hierarchy (Board → director → leads → ICs with member ids), operations + owners, goals, runtime line, cadre checkout path.
</step>

<step name="board-proxy-rules">
§2 is near-universal — keep the five: never resolve Gates; never spend; never publish externally; steer HOW not WHAT; Records/Comments/usage events are immutable.
</step>

<step name="structural-nevers">
§3 is the firm-specific containment wall. Derive from what the members can TOUCH:
- Members reach real external services → SANDBOX-ONLY law + credentials law (firm-provisioned only; missing access = procurement escalation)
- Members can provision infrastructure → MONEY GATE (anything that bills → Gate first)
- Dev firm → SHIP GATE (final `depends_on` unit is a security/standards audit; never bypass)
- Content firm → never-publish law (drafts only; publishing is Board-only)
- Game firm → fair-dice law (`cadre roll` only; models never generate results)
Write at least 3. Each NEVER should be *structural* where possible — enforceable by loadout, validator, or chain, with the charter line as the statement of intent.
</step>

<step name="accuracy-and-cadence">
§4: verified-evidence reporting tiers ("live" = URL read back 200; "staged" = counts read back; "validated" = walked end-to-end; unknowns escalated, never invented). §5: /pulse protocol + board-pack export target + "you are oversight, not labor". §6: escalation catalog.
</step>

</steps>

<output>
`<workspace>/CLAUDE.md` following the template's section order.
</output>

<acceptance-criteria>
- [ ] §0 preface is verbatim from the template (paths substituted only)
- [ ] ≥3 firm-specific structural NEVERs, each mapped to what members can actually touch
- [ ] Board-Proxy five rules present
- [ ] Board-pack export target named and Windows-side
</acceptance-criteria>
