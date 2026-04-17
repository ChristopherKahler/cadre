# Sterling Queue — Delegate Work to Quill

Create a new Unit and assign it to Quill (MEM-001). This is Sterling's primary delegation action as CMO.

## Pre-flight: Validate delegation

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from firm.core.db import connect, get_db_path
from firm.services.member import can_delegate_to
from pathlib import Path
conn = connect(get_db_path(Path.cwd()))
valid = can_delegate_to(conn, 'MEM-002', 'MEM-001')
print('DELEGATION_VALID' if valid else 'DELEGATION_BLOCKED')
conn.close()
"
```

If `DELEGATION_BLOCKED`: report "Cannot delegate to Quill — check member status and reports_to chain." Stop.

## Gather requirements from Board

Ask the Board (user) for:
1. **Topic/title** — What should this blog post be about?
2. **Acceptance criteria** — What defines a successful output? (minimum 2 criteria)
3. **Priority** — urgent, high, medium, or low (default: medium)

Do NOT proceed without Board input on at least the topic.

## Create Unit and assign to Quill

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'src')
from firm.core.db import connect, get_db_path
from firm.services.unit import create_unit, checkout_unit
from pathlib import Path
conn = connect(get_db_path(Path.cwd()))
unit = create_unit(conn, 'chrisai', {
    'name': '$TOPIC',
    'project_id': 'PRJ-001',
    'priority': '$PRIORITY',
    'acceptance_criteria': json.dumps($ACCEPTANCE_CRITERIA),
})
unit_id = unit['id']
checkout_unit(conn, unit_id, 'MEM-001')
print(json.dumps({'unit_id': unit_id, 'assigned_to': 'MEM-001', 'name': '$TOPIC'}))
conn.close()
"
```

Replace:
- `$TOPIC` with the Board-provided topic
- `$PRIORITY` with the chosen priority (default: medium)
- `$ACCEPTANCE_CRITERIA` with a Python list of criteria strings

## Report

```
Sterling delegation complete:
  Unit: {unit_id} — "{topic}"
  Assigned to: Quill (MEM-001)
  Priority: {priority}
  Acceptance criteria: {count} defined

Quill can execute via /quill:run <stage> on this Unit.
```
