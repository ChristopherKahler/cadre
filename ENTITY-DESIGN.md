# Agent Company Architecture — Entity Design

**Status:** In progress. Walking Paperclip's 13 entities top-down, locking decisions per entity.
**Started:** 2026-04-14
**Companion doc:** `LANDSCAPE.md` (Paperclip research artifact, 2026-04-13)

---

## Design Principles (Decided)

1. **Business-oriented vocabulary** — no medieval/nautical/military metaphors. Firm, not Guild.
2. **Firm-scoped from day one** — every entity carries `firm_id`, defaulted to `chrisai`. Enables later C&C co-owned Firm migration at ~1-2 hr cost.
3. **Identity / runtime split** — Member (identity) separate from Contract (runtime) for swappable execution.
4. **Free access with suggested loadout** — solo operator, no trust boundaries. Loadout = context pre-load, not permission.
5. **Budget scaffolded, not wired** — structure ready for ccusage integration. Turn-key when plan upgrades warrant it.
6. **Session-pulse activation** — no 24/7 heartbeats. Hooks fire at session boundaries.
7. **Framework is standalone.** This framework is independent of BASE, CARL, and PAUL. It does NOT extend, integrate with, or depend on them. It operates at a different layer of abstraction (autonomous AI team orchestration) than BASE (solo operator workspace state). Separate folder (`.firm/`), separate hooks, separate MCP (when built), separate mental model.
8. **Fresh wins.** When this framework's design needs something, build it fresh. Don't inherit conventions from BASE/CARL/PAUL for migration convenience — those systems solve different problems.
9. **Pulse as activation concept.** The pulse-style session-start activation was borrowed as an idea from BASE, but this framework implements its own hooks independently. No shared hook code.

---

## Vocabulary Map (Paperclip → ChrisAI)

| Paperclip | ChrisAI | Notes |
|---|---|---|
| Company | **Firm** | `firm_id` scaffold on all entities |
| Agent | **Member** | Named worker, role-defined |
| Goal | **Goal** | Measurable metric; modifier attached to entities via `parent_ref` |
| — | **Operation** | NEW — replaces "Initiative" (OPS-*). Mid-tier between Firm and Project. |
| — | **Acceptance Criteria (AC)** | NEW — qualitative completion checks; embedded on parent entity |
| Project | Project | REUSE existing BASE Projects |
| Issue / Task | **Unit** | Atomic work |
| Issue Comment | **Unit Comment** | |
| Heartbeat Run | **Member Run** | Pulse-based, session-hook activation |
| Cost Event | **Usage Event** | Money + subscription factors |
| Approval | **Gate** | |
| Activity Log | **Records** | |
| Company Secret | **Firm Secret** | |
| Document | Document | REUSE (PAUL STATE.md, PLANNING.md precedents) |
| Adapter | **Contract** | Runtime execution interface |

---

## Entity Walkthrough

### ✅ Entity 1: Firm

**Decision:** NEW entity. Standalone — does NOT reuse BASE workspace. The Firm record lives in `.firm/firm.json` and is the top-level container for this framework.

**v1 Schema:**

```json
{
  "id": "chrisai",
  "name": "ChrisAI",
  "description": "Chris Kahler's personal AI-operated firm.",
  "operator": {
    "name": "Chris Kahler",
    "role": "Board / Founder"
  },
  "north_star": "$7k+/month on 25 hours/week or less — no obligatory night work by Sept 2026",
  "values": ["Balance", "Integrity", "Experience", "Humility", "Alignment"],
  "vision": "Life moves at the pace I choose — spontaneous, present, unrestricted.",
  "partners": [],
  "created_at": "2026-04-14"
}
```

**Scaffolding:** `firm_id` field on all downstream entities, defaulted to `"chrisai"`.

**Future:** C&C becomes a separate Firm record (`id: "cc-strategic"`, partners populated) when the relationship formalizes. Migration = create Firm record + reassign entities. Estimated 1-2 hr effort.

**C&C handling (v1):** External client tagging on Projects. No Firm promotion yet.

**Relationship to operator profile:** The operator North Star/values/vision are *duplicated* into Firm for this framework's self-contained reality. BASE's operator profile is separate and serves BASE's purposes.

**Storage:** `.firm/firm.json`

---

### ✅ Entity 2: Member

**Decision:** NEW entity. Members are the missing primitive.

