# Quill Run — Stage Dispatch

Execute a blog production stage as Quill (MEM-001) with full member_run lifecycle tracking.

## Pre-flight

### 1. Resolve the stage

Run the following to map the requested stage to a blog-post-master command:

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from firm.core.db import connect, get_db_path
from firm.contracts.dispatch import resolve_stage
from pathlib import Path
conn = connect(get_db_path(Path.cwd()))
print(resolve_stage(conn, 'MEM-001', '$STAGE'))
conn.close()
"
```

Replace `$STAGE` with the user's requested stage. If this errors, report the available stages and stop.

### 2. Find assigned Unit

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'src')
from firm.core.db import connect, get_db_path
from firm.core.repo import find
from pathlib import Path
conn = connect(get_db_path(Path.cwd()))
units = find(conn, 'unit', claimed_by='MEM-001')
active = [dict(u) for u in units if u.get('status') in ('pending', 'in_progress')]
for u in active:
    print(json.dumps({'id': u['id'], 'name': u['name'], 'project_id': u.get('project_id')}))
if not active:
    print('NO_UNITS')
conn.close()
"
```

If no active units, report: "Quill has no assigned Units. Use `/firm:unit create` + `/firm:unit checkout` to assign work."

### 3. Create member_run

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from firm.core.db import connect, get_db_path
from firm.core.repo import create
from firm.services._id import next_id
from pathlib import Path
from datetime import datetime, timezone
conn = connect(get_db_path(Path.cwd()))
run_id = next_id(conn, 'member_run', 'chrisai')
create(conn, 'member_run', {
    'id': run_id,
    'firm_id': 'chrisai',
    'member_id': 'MEM-001',
    'unit_id': '$UNIT_ID',
    'status': 'running',
    'started_at': datetime.now(tz=timezone.utc).isoformat(),
    'invocation_source': 'manual',
})
print(run_id)
conn.close()
"
```

Record the `run_id` for post-flight finalization.

## Execute

Route to the resolved blog-post-master command and execute its full workflow:

| Stage | Routes to | Description |
|-------|-----------|-------------|
| init | `/blog:init` | Scaffold blog infrastructure |
| strategy | `/blog:strategy` | Content strategy refinement |
| surface | `/blog:surface` | Surface blog-worthy topics |
| ideate | `/blog:ideate` | Generate topic ideas |
| research | `/blog:research` | Deep research on a topic |
| write | `/blog:write` | Full gated pipeline (research → publish) |
| audit | `/blog:audit` | Content strategy health check |
| chronicle | `/blog:chronicle` | Narrative log entry |
| publish | `/blog:publish` | Move approved post to live |
| repurpose | `/blog:repurpose` | Transform for other platforms |
| full | `/blog:write` | Alias for full pipeline |

Execute the blog command's workflow. You are operating as Quill (MEM-001) on an assigned Unit.

## Post-flight

### 4. Finalize member_run

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from firm.core.db import connect, get_db_path
from firm.core.repo import update
from pathlib import Path
from datetime import datetime, timezone
conn = connect(get_db_path(Path.cwd()))
update(conn, 'member_run', '$RUN_ID', {
    'status': '$FINAL_STATUS',
    'ended_at': datetime.now(tz=timezone.utc).isoformat(),
})
conn.close()
print('member_run $RUN_ID finalized as $FINAL_STATUS')
"
```

Set `$FINAL_STATUS` to `completed` if the stage finished successfully, `failed` if it did not.

Report: "Quill run $RUN_ID: $STAGE on $UNIT_ID — $FINAL_STATUS"
