# agent-company-architecture

## What This Is

A standalone framework for orchestrating a company of AI Members — Paperclip-inspired, released publicly, runtime-agnostic by design. Framework encodes the autonomous-team mental model (Members with roles, Operations they own, Goals they pursue, Units of atomic work, Gates the Board decides on) scoped for solo-to-small operators. Built for Claude Code but designed to swap to OpenClaw / Codex / other runtimes via a formal 3-method Contract interface.

## Core Value

Solo operators can treat their AI workflows as a company — with Members, Goals, and autonomous delegation — instead of hand-driving every session.

## Current State

| Attribute | Value |
|-----------|-------|
| Type | Application |
| Version | 0.0.0 |
| Status | Initializing |
| Last Updated | 2026-04-14 |

## Requirements

### Core Features

- 14 formal entity types with validated schemas (Firm, Member, Goal, Operation, Project, Unit, Comment, Member Run, Usage Event, Gate, Records, Firm Secret, Document, Contract)
- Session-pulse hooks that inject active roster, pending Gates, and Goal health into Claude Code sessions
- Slash command surface for all entity lifecycle operations (`/member:create`, `/unit:checkout`, `/gate:request`, etc.)
- Per-Member dispatch commands (e.g., `/quill:run <stage>`) that route to skill loadouts
- Formal 3-method Contract interface (invoke, status, cancel) for runtime swappability
- MCP server exposing programmatic entity access for Members to manipulate framework state

### Validated (Shipped)

None yet.

### Active (In Progress)

- [ ] Phase 1: Schema + Storage Layer (starting point)

### Planned (Next)

- Phase 2: Hook Layer (session-pulse, unit-completion, run-record)
- Phase 3: Core Slash Commands
- Phase 4: Quill End-to-End (first Member operational)
- Phase 5: Leadership Layer (Sterling + Sage Contracts)
- Phase 6: MCP Server (programmatic access)
- Phase 7: Gap Detection (Sterling identifies Member needs)
- Phase 8: Public Release (installer + docs)

### Out of Scope

- Hosted service, web dashboard, SaaS layer — CLI + hooks + active-awareness is the UX
- 24/7 heartbeat cron (pulse-based activation only)
- Multi-operator governance at v1

## Target Users

**Primary:** Chris Kahler — solo operator running ChrisAI firm. First Firm is `chrisai` with Members Quill (Blog Author), Sterling (CMO), Sage (Content Strategist).

**Secondary:** Other solo technical builders who want to run a named AI-operated firm with Members, Operations, and Goals. Eventually released publicly so users can bring their own Firm + customize Members + choose runtime.

## Context

**Business Context:** Framework is part of Chris AI Systems (personal brand). Content pillar: "I run a firm of AI Members." Released publicly as an open framework once battle-tested by Chris's internal use.

**Technical Context:** Lives in workspace at `apps/agent-company-architecture/`. Data at workspace-root `.firm/`. Hooks register into `.claude/hooks/`. Independent of BASE/CARL/PAUL conceptually but operates alongside them.

## Constraints

### Technical Constraints

- JSON file store — no database (git-friendly, no hosting)
- Python for hooks (ecosystem fit with Claude Code)
- Firm Secrets are metadata-only — actual values NEVER in `.firm/` files
- Atomic Unit checkout required (prevents Member collision)
- All Member Runs linked to Member ID for attribution
- Schema validation on every entity write

### Business Constraints

- Build parallel to existing BASE — no integration yet
- First use case (Quill + blog pipeline) must work end-to-end before Phase 8 public release
- Framework name may change before public release (current folder name is descriptive, not final brand)

### Compliance Constraints

- None (personal framework; no PII handled directly by framework itself)

## Key Decisions

| Decision | Rationale | Date | Status |
|----------|-----------|------|--------|
| Build standalone, not BASE-extension | Framework operates at different abstraction layer than BASE | 2026-04-14 | Active |
| Identity/runtime split (Member + Contract) | Swappable runtimes (Claude Code → OpenClaw → others) | 2026-04-14 | Active |
| Firm-scoped from day one | Enables multi-Firm later at ~1-2 hr migration cost | 2026-04-14 | Active |
| Pulse activation, not heartbeat | No 24/7 cron; session-start hooks fire Members | 2026-04-14 | Active |
| Polymorphic modifiers (Goal + Comment use parent_ref) | Consistent pattern, flexibility | 2026-04-14 | Active |
| Immutable Comments and Records | Audit trail can't be silently rewritten | 2026-04-14 | Active |
| Formal 3-method Contract interface | Runtime-agnostic from v1 (framework is being released publicly) | 2026-04-14 | Active |
| Hard-gated Unit dependencies | Real dependency enforcement, not soft suggestions | 2026-04-14 | Active |
| Hybrid priority (categorical + decimal stack rank) | Deterministic AI ordering + human-readable buckets | 2026-04-14 | Active |
| Earn-the-pace throughput rule | Prevents AI throughput creep | 2026-04-14 | Active |
| Board = yes/no authority, not scope-definer | Team runs the firm; Board approves/rejects | 2026-04-14 | Active |

## Success Metrics

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Schema validation | All entity writes pass schema | - | Not started |
| End-to-end Quill run | Blog post publishes via `/quill:run full` | - | Not started |
| Atomic checkout verified | Parallel Members can't claim same Unit | - | Not started |
| Framework installed on fresh workspace | Non-Chris user can install and run | - | Not started |

## Tech Stack / Tools

| Layer | Technology | Notes |
|-------|------------|-------|
| Data store | JSON files in `.firm/` | Git-friendly, human-readable |
| Hooks | Python | Matches existing Claude Code hook ecosystem |
| Commands | Claude Code skills + slash commands | Native integration |
| MCP server | Node.js or Python (TBD) | Programmatic entity access for Members |
| Runtime adapters | Formal 3-method interface | Claude Code default; OpenClaw/Codex/Cursor pluggable |

## Links

| Resource | URL |
|----------|-----|
| Repository | apps/agent-company-architecture/ (local, git initialized) |
| Source material | LANDSCAPE.md, ENTITY-DESIGN.md, MEMBERS-DESIGN.md, PLANNING.md |
| Reference | z-dump/references/paperclip/ (Paperclip reference clone) |

---
*Created: 2026-04-14*
*PROJECT.md — Updated when requirements or context change*