**Design choices locked:**

| Question | Decision |
|---|---|
| Is Chris a Member? | No — Board role, external. Stand-in Member can be generated for attribution when needed (role: "Board Operator"). |
| `reports_to` in v1? | Yes. Null means reports to Board. Member ID for sub-team hierarchy. |
| Budget enforcement in v1? | Scaffolded, off. Ready for ccusage integration when plan upgrades. |
| Skill access model | Free access + suggested loadout (context pre-load, not permission). |
| Identity / runtime | Split. Member holds identity, Contract holds runtime. |

**v1 Schema:**

```json
{
  "id": "MEM-001",
  "firm_id": "chrisai",
  "name": "ScribeBot",
  "role": "Content writer",
  "description": "Writes blog posts, scripts, reels in Chris's voice.",
  "status": "active",
  "reports_to": null,
  "contract_id": "CON-001",
  "suggested_skills": ["humanizer", "blog:write", "blog:repurpose"],
  "suggested_domains": ["content"],
  "budget": {
    "enforcement": "off",
    "limits": {
      "api_monthly_usd": null,
      "window_percent_cap": null,
      "plan": null
    }
  },
  "created_at": "2026-04-14"
}
```

**Storage:** `.firm/members.json` (proposed)

**Budget scaffolding detail (for later ccusage wiring):**
- `api_monthly_usd` — for API-based spend caps
- `window_percent_cap` — % of Claude Code 5hr block this Member is allowed to consume
- `plan` — declares which plan runtime: `claude_pro_100` | `claude_pro_200` | `api`
- `enforcement` — `off` | `soft` (warn) | `hard` (pause Member)

**First Member (v1):**
- **Quill** — owns the blog authoring pipeline end-to-end. Scope: ideate → research → outline → draft → humanize → editorial → publish. Does NOT own social repurposing or video adaptations.

**Scope principle locked:** A Member = one cohesive role a person could plausibly own as a job. Start broad, split when skill boundaries or voice boundaries emerge. Scope as wide as skills stay coherent.

**Future Members (reserved names, for when work demands splits):**
- **Echo** — blog-to-social amplification and repurposing (different voice, different platforms)
- **Harbor** — longform video production (YouTube scripts, adaptations — different skill set)

---

### ✅ Entity 3: Goal

**Decision:** Goal is a standalone entity BUT not hierarchical. It is a **modifier** attached to other entities (Firm, Operation, Project, Unit, Member) via `parent_ref`.

**Core principle:** Separation of concerns.
- **Goal** = measurable outcome (quantitative metric, tracked to numbers)
- **Acceptance Criteria (AC)** = qualitative completion conditions (embedded on parent entity)
- **Definition of Done (DoD)** = composite: all AC resolved + Goal metric achievable

**Design choices locked:**

| Question | Decision |
|---|---|
| Goal as hierarchical entity or modifier? | **Modifier.** Attaches to parent via `parent_ref`. |
| Ownership model | **Derived from parent entity's assignee.** No `owner_member_id` on Goal. |
| Multiple Goals per entity? | **Yes** — if each is measurable. Otherwise it's AC. |
| Inheritance | **Computed at read time** by walking parent chain. Injected as `<inherited-goals>`. |
| AC as separate entity? | **No, embedded on parent** (v1). Extract to library later when patterns dial in (e.g., standard blog post AC). |
| Metric `current` field | **Manually updated v1.** Auto-refresh from data sources future. |
| Status lifecycle | `active | achieved | abandoned` |
| Metric structure | Structured (type, value, unit, deadline, current). Ambiguity breaks tracking. |

**Goal v1 Schema:**

```json
{
  "id": "GOAL-003",
  "firm_id": "chrisai",
  "level": "project",
  "parent_ref": { "type": "project", "id": "PRJ-045" },
  "target": "Generate $2k from blog in Q2 2026",
  "metric": {
    "type": "revenue",
    "value": 2000,
    "unit": "USD",
    "deadline": "2026-06-30",
    "current": 0
  },
  "status": "active",
  "created_at": "2026-04-14"
}
```

**Entity-with-goals-and-AC example (Project):**

