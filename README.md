# Agent Company Architecture

> A standalone framework for orchestrating a company of AI Members - Paperclip-inspired, released publicly, runtime-agnostic by design.

**Type:** Application
**Stack:** SQLite (`.firm/firm.db`) + Python (core / hooks / MCP server) + Claude Code skills + pluggable runtime adapters
**Skill Loadout:** ui-ux-pro-max, /paul:audit, humanizer
**Quality Gates:** schema validation, dependency cycle detection, atomic Unit checkout (via SQL `UPDATE ... WHERE claimed_by IS NULL`)

## Install (development)

```bash
cd apps/agent-company-architecture
pip install -e ".[dev]"
python -m firm --help
python -m firm init /path/to/workspace   # creates .firm/firm.db
pytest
```

The package name is `firm` internally. Subject to rename at Phase 8 before public release.

---

## Overview

Solo operators and small teams building with Claude Code have no structured way to treat AI workflows as a company. Paperclip (53k stars) validates the conceptual model but is scoped for multi-operator orgs with 24/7 cron heartbeats. This framework encodes the autonomous-team mental model - Members with roles, Operations they own, Goals they pursue, Units of atomic work, Gates the Board decides on - scoped for solo-to-small operators, built for Claude Code but designed to swap to OpenClaw / Codex / other runtimes.

**First Firm:** ChrisAI - Chris Kahler's personal AI-operated firm. Framework released publicly so other solo builders can install and adapt.

---

## Architecture

14 entity types with formal schemas. Full detail in design docs.

| Entity | Prefix | Purpose |
|--------|--------|---------|
| Firm | scalar id | Top-level container |
| Member | `MEM-*` | Named worker with role, contract, skill loadout |
| Goal | `GOAL-*` | Measurable metric attached via `parent_ref` |
| Operation | `OPS-*` | Ongoing business function |
| Project | `PROJ-*` | Bounded deliverable |
| Unit | `UNIT-*` / `SUB-*` | Atomic work, single-assignee atomic checkout |
| Comment | `COM-*` | Polymorphic, immutable, flat + reply |
| Member Run | `RUN-*` | Per-session execution record |
| Usage Event | `USG-*` | Granular consumption data |
| Gate | `GATE-*` | Board-approval checkpoints |
| Records | `LOG-*` | Immutable audit trail |
| Firm Secret | `KEY-*` | Reference-only metadata; values in env/keychain |
| Document | `DOC-*` | Metadata record + file on disk |
| Contract | `CON-*` | Runtime execution interface (invoke/status/cancel) |

### Key Design Principles

1. **Standalone** - independent of BASE/CARL/PAUL. Separate folder (`.firm/`), hooks, MCP.
2. **Identity / runtime split** - Member (identity) and Contract (runtime) separable. Swappable runtimes.
3. **Firm-scoped from day one** - `firm_id` on every entity. Supports multi-Firm later.
4. **Pulse activation** - session-start hooks fire Members. No 24/7 heartbeats.
5. **Polymorphic modifiers** - Goal and Comment both use `parent_ref` pattern.
6. **Immutable append-only** - Comments and Records never rewritten.
7. **Formal 3-method Contract interface** - runtime-agnostic from v1.
8. **Hard-gated dependencies** - Units can't run until `depends_on` is done.
9. **Hybrid priority** - categorical bucket + decimal stack rank for deterministic AI ordering.
10. **Board = yes/no authority** - team runs the firm; Board approves/rejects.

---

## Data Model

Full entity schemas in `ENTITY-DESIGN.md` (migrated from projects/ during graduation - see References).

Storage: single monolithic JSON files per entity type in `.firm/` (open question: revisit if files grow unwieldy).

---

## API Surface

### Slash Commands (primary operator interface)

- `/firm:init` - bootstrap
- `/member:create`, `/member:update`, `/member:run <name>` - Members
- `/quill:run <stage>` - per-Member dispatch pattern
- `/operation:*`, `/project:*`, `/unit:*`, `/gate:*`, `/goal:*` - entity lifecycles
- `/firm:status` - comprehensive health check

### Hooks

- `session-pulse` - injects `<active-roster>`, `<pending-gates>`, `<goal-health>`
- `unit-completion` - Records log + Project AC update
- `run-record` - writes RUN + USG entities

### MCP Surface (post-v1)

Programmatic entity access for Members and external tools.

---

## Deployment

**Local:** Framework at `apps/agent-company-architecture/`. Data at workspace's `.firm/`. Hooks register into `.claude/hooks/`.

**Public Release:** Installer script clones framework, scaffolds `.firm/`, registers hooks. Users bring their own Firm + customize Members + choose runtime.

