-- 002_entities: Creates the 14 entity tables for the firm framework.
-- Canonical schema source: ENTITY-DESIGN.md (2026-04-14).
-- Storage notes in ENTITY-DESIGN.md referencing JSON files are superseded
-- by the SQLite pivot (2026-04-15); only field lists and constraints apply.
--
-- Conventions applied consistently:
--   * id TEXT PRIMARY KEY storing full prefixed ID (e.g. 'MEM-001', 'UNIT-001')
--   * firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE (all but firm)
--   * Timestamps: created_at NOT NULL DEFAULT (datetime('now'))
--   * Mutable entities also get updated_at with same default
--   * Status enums implemented as CHECK constraints (not sentinels)
--   * Array/object fields stored as TEXT (JSON) — application validates shape
--   * Polymorphic refs split into *_entity_type + *_entity_id (no composite FK)
--
-- Immutability triggers on: comment, records, usage_event.
-- (member_run has a running→completed lifecycle and must remain mutable;
--  the plan listed it as immutable — correction applied here.)

-----------------------------------------------------------------------------
-- 1. firm
-----------------------------------------------------------------------------
CREATE TABLE firm (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    operator TEXT,          -- JSON object {name, role}
    north_star TEXT,
    core_values TEXT,       -- JSON array (renamed from 'values' — SQL keyword)
    vision TEXT,
    partners TEXT,          -- JSON array
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-----------------------------------------------------------------------------
-- 2. contract
-- Note: contract.member_id is a soft reference (no FK) to break the circular
-- dep with member.contract_id. The authoritative direction is member→contract.
-----------------------------------------------------------------------------
CREATE TABLE contract (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    member_id TEXT,                 -- soft ref to member(id); no FK (circular)
    runtime_type TEXT NOT NULL
        CHECK (runtime_type IN ('claude_code', 'openclaw', 'codex', 'cursor', 'api_direct', 'custom')),
    runtime_config TEXT,            -- JSON object, shape varies by runtime
    skill_loadout TEXT,             -- JSON array
    domain_loadout TEXT,            -- JSON array
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_contract_firm_id ON contract(firm_id);

-----------------------------------------------------------------------------
-- 3. member
-----------------------------------------------------------------------------
CREATE TABLE member (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'paused', 'retired')),
    reports_to_member_id TEXT REFERENCES member(id) ON DELETE SET NULL,
    contract_id TEXT REFERENCES contract(id) ON DELETE SET NULL,
    suggested_skills TEXT,          -- JSON array
    suggested_domains TEXT,         -- JSON array
    budget TEXT,                    -- JSON object
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_member_firm_id ON member(firm_id);
CREATE INDEX idx_member_reports_to ON member(reports_to_member_id);

-----------------------------------------------------------------------------
-- 4. goal  (polymorphic parent; modifier attached to other entities)
-----------------------------------------------------------------------------
CREATE TABLE goal (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    level TEXT
        CHECK (level IS NULL OR level IN ('firm', 'operation', 'project', 'unit', 'member')),
    parent_entity_type TEXT NOT NULL
        CHECK (parent_entity_type IN ('firm', 'member', 'operation', 'project', 'unit')),
    parent_entity_id TEXT NOT NULL,
    target TEXT,
    metric TEXT,                    -- JSON object {type, value, unit, deadline, current}
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'achieved', 'abandoned')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_goal_firm_id ON goal(firm_id);
CREATE INDEX idx_goal_parent ON goal(parent_entity_type, parent_entity_id);

-----------------------------------------------------------------------------
-- 5. operation
-----------------------------------------------------------------------------
CREATE TABLE operation (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    owner_member_id TEXT REFERENCES member(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'paused', 'retired')),
    goal_ids TEXT,                  -- JSON array
    acceptance_criteria TEXT,       -- JSON array of {id, condition, resolved, resolved_by}
    priority TEXT NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('urgent', 'high', 'medium', 'low')),
    category TEXT,
    project_ids TEXT,               -- JSON array (denormalized; project.operation_id is canonical)
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_operation_firm_id ON operation(firm_id);
CREATE INDEX idx_operation_owner ON operation(owner_member_id);
CREATE INDEX idx_operation_status ON operation(status);