```json
{
  "id": "PRJ-045",
  "name": "Blog Post Master",
  "goal_ids": ["GOAL-002", "GOAL-003"],
  "acceptance_criteria": [
    { "id": "AC-1", "condition": "All 12 Phase 1 posts published", "resolved": false, "resolved_by": null },
    { "id": "AC-2", "condition": "Avg post length > 2000 words", "resolved": false, "resolved_by": null },
    { "id": "AC-3", "condition": "UNIT-012 marked achieved", "resolved": false, "resolved_by": "UNIT-012" }
  ]
}
```

**Inheritance waterfall:**
- Every entity carries its own `goal_ids` array
- At read time, hooks walk parent chain and surface inherited Goals in a separate context block
- Own Goals take priority but must align with inherited Goals
- Owner of inherited Goal remains the original parent entity's assignee (reports_to chain preserved)

**Accountability cascade ("whatever it takes"):** Captured as **post-v1 orchestration behavior**. Schema supports it today:
- Members can create Units (not just receive them)
- Units reference parent Project → inherit Goal
- Gap detection runs when Goal metric is off-pace
- Owner Member spawns sub-Units with their own Goals + AC until parent Goal closes

First 3 Members driven manually; autonomous behavior deferred.

**Storage:**
- `.firm/goals.json` — Goal records
- AC lives inline on parent entity files

---

### ✅ Entity 4: Operation

**Decision:** NEW entity. Built fresh, parallel to existing BASE Initiatives. No migration yet.

**What it is:** An ongoing business function or strategic theme. Sits between Firm and Project. Not time-bound — perpetual unless retired.

**Examples:**

| Operation (ongoing) | Projects under it (bounded) |
|---|---|
| Blog Content Engine | Blog Post Master, Blog Repurpose |
| Community Building | Skool Recalibration, FZTD Course |
| Client Delivery | CaseGate, Hunter Exotics, Occupancy Lift |
| Product Development | PAUL, BASE, CARL, Extension Architecture |

**Design choices locked:**

| Question | Decision |
|---|---|
| Status lifecycle | `active | paused | retired` |
| Can stand alone (zero Projects)? | Yes |
| Must have a Goal? | Optional — allows exploratory Operations |
| Migration of existing INI-* | Deferred. Build OPS fresh in parallel. Reference INI-* to inform design only. |
| Owner model | Every Operation has `owner_member_id`. Null defaults to Board. |

**v1 Schema:**

```json
{
  "id": "OPS-001",
  "firm_id": "chrisai",
  "name": "Blog Content Engine",
  "description": "Ongoing content engine — publishing, repurposing, strategy.",
  "owner_member_id": null,
  "status": "active",
  "goal_ids": [],
  "acceptance_criteria": [],
  "priority": "high",
  "category": "content",
  "project_ids": [],
  "created_at": "2026-04-14"
}
```

**Storage:** `.firm/operations.json`

### ✅ Entity 5: Project

**Decision:** NEW entity. Built fresh, parallel to existing BASE Projects. No migration yet. Fresh-wins principle applies.

**What it is:** A bounded deliverable that serves an Operation. Time-bound, has a concrete endpoint, contains Units.

**Design choices locked:**

| Question | Decision |
|---|---|
| Status lifecycle | `in_progress | blocked | paused | in_review | done | cancelled` (no backlog — pre-commitment lives elsewhere) |
| Can exist without Operation? | No — `operation_id` required. Pre-commitment stays in SEED/ideation space. |
| Due date required? | Yes — required. Extensible if initial estimate is wrong. Differentiates Project from Operation. |
| ID prefix | `PROJ-*` during parallel-build phase. `firm_id` scoped in payload. |
| Owner model | Single `owner_member_id`. Contributors surface via Unit assignments. |

**v1 Schema:**

```json
{
  "id": "PROJ-001",
  "firm_id": "chrisai",
  "operation_id": "OPS-001",
  "name": "Blog Post Master v1",
  "description": "Build research-driven blog pipeline. 12 posts published, installer working, templates dialed.",
  "owner_member_id": "MEM-002",
  "status": "in_progress",
  "goal_ids": ["GOAL-003"],
  "acceptance_criteria": [],
  "unit_ids": [],
  "priority": "high",
  "due_date": "2026-05-31",
  "tags": ["content", "paul-project"],
  "created_at": "2026-04-14"
}
```

**Storage:** `.firm/projects.json`

**Key principle applied:** Backlog is not a Project state. Pre-commitment ideas live in SEED or similar ideation surface. A record becomes a Project at the moment of commitment (status starts at `in_progress` or `paused`).

