# Entity Schemas

Quick-reference for all 14 entity field specs, types, constraints, and relationships. Source: `src/firm/migrations/002_entities.sql`.

## Core Concepts

### Field Type Legend

| Type | SQLite | Notes |
|------|--------|-------|
| TEXT | TEXT | String |
| JSON | TEXT | JSON-encoded array or object, deserialized by repo |
| INT | INTEGER | Integer |
| REAL | REAL | Floating point |
| BOOL | INTEGER | 0 or 1 |
| TIMESTAMP | TEXT | ISO 8601 datetime string |

### Mutability

| Category | Tables | Rule |
|----------|--------|------|
| Immutable | comment, records, usage_event | DB triggers reject UPDATE/DELETE |
| Mutable | All others (11 tables) | Have `updated_at`, auto-touched on update |

---

## Entity Reference

### 1. firm

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| id | TEXT PK | yes | User-defined (e.g., "chrisai") |
| name | TEXT | yes | |
| description | TEXT | no | |
| operator | JSON | no | {name, role} |
| north_star | TEXT | no | |
| core_values | JSON | no | Array of strings |
| vision | TEXT | no | |
| partners | JSON | no | Array |
| created_at | TIMESTAMP | auto | |
| updated_at | TIMESTAMP | auto | |

No status lifecycle. Top-level container.

### 2. member

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| id | TEXT PK | yes | MEM-NNN |
| firm_id | TEXT FK | yes | References firm(id) |
| name | TEXT | yes | |
| role | TEXT | yes | |
| description | TEXT | no | |
| status | TEXT | yes | active, paused, retired (default: active) |
| reports_to_member_id | TEXT FK | no | References member(id) |
| contract_id | TEXT FK | no | References contract(id) |
| suggested_skills | JSON | no | Array of skill names |
| suggested_domains | JSON | no | Array of domain names |
| budget | JSON | no | {enforcement, limits: {api_monthly_usd, window_percent_cap, plan}} |
| created_at | TIMESTAMP | auto | |
| updated_at | TIMESTAMP | auto | |

### 3. operation

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| id | TEXT PK | yes | OPS-NNN |
| firm_id | TEXT FK | yes | References firm(id) |
| name | TEXT | yes | |
| description | TEXT | no | |
| owner_member_id | TEXT FK | no | References member(id) |
| status | TEXT | yes | active, paused, retired (default: active) |
| goal_ids | JSON | no | Array of GOAL IDs |
| acceptance_criteria | JSON | no | Array of {id, condition, resolved, resolved_by} |
| priority | TEXT | yes | urgent, high, medium, low (default: medium) |
| category | TEXT | no | |
| project_ids | JSON | no | Denormalized; project.operation_id is canonical |
| created_at | TIMESTAMP | auto | |
| updated_at | TIMESTAMP | auto | |

### 4. project

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| id | TEXT PK | yes | PROJ-NNN |
| firm_id | TEXT FK | yes | References firm(id) |
| operation_id | TEXT FK | yes | References operation(id), ON DELETE RESTRICT |
| name | TEXT | yes | |
| description | TEXT | no | |
| owner_member_id | TEXT FK | no | References member(id) |
| status | TEXT | yes | in_progress, blocked, paused, in_review, done, cancelled (no default) |
| goal_ids | JSON | no | Array of GOAL IDs |
| acceptance_criteria | JSON | no | Array of {id, condition, resolved, resolved_by} |
| unit_ids | JSON | no | Denormalized; unit.project_id is canonical |
| priority | TEXT | yes | urgent, high, medium, low (default: medium) |
| due_date | TEXT | yes | Required per design |
| tags | JSON | no | Array of strings |
| created_at | TIMESTAMP | auto | |
| updated_at | TIMESTAMP | auto | |

### 5. unit

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| id | TEXT PK | yes | UNIT-NNN or SUB-NNN |
| firm_id | TEXT FK | yes | References firm(id) |
| project_id | TEXT FK | yes | References project(id) |
| parent_unit_id | TEXT FK | no | References unit(id), 1 level max |
| name | TEXT | yes | |
| description | TEXT | no | |
| assignee_member_id | TEXT FK | no | References member(id) |
| status | TEXT | yes | pending, in_progress, blocked, in_review, done, cancelled (default: pending) |
| priority | TEXT | yes | urgent, high, medium, low (default: medium) |
| rank | REAL | no | Decimal for deterministic ordering within priority bucket |
| goal_ids | JSON | no | Array of GOAL IDs |
| acceptance_criteria | JSON | no | Array of {id, condition, resolved, resolved_by} |
| depends_on | JSON | no | Array of UNIT IDs, cycle-checked |
| due_date | TEXT | no | Inherits from project if not set |
| outputs | JSON | no | Array of {type, path/url/...} |
| tags | JSON | no | Array of strings |
| claimed_by | TEXT FK | no | Atomic checkout: member(id), set via `WHERE claimed_by IS NULL RETURNING *` |
| claimed_at | TIMESTAMP | no | Set on checkout |
| created_at | TIMESTAMP | auto | |
| updated_at | TIMESTAMP | auto | |

