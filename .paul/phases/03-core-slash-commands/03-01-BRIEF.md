# Phase 3 Research Brief — Core Slash Commands

**Plan:** 03-01 (research)
**Produced:** 2026-04-15
**Supersedes:** ROADMAP §Phase 3 "TBD" scope
**Sources:** ENTITY-DESIGN.md (14 entity schemas), Skillsmith conventions (6 spec files), Phase 1 repo.py + Phase 2 hooks

---

## 1. Command Surface Map

### 1.1 Entity Command Coverage

| Entity | Phase 3? | Command | Sub-actions | Rationale |
|--------|----------|---------|-------------|-----------|
| firm | Yes | `/firm:init` | init | Wraps existing `firm init` CLI |
| firm | Yes | `/firm:status` | status | Dashboard aggregate |
| member | Yes | `/firm:member` | create, list, view, update | Core workforce entity |
| operation | Yes | `/firm:operation` | create, list, view, update | Strategic layer |
| project | Yes | `/firm:project` | create, list, view, update | Bounded deliverables |
| unit | Yes | `/firm:unit` | create, list, view, checkout, complete, update | Atomic work + Phase 2 handler wraps |
| gate | Yes | `/firm:gate` | request, approve, reject, list, view | Decision checkpoints |
| goal | Yes | `/firm:goal` | create, list, view, update | Measurable outcomes |
| comment | Yes | `/firm:comment` | add, list | Polymorphic discussion |
| contract | Yes | `/firm:contract` | create, view, update | Runtime interface |
| document | Yes | `/firm:document` | create, list, view | Metadata pointers |
| member_run | **Deferred** | - | - | Lifecycle handled by Phase 2 `on_run_end`. Query surface at Phase 6 MCP. |
| usage_event | **Deferred** | - | - | Created by run-record hook. Query at Phase 6 MCP. |
| records | **Deferred** | - | - | Immutable audit trail, auto-written by services. Query at Phase 6 MCP. |
| firm_secret | **Deferred** | - | - | Metadata-only, low priority. Direct DB or Phase 6 MCP. |

**Total:** 10 entities with commands, 4 deferred. 11 task files (init and status are separate commands under the `firm` namespace).

### 1.2 Detailed Command Profiles

#### `/firm:init`

| Field | Value |
|-------|-------|
| Task file | tasks/init.md |
| Sub-actions | (none - single action) |
| Required args | workspace (optional, defaults to cwd) |
| Optional args | --force |
| Reads | filesystem (checks .firm/ exists) |
| Writes | .firm/ directory, firm.db (migrations), firm row |
| Records entry | Yes: `firm.initialized` |
| Wraps | `firm.cli.init.run_init()` (existing Phase 1 CLI) |

#### `/firm:status`

| Field | Value |
|-------|-------|
| Task file | tasks/status.md |
| Sub-actions | (none - single action) |
| Required args | (none - uses $FIRM_ID) |
| Optional args | --workspace |
| Reads | All entity tables (aggregate counts, active roster, pending gates, goal health) |
| Writes | Nothing |
| Records entry | No |
| Notes | Renders a dashboard combining session-pulse data with aggregate stats |

#### `/firm:member`

| Sub-action | Required | Optional | Validates | Writes | Records |
|------------|----------|----------|-----------|--------|---------|
| create | name, role | description, reports_to, contract_id, suggested_skills, suggested_domains, budget | reports_to exists (if set), contract_id exists (if set) | member | Yes: `member.created` |
| list | (none) | status (default: active), reports_to | - | (read-only) | No |
| view \<id\> | member_id | (none) | member exists | (read-only) | No |
| update \<id\> | member_id | name, role, description, status, reports_to, contract_id, suggested_skills, suggested_domains | FK refs exist | member | Yes on status transition: `member.status_transition` |

**ID prefix:** `MEM-NNN`

#### `/firm:operation`

| Sub-action | Required | Optional | Validates | Writes | Records |
|------------|----------|----------|-----------|--------|---------|
| create | name | description, owner_member_id, priority, category, goal_ids, acceptance_criteria | owner_member_id exists (if set) | operation | Yes: `operation.created` |
| list | (none) | status (default: active), category, owner_member_id | - | (read-only) | No |
| view \<id\> | operation_id | (none) | operation exists | (read-only) | No |
| update \<id\> | operation_id | name, description, status, owner_member_id, priority, category | FK refs exist | operation | Yes on status transition |

**ID prefix:** `OPS-NNN`

#### `/firm:project`