---

### ✅ Entity 6: Unit

**Decision:** NEW entity. Atomic work unit, single-assignee, Project-scoped.

**Design choices locked:**

| Question | Decision |
|---|---|
| Status lifecycle | `pending | in_progress | blocked | in_review | done | cancelled` |
| Assignee model | Single assignee, enforced atomic checkout. No reassignment while `in_progress`. |
| Parent requirement | `project_id` required. No orphan Units. |
| Sub-Units | Allowed, 1 level deep max. Both Units and sub-Units get AC. Sub-Units inherit parent Unit's Goal chain (no new Goal level). |
| Dependencies | `depends_on: [UNIT-IDs]` with hard gating — cannot enter `in_progress` until all deps are `done`. Cycle detection required. `cancelled` deps do not auto-unblock. |
| Due date | Inherit from parent Project. Override supported. |
| Priority | Hybrid — categorical (`urgent | high | medium | low`) + decimal stack rank within bucket for deterministic AI ordering. Decimal-point insertion avoids cascade rewrites. |
| ID prefix | `UNIT-*` for Units, `SUB-*` for sub-Units (globally scoped). |
| Outputs | Structured `outputs: []` array of produced artifacts (file, url, commit, etc.) for downstream Units to consume. |

**v1 Schema:**

```json
{
  "id": "UNIT-001",
  "firm_id": "chrisai",
  "project_id": "PROJ-001",
  "parent_unit_id": null,
  "name": "Write post #14 on MCP patterns",
  "description": "Long-form blog post covering MCP bridge protocol and practical setup.",
  "assignee_member_id": "MEM-002",
  "status": "pending",
  "priority": "high",
  "rank": 2.0,
  "goal_ids": [],
  "acceptance_criteria": [
    { "id": "AC-1", "condition": "Min 1500 words", "resolved": false, "resolved_by": null },
    { "id": "AC-2", "condition": "CTA placed", "resolved": false, "resolved_by": null }
  ],
  "depends_on": [],
  "due_date": null,
  "outputs": [],
  "tags": ["blog", "content"],
  "created_at": "2026-04-14"
}
```

**Sort order for AI work-picking:**
1. Filter: `status: pending`, `depends_on` all `done`, assignee matches (or claimable)
2. Sort: priority weight descending (urgent → low), then `rank` ascending within bucket

**Storage:** `.firm/units.json` (proposed). Sub-Units in same file, distinguished by `parent_unit_id` non-null.

---

### ✅ Entity 7: Comment

**Decision:** NEW entity. Polymorphic — attaches to ANY entity via `parent_ref` (same pattern as Goal). Vocabulary updated from "Unit Comment" to just "Comment."

**Design choices locked:**

| Question | Decision |
|---|---|
| Scope | Polymorphic via `parent_ref: {type, id}`. Can attach to Firm, Member, Operation, Project, Goal, Unit, or any entity. |
| Author model | Member OR Board. `author: {type: "member" | "board", id: "MEM-XXX" | null}` |
| Mutability | Immutable, append-only. `archived: true` flag possible but body never changes. |
| Threading | Flat + optional `in_reply_to: "COM-XXX"` (points to Comment ID, not Member ID). |
| ID prefix | `COM-*` (comments proliferate; brevity helps) |

**v1 Schema:**

```json
{
  "id": "COM-001",
  "firm_id": "chrisai",
  "parent_ref": { "type": "unit", "id": "UNIT-012" },
  "author": { "type": "member", "id": "MEM-002" },
  "in_reply_to": null,
  "body": "Draft complete. 1850 words. Handing off to EditorBot via SUB-002.",
  "archived": false,
  "created_at": "2026-04-14T15:20:00-05:00"
}
```

**Reply example:**
```json
{
  "id": "COM-002",
  "parent_ref": { "type": "unit", "id": "UNIT-012" },
  "author": { "type": "board", "id": null },
  "in_reply_to": "COM-001",
  "body": "Confirmed. Move forward with edit pass.",
  ...
}
```

**Rationale for polymorphic:** Goals already use `parent_ref`. Consistent pattern. Prevents "ghost Units" created only to hold discussion. AI Members can attach observations to the right entity (Goal, Operation, etc.) without ceremony.

**Storage:** `.firm/comments.json`

---

### ✅ Entity 8: Member Run