### 6. goal (polymorphic parent)

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| id | TEXT PK | yes | GOAL-NNN |
| firm_id | TEXT FK | yes | References firm(id) |
| level | TEXT | no | firm, operation, project, unit, member |
| parent_entity_type | TEXT | yes | firm, member, operation, project, unit |
| parent_entity_id | TEXT | yes | ID of parent entity |
| target | TEXT | no | Human-readable goal description |
| metric | JSON | no | {type, value, unit, deadline, current} |
| status | TEXT | yes | active, achieved, abandoned (default: active) |
| created_at | TIMESTAMP | auto | |
| updated_at | TIMESTAMP | auto | |

### 7. gate

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| id | TEXT PK | yes | GATE-NNN |
| firm_id | TEXT FK | yes | References firm(id) |
| requesting_member_id | TEXT FK | yes | References member(id), ON DELETE RESTRICT |
| action | TEXT | yes | Free-form (publish_post, close_project, hire_member, etc.) |
| target_entity_type | TEXT | yes | firm, member, operation, project, unit, goal, document, firm_secret, contract |
| target_entity_id | TEXT | yes | ID of target entity |
| context | TEXT | no | Justification text |
| status | TEXT | yes | pending, approved, rejected, expired, revoked (default: pending) |
| approver_type | TEXT | no | board, member (set on decision) |
| approver_id | TEXT | no | NULL for board |
| approver_comment | TEXT | no | |
| expires_at | TIMESTAMP | no | Auto-expires gate if set |
| decided_at | TIMESTAMP | no | Set on approve/reject |
| created_at | TIMESTAMP | auto | |
| updated_at | TIMESTAMP | auto | |

### 8. comment (immutable, polymorphic parent)

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| id | TEXT PK | yes | COM-NNN |
| firm_id | TEXT FK | yes | References firm(id) |
| parent_entity_type | TEXT | yes | firm, member, operation, project, unit, goal, gate, document |
| parent_entity_id | TEXT | yes | ID of parent entity |
| author_type | TEXT | yes | member, board |
| author_id | TEXT | no | NULL for board |
| in_reply_to | TEXT FK | no | References comment(id) |
| body | TEXT | yes | |
| archived | BOOL | yes | Default 0. Archived flag only (body never changes). |
| created_at | TIMESTAMP | auto | |

No `updated_at`. Immutable: DB triggers reject UPDATE and DELETE.

### 9. contract

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| id | TEXT PK | yes | CON-NNN |
| firm_id | TEXT FK | yes | References firm(id) |
| name | TEXT | yes | |
| member_id | TEXT | no | Soft ref to member(id), no FK (circular dep) |
| runtime_type | TEXT | yes | claude_code, openclaw, codex, cursor, api_direct, custom |
| runtime_config | JSON | no | Shape varies by runtime_type |
| skill_loadout | JSON | no | Array of skill names |
| domain_loadout | JSON | no | Array of domain names |
| created_at | TIMESTAMP | auto | |
| updated_at | TIMESTAMP | auto | |

### 10. document (polymorphic parent)

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| id | TEXT PK | yes | DOC-NNN |
| firm_id | TEXT FK | yes | References firm(id) |
| parent_entity_type | TEXT | yes | firm, member, operation, project, unit, goal, gate |
| parent_entity_id | TEXT | yes | ID of parent entity |
| type | TEXT | yes | Free-form: plan, design, notes, spec, handoff, research, chronicle |
| name | TEXT | yes | |
| content_path | TEXT | yes | Relative path to .md file on disk |
| author_type | TEXT | no | member, board |
| author_id | TEXT | no | NULL for board |
| version | INT | yes | Default 1, increment on revision |
| status | TEXT | yes | active, archived, deprecated (default: active) |
| created_at | TIMESTAMP | auto | |
| updated_at | TIMESTAMP | auto | |