| Sub-action | Required | Optional | Validates | Writes | Records |
|------------|----------|----------|-----------|--------|---------|
| create | name, operation_id, due_date | description, owner_member_id, priority, tags, goal_ids, acceptance_criteria | operation_id exists, owner_member_id exists (if set) | project + operation.project_ids append | Yes: `project.created` |
| list | (none) | status, operation_id, owner_member_id | - | (read-only) | No |
| view \<id\> | project_id | (none) | project exists | (read-only) | No |
| update \<id\> | project_id | name, description, status, owner_member_id, priority, due_date, tags | FK refs exist | project | Yes on status transition |

**ID prefix:** `PROJ-NNN`

#### `/firm:unit`

| Sub-action | Required | Optional | Validates | Writes | Records |
|------------|----------|----------|-----------|--------|---------|
| create | name, project_id | description, assignee_member_id, priority, rank, depends_on, due_date, tags, acceptance_criteria, parent_unit_id | project_id exists, cycle check on depends_on, parent_unit_id 1-level max | unit + project.unit_ids append | Yes: `unit.created` |
| list | (none) | status, project_id, assignee_member_id, priority | - | (read-only) | No |
| view \<id\> | unit_id | (none) | unit exists | (read-only) | No |
| checkout \<id\> | unit_id, member_id | (none) | unit exists, member exists, unit unclaimed | unit (claimed_by, status) | Yes: `unit.checked_out` |
| complete \<id\> | unit_id, member_id | run_id | unit exists, member exists | unit (status -> done) + records + AC resolution | Yes: `unit.completed` (via Phase 2 handler) |
| update \<id\> | unit_id | name, description, status, assignee_member_id, priority, rank, depends_on, tags | cycle check if depends_on changes | unit | Yes on status transition |

**ID prefix:** `UNIT-NNN` (or `SUB-NNN` for sub-units when parent_unit_id set)

#### `/firm:gate`

| Sub-action | Required | Optional | Validates | Writes | Records |
|------------|----------|----------|-----------|--------|---------|
| request | requesting_member_id, action, target_ref | context, expires_at | member exists, target_ref entity exists | gate | Yes: `gate.requested` |
| approve \<id\> | gate_id | approver_comment | gate exists, status == pending | gate (status -> approved, approver_ref -> board) | Yes: `gate.approved` |
| reject \<id\> | gate_id | approver_comment | gate exists, status == pending | gate (status -> rejected) | Yes: `gate.rejected` |
| list | (none) | status (default: pending), requesting_member_id | - | (read-only) | No |
| view \<id\> | gate_id | (none) | gate exists | (read-only) | No |

**ID prefix:** `GATE-NNN`

#### `/firm:goal`

| Sub-action | Required | Optional | Validates | Writes | Records |
|------------|----------|----------|-----------|--------|---------|
| create | target, parent_ref (type + id) | level, metric (type, value, unit, deadline), status | parent_ref entity exists | goal + parent entity goal_ids append | Yes: `goal.created` |
| list | (none) | status (default: active), level, parent_ref_type | - | (read-only) | No |
| view \<id\> | goal_id | (none) | goal exists | (read-only) | No |
| update \<id\> | goal_id | target, status, metric | - | goal | Yes: `goal.metric_updated` (when metric.current changes), `goal.status_transition` (when status changes) |

**ID prefix:** `GOAL-NNN`

#### `/firm:comment`

| Sub-action | Required | Optional | Validates | Writes | Records |
|------------|----------|----------|-----------|--------|---------|
| add | parent_ref (type + id), body | author (defaults to board), in_reply_to | parent_ref entity exists, in_reply_to comment exists (if set) | comment (immutable) | No (comments are themselves an audit artifact) |
| list | parent_ref (type + id) | (none) | parent_ref entity exists | (read-only) | No |

**ID prefix:** `COM-NNN`

#### `/firm:contract`

| Sub-action | Required | Optional | Validates | Writes | Records |
|------------|----------|----------|-----------|--------|---------|
| create | name, member_id, runtime_type | runtime_config, skill_loadout, domain_loadout | member_id exists | contract | Yes: `contract.created` |
| view \<id\> | contract_id | (none) | contract exists | (read-only) | No |
| update \<id\> | contract_id | name, runtime_type, runtime_config, skill_loadout, domain_loadout | - | contract | Yes: `contract.updated` |

**ID prefix:** `CON-NNN`

#### `/firm:document`

| Sub-action | Required | Optional | Validates | Writes | Records |
|------------|----------|----------|-----------|--------|---------|
| create | name, parent_ref (type + id), content_path | type, author, version | parent_ref entity exists, content_path file exists on disk | document | Yes: `document.created` |
| list | (none) | parent_ref, type, status | - | (read-only) | No |
| view \<id\> | document_id | (none) | document exists | (read-only) | No |

