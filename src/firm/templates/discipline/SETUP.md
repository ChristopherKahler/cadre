# Discipline Templates — Setup Guide

Five minutes from install to verified. Prerequisites: an initialized firm workspace (`cadre init .`) with contracts seeded.

## 1. Install the family

```bash
cd <your-firm-workspace>
cadre templates install discipline
```

What this does:

- `15-execution-discipline.md` → `.firm/protocols/` — active immediately; protocols concatenate into every member's run prompt, no restart needed.
- `lead-unit-authoring.json`, `dev-discipline.json`, this guide → `.firm/templates/discipline/` — staged for step 2.

Existing files are never overwritten unless you pass `--force`.

## 2. Merge the loadout packs into contracts

Loadout packs are merged into `contract.skill_loadout` by your firm's seed script (the append-if-absent idiom keeps re-seeding idempotent):

```python
import json
from firm.core import repo

def merge_pack(conn, contract_id: str, pack_path: str) -> None:
    pack = json.load(open(pack_path))
    row = repo.get(conn, "contract", contract_id)
    loadout = json.loads(row["skill_loadout"]) if row["skill_loadout"] else {}
    for key in ("duties", "policies"):
        for line in pack.get(key, []):
            if line not in loadout.setdefault(key, []):
                loadout[key].append(line)
    repo.update(conn, "contract", contract_id, {"skill_loadout": json.dumps(loadout)})

# lead/coordinator contracts get the unit-authoring law:
merge_pack(conn, "CON-LEAD", ".firm/templates/discipline/lead-unit-authoring.json")
# engineer contracts (dev firms) get the dev law:
merge_pack(conn, "CON-ENG", ".firm/templates/discipline/dev-discipline.json")
conn.commit()
```

Scoping rule of thumb: `lead-unit-authoring` goes to any member who *creates* units; `dev-discipline` goes to any member who *writes code*. Non-dev firms skip `dev-discipline` entirely.

## 3. Verify

```bash
# the protocol renders into a real assembled prompt:
FIRM_ID=<id> .venv/bin/python -c "
import sqlite3
from firm.pulse.prompt import assemble_prompt
conn = sqlite3.connect('.firm/firm.db'); conn.row_factory = sqlite3.Row
p = assemble_prompt(conn, '<firm-id>', '<MEM-ID>', '<UNT-ID>', cwd='.')
assert 'Evidence before claims' in p, 'protocol missing'
print('protocol renders OK,', len(p), 'chars')
"

# and the loadout lines reached the right members (dashboard):
#   GET /f/<firm>/api/member/<MEM-ID> → prompt_preview shows the new duties/policies
```

A dry-run pulse (`FIRM_ID=<id> .venv/bin/firm pulse --dry-run`) should be unchanged: the packs alter prompts, not routing.

## Troubleshooting

| Symptom | Cause |
| :--- | :--- |
| Pack lines don't appear in `prompt_preview` | They were merged under a non-rendering key — only `stages`/`tools`/`duties`/`policies` in `skill_loadout` reach prompts |
| Protocol doesn't appear in assembled prompts | File isn't in `<workspace>/.firm/protocols/` (the install `--workspace` must be the firm root, not `.firm/` itself) |
| Duplicate lines after re-seeding | The merge skipped the append-if-absent guard — match on full line content |
| Members acknowledge the law but skip the evidence | Pair the protocol with contract `validation_config` (e.g. `file_exists` + `require_written`) — prompts persuade, validators enforce |