### 11. member_run (Phase 2 - hook managed)

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| id | TEXT PK | yes | RUN-NNN |
| firm_id | TEXT FK | yes | |
| member_id | TEXT FK | yes | ON DELETE RESTRICT |
| unit_id | TEXT FK | no | |
| sub_unit_id | TEXT FK | no | |
| status | TEXT | yes | running, completed, failed, cancelled, timed_out |
| started_at | TIMESTAMP | yes | |
| ended_at | TIMESTAMP | no | |
| usage_event_ids | JSON | no | Array of USG IDs |
| outputs | JSON | no | Array of artifacts |
| error | TEXT | no | JSON object or message |
| notes | TEXT | no | Credential-redacted before write |
| created_at | TIMESTAMP | auto | |
| updated_at | TIMESTAMP | auto | |

### 12. usage_event (immutable, Phase 2 - hook managed)

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| id | TEXT PK | yes | USG-NNN |
| firm_id | TEXT FK | yes | |
| member_id | TEXT FK | yes | ON DELETE RESTRICT |
| run_id | TEXT FK | no | |
| unit_id | TEXT FK | no | |
| timestamp | TIMESTAMP | yes | |
| plan | TEXT | yes | claude_pro_100, claude_pro_200, api, custom |
| model | TEXT | no | |
| tokens_in | INT | no | |
| tokens_out | INT | no | |
| cache_read_tokens | INT | no | |
| cache_create_tokens | INT | no | |
| dollar_equivalent | REAL | no | |
| window_percent_consumed | REAL | no | |
| window_id | TEXT | no | |
| created_at | TIMESTAMP | auto | |

### 13. records (immutable audit trail)

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| id | TEXT PK | yes | LOG-NNN |
| firm_id | TEXT FK | yes | |
| event_type | TEXT | yes | Dotted: entity.event (e.g., unit.status_transition) |
| actor_type | TEXT | yes | member, board, system |
| actor_id | TEXT | no | NULL for board/system |
| target_entity_type | TEXT | yes | |
| target_entity_id | TEXT | yes | |
| details | JSON | no | Event-specific payload |
| run_id | TEXT FK | no | Links to member_run if applicable |
| timestamp | TIMESTAMP | auto | |
| created_at | TIMESTAMP | auto | |

### 14. firm_secret (metadata only, deferred)

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| id | TEXT PK | yes | KEY-NNN |
| firm_id | TEXT FK | yes | |
| name | TEXT | yes | |
| description | TEXT | no | |
| source | TEXT | yes | env, keychain, 1password, bitwarden, custom |
| env_var_name | TEXT | no | |
| used_by_member_ids | JSON | no | Array of MEM IDs |
| last_rotated_at | TIMESTAMP | no | |
| rotation_cadence_days | INT | no | |
| notes | TEXT | no | |
| created_at | TIMESTAMP | auto | |
| updated_at | TIMESTAMP | auto | |

**Security invariant:** No secret value ever stored in this table. Only metadata.

---

## Relationships

### FK Graph (canonical direction)

```
firm
 ├── member (firm_id)
 │    ├── contract (member.contract_id → contract.id)
 │    └── member (reports_to_member_id → member.id)
 ├── operation (firm_id)
 │    └── project (operation_id)
 │         └── unit (project_id)
 │              └── unit (parent_unit_id, 1 level max)
 ├── goal (firm_id, parent_entity_type + parent_entity_id)
 ├── gate (firm_id, requesting_member_id, target refs)
 ├── comment (firm_id, parent_entity_type + parent_entity_id)
 ├── document (firm_id, parent_entity_type + parent_entity_id)
 ├── member_run (firm_id, member_id, unit_id)
 │    └── usage_event (run_id)
 ├── records (firm_id, targets any entity)
 └── firm_secret (firm_id)
```

### Polymorphic Entities

| Entity | Ref Pattern | Valid Parent Types |
|--------|-------------|-------------------|
| goal | parent_entity_type + parent_entity_id | firm, member, operation, project, unit |
| comment | parent_entity_type + parent_entity_id | firm, member, operation, project, unit, goal, gate, document |
| gate | target_entity_type + target_entity_id | firm, member, operation, project, unit, goal, document, firm_secret, contract |
| document | parent_entity_type + parent_entity_id | firm, member, operation, project, unit, goal, gate |

### Denormalized Arrays

| Parent | Field | Canonical FK |
|--------|-------|-------------|
| operation | project_ids | project.operation_id |
| project | unit_ids | unit.project_id |
| (various) | goal_ids | goal.parent_entity_id |