**ID prefix:** `DOC-NNN`

---

## 2. Scope Decisions

| Decision | Rationale |
|----------|-----------|
| 10 entities get Phase 3 commands, 4 deferred | member_run, usage_event, records are auto-generated by hooks/services. firm_secret is metadata-only and low priority. All 4 get query surfaces in Phase 6 MCP. |
| `init` and `status` are separate task files, not a single `firm` task | Different concerns. init is a one-time setup; status is a recurring dashboard. Separate files keep tasks focused per Skillsmith rules. |
| Records entries auto-written by service layer, not by skill tasks | Services own the write path. Skill tasks orchestrate user interaction and call services. Consistent audit trail without relying on skill authors to remember. |
| Sub-units use `SUB-NNN` prefix, not `UNIT-NNN` | ENTITY-DESIGN.md specifies distinct prefixes. ID generation checks `parent_unit_id` to pick prefix. |
| comment.add does NOT write a Records entry | Comments are themselves immutable audit artifacts. Recording "a comment was added" in Records is redundant. |
| `/firm:unit complete` wraps Phase 2's `on_unit_done` | No re-implementation. Service layer calls the existing handler, adding pre-validation and Records entry. |
| Board is the default author/approver in v1 | Single-operator Firm. Gate.approve sets `approver_ref = {type: "board", id: null}`. Members can approve once reports_to hierarchy is enforced in Phase 5. |

---

## 3. Skillsmith Skill Spec

```
# Skill Spec: firm

## Identity

| Field | Value |
|-------|-------|
| Name | firm |
| Type | suite |
| Version | 0.1.0 |
| Category | operations |
| Description | Entity lifecycle commands for the AI Firm framework - create, view, update, and manage Members, Operations, Projects, Units, Gates, Goals, and supporting entities |

## Persona

**Role:** Firm operations manager - executes entity lifecycle commands against the .firm/ data store. Direct, structured, uses entity IDs in all responses.

**Style:**
- Terse confirmation after writes (entity ID + key fields, not full dumps)
- Tables for list output (ID, name, status, key relationship)
- Full detail on view commands (all fields, related entities)
- Entity IDs always shown in output (MEM-001, UNIT-042, etc.)

**Expertise:**
- 14-entity schema awareness (field types, constraints, relationships)
- Polymorphic references (parent_ref pattern on Goal, Comment, Gate, Document)
- Atomic operations (Unit checkout, Gate approve/reject)
- ID conventions (prefix-NNN per entity type)

## Activation

**What:** Slash command surface for all entity lifecycle operations in the AI Firm framework. Creates, lists, views, and updates Members, Operations, Projects, Units, Gates, Goals, Comments, Contracts, and Documents via firm.services Python backend.

**When to Use:**
- Managing AI Firm entities (create Members, assign Units, approve Gates, track Goals)
- Initializing a new Firm workspace (/firm:init)
- Checking Firm state and health (/firm:status)
- Any entity CRUD that would otherwise require direct DB access

**Not For:**
- Member dispatch/execution (Phase 4: /quill:run, etc.)
- Querying Records, Usage Events, or Member Runs (Phase 6 MCP)
- Managing Firm Secrets (direct DB or Phase 6)
- Hook configuration (hooks are installed separately)
- Runtime/Contract invocation (Phase 4+)

## Commands

| Command | Description | Routes To |
|---------|-------------|-----------|
| /firm:init | Initialize .firm/ workspace and database | tasks/init.md |
| /firm:status | Firm dashboard with aggregate stats | tasks/status.md |
| /firm:member | Member lifecycle (create, list, view, update) | tasks/member.md |
| /firm:operation | Operation lifecycle (create, list, view, update) | tasks/operation.md |
| /firm:project | Project lifecycle (create, list, view, update) | tasks/project.md |
| /firm:unit | Unit lifecycle (create, list, view, checkout, complete, update) | tasks/unit.md |
| /firm:gate | Gate lifecycle (request, approve, reject, list, view) | tasks/gate.md |
| /firm:goal | Goal lifecycle (create, list, view, update) | tasks/goal.md |
| /firm:comment | Comment operations (add, list) | tasks/comment.md |
| /firm:contract | Contract lifecycle (create, view, update) | tasks/contract.md |
| /firm:document | Document lifecycle (create, list, view) | tasks/document.md |

## Content Architecture

### Tasks
| File | Purpose | Loading |
|------|---------|---------|
| init.md | Initialize .firm/ workspace with migrations and seed data | on-command |
| status.md | Render Firm dashboard (roster, gates, goals, stats) | on-command |
| member.md | Member CRUD with FK validation and Records auto-entry | on-command |
| operation.md | Operation CRUD with project linkage | on-command |
| project.md | Project CRUD with operation and unit linkage | on-command |
| unit.md | Unit lifecycle including atomic checkout and complete | on-command |
| gate.md | Gate request/approve/reject flow | on-command |
| goal.md | Goal CRUD with metric tracking and parent linkage | on-command |
| comment.md | Polymorphic comment add/list | on-command |
| contract.md | Runtime contract management | on-command |
| document.md | Document metadata pointers | on-command |

### Frameworks
| File | Purpose | Loading |
|------|---------|---------|
| entity-schemas.md | Quick-reference for all 14 entity field specs, types, constraints | on-demand |
| id-conventions.md | ID prefix rules, generation scheme, reserved prefixes | on-demand |

### Context
| File | Purpose | Loading |
|------|---------|---------|
| firm-state.md | Workspace path, firm_id, db location, install status | always |

### Checklists
| File | Purpose |
|------|---------|
| entity-creation.md | Validation gates for any entity create (required fields, FK checks, ID format) |

### Data Directories
None (data lives in .firm/firm.db, not in the skill directory).

## Notes

- Skill source lives at `apps/agent-company-architecture/src/firm/commands/firm/`. An installer (Phase 8 or earlier) copies to `<workspace>/.claude/commands/firm/`.
- All task files call Python service layer functions via subprocess (`python -m firm <verb>`) or direct import. Service layer handles DB connection, validation, ID generation, and Records auto-entry.
- The `firm-state.md` context file is always-loaded so every command knows the workspace and firm_id without asking.
- Entity-schemas.md framework is on-demand to save context tokens. Task files reference it when they need field-level details (e.g., which fields are required for create).
```