Not in scope: hosted service, web dashboard, SaaS layer. CLI + hooks + active-awareness is the UX.

---

## Security

- Firm Secrets are metadata-only; values stay in env/keychain
- Atomic Unit checkout prevents Member collision
- Gate enforcement for Board-required actions
- Immutable Records for audit integrity
- All Member Runs linked to Member ID for attribution

---

## Integration Points

| Integration | Type | Purpose |
|-------------|------|---------|
| Claude Code | Runtime adapter (default) | Primary Member execution |
| OpenClaw | Runtime adapter (future) | Alternative runtime |
| ccusage | Usage Event ingestion | Parse JSONL → USG entities |
| blog-post-master (apps/) | Skill loadout source for Quill | `/blog:*` skills Quill dispatches to |
| BASE | Reference only | Separate system; no data shared |

---

## Implementation Phases

**Phase 1 - Schema + Storage Layer.** Build entity schemas + CRUD, `.firm/` scaffold. Testable: create/read/update/delete each entity. Outcome: manually populate `.firm/` with Quill/Sterling/Sage roster.

**Phase 2 - Hook Layer.** session-pulse, injection tags, unit-completion, run-record hooks. Testable: session injects active roster; Unit completion writes Record. Outcome: Firm context visible every session.

**Phase 3 - Core Slash Commands.** All entity lifecycle commands. Testable: full Member lifecycle via commands. Outcome: operator orchestrates from slash commands.

**Phase 4 - Quill End-to-End.** `/quill:run <stage>`, CON-001 wiring, blog-post-master integration. Testable: Quill produces a post end-to-end. Outcome: first Member operational.

**Phase 5 - Leadership Layer.** Sterling + Sage Contracts, reports_to enforcement, delegation flows. Testable: Sterling assigns Unit to Quill; Sage surfaces opportunities. Outcome: Chris operates at Board level.

**Phase 6 - MCP Server.** Programmatic entity access. Testable: Members create/update entities during Runs. Outcome: autonomous team behavior unlocked.

**Phase 7 - Gap Detection.** Sterling's Member gap identification - heuristics surface "we need X role" Gates. Outcome: team grows itself under Board direction.

**Phase 8 - Public Release.** Installer, public docs, runtime adapter templates. Testable: fresh-workspace install works. Outcome: framework shippable externally.

---

## Skill Loadout & Quality Gates

| Skill | When | Purpose |
|-------|------|---------|
| ui-ux-pro-max | CLI output / injection format phases | Clean terminal output |
| /paul:audit | End of each milestone | Architecture review |
| humanizer | Phase 8 public docs | Avoid AI-tell release docs |

| Gate | Threshold | When |
|------|-----------|------|
| Schema validation | All entity writes pass schema | Every phase |
| Dependency cycle detection | No cycles on Unit `depends_on` | Phase 3+ |
| Atomic checkout verified | Two Members can't claim same Unit | Phase 3 |
| Hook injection correctness | Tags appear in session context as specified | Phase 2 |
| End-to-end Quill run | Blog post actually publishes | Phase 4 |

---

## Open Questions

1. Storage layout: monolithic JSON files or per-entity directories?
2. Hook injection format specifics
3. MCP server language (Node.js vs Python)
4. ccusage integration surface / timing
5. Framework public name (if rename needed before release)
6. blog-post-master - port or reference externally?
7. Gap-detection heuristics - what triggers "need a new Member"?
8. BASE relationship long-term

---

## Current Roster (First Firm: ChrisAI)

```
Board (Chris - yes/no authority)
  └─ Sterling (MEM-002, CMO) - owns OPS-001 Content Publishing
        ├─ Sage (MEM-003, Content Strategist)
        └─ Quill (MEM-001, Blog Author)
              └─ UNIT-000: "The CLAUDE.md Strategy" (retroactive, live on chrisai.cv)

Reserved future Member names: Echo (social repurposing), Harbor (video)
```

First Project: PROJ-001 "Quill Cadence - First 8 Posts," 2 posts/week, due 2026-05-12.

---

## References

- `PLANNING.md` - full planning artifact
- `ENTITY-DESIGN.md` - authoritative schemas, 14 entities, complete design decision history
- `MEMBERS-DESIGN.md` - concrete roster (Quill, Sterling, Sage), Operations, Goals, Projects, Contracts, Documents
- `LANDSCAPE.md` - Paperclip research artifact, entity mapping
- BASE project entry: PRJ-050 "Agent Company Architecture - Meta-Framework for Agent Orchestration"

---

*Graduated: 2026-04-14 from `projects/agent-company-architecture/`*