-----------------------------------------------------------------------------
-- 6. project
-----------------------------------------------------------------------------
CREATE TABLE project (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    operation_id TEXT NOT NULL REFERENCES operation(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    description TEXT,
    owner_member_id TEXT REFERENCES member(id) ON DELETE SET NULL,
    status TEXT NOT NULL
        CHECK (status IN ('in_progress', 'blocked', 'paused', 'in_review', 'done', 'cancelled')),
    goal_ids TEXT,                  -- JSON array
    acceptance_criteria TEXT,       -- JSON array
    unit_ids TEXT,                  -- JSON array (denormalized; unit.project_id is canonical)
    priority TEXT NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('urgent', 'high', 'medium', 'low')),
    due_date TEXT NOT NULL,         -- required per ENTITY-DESIGN
    tags TEXT,                      -- JSON array
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_project_firm_id ON project(firm_id);
CREATE INDEX idx_project_operation ON project(operation_id);
CREATE INDEX idx_project_owner ON project(owner_member_id);
CREATE INDEX idx_project_status ON project(status);

-----------------------------------------------------------------------------
-- 7. unit  (atomic work; UNIT-* or SUB-* ids; supports atomic checkout)
-----------------------------------------------------------------------------
CREATE TABLE unit (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    parent_unit_id TEXT REFERENCES unit(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    assignee_member_id TEXT REFERENCES member(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'blocked', 'in_review', 'done', 'cancelled')),
    priority TEXT NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('urgent', 'high', 'medium', 'low')),
    rank REAL,
    goal_ids TEXT,                  -- JSON array
    acceptance_criteria TEXT,       -- JSON array
    depends_on TEXT,                -- JSON array of UNIT ids
    due_date TEXT,
    outputs TEXT,                   -- JSON array
    tags TEXT,                      -- JSON array
    claimed_by TEXT REFERENCES member(id) ON DELETE SET NULL,   -- atomic checkout
    claimed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_unit_firm_id ON unit(firm_id);
CREATE INDEX idx_unit_project ON unit(project_id);
CREATE INDEX idx_unit_parent ON unit(parent_unit_id);
CREATE INDEX idx_unit_assignee ON unit(assignee_member_id);
CREATE INDEX idx_unit_status ON unit(status);
CREATE INDEX idx_unit_claimed_by ON unit(claimed_by);

-----------------------------------------------------------------------------
-- 8. comment  (polymorphic parent; IMMUTABLE)
-----------------------------------------------------------------------------
CREATE TABLE comment (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    parent_entity_type TEXT NOT NULL
        CHECK (parent_entity_type IN ('firm', 'member', 'operation', 'project', 'unit', 'goal', 'gate', 'document')),
    parent_entity_id TEXT NOT NULL,
    author_type TEXT NOT NULL
        CHECK (author_type IN ('member', 'board')),
    author_id TEXT,                 -- NULL for board
    in_reply_to TEXT REFERENCES comment(id) ON DELETE SET NULL,
    body TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
        CHECK (archived IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_comment_firm_id ON comment(firm_id);
CREATE INDEX idx_comment_parent ON comment(parent_entity_type, parent_entity_id);
CREATE INDEX idx_comment_author ON comment(author_type, author_id);
CREATE INDEX idx_comment_reply_to ON comment(in_reply_to);

CREATE TRIGGER trg_comment_no_update
    BEFORE UPDATE ON comment
    FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'comment is immutable');
END;

CREATE TRIGGER trg_comment_no_delete
    BEFORE DELETE ON comment
    FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'comment is immutable');
END;

-----------------------------------------------------------------------------
-- 9. member_run  (mutable — has running→completed lifecycle)
-----------------------------------------------------------------------------
CREATE TABLE member_run (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    member_id TEXT NOT NULL REFERENCES member(id) ON DELETE RESTRICT,
    unit_id TEXT REFERENCES unit(id) ON DELETE SET NULL,
    sub_unit_id TEXT REFERENCES unit(id) ON DELETE SET NULL,
    status TEXT NOT NULL
        CHECK (status IN ('running', 'completed', 'failed', 'cancelled', 'timed_out')),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    usage_event_ids TEXT,           -- JSON array
    outputs TEXT,                   -- JSON array
    error TEXT,                     -- JSON object or message string
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_member_run_firm_id ON member_run(firm_id);
CREATE INDEX idx_member_run_member ON member_run(member_id);
CREATE INDEX idx_member_run_unit ON member_run(unit_id);
CREATE INDEX idx_member_run_status ON member_run(status);

-----------------------------------------------------------------------------
-- 10. usage_event  (IMMUTABLE)
-----------------------------------------------------------------------------
CREATE TABLE usage_event (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    member_id TEXT NOT NULL REFERENCES member(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES member_run(id) ON DELETE SET NULL,
    unit_id TEXT REFERENCES unit(id) ON DELETE SET NULL,
    timestamp TEXT NOT NULL,
    plan TEXT NOT NULL
        CHECK (plan IN ('claude_pro_100', 'claude_pro_200', 'api', 'custom')),
    model TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cache_read_tokens INTEGER,
    cache_create_tokens INTEGER,
    dollar_equivalent REAL,
    window_percent_consumed REAL,
    window_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_usage_event_firm_id ON usage_event(firm_id);
CREATE INDEX idx_usage_event_member ON usage_event(member_id);
CREATE INDEX idx_usage_event_run ON usage_event(run_id);
CREATE INDEX idx_usage_event_unit ON usage_event(unit_id);

CREATE TRIGGER trg_usage_event_no_update
    BEFORE UPDATE ON usage_event
    FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'usage_event is immutable');
END;

CREATE TRIGGER trg_usage_event_no_delete
    BEFORE DELETE ON usage_event
    FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'usage_event is immutable');
END;

-----------------------------------------------------------------------------
-- 11. gate
-----------------------------------------------------------------------------
CREATE TABLE gate (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    requesting_member_id TEXT NOT NULL REFERENCES member(id) ON DELETE RESTRICT,
    action TEXT NOT NULL,
    target_entity_type TEXT NOT NULL
        CHECK (target_entity_type IN ('firm', 'member', 'operation', 'project', 'unit', 'goal', 'document', 'firm_secret', 'contract')),
    target_entity_id TEXT NOT NULL,
    context TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'revoked')),
    approver_type TEXT
        CHECK (approver_type IS NULL OR approver_type IN ('board', 'member')),
    approver_id TEXT,               -- NULL for board
    approver_comment TEXT,
    expires_at TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_gate_firm_id ON gate(firm_id);
CREATE INDEX idx_gate_requester ON gate(requesting_member_id);
CREATE INDEX idx_gate_target ON gate(target_entity_type, target_entity_id);
CREATE INDEX idx_gate_status ON gate(status);

-----------------------------------------------------------------------------
-- 12. records  (IMMUTABLE audit trail)
-----------------------------------------------------------------------------
CREATE TABLE records (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL
        CHECK (actor_type IN ('member', 'board', 'system')),
    actor_id TEXT,                  -- NULL for board/system
    target_entity_type TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    details TEXT,                   -- JSON
    run_id TEXT REFERENCES member_run(id) ON DELETE SET NULL,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_records_firm_id ON records(firm_id);
CREATE INDEX idx_records_event_type ON records(event_type);
CREATE INDEX idx_records_actor ON records(actor_type, actor_id);
CREATE INDEX idx_records_target ON records(target_entity_type, target_entity_id);
CREATE INDEX idx_records_run ON records(run_id);
CREATE INDEX idx_records_timestamp ON records(timestamp);

CREATE TRIGGER trg_records_no_update
    BEFORE UPDATE ON records
    FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'records is immutable');
END;

CREATE TRIGGER trg_records_no_delete
    BEFORE DELETE ON records
    FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'records is immutable');