---

## 4. Python Service Layer Design

### 4.1 Package Structure

```
src/firm/services/
├── __init__.py          (re-exports all public functions)
├── _id.py               (ID generation: next_id)
├── _validate.py         (shared validation: require_exists, validate_status, validate_parent_ref)
├── _records.py          (auto-write Records entries on significant operations)
├── firm_svc.py          (init_firm, get_status)
├── member.py            (create_member, list_members, get_member, update_member)
├── operation.py         (create_operation, list_operations, get_operation, update_operation)
├── project.py           (create_project, list_projects, get_project, update_project)
├── unit.py              (create_unit, list_units, get_unit, checkout_unit, complete_unit, update_unit)
├── gate.py              (request_gate, approve_gate, reject_gate, list_gates, get_gate)
├── goal.py              (create_goal, list_goals, get_goal, update_goal, update_metric)
├── comment.py           (add_comment, list_comments)
├── contract.py          (create_contract, get_contract, update_contract)
└── document.py          (create_document, list_documents, get_document)
```

### 4.2 Cross-Cutting Modules

#### `_id.py` - ID Generation

```python
def next_id(conn: sqlite3.Connection, table: str, prefix: str, firm_id: str) -> str:
    """Generate next sequential ID for any entity.
    
    Pattern: PREFIX-NNN where NNN = COUNT(*) + 1 for rows with matching firm_id.
    Matches LOG-NNN and USG-NNN patterns from Phase 2.
    
    Not concurrency-safe (v1 single-operator). Flag for Phase 6 MCP.
    """
```

**Prefix registry:**

| Entity | Prefix | Example |
|--------|--------|---------|
| member | MEM | MEM-001 |
| operation | OPS | OPS-001 |
| project | PROJ | PROJ-001 |
| unit | UNIT | UNIT-001 |
| unit (sub) | SUB | SUB-001 |
| gate | GATE | GATE-001 |
| goal | GOAL | GOAL-001 |
| comment | COM | COM-001 |
| contract | CON | CON-001 |
| document | DOC | DOC-001 |
| records | LOG | LOG-001 (existing Phase 2) |
| usage_event | USG | USG-001 (existing Phase 2) |

#### `_validate.py` - Shared Validation

```python
def require_exists(conn: sqlite3.Connection, table: str, id: str) -> dict[str, Any]:
    """Fetch row by ID or raise ValueError with entity-aware message."""

def validate_status(status: str, allowed: list[str]) -> None:
    """Raise ValueError if status not in allowed set."""

def validate_parent_ref(conn: sqlite3.Connection, parent_ref: dict) -> dict[str, Any]:
    """Validate {type: str, id: str} polymorphic reference.
    Checks type is a known entity table, id exists in that table.
    Returns the target entity row."""

def validate_fk(conn: sqlite3.Connection, table: str, id: str | None) -> dict[str, Any] | None:
    """If id is not None, validate it exists in table. Returns row or None."""
```

