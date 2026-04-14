# Agent Company Architecture

> A standalone framework for orchestrating a company of AI Members — Paperclip-inspired, released publicly, runtime-agnostic by design.

**Created:** 2026-04-13
**Type:** Application
**Stack:** JSON file store + Python hooks + Claude Code skills + MCP (future) + optional runtime adapters (Claude Code, OpenClaw, Codex, etc.)
**Skill Loadout:** ui-ux-pro-max (CLI/terminal output), /paul:audit (end of each milestone), humanizer (for release docs)
**Quality Gates:** schema validation on all entity writes, dependency cycle detection, atomic Unit checkout enforcement

---

## Problem Statement

Solo operators and small teams building with Claude Code have no structured way to treat AI workflows as a company. They either:
- Hand-drive every session (Chris's current reality — every task implicitly routes to the operator)
- Use task trackers (Linear, Notion) designed for humans, not for AI-orchestrated work
- Copy Paperclip (open-source, 53k stars) — but Paperclip is a control plane for multi-operator orgs with 24/7 cron heartbeats, not a fit for solo-operator business-hours reality

**The gap:** A framework that encodes the autonomous-team mental model — Members with roles, Operations they own, Goals they pursue, Units of atomic work, Gates the Board decides on — scoped for solo-to-small operators, built for Claude Code but designed to swap to OpenClaw / Codex / other runtimes.

**Who it's for:** Chris first (solo operator running ChrisAI firm). Released publicly so other solo builders can install and adapt.

**Why build vs buy:** Paperclip's model is right but doesn't fit solo reality. Linear/Notion are human-centric. No existing tool treats AI Members as first-class entities with reports_to chains, swappable runtimes, and autonomous gap-identification.

---

## Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Data store | JSON files in `.firm/` | Git-friendly, human-readable, no DB to host. Matches BASE's model. |
| Hooks | Python | Matches existing Claude Code hook ecosystem |
| Skills | Claude Code skills + slash commands | Native integration; `/member:create`, `/unit:checkout`, `/gate:request`, etc. |
| MCP server | Node.js or Python (TBD) | Programmatic entity access for Members and external tools |
| Runtime adapters | Formal 3-method interface (invoke, status, cancel) | Swappable — Claude Code today, OpenClaw / Codex / Cursor later |

### Research Needed
- ccusage integration surface for Usage Event attribution (see `apps/ccusage/`)
- OpenClaw adapter implementation patterns (when their public API stabilizes)
- Hook-level injection format: how `<active-roster>`, `<pending-gates>`, `<goal-health>` tags inject into active-awareness

---

## Data Model

14 entity types — full schemas in `ENTITY-DESIGN.md`. Quick reference:

| Entity | Prefix | Purpose |
|--------|--------|---------|
| Firm | (scalar id) | Top-level container |
| Member | `MEM-*` | Named worker with role, contract, skill loadout |
| Goal | `GOAL-*` | Measurable metric attached to any entity via `parent_ref` |
| Operation | `OPS-*` | Ongoing business function |
| Project | `PROJ-*` | Bounded deliverable under an Operation |
| Unit | `UNIT-*` / `SUB-*` | Atomic work, single-assignee with atomic checkout |
| Comment | `COM-*` | Polymorphic, immutable, flat with optional replies |
| Member Run | `RUN-*` | Per-session execution record |
| Usage Event | `USG-*` | Granular consumption data (tokens, window %, cost) |
| Gate | `GATE-*` | Board-approval checkpoints |
| Records | `LOG-*` | Immutable audit trail, monthly sharding |
| Firm Secret | `KEY-*` | Reference-only metadata; values stay in env/keychain |
| Document | `DOC-*` | Metadata record + file on disk |
| Contract | `CON-*` | Runtime execution interface (3-method contract) |

### Relationship Notes
- Polymorphic `parent_ref` pattern (Goal, Comment) — attach to any entity
- Single-assignee with atomic checkout on Unit (prevents collision)
- Hard-gated dependencies (`depends_on`) on Units
- Goal inheritance cascades via parent chain (computed, not stored)

---

## API Surface

### Slash Command Surface (primary operator interface)

| Command | Purpose |
|---------|---------|
| `/firm:init` | Bootstrap a new Firm |
| `/member:create`, `/member:update` | Manage Members |
| `/member:run <name> <directive>` | Invoke any Member |
| `/quill:run <stage>` | Quill-specific dispatch (per-Member commands) |
| `/operation:create`, `/operation:update` | Manage Operations |
| `/project:create`, `/project:update` | Manage Projects |
| `/unit:create`, `/unit:checkout`, `/unit:complete` | Manage Units |
| `/gate:request`, `/gate:decide` | Gate lifecycle |
| `/goal:update` | Update metric.current |
| `/firm:status` | Comprehensive health check |

### MCP Surface (post-v1)

Tools: `firm_create_member`, `firm_list_units`, `firm_transition_unit`, `firm_record_run`, `firm_log_usage`, etc. — programmatic access for Members to manipulate framework state during their Runs.

### Hook Surface

| Hook | Fires on | Injects |
|------|----------|---------|
| `session-pulse` | SessionStart | `<active-roster>`, `<pending-gates>`, `<goal-health>` |
| `unit-completion` | Unit status → done | Records log entry, updates Project AC |
| `run-record` | Member run end | Writes RUN entity + USG entity |

---

## Deployment Strategy

### Local Development (Chris's workspace)
Framework lives at `/home/chriskahler/chris-ai-systems/apps/agent-company-architecture/` after graduation. Data at `chris-ai-systems/.firm/`. Hooks in `.claude/hooks/`.

### Public Release
- Installer script: clones framework into target workspace, writes `.firm/` scaffold, registers hooks
- Supports multiple runtime adapters out of the box (Claude Code native; OpenClaw, Codex as optional)
- Users bring their own Firm (customize `firm.json`), define their own Members, pick their runtime

### Not in scope
- Hosted service, dashboard UI, SaaS layer — CLI + hooks + active-awareness is the entire UX

---

## Security Considerations

- **Firm Secret entity:** metadata-only. Actual secret values NEVER enter `.firm/` files. Live in `.env` / OS keychain.
- **Atomic Unit checkout:** SQL-like guard prevents two Members from claiming the same Unit simultaneously
- **Gate enforcement:** configurable per-Member actions require Board (or delegated manager) approval before execution
- **Records immutability:** append-only, monthly sharded — audit trail can't be silently rewritten
- **Member run attribution:** all Runs linked to a Member ID for accountability and budget tracking
- **No secrets in Records or Comments:** any logged payload strips credential-shaped data

---

## UI/UX Needs

### Interface
CLI / terminal only. No web dashboard. Active-awareness injection + slash commands IS the UX.

### Primary surfaces
- `<active-roster>` tag in session context (like `<active-awareness>` in BASE)
- `<pending-gates>` tag surfacing Gates awaiting Board decision
- `<goal-health>` tag surfacing Operation/Project goal status
- Slash command output (structured, compact)

### Design System
No design system — pure text output. Future consideration: a Remotion-rendered weekly summary video or TUI dashboard (deferred).

---

## Integration Points

| Integration | Type | Purpose |
|-------------|------|---------|
| Claude Code | Runtime adapter (default) | Primary Member execution runtime |
| OpenClaw | Runtime adapter (future) | Alternative execution runtime |
| ccusage | Usage Event ingestion | Parse ccusage JSONL → Usage Event records |
| blog-post-master (apps/) | Skill loadout for Quill | Quill's `/blog:*` skills come from here |
| BASE | Reference only (no data share) | Separate system; framework operates independently |

---

## Phase Breakdown

**Phase 1 — Schema + Storage Layer**
- Build: JSON schemas for all 14 entities, CRUD operations, `.firm/` directory scaffold
- Testable: create/read/update/delete each entity type, schema validation rejects malformed records
- Outcome: can manually populate `.firm/` with the locked Quill + Sterling + Sage roster

**Phase 2 — Hook Layer**
- Build: session-pulse hook, injection format for active-awareness, unit-completion hook, run-record hook
- Testable: opening a Claude Code session injects active roster; completing a Unit writes a Record
- Outcome: Firm context visible in every session; actions auto-logged

**Phase 3 — Core Slash Commands**
- Build: all `/member:*`, `/operation:*`, `/project:*`, `/unit:*`, `/gate:*`, `/goal:*` commands
- Testable: full Member lifecycle runnable via commands
- Outcome: operator can orchestrate entire Firm from slash commands

**Phase 4 — Quill End-to-End**
- Build: `/quill:run <stage>` command, CON-001 wiring, full pipeline integration with blog-post-master
- Testable: Quill produces a blog post end-to-end via `/quill:run full` on a Unit
- Outcome: first Member fully operational; Project #1 can begin

**Phase 5 — Leadership Layer**
- Build: Sterling + Sage Contracts, reports_to chain enforcement, delegation flows (Sterling queues Units for Quill)
- Testable: Sterling can assign a Unit to Quill; Sage can surface pillar opportunities
- Outcome: Chris operates at Board level; team handles day-to-day

**Phase 6 — MCP Server**
- Build: MCP server exposing programmatic entity access (firm-mcp)
- Testable: Members can create/update entities from within their Runs
- Outcome: autonomous behavior — Sterling can spawn Units, Sage can propose topics, all via MCP

**Phase 7 — Gap Detection**
- Build: Sterling's Member gap identification — heuristics that surface "we need X role" based on Goal health + Unit patterns
- Testable: when a pattern matches, Sterling proposes a hire Gate to Board
- Outcome: team grows itself under Board direction

**Phase 8 — Public Release**
- Build: installer script, public docs, runtime adapter templates (Claude Code, stub OpenClaw, stub Codex)
- Testable: fresh-workspace install works for a non-Chris user
- Outcome: framework shippable; others can install and run their own Firm

---

## Skill Loadout & Quality Gates

### Skills Used During Build

| Skill | When It Fires | Purpose |
|-------|--------------|---------|
| ui-ux-pro-max | Phases involving CLI output / injection format | Keep terminal output clean and scannable |
| /paul:audit | End of each milestone | Architecture review |
| humanizer | Writing public-facing docs (Phase 8) | Avoid AI-tell release docs |

### Quality Gates

| Gate | Threshold | When |
|------|-----------|------|
| Schema validation | All entity writes pass schema | Every phase |
| Dependency cycle detection | No cycles on Unit `depends_on` | Phase 3+ |
| Atomic checkout verified | Two parallel Members can't claim same Unit | Phase 3 |
| Hook injection correctness | Tags appear in session context as specified | Phase 2 |
| End-to-end Quill run | Blog post actually publishes | Phase 4 |

---

## Design Decisions

Full details in `ENTITY-DESIGN.md`. Summary of key decisions:

1. **Build standalone, not BASE-extension** — framework is independent of BASE/CARL/PAUL. Separate folder (`.firm/`), separate hooks, separate MCP.
2. **Business-oriented vocabulary** — Firm, Member, Operation, Project, Unit, Goal, Gate, Records, Firm Secret, Document, Contract. No medieval/nautical metaphors.
3. **Identity / runtime split** — Member holds identity; Contract holds runtime config. Swappable by design.
4. **Firm-scoped from day one** — every entity carries `firm_id`. Default `"chrisai"`. Enables later multi-Firm (e.g., C&C co-owned) at ~1-2 hr migration cost.
5. **Pulse activation, not heartbeat** — session-start hook fires Members. No 24/7 cron polling.
6. **Polymorphic modifiers** — Goal and Comment both attach to any entity via `parent_ref`. Consistent pattern.
7. **Immutable Comments and Records** — append-only. Audit trail can't be silently rewritten.
8. **Formal 3-method Contract interface** — `invoke`, `status`, `cancel`. Runtime-agnostic from v1.
9. **Hard-gated Unit dependencies** — Units can't enter `in_progress` until all `depends_on` are `done`.
10. **Hybrid priority system** — categorical bucket (urgent/high/medium/low) + decimal stack rank for deterministic AI ordering.
11. **Earn-the-pace throughput rule** — cadence bumps must be earned by prior Project performance.
12. **Board = yes/no authority** — NOT scope-definer. The team runs the firm; Board approves/rejects.
13. **Quill USES blog skills, isn't blog skill** — Members are personas that invoke skills, not the skills themselves.
14. **Fresh wins** — when framework design conflicts with BASE conventions, build what makes framework proper.

---

## Open Questions

1. Storage layout inside `.firm/` — single monolithic files per entity type (`.firm/members.json`) vs per-entity directories (`.firm/members/MEM-001.json`)?
2. Hook injection format specifics — what exactly goes in `<active-roster>` vs `<goal-health>` vs `<pending-gates>`?
3. MCP server language — Node.js or Python?
4. ccusage integration surface — when does the parser run? On session end? Periodically? Hook-triggered?
5. Does the framework need its own name (`firm` too generic) before public release, or is the repo name `agent-company-architecture` fine?
6. How much of blog-post-master gets ported into `apps/agent-company-architecture/` vs referenced externally?
7. Post-v1 autonomous gap-detection heuristics — what patterns trigger "we need a new Member"?
8. Relationship-to-BASE decision point — if framework matures, does BASE get retired, merged, or kept separate permanently?

---

## Next Actions

- [ ] Graduate to `apps/agent-company-architecture/` via `/seed:tasks:launch`
- [ ] Run `/paul:init` in the graduated directory
- [ ] Phase 1: schema + storage layer build begins

---

## References

- `LANDSCAPE.md` — Paperclip research artifact, mapped entities to Chris AI Systems stack
- `ENTITY-DESIGN.md` — authoritative schemas for all 14 entities, full decision history
- `MEMBERS-DESIGN.md` — concrete Quill/Sterling/Sage roster, PROJ-001, AC, Contracts
- `z-dump/references/paperclip/` — reference clone of Paperclip for architectural signals
- `apps/blog-post-master/` — existing blog pipeline Quill will use
- `apps/ccusage/` — consumption tracking Chris intends to integrate

---

*Last updated: 2026-04-14*
