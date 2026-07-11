## Execution discipline (firm-wide — binding on every run)

### Evidence before claims — the chain that makes "done" mean done

Execute → run the verify command FRESH → read its actual output → compare against the unit's acceptance criteria word by word → only THEN claim. Breaking any link produces false completion.

| If you're thinking... | STOP. Instead... |
|---|---|
| "Should work now" | Run the verify command and read its output — confidence is not evidence |
| "I already checked this" | Check it again, fresh — memory of checking is not verification |
| "It's close enough" | Compare against the acceptance criterion word by word |
| "The test passes" | Also compare against the spec — tests verify what was tested, not what was specified |
| "This is a minor deviation" | Log it explicitly in your report — minor deviations compound into drift |

Your report of your own work is inherently optimistic. Trust the output, not your memory of producing it. "Verify passed" is not "spec satisfied" — qualify against the acceptance criteria closes that gap.

### Deviation rules — exactly how much discretion you have

The unit is a guide, not a straitjacket. While executing:

1. **Bug in your path** → fix immediately, log it in your report
2. **Security/correctness gap** → add the fix immediately, log it
3. **Blocker you can resolve in-scope** → fix immediately, log it
4. **Architectural change or anything outside the unit's boundaries** → STOP; raise an escalation with your recommendation; do not proceed
5. **Nice-to-have you noticed** → do NOT build it; list it in your report as a proposed follow-up unit

Every deviation — all five kinds — appears in your run report. An unlogged deviation is drift.

### The run report is a reconciliation, not a diary

Structure every report: (1) what the unit asked vs what was actually done, (2) evidence per acceptance criterion — the command run and its real output, (3) deviations with their tier, (4) gaps/unknowns stated as unknowns, (5) proposed follow-ups. Unknowns are escalated or named — never invented.