#### `_records.py` - Auto Records

```python
def log_event(
    conn: sqlite3.Connection,
    *,
    firm_id: str,
    event_type: str,
    actor: dict,          # {"type": "board"|"member"|"system", "id": str|None}
    target_ref: dict,     # {"type": str, "id": str}
    details: dict | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Write an immutable Records entry. Generates LOG-NNN id.
    
    Called internally by all service functions on significant operations.
    Uses raw SQL INSERT (records is immutable, repo.create commits internally).
    """
```

**Event type conventions:**

| Pattern | Example | Trigger |
|---------|---------|---------|
| `{entity}.created` | `member.created` | Any entity create |
| `{entity}.status_transition` | `unit.status_transition` | Status field change |
| `{entity}.checked_out` | `unit.checked_out` | Unit checkout |
| `{entity}.completed` | `unit.completed` | Unit completion |
| `gate.requested` | - | Gate creation |
| `gate.approved` | - | Gate approve |
| `gate.rejected` | - | Gate reject |
| `goal.metric_updated` | - | metric.current change |

### 4.3 Entity Service Functions

All service functions follow the Phase 2 handler pattern:

```python
def create_{entity}(
    conn: sqlite3.Connection,
    *,
    firm_id: str,
    {required_fields},
    {optional_fields} = {defaults},
) -> dict[str, Any]:
    """Create a {entity} and write a Records entry.
    
    1. Generate ID via next_id(conn, "{table}", "{PREFIX}", firm_id)
    2. Validate FK references (require_exists / validate_fk)
    3. Build data dict with all fields
    4. repo.create(conn, "{table}", data)
    5. _records.log_event(..., event_type="{entity}.created")
    6. Return created row
    """
```

#### `firm_svc.py`

```python
def init_firm(workspace: Path, *, force: bool = False) -> dict:
    """Wraps firm.cli.init.run_init(). Returns status dict."""

def get_status(conn: sqlite3.Connection, *, firm_id: str) -> dict:
    """Aggregate dashboard:
    - firm: name, north_star, values
    - members: count by status, list active with current claims
    - operations: count by status
    - projects: count by status, overdue count
    - units: count by status, pending + unassigned count
    - gates: pending count
    - goals: active count, achieved count, off-pace count
    Returns structured dict for task file to render.
    """
```

#### `member.py`

```python
def create_member(conn, *, firm_id, name, role, description=None,
                  reports_to=None, contract_id=None,
                  suggested_skills=None, suggested_domains=None,
                  budget=None) -> dict:
    # ID: MEM-NNN
    # Validates: reports_to (member exists), contract_id (contract exists)

def list_members(conn, *, firm_id, status="active", **filters) -> list[dict]:
    # Passes to repo.find("member", firm_id=firm_id, status=status, **filters)

def get_member(conn, *, firm_id, member_id) -> dict:
    # require_exists + enriches with contract detail and claimed units

def update_member(conn, *, firm_id, member_id, **updates) -> dict:
    # Validates FKs, calls repo.update, logs status transitions
```

#### `operation.py`

```python
def create_operation(conn, *, firm_id, name, description=None,
                     owner_member_id=None, priority="medium",
                     category=None, goal_ids=None,
                     acceptance_criteria=None) -> dict:
    # ID: OPS-NNN
    # Validates: owner_member_id exists (if set)

def list_operations(conn, *, firm_id, status="active", **filters) -> list[dict]
def get_operation(conn, *, firm_id, operation_id) -> dict
def update_operation(conn, *, firm_id, operation_id, **updates) -> dict
```

#### `project.py`

```python
def create_project(conn, *, firm_id, name, operation_id, due_date,
                   description=None, owner_member_id=None,
                   priority="medium", tags=None, goal_ids=None,
                   acceptance_criteria=None) -> dict:
    # ID: PROJ-NNN
    # Validates: operation_id exists, owner_member_id exists
    # Side-effect: appends project ID to operation.project_ids

def list_projects(conn, *, firm_id, **filters) -> list[dict]
def get_project(conn, *, firm_id, project_id) -> dict
def update_project(conn, *, firm_id, project_id, **updates) -> dict
```

#### `unit.py`