**Decision:** NEW entity. Records of Member execution sessions on Units.

**Design choices locked:**

| Question | Decision |
|---|---|
| Trigger scope | Only when a Member works on a Unit. Non-Unit invocations logged in Records instead. |
| Status lifecycle | `running | completed | failed | cancelled | timed_out` |
| Granularity | One Run per session. A Unit worked across 3 sessions = 3 Runs, grouped via `(member_id, unit_id)` queries. |
| ID prefix | `RUN-*` |

**v1 Schema:**

```json
{
  "id": "RUN-001",
  "firm_id": "chrisai",
  "member_id": "MEM-002",
  "unit_id": "UNIT-012",
  "sub_unit_id": null,
  "status": "completed",
  "started_at": "2026-04-14T14:30:00-05:00",
  "ended_at": "2026-04-14T14:48:12-05:00",
  "usage_event_ids": ["USE-014"],
  "outputs": [
    { "type": "file", "path": "content/blog/post-14.md" }
  ],
  "error": null,
  "notes": null,
  "created_at": "2026-04-14T14:30:00-05:00"
}
```

**Field notes:**
- `usage_event_ids` — references to Usage Event records (entity #9) tying this Run to its token/cost consumption.
- `outputs` — mirrors Unit's outputs field. A Run's outputs get rolled up to its Unit on completion.
- `error` — populated only when `status: failed`. Includes message + optional stacktrace.
- `sub_unit_id` — if the Run is scoped to a specific sub-Unit rather than the parent Unit.

**Storage:** `.firm/runs.json` (proposed). Can split to daily shards if file grows (e.g., `.firm/runs/2026-04.json`).

---

### ✅ Entity 9: Usage Event

**Decision:** NEW entity. Records resource consumption (monetary + subscription-based) tied to Member Runs.

**Design choices locked:**

| Question | Decision |
|---|---|
| Granularity | One Usage Event per Run. |
| Metric detail | Granular breakdown — tokens in/out, cache, model, plan, dollar equivalent, window percent. Mirrors ccusage output. |
| ID prefix | `USG-*` |

**v1 Schema:**

```json
{
  "id": "USG-001",
  "firm_id": "chrisai",
  "member_id": "MEM-002",
  "run_id": "RUN-001",
  "unit_id": "UNIT-012",
  "timestamp": "2026-04-14T14:48:12-05:00",
  "plan": "claude_pro_100",
  "model": "claude-opus-4-6",
  "tokens_in": 12400,
  "tokens_out": 3200,
  "cache_read_tokens": 8900,
  "cache_create_tokens": 0,
  "dollar_equivalent": 0.23,
  "window_percent_consumed": 5.2,
  "window_id": "2026-04-14T12:00:00-05:00"
}
```

**Field notes:**
- `plan` — one of `claude_pro_100 | claude_pro_200 | api | custom`
- `window_id` — for subscription plans, identifies the 5hr block this Run contributed to
- `window_percent_consumed` — null for API mode; populated for subscription plans
- `dollar_equivalent` — normalized monetary value regardless of plan (for cross-plan comparison)

**Storage:** `.firm/usage-events.json` (proposed). Likely sharded by month if volume grows.

**Integration note (parking lot):** Investigate `apps/ccusage/` at integration time. Its JSONL format should map directly into this schema via a parser.

---

### ✅ Entity 10: Gate

**Decision:** NEW entity. Decision checkpoints requiring Board (or delegated manager) sign-off before a Member can proceed.

**Design choices locked:**

| Question | Decision |
|---|---|
| Gate-requiring actions | Configurable per Member + sensible defaults. New Members ship with safe defaults; tune per role over time. |
| Status lifecycle | `pending | approved | rejected | expired | revoked` |
| Approver | Board OR reporting chain (any Member above requester in reports_to chain). Schema supports it; v1 reality is Board-only since hierarchy is flat. |
| ID prefix | `GATE-*` |

**v1 Schema:**

```json
{
  "id": "GATE-001",
  "firm_id": "chrisai",
  "requesting_member_id": "MEM-002",
  "action": "publish_post",
  "target_ref": { "type": "unit", "id": "UNIT-012" },
  "context": "Blog post #14 draft complete, AC resolved, ready for publish.",
  "status": "pending",
  "approver_ref": null,
  "approver_comment": null,
  "expires_at": "2026-04-15T18:00:00-05:00",
  "decided_at": null,
  "created_at": "2026-04-14T15:30:00-05:00"
}
```

**Field notes:**
- `action` — what the Member is requesting permission to do. Common values: `publish`, `close_project`, `exceed_budget`, `revise_goal`, `hire_member`, etc.
- `target_ref` — what entity the action applies to (polymorphic, same pattern as Goal/Comment).
- `approver_ref` — populated once someone decides. `{type: "board", id: null}` or `{type: "member", id: "MEM-XXX"}`.
- `expires_at` — if null, Gate never expires. Otherwise auto-transitions to `expired` at that time.

**Storage:** `.firm/gates.json`

---

### ✅ Entity 11: Records

**Decision:** NEW entity. Immutable audit trail of significant events in the Firm.

**Design choices locked:**

| Question | Decision |
|---|---|
| Triggers | Significant events only — entity creation, status transitions, ownership/assignment changes, Gate decisions, Goal updates. Not every minor field edit. |
| Retention | Sharded by month, older shards archived/offloaded to cold storage. |
| ID prefix | `LOG-*` |

**v1 Schema:**

```json
{
  "id": "LOG-001",
  "firm_id": "chrisai",
  "event_type": "unit.status_transition",
  "actor": { "type": "member", "id": "MEM-002" },
  "target_ref": { "type": "unit", "id": "UNIT-012" },
  "details": {
    "from": "pending",
    "to": "in_progress"
  },
  "run_id": "RUN-001",
  "timestamp": "2026-04-14T14:30:00-05:00"
}
```

**Field notes:**
- `event_type` — dotted string like `unit.status_transition`, `gate.approved`, `goal.metric_updated`, `member.hired`, `project.owner_changed`. Extensible.
- `actor` — who caused the event. Can be `member`, `board`, or `system` (for automated state transitions).
- `target_ref` — which entity the event applies to.
- `details` — event-specific payload. Shape varies by event_type.
- `run_id` — if the event happened during a Member Run, links back for context.

**Storage:**
- Hot: `.firm/records/YYYY-MM.json` (current and prior month)
- Cold: `.firm/records/archive/YYYY-MM.json.gz` (older months, compressed)

**Distinction from Comments:** Records = system-generated event logs. Comments = intentional human/Member communication. Don't conflate.

---

### ✅ Entity 12: Firm Secret

**Decision:** NEW entity. **Reference-only** — metadata lives in framework, actual secret values stay in `.env` / OS keychain.

**Design choices locked:**

| Question | Decision |
|---|---|
| Scope | Reference-only. Framework tracks which secrets exist and who uses them; values never enter `.firm/` files. |
| ID prefix | `KEY-*` |

**v1 Schema:**

```json
{
  "id": "KEY-001",
  "firm_id": "chrisai",
  "name": "SLACK_TOKEN",
  "description": "Bot token for Slack MCP integration. Chris Kahler's workspace.",
  "source": "env",
  "env_var_name": "SLACK_BOT_TOKEN",
  "used_by_member_ids": ["MEM-003"],
  "last_rotated_at": "2026-03-10",
  "rotation_cadence_days": 90,
  "notes": null,
  "created_at": "2026-04-14"
}
```

**Field notes:**
- `source` — `env | keychain | 1password | bitwarden` etc. Declares *where* the value lives.
- `env_var_name` — the actual env variable name if `source: env`. Members know to read `process.env[env_var_name]`.
- `used_by_member_ids` — dependency tracking. Rotate a key → surface affected Members.
- `rotation_cadence_days` — optional reminder cadence for rotation hygiene.

**Security invariant:** No secret value ever lives in `.firm/secrets.json`. If a value shows up there, it's a bug. Only metadata.

**Storage:** `.firm/secrets.json` (metadata only)

---

### ✅ Entity 13: Document

**Decision:** NEW entity. Metadata record in the framework pointing at a markdown file on disk. Same pattern as PAUL's STATE.md/PLANNING.md.

**Design choices locked:**

| Question | Decision |
|---|---|
| Storage model | Database entity (metadata) + file on disk (content). Content stays in proper `.md` files; framework tracks attribution and versioning. |
| ID prefix | `DOC-*` |

**v1 Schema:**

```json
{
  "id": "DOC-001",
  "firm_id": "chrisai",
  "parent_ref": { "type": "project", "id": "PROJ-050" },
  "type": "plan",
  "name": "Entity Design",
  "content_path": "projects/agent-company-architecture/ENTITY-DESIGN.md",
  "author": { "type": "board", "id": null },
  "version": 1,
  "status": "active",
  "created_at": "2026-04-14",
  "updated_at": "2026-04-14"
}
```

**Field notes:**
- `parent_ref` — polymorphic attachment (any entity can have Documents).
- `type` — free-form string. Common values: `plan`, `design`, `notes`, `spec`, `handoff`, `research`, `chronicle`. Extensible.
- `content_path` — relative path to the markdown file.
- `version` — incremented on significant revisions. Members can reference specific versions for consistency.
- `status` — `active | archived | deprecated`

**Storage:** `.firm/documents.json` (metadata). Content in various `.md` files across the workspace.

---

### ✅ Entity 14: Contract

**Decision:** NEW entity. Runtime execution interface for Members. Formal 3-method interface from day one — framework is released publicly, multi-runtime support is table stakes (users bring OpenClaw, Codex, Cursor, etc.).

**Design choices locked:**

| Question | Decision |
|---|---|
| Interface formality | Formal 3-method interface: `invoke(member, context)`, `status(run)`, `cancel(run)`. Runtime-agnostic. |
| ID prefix | `CON-*` |

**v1 Schema:**

```json
{
  "id": "CON-001",
  "firm_id": "chrisai",
  "name": "ScribeBot Runtime",
  "member_id": "MEM-002",
  "runtime_type": "claude_code",
  "runtime_config": {
    "model": "claude-opus-4-6",
    "working_dir": "/home/chriskahler/chris-ai-systems",
    "entry_command": "/agent:scribebot"
  },
  "skill_loadout": ["humanizer", "blog:write", "blog:repurpose"],
  "domain_loadout": ["content"],
  "created_at": "2026-04-14"
}
```

**Field notes:**
- `runtime_type` — declares which runtime handler to use. Values: `claude_code | openclaw | codex | cursor | api_direct | custom`. Framework has a registry mapping `runtime_type` → handler module implementing the 3-method interface.
- `runtime_config` — opaque blob the runtime handler understands. Shape varies by runtime.
- `skill_loadout` / `domain_loadout` — context pre-load when Member runs via this Contract.
- `member_id` — which Member this Contract serves. A Member can have multiple Contracts (e.g., dev Contract vs prod Contract).

**Interface contract (handler modules must implement):**
```
invoke(member, context) → Promise<RunId>
status(run_id) → Promise<RunStatus>
cancel(run_id) → Promise<void>
```

**Swappability story:** To run ScribeBot on OpenClaw instead of Claude Code:
1. Create new Contract with `runtime_type: "openclaw"` and appropriate `runtime_config`
2. Update Member's `contract_id` to point at new Contract
3. No changes to Member identity, Units, Goals, or anything else

**Storage:** `.firm/contracts.json`

---

## 🎉 All 14 Entities Locked

Vocabulary complete. Schemas defined. Design principles established.

**Concrete instance data** (first Firm, first Member, first Operation, first Goals, first Projects) lives in `MEMBERS-DESIGN.md` — this doc stays pure schema.

---

## Parking Lot (Decisions Deferred)

- **Session tagging for per-Member usage attribution** — needed before budget enforcement goes live. Investigate ccusage integration at `apps/ccusage/`.
- **Board stand-in Member generation** — convention, not v1 primitive. Add when needed for attribution.
- **Content pillar implications** — "I run a Firm of AI Members" as narrative. Revisit once first 3 Members are operational.
- **AC library extraction** — when patterns dial in (e.g., standard blog post AC template), extract AC into reusable library. Not v1.
- **Autonomous gap-closing behavior** — "whatever it takes" cascade. Goal owner spawns sub-Units until Goal metric closes. Post-v1 orchestration layer; schema supports it today.
- **Goal metric auto-refresh** — integrate with data sources (GA, Stripe, ccusage, revenue tracking) to pull `current` automatically. v1 is manual.
- **Reserved Member names (for future splits):** Echo (blog-to-social repurposing), Harbor (longform video production). Activate when scope pressure demands splits from Quill.
- **Post-v1 buildout:** MCP tool surface, hook architecture specifics (session-pulse activation), storage directory layout within `.firm/`, relationship-to-BASE decision point.
