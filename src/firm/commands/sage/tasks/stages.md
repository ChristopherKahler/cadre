# Sage Stages — List Available Stages

Query Sage's Contract skill_loadout and display available stages.

## Execute

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from firm.core.db import connect, get_db_path
from firm.contracts.dispatch import list_stages
from pathlib import Path
conn = connect(get_db_path(Path.cwd()))
stages = list_stages(conn, 'MEM-003')
if stages:
    for name, cmd in sorted(stages.items()):
        print(f'  {name:12s} -> {cmd}')
else:
    print('No stages configured. Check CON-003 skill_loadout.')
conn.close()
"
```

Present the output as Sage's available dispatch stages.