```python
def create_unit(conn, *, firm_id, name, project_id,
                description=None, assignee_member_id=None,
                priority="medium", rank=1.0, depends_on=None,
                due_date=None, tags=None, acceptance_criteria=None,
                parent_unit_id=None) -> dict:
    # ID: UNIT-NNN (or SUB-NNN if parent_unit_id set)
    # Validates: project_id exists, cycle check, parent 1-level max
    # Side-effect: appends unit ID to project.unit_ids

def list_units(conn, *, firm_id, **filters) -> list[dict]:
    # Sort: priority weight desc, rank asc

def get_unit(conn, *, firm_id, unit_id) -> dict

def checkout_unit(conn, *, unit_id, member_id) -> dict:
    # Wraps firm.core.units.checkout()
    # Logs: unit.checked_out

def complete_unit(conn, *, firm_id, unit_id, member_id,
                  run_id=None) -> dict:
    # Wraps firm.hooks.unit_completion.on_unit_done()
    # Caller sets unit.status = "done" first, then calls handler

def update_unit(conn, *, firm_id, unit_id, **updates) -> dict:
    # Cycle check if depends_on changes
```

#### `gate.py`

```python
def request_gate(conn, *, firm_id, requesting_member_id, action,
                 target_ref, context=None, expires_at=None) -> dict:
    # ID: GATE-NNN
    # Validates: member exists, target_ref entity exists

def approve_gate(conn, *, firm_id, gate_id,
                 approver_comment=None) -> dict:
    # Validates: gate exists, status == pending
    # Sets: status -> approved, approver_ref -> {type: "board", id: null}

def reject_gate(conn, *, firm_id, gate_id,
                approver_comment=None) -> dict:
    # Same pattern as approve, status -> rejected

def list_gates(conn, *, firm_id, status="pending", **filters) -> list[dict]
def get_gate(conn, *, firm_id, gate_id) -> dict
```

#### `goal.py`

```python
def create_goal(conn, *, firm_id, target, parent_ref,
                level=None, metric=None, status="active") -> dict:
    # ID: GOAL-NNN
    # Validates: parent_ref entity exists
    # Side-effect: appends goal ID to parent entity's goal_ids

def list_goals(conn, *, firm_id, status="active", **filters) -> list[dict]
def get_goal(conn, *, firm_id, goal_id) -> dict

def update_goal(conn, *, firm_id, goal_id, **updates) -> dict:
    # Detects metric.current changes -> logs goal.metric_updated

def update_metric(conn, *, firm_id, goal_id, current) -> dict:
    # Convenience: reads existing metric, updates current field
    # Logs: goal.metric_updated with from/to values
```

#### `comment.py`

```python
def add_comment(conn, *, firm_id, parent_ref, body,
                author=None, in_reply_to=None) -> dict:
    # ID: COM-NNN
    # author defaults to {"type": "board", "id": null}
    # Validates: parent_ref entity exists, in_reply_to comment exists
    # No Records entry (comments are audit artifacts themselves)

def list_comments(conn, *, firm_id, parent_ref) -> list[dict]:
    # Filter by parent_entity_type + parent_entity_id
```

#### `contract.py`

```python
def create_contract(conn, *, firm_id, name, member_id, runtime_type,
                    runtime_config=None, skill_loadout=None,
                    domain_loadout=None) -> dict:
    # ID: CON-NNN
    # Validates: member_id exists

def get_contract(conn, *, firm_id, contract_id) -> dict
def update_contract(conn, *, firm_id, contract_id, **updates) -> dict
```

#### `document.py`

```python
def create_document(conn, *, firm_id, name, parent_ref, content_path,
                    doc_type=None, author=None, version=1,
                    status="active") -> dict:
    # ID: DOC-NNN
    # Validates: parent_ref entity exists, content_path file exists on disk
    # author defaults to {"type": "board", "id": null}

def list_documents(conn, *, firm_id, **filters) -> list[dict]
def get_document(conn, *, firm_id, document_id) -> dict
```

### 4.4 CLI Extension

The existing `firm` CLI (`src/firm/__main__.py`) gains new subcommands as thin wrappers around service functions. Each service module's public functions are callable via:

```
python -m firm member create --name "Quill" --role "Blog Author" --firm-id chrisai
python -m firm unit checkout UNIT-001 --member MEM-001
python -m firm gate approve GATE-001 --comment "Looks good"
python -m firm status
```

This gives skill task files two invocation options:
1. **Subprocess:** `python -m firm member create ...` (simpler, process isolation)
2. **Direct import:** `from firm.services.member import create_member` (faster, no subprocess overhead)

Task files should use subprocess for v1 (matches hook precedent). Direct import is a future optimization.

---

## 5. Execute Plan Breakdown

### Overview

