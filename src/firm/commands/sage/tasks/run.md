# Sage Run — Stage Dispatch

Execute a content strategy stage as Sage (MEM-003) with full member_run lifecycle tracking.

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
result = preflight(conn, 'MEM-003', '$STAGE')
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
| surface | `/sage:surface` | Surface content pillar opportunities from market research |
| analyze | `/sage:analyze` | Analyze existing content coverage and identify gaps |
| recommend | `/sage:recommend` | Generate topic recommendations with rationale and priority |

Execute the resolved stage's workflow. You are operating as Sage (MEM-003), Content Strategist for the ChrisAI firm.

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

Report: "Sage run $RUN_ID: $STAGE — $FINAL_STATUS"
