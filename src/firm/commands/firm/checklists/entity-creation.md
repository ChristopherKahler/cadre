# Entity Creation Checklist

**Purpose:** Validation gates for any entity create operation. Reference before writing to the database.

## Required Fields

- [ ] `id` generated via `next_id()` with correct prefix for entity type
- [ ] `firm_id` resolved from `$FIRM_ID` or default `chrisai`
- [ ] Entity-specific required fields present (see per-entity below)

### Per-Entity Required Fields

- [ ] **member:** name, role
- [ ] **operation:** name
- [ ] **project:** name, operation_id, due_date, status
- [ ] **unit:** name, project_id
- [ ] **gate:** requesting_member_id, action, target_entity_type, target_entity_id
- [ ] **goal:** parent_entity_type, parent_entity_id
- [ ] **comment:** parent_entity_type, parent_entity_id, body, author_type
- [ ] **contract:** name, member_id, runtime_type
- [ ] **document:** parent_entity_type, parent_entity_id, type, name, content_path

## FK Validation

- [ ] `operation_id` references an existing operation (project create)
- [ ] `project_id` references an existing project (unit create)
- [ ] `member_id` / `owner_member_id` references an existing member (if provided)
- [ ] `contract_id` references an existing contract (if provided)
- [ ] `reports_to_member_id` references an existing member (if provided)
- [ ] `requesting_member_id` references an existing member (gate request)
- [ ] `in_reply_to` references an existing comment (if provided)

## Polymorphic Parent Ref Validation

- [ ] `parent_entity_type` is a valid entity table name (goal, comment, gate, document)
- [ ] `parent_entity_id` exists in the table named by `parent_entity_type`
- [ ] `target_entity_type` + `target_entity_id` target exists (gate)

## Status Enum Validity

- [ ] **member:** active, paused, retired (default: active)
- [ ] **operation:** active, paused, retired (default: active)
- [ ] **project:** in_progress, blocked, paused, in_review, done, cancelled (no default - required)
- [ ] **unit:** pending, in_progress, blocked, in_review, done, cancelled (default: pending)
- [ ] **gate:** pending, approved, rejected, expired, revoked (default: pending)
- [ ] **goal:** active, achieved, abandoned (default: active)
- [ ] **document:** active, archived, deprecated (default: active)

## ID Format

- [ ] ID matches `{PREFIX}-{NNN}` pattern
- [ ] Prefix matches entity type (MEM for member, OPS for operation, etc.)
- [ ] NNN is sequential and zero-padded to 3 digits
- [ ] Sub-units use SUB prefix, not UNIT

## Constraint Checks

- [ ] `runtime_type` is one of: claude_code, openclaw, codex, cursor, api_direct, custom (contract)
- [ ] `priority` is one of: urgent, high, medium, low (operation, project, unit)
- [ ] `plan` is one of: claude_pro_100, claude_pro_200, api, custom (usage_event)
- [ ] `level` is one of: firm, operation, project, unit, member (goal, if provided)
- [ ] Unit `depends_on` list does not create a cycle (validated by `validate_no_cycle()`)
- [ ] Sub-unit is max 1 level deep (parent_unit_id's own parent_unit_id must be NULL)