| Plan | Name | Wave | Depends On | Tasks | Autonomous | Files Created/Modified |
|------|------|------|------------|-------|------------|----------------------|
| 03-02 | Skill Scaffold + Entry Point | 1 | - | 2 | yes | 6 files (entry point, frameworks, context, checklist) |
| 03-03 | Service Infrastructure | 1 | - | 2 | yes | 5 files (_id, _validate, _records, tests) |
| 03-04 | Firm + Member + Operation Services | 2 | 03-03 | 3 | yes | 6 files (3 services, 3 test files) |
| 03-05 | Project + Unit Services | 2 | 03-03 | 2 | yes | 4 files (2 services, 2 test files) |
| 03-06 | Gate + Goal Services | 2 | 03-03 | 2 | yes | 4 files (2 services, 2 test files) |
| 03-07 | Comment + Contract + Document Services | 2 | 03-03 | 2 | yes | 6 files (3 services, 3 test files) |
| 03-08 | Task Files Batch 1 (init, status, member, operation, project) | 3 | 03-02, 03-04, 03-05 | 2 | yes | 5 task .md files |
| 03-09 | Task Files Batch 2 (unit, gate, goal, comment, contract, document) | 3 | 03-02, 03-05, 03-06, 03-07 | 2 | yes | 6 task .md files |
| 03-10 | Integration Validation + Skillsmith Audit | 4 | 03-08, 03-09 | 2 | no (checkpoint) | 0 new files (validation only) |

### Wave Execution Graph

```
Wave 1 (parallel):
  03-02  Skill Scaffold ─────────────────────┐
  03-03  Service Infrastructure ──┐           │
                                  │           │
Wave 2 (parallel, after 03-03):  │           │
  03-04  Firm/Member/Op ──────────┤           │
  03-05  Project/Unit ────────────┤           │
  03-06  Gate/Goal ───────────────┤           │
  03-07  Comment/Contract/Doc ────┤           │
                                  │           │
Wave 3 (parallel, after W1+W2):  │           │
  03-08  Task Files Batch 1 ──────┼───────────┤
  03-09  Task Files Batch 2 ──────┤           │
                                              │
Wave 4 (sequential, after W3):               │
  03-10  Integration + Audit ─────────────────┘
```

### Plan Details

#### 03-02: Skill Scaffold + Entry Point (Wave 1)

**Creates:**
- `src/firm/commands/firm/firm.md` (entry point)
- `src/firm/commands/firm/context/firm-state.md`
- `src/firm/commands/firm/frameworks/entity-schemas.md`
- `src/firm/commands/firm/frameworks/id-conventions.md`
- `src/firm/commands/firm/checklists/entity-creation.md`
- Directories: `tasks/` (empty, populated by 03-08/03-09)

**Tasks:**
1. Create directory structure and entry point following Skillsmith entry-point rules (YAML frontmatter, 5 XML sections)
2. Create framework, context, and checklist files following respective Skillsmith rules

#### 03-03: Service Infrastructure (Wave 1, parallel with 03-02)

**Creates:**
- `src/firm/services/__init__.py`
- `src/firm/services/_id.py`
- `src/firm/services/_validate.py`
- `src/firm/services/_records.py`
- `tests/services/__init__.py`
- `tests/services/test_id.py`
- `tests/services/test_validate.py`
- `tests/services/test_records.py`

**Tasks:**
1. Implement _id.py (next_id with prefix registry) + _validate.py (require_exists, validate_status, validate_parent_ref, validate_fk) + tests
2. Implement _records.py (log_event with LOG-NNN generation) + tests

#### 03-04: Firm + Member + Operation Services (Wave 2)

**Creates:**
- `src/firm/services/firm_svc.py` (init_firm, get_status)
- `src/firm/services/member.py`
- `src/firm/services/operation.py`
- `tests/services/test_firm_svc.py`
- `tests/services/test_member.py`
- `tests/services/test_operation.py`

**Tasks:**
1. Implement firm_svc.py (init wraps CLI, status aggregates all tables) + tests
2. Implement member.py (CRUD with FK validation, Records on create/status change) + tests
3. Implement operation.py (CRUD with owner validation, Records) + tests

#### 03-05: Project + Unit Services (Wave 2, parallel with 03-04)

**Creates:**
- `src/firm/services/project.py`
- `src/firm/services/unit.py`
- `tests/services/test_project.py`
- `tests/services/test_unit.py`

**Tasks:**
1. Implement project.py (CRUD + operation.project_ids linkage + Records) + tests
2. Implement unit.py (CRUD + checkout wrapping units.checkout() + complete wrapping on_unit_done + project.unit_ids linkage + Records) + tests

#### 03-06: Gate + Goal Services (Wave 2, parallel with 03-04)

