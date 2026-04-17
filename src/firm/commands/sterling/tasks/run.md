# Sterling Run — Stage Dispatch

Execute a CMO stage as Sterling (MEM-002) with full member_run lifecycle tracking.

## Pre-flight

### 1. Resolve stage and create member_run

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from firm.commands.member_dispatch import preflight
from firm.core.db import connect, get_db_path
from pathlib import Path
import json
conn = connect(get_db_path(Path.cwd()))
result = preflight(conn, 'MEM-002', '$STAGE')
print(json.dumps({
    'resolved_cmd': result['resolved_cmd'],
    'unit_id': result['unit']['id'] if result['unit'] else None,
    'run_id': result['run_id'],
}))
conn.close()
"
```

Replace `$STAGE` with the user's requested stage. If this errors, report the available stages and stop.

Record `run_id` (if present) for post-flight finalization.

## Execute

Route to the resolved command and execute its workflow:

| Stage | Routes to | Description |
|-------|-----------|-------------|
| audit | `/sterling:audit` | Review content pipeline health — Units in progress, stale work, goal metrics |
| queue | `/sterling:queue` | Create and assign Units to Quill based on content gaps |
| review | `/sterling:review` | Review completed Units from Quill for quality |

Execute the resolved stage's workflow. You are operating as Sterling (MEM-002), CMO of the ChrisAI firm.

## Post-flight

### 2. Finalize member_run (if run_id was created)

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from firm.commands.member_dispatch import postflight
from firm.core.db import connect, get_db_path
from pathlib import Path
import json
conn = connect(get_db_path(Path.cwd()))
result = postflight(conn, '$RUN_ID', '$FINAL_STATUS')
print(json.dumps(result))
conn.close()
"
```

Set `$FINAL_STATUS` to `completed` if the stage finished successfully, `failed` if it did not.

Report: "Sterling run $RUN_ID: $STAGE — $FINAL_STATUS"