END;

-----------------------------------------------------------------------------
-- 13. firm_secret  (METADATA-ONLY; values never stored here)
-----------------------------------------------------------------------------
CREATE TABLE firm_secret (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    source TEXT NOT NULL
        CHECK (source IN ('env', 'keychain', '1password', 'bitwarden', 'custom')),
    env_var_name TEXT,
    used_by_member_ids TEXT,        -- JSON array
    last_rotated_at TEXT,
    rotation_cadence_days INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_firm_secret_firm_id ON firm_secret(firm_id);
CREATE INDEX idx_firm_secret_name ON firm_secret(firm_id, name);

-----------------------------------------------------------------------------
-- 14. document  (metadata pointing at .md file on disk)
-----------------------------------------------------------------------------
CREATE TABLE document (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    parent_entity_type TEXT NOT NULL
        CHECK (parent_entity_type IN ('firm', 'member', 'operation', 'project', 'unit', 'goal', 'gate')),
    parent_entity_id TEXT NOT NULL,
    type TEXT NOT NULL,             -- free-form: plan, design, notes, spec, handoff, research, chronicle, ...
    name TEXT NOT NULL,
    content_path TEXT NOT NULL,
    author_type TEXT
        CHECK (author_type IS NULL OR author_type IN ('member', 'board')),
    author_id TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived', 'deprecated')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_document_firm_id ON document(firm_id);
CREATE INDEX idx_document_parent ON document(parent_entity_type, parent_entity_id);
CREATE INDEX idx_document_type ON document(type);
CREATE INDEX idx_document_status ON document(status);
