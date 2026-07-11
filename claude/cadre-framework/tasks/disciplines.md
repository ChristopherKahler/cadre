<purpose>
Install the discipline template family into a firm and attach the role packs to the right contracts. The universal execution law reaches every member automatically; the role law reaches exactly who needs it.
</purpose>

<context>
No upfront loads. The family's own docs land in `.firm/templates/discipline/` (README + SETUP) — read them there if depth is needed.
</context>

<steps>

<step name="install">
New firms already have the family — `cadre init` installs it at birth. Retrofit older firms:
```bash
cd <workspace> && .venv/bin/cadre templates install discipline
```
`15-execution-discipline.md` → `.firm/protocols/` (concatenates into EVERY member's run prompt — active immediately). Packs + docs → `.firm/templates/discipline/`. Existing files are skipped, never clobbered (`--force` to override deliberately).
</step>

<step name="attach">
The one human call: which contract is which role. Then:
```bash
.venv/bin/cadre templates apply discipline --map lead=<CON-LEAD-ID> --map dev=<CON-ENG-ID>[,<CON-...>]
```
- `lead` pack → any contract whose members CREATE units (director, dev lead, editor-in-chief, DM)
- `dev` pack → any contract whose members WRITE CODE (includes the AEGIS+MIDAS ship-gate policy)
- Non-dev firms skip the dev pack entirely
Append-if-absent: re-applying is a no-op; unknown contract writes nothing.
</step>

<step name="verify">
```bash
.venv/bin/python -c "
import sqlite3
from firm.pulse.prompt import assemble_prompt
conn = sqlite3.connect('.firm/firm.db'); conn.row_factory = sqlite3.Row
p = assemble_prompt(conn, '<firm-id>', '<engineer-MEM-ID>', '<any-UNT-ID>', cwd='.')
for m in ('Evidence before claims','Deviation rules','TDD heuristic'): print(m, '→', m in p)
"
```
Universal markers must be True for every member; `TDD heuristic` only for contracts you mapped `dev` onto. Prompts persuade, validators enforce — pair the protocol with contract `validation_config` where the law must be mechanical.
</step>

</steps>

<output>
Protocol active firm-wide; role packs attached to the named contracts; render verified in an actual assembled prompt.
</output>

<acceptance-criteria>
- [ ] Universal markers render in an assembled prompt (not just the dashboard preview)
- [ ] Role-pack markers render ONLY for mapped contracts
- [ ] Re-apply reports "already applied" (idempotence witnessed)
</acceptance-criteria>