**Creates:**
- `src/firm/services/gate.py`
- `src/firm/services/goal.py`
- `tests/services/test_gate.py`
- `tests/services/test_goal.py`

**Tasks:**
1. Implement gate.py (request/approve/reject + status validation + Records) + tests
2. Implement goal.py (CRUD + parent goal_ids linkage + metric update convenience + Records) + tests

#### 03-07: Comment + Contract + Document Services (Wave 2, parallel with 03-04)

**Creates:**
- `src/firm/services/comment.py`
- `src/firm/services/contract.py`
- `src/firm/services/document.py`
- `tests/services/test_comment.py`
- `tests/services/test_contract.py`
- `tests/services/test_document.py`

**Tasks:**
1. Implement comment.py (add with polymorphic parent_ref + list) + contract.py (CRUD) + tests
2. Implement document.py (CRUD with content_path validation) + tests

#### 03-08: Task Files Batch 1 (Wave 3)

**Creates:**
- `src/firm/commands/firm/tasks/init.md`
- `src/firm/commands/firm/tasks/status.md`
- `src/firm/commands/firm/tasks/member.md`
- `src/firm/commands/firm/tasks/operation.md`
- `src/firm/commands/firm/tasks/project.md`

**Tasks:**
1. Write init.md + status.md task files (Skillsmith task rules: purpose, user-story, when-to-use, steps, output, acceptance-criteria)
2. Write member.md + operation.md + project.md task files (multiplexed sub-action routing in steps)

#### 03-09: Task Files Batch 2 (Wave 3, parallel with 03-08)

**Creates:**
- `src/firm/commands/firm/tasks/unit.md`
- `src/firm/commands/firm/tasks/gate.md`
- `src/firm/commands/firm/tasks/goal.md`
- `src/firm/commands/firm/tasks/comment.md`
- `src/firm/commands/firm/tasks/contract.md`
- `src/firm/commands/firm/tasks/document.md`

**Tasks:**
1. Write unit.md + gate.md + goal.md task files
2. Write comment.md + contract.md + document.md task files

#### 03-10: Integration Validation + Skillsmith Audit (Wave 4)

**Creates:** No new files. Validation only.

**Tasks:**
1. Install skill to workspace (.claude/commands/firm/), smoke test every command against live .firm/firm.db with seeded data
2. Run `/skillsmith audit` against the installed skill, fix any compliance issues

**Checkpoint:** Human-verify the status dashboard rendering and a complete entity lifecycle (create operation -> create project -> create unit -> checkout -> complete).

---

## 6. Decision Log Entries (for PROJECT.md)

| Decision | Rationale | Date | Status |
|----------|-----------|------|--------|
| Skillsmith-compliant skill architecture for slash commands | Operator mandate. Ensures consistent XML structure, routing, naming conventions across all 11 task files. Passes Skillsmith audit. | 2026-04-15 | Active (03-01) |
| Python service layer (`firm.services.*`) as bridge between skill tasks and repo | Skill tasks orchestrate user interaction; services own validation, ID generation, Records auto-entry, and repo calls. Clean separation of concerns. | 2026-04-15 | Active (03-01) |
| 10 entities get Phase 3 commands; 4 deferred to Phase 6 MCP | member_run, usage_event, records are auto-generated. firm_secret is metadata-only. All 4 get query surface in MCP. | 2026-04-15 | Active (03-01) |
| Unified ID generation via `services._id.next_id()` | Standardizes MEM-NNN, OPS-NNN, etc. across all entities. Matches Phase 2 LOG-NNN/USG-NNN pattern. COUNT(*)-based, not concurrency-safe (v1). | 2026-04-15 | Active (03-01) |
| Records auto-entry on significant operations (create, status transition, checkout, gate decisions) | Services write Records entries internally. Skill authors don't manually log. Consistent audit trail. | 2026-04-15 | Active (03-01) |
| Skill source at `src/firm/commands/firm/`, install target at `<workspace>/.claude/commands/firm/` | Source in app repo, install to workspace. Matches hook install pattern (`<workspace>/.claude/hooks/firm-*.py`). | 2026-04-15 | Active (03-01) |
| v1 skill tasks invoke services via subprocess (`python -m firm <verb>`) | Process isolation, matches hook precedent. Direct import is a future optimization. | 2026-04-15 | Active (03-01) |
| Board is default author/approver in v1 | Single-operator Firm. No reports_to enforcement on Gate approval until Phase 5 Leadership Layer. | 2026-04-15 | Active (03-01) |

---

*Phase: 03-core-slash-commands, Plan: 01 (research)*
*Completed: 2026-04-15*
