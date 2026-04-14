# Roadmap: agent-company-architecture

## Overview

Build a standalone framework that treats AI workflows as a company. Journey: start with entity schemas and storage, build hooks for session-time integration, ship slash commands for entity lifecycle, get Quill (first Member) running end-to-end, layer in Sterling + Sage for autonomous delegation, add MCP for programmatic access, implement gap-detection heuristics, then release publicly.

## Current Milestone

**v0.1 Initial Release** (v0.1.0)
Status: Not started
Phases: 0 of 8 complete

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with [INSERTED])

Phases execute in numeric order: 1 → 2 → 2.1 → 2.2 → 3 → 3.1 → 4

| Phase | Name | Plans | Status | Completed |
|-------|------|-------|--------|-----------|
| 1 | Schema + Storage Layer | TBD | Not started | - |
| 2 | Hook Layer | TBD | Not started | - |
| 3 | Core Slash Commands | TBD | Not started | - |
| 4 | Quill End-to-End | TBD | Not started | - |
| 5 | Leadership Layer | TBD | Not started | - |
| 6 | MCP Server | TBD | Not started | - |
| 7 | Gap Detection | TBD | Not started | - |
| 8 | Public Release | TBD | Not started | - |

## Phase Details

### Phase 1: Schema + Storage Layer

**Goal:** All 14 entity types have formal schemas with validation. `.firm/` directory structure scaffolded. CRUD operations implemented for each entity.
**Depends on:** Nothing (first phase)
**Research:** Unlikely (entities fully designed in ENTITY-DESIGN.md)

**Scope:**
- JSON schemas for all 14 entities (Firm, Member, Goal, Operation, Project, Unit, Comment, Member Run, Usage Event, Gate, Records, Firm Secret, Document, Contract)
- `.firm/` directory scaffold generator
- CRUD operations per entity with schema validation on every write
- Storage layout decision (monolithic files vs per-entity directories)
- Dependency cycle detection for Unit `depends_on`

**Plans:** TBD during `/paul:plan`

### Phase 2: Hook Layer

**Goal:** Session-start hook injects active roster, pending Gates, and Goal health into Claude Code sessions. Unit completion hook writes Records. Run-record hook captures Member Runs + Usage Events.
**Depends on:** Phase 1 (needs schemas to read/write)
**Research:** Likely (hook injection format specifics — what goes in `<active-roster>` vs `<goal-health>` vs `<pending-gates>`)

**Scope:**
- `session-pulse` hook with injection tags
- `unit-completion` hook
- `run-record` hook
- Injection tag format design

**Plans:** TBD during `/paul:plan`

### Phase 3: Core Slash Commands

**Goal:** All entity lifecycle operations runnable from slash commands. Full Member/Operation/Project/Unit/Gate/Goal management via CLI.
**Depends on:** Phase 2 (commands integrate with hook-generated context)
**Research:** Unlikely

**Scope:**
- `/firm:init`, `/firm:status`
- `/member:*` commands (create, update, run)
- `/operation:*`, `/project:*`, `/unit:*`, `/gate:*`, `/goal:*` commands
- Atomic Unit checkout implementation

**Plans:** TBD during `/paul:plan`

### Phase 4: Quill End-to-End

**Goal:** Quill (MEM-001) produces a blog post end-to-end via `/quill:run full` on an assigned Unit. First Member fully operational.
**Depends on:** Phase 3 (needs commands for Unit lifecycle)
**Research:** Unlikely (blog-post-master pipeline already works)

**Scope:**
- `/quill:run <stage>` dispatch command
- CON-001 Contract wiring
- blog-post-master skill integration
- End-to-end validation with a real Unit (Project #1)

**Plans:** TBD during `/paul:plan`

### Phase 5: Leadership Layer

**Goal:** Sterling (CMO, MEM-002) and Sage (Content Strategist, MEM-003) are operational. Sterling queues Units for Quill; Sage surfaces pillar opportunities. reports_to chain enforced.
**Depends on:** Phase 4 (needs one Member proven before scaling)
**Research:** Unlikely

**Scope:**
- CON-002 and CON-003 Contracts
- reports_to chain enforcement in Gate/assignment flows
- Delegation pattern (Sterling → Quill via Unit assignment)
- Per-Member dispatch command pattern generalization

**Plans:** TBD during `/paul:plan`

### Phase 6: MCP Server

**Goal:** MCP server exposes programmatic entity access. Members can create/update entities during their Runs without slash commands.
**Depends on:** Phase 5 (needs leadership Members proven before giving programmatic power)
**Research:** Likely (MCP server language choice — Node.js vs Python)

**Scope:**
- MCP tool surface for all entity CRUD operations
- Firm-scoped permissions
- Language/runtime decision

**Plans:** TBD during `/paul:plan`

### Phase 7: Gap Detection

**Goal:** Sterling's Member gap identification — heuristics surface "we need X role" based on Goal health + Unit patterns. Sterling proposes hire Gates to Board autonomously.
**Depends on:** Phase 6 (needs Sterling able to mutate state via MCP)
**Research:** Likely (heuristic patterns — what actually signals a gap)

**Scope:**
- Gap-detection pattern library
- Sterling's hire-proposal flow
- Gate generation for new Member hires
- Board review UX

**Plans:** TBD during `/paul:plan`

### Phase 8: Public Release

**Goal:** Framework installable on a fresh workspace by a non-Chris user. Public docs, runtime adapter templates, example Firm.
**Depends on:** Phase 7 (autonomous behavior must be proven before external release)
**Research:** Likely (installer patterns, adapter template design for OpenClaw/Codex)

**Scope:**
- Installer script
- Public README + docs
- Runtime adapter templates (Claude Code reference impl, stub OpenClaw, stub Codex)
- Example Firm configuration for new users
- Final framework naming decision

**Plans:** TBD during `/paul:plan`

---
*Roadmap created: 2026-04-14*
*Last updated: 2026-04-14*
