# Cadre vs Paperclip

*Last updated: 2026-04-17*

Paperclip gets compared to Cadre a lot, so let's be explicit about the differences. This isn't a feature checklist — it's a category distinction. The two frameworks solve related but structurally different problems.

## TL;DR

**Paperclip** is a multi-operator control plane for a company of autonomous AI agents running 24/7 on cron heartbeats. Postgres, Node server, React dashboard, RBAC, approval workflows-as-database-state, plugin SDK. Enterprise-shaped.

**Cadre** is a local-first, session-activated framework for orchestrating an AI Firm inside your editor. SQLite, Python package, session-pulse hooks, no server process. Solo-operator-shaped.

They aren't the same product at different scales. They target different user types with different assumptions about what "running an AI company" means.

## The category distinction

| | Paperclip | Cadre |
|---|---|---|
| **Mental model** | Salesforce for AI companies | Git for AI companies |
| **User** | Multi-operator team | Solo builder |
| **Activation** | 24/7 heartbeat cron | Session-start pulse (wake when you work) |
| **Operator surface** | React dashboard at localhost:3100 | Claude Code session context (no separate UI) |
| **Persistence** | Postgres (embedded PGlite or hosted) | SQLite (stdlib, single file) |
| **Process model** | Persistent Node server + workers | Python package, no daemon |
| **Deployment** | Docker / hosted / self-hosted server | `pip install` |
| **Governance** | Approvals as DB state machine rows | Rules-as-code (CARL integration) + Gate entities |
| **Extension** | TypeScript plugin SDK, lifecycle hooks | Python Protocol, 3 methods |
| **Audit** | `activity_log` + `cost_events` + `heartbeat_run_events` + `budget_incidents` | Single immutable `Records` stream |
| **Work assignment** | Issue → Agent (assignee FK) | Unit → Member (atomic checkout, `UPDATE ... WHERE claimed_by IS NULL`) |
| **Multi-tenancy** | Native (`company_id` FK on every row) | Single Firm per install (1-2h migration path to multi) |
| **Tables / entities** | ~65 | ~14 |

## Entity model side-by-side

| Concept | Paperclip | Cadre | Notable difference |
|---|---|---|---|
| Org | `Company` (multi-tenant) | `Firm` (single) | PC isolates many orgs per install; Cadre is one Firm per DB |
| Agent | `Agent` (role, status, adapter_type + adapter_config JSONB) | `Member` + `Contract` (separate tables) | Cadre splits identity (Member) from runtime binding (Contract) at the schema layer |
| Work unit | `Issue` (parent/child hierarchy) | `Unit` (atomic checkout, hard-gated `depends_on`) | Cadre enforces dependency DAG at write time |
| Recurring work | `Routine` + `RoutineTrigger` (cron) | *Not modeled in v0.1 (pulse-activated instead)* | PC's cron is mature; Cadre inverts the whole premise |
| Approval | `Approval` (rich state machine) | `Gate` (pending/approved/rejected + payload) | Cadre's Gate is simpler; CARL rules handle the "when do we need approval" logic |
| Budget | `BudgetPolicy` + `BudgetIncident` (separate enforcement service) | *To be added as Member property, not a separate service* | PC: explicit budget subsystem. Cadre: cost-gate at invoke time |
| Cost tracking | `CostEvent` (per-model, per-token) | `UsageEvent` (generic) | PC: deep LLM accounting. Cadre: framework for it, not the default |
| Execution history | `HeartbeatRun` + `ExecutionWorkspace` + run events | `MemberRun` + immutable `Records` | PC: fragmented across several tables. Cadre: everything replays from `Records` |
| Skills | `CompanySkill` (uploaded via UI) | `Contract.skill_loadout` (list reference) | PC: mutable skill library. Cadre: skills are part of the Contract, versioned with it |
| Comments | `IssueComment` (scoped to issues) | `Comment` (polymorphic `parent_ref`) | Cadre's polymorphic refs mean new entities don't need new comment tables |
| Secrets | `CompanySecret` + `CompanySecretVersion` (rotation built in) | Env-bound via Contract config (metadata only) | PC: production secret store. Cadre: offload to env/keychain |

**Interpretation.** Paperclip's 65 tables are not bloat — they're what you need for a hosted, multi-tenant, audited, governed agent platform. Cadre's 14 tables aren't "lean" — they're what you need when one operator owns the whole thing and Records can serve as the unified truth stream.

## Architectural assumptions

### Scheduling

**Paperclip** runs a 24/7 in-process scheduler. `RoutineTrigger` rows specify cron expressions. The server polls, matches triggers, spawns agent invocations. Background workers execute. Cost events stream to `cost_events`. The scheduler never sleeps.

**Cadre** has no scheduler. The `session-pulse` hook fires at Claude Code `SessionStart:startup`. It injects `<active-roster>`, `<pending-gates>`, `<goal-health>` into the session context. Members activate when you open Claude Code, or when you explicitly run `firm pulse`. No daemon, no background CPU, no cloud bill.

**Why this matters.** Paperclip's premise is "your AI company runs around the clock." Cadre's premise is "your AI team wakes when you do." These are philosophically different products for different users. A solo builder doesn't want a 24/7 process watching their laptop battery. An enterprise with ops teams does.

### Storage

**Paperclip** requires Postgres. The "embedded" mode still runs PGlite (a WASM Postgres) as a subprocess. Prod deployments use managed Postgres or Supabase. Every table has `company_id` FK for multi-tenancy.

**Cadre** runs on SQLite via stdlib `sqlite3`. One file. ACID. Zero deployment. Atomic Unit checkout via single `UPDATE ... WHERE claimed_by IS NULL RETURNING *`. JSON export/import round-trips full Firm state for backup or portability.

**Why this matters.** Postgres is correct for Paperclip's shape (multi-tenant, concurrent writers, hosted). SQLite is correct for Cadre's shape (single operator, local, no infra). Forcing either framework into the other's storage would misshape it.

### Identity vs runtime

**Paperclip** stores agent runtime as `agent.adapter_type` + `agent.adapter_config` (JSONB). If you want to swap which runtime an agent uses, you mutate the agent row. `agent_config_revisions` tracks changes, but revisions are on the agent.

**Cadre** separates `Member` (identity: role, reports-to, name) from `Contract` (runtime binding: `runtime_type`, `skill_loadout`, `runtime_config`). Different tables, different IDs. Quill the Member is immutable; Quill's Contract can be swapped, versioned, or forked without touching Quill's identity.

**Why this matters.** If you want Quill on Claude Code this month and OpenClaw next month (cost, latency, whatever), Cadre's schema supports it directly — Quill gets a new Contract, keeps the same Member ID, keeps the same Records history. In Paperclip you'd mutate adapter_config and hope revisions capture what you need.

### Operator interface

**Paperclip** is a full React app (Vite, Radix UI, TanStack Query). You open a browser to localhost:3100, see what Agents are doing, click to create a task, click to approve an Approval. Elegant, discoverable.

**Cadre** injects roster + gates + goal health directly into Claude Code session context via the `session-pulse` hook. No browser, no separate app. The Firm state is already in your editor when you open it.

**Why this matters.** For a solo operator in their terminal all day, zero context-switch is a 10x UX difference. For a multi-operator team with non-engineer stakeholders, a browser dashboard is non-negotiable. Different users, different interfaces.

### Extension model

**Paperclip** plugins use a TypeScript SDK (`@paperclipai/plugin-sdk`). Plugins can register lifecycle hooks, custom entities, webhooks, UI pages. The plugin spec doc is ~65k characters. Comprehensive and powerful, but steep.

**Cadre** extensions are Python Protocol implementations. Want a new Contract runtime? Implement `invoke`, `status`, `cancel`. Register it. ~50 lines total. MCP tools are the other extension surface — language-agnostic over stdio.

**Why this matters.** Paperclip's plugin power serves enterprise customization. Cadre's trivially-small extension surface serves solo builders who want to glue things together in an afternoon.

### Governance

**Paperclip** governance is data-driven. An `Approval` row with type=`hire` sits pending until someone clicks approve in the UI. If you want different approval logic, you write a plugin or extend the schema.

**Cadre** governance is code-driven. CARL domain rules encode the policy ("HIRING domain requires Board.yes before agent_hire completes"). The rule is readable, versionable, composable. Gates are just the runtime state of a governance rule firing — not the source of truth for governance itself.

**Why this matters.** Rules-as-code scales better for solo operators who change their minds. "I trust Sterling to hire engineers but not marketing folk" → add a rule. In Paperclip that's a plugin.

## Paperclip's real strengths (worth respecting)

1. **Atomic work checkout with resumable session state.** Paperclip's `agent_task_sessions` table persists agent context across heartbeats. Worker wakes, resumes prior context, finishes. This is genuinely hard to get right.
2. **Goal ancestry tracing.** Every Issue traces back to Company mission via `parent_id` walk. Agents see *why*, not just the ticket.
3. **Budget enforcement at checkout time.** Cost is checked before task assignment. Over budget → task auto-paused, comment added. Solid pattern.
4. **Cron + timezone-aware routines.** `/server/src/services/routines.ts` handles 4-year search windows, leap years, DST. Mature code.
5. **Multi-runtime adapter ecosystem.** 8 production runtimes (claude-local, codex-local, cursor, gemini-local, openclaw-gateway, opencode, pi, http, process). Proves the runtime-agnostic model works.

Cadre should borrow some of these patterns selectively: goal ancestry surfacing in the pulse hook, cost-gating at invoke time, and eventually a `firm pulse --cron` scheduler. What Cadre should *not* borrow is the architectural weight those patterns come with in Paperclip.

## Paperclip's real frictions for solo operators

1. **Postgres is required, even in "embedded" mode.** PGlite subprocess with binary dependencies. Not zero-overhead.
2. **Server process is mandatory.** `pnpm dev` runs Express + React + workers. You can't just call a CLI and be done.
3. **Multi-company schema taxes single-operator use.** Every new entity needs `company_id`. 65+ tables. Isolation is correctness-preserving, not bloat, but it doubles schema overhead if you only have one Firm.
4. **Approval workflows block autonomy.** `approval.status = pending` halts execution until someone clicks. Good for governance, slow for iteration.
5. **Heartbeat adapters require external runtimes to know Paperclip's API.** Approvals, cost reporting, session state — big API surface. Cadre's Members just use MCP tools, which are the same tools the operator uses.
6. **Plugin system requires TypeScript.** If you want a Python evaluator, you write glue code.

## What Cadre should NOT copy (anti-patterns)

1. **Don't split audit into `activity_log` + `cost_events` + `heartbeat_run_events` + `budget_incidents`.** Keep Records unified. It's queryable, replay-able, easier to reason about.
2. **Don't make budget a separate enforcement service with policies + incidents + soft alerts + hard pauses.** Put `monthly_budget` + `spent_this_month` on Member or Contract. Enforce at invoke time. One concept.
3. **Don't build a web dashboard as the primary interface.** The hook-based session injection *is* the interface. A UI would be a concession, not a feature. If one ever ships, keep it secondary.
4. **Don't assume 24/7 scheduling is necessary.** Pulse activation is the better default for Cadre's user. Only add `firm pulse --cron` if there's a concrete use case.
5. **Don't reify approvals into a rich DB state machine until required.** Gates + CARL rules are enough. If approval logic gets multi-step or conditional later, borrow the Approval pattern then.
6. **Don't let `runtime_config` JSONB swallow typed config.** Keep runtime configs typed (dataclasses or Pydantic). Bare JSONB is a complexity trap.
7. **Don't store skills as a mutable library separate from Contracts.** `Contract.skill_loadout` is the right scope — skills are part of the runtime binding, versioned with it.

## The strategic positioning

Paperclip is building **Salesforce for AI companies**: hosted-ready, multi-tenant, enterprise-governed, webinar-friendly. They'll win deals in companies that already run ops on a dashboard.

Cadre is building **Git for AI companies**: local-first, session-activated, editor-native, zero-infra. Cadre wins for the solo builder who lives in their terminal and doesn't want to run a Node server to coordinate three agents.

Git didn't win because it was better SVN. It won because it was a fundamentally different model (distributed, local-first, snapshot-based) optimized for a different user (developers who wanted control on their own machine). Cadre's lane is structurally analogous.

These two frameworks can coexist in the same ecosystem without competing for the same operator.

## When to use which

**Use Paperclip if:**
- Multiple people operate your AI company
- You want agents running 24/7 regardless of whether any human is active
- You need a web dashboard because non-engineers need to see what's happening
- You have ops infrastructure (Postgres, hosting, auth) already
- Approval workflows, RBAC, and audit compliance are requirements

**Use Cadre if:**
- You're a solo builder or small technical team
- Your agents should wake when *you* work, not run in the background
- You live in Claude Code / terminal and want the Firm state in your session context
- You want `pip install cadre` and nothing else to think about
- You want governance-as-code (CARL rules), not governance-as-DB-state

---

## Acknowledgment

Paperclip's entity vocabulary (Company / Agent / Operation / Issue / Approval) was formative for Cadre's thinking. The "AI-operated company" mental model is theirs, validated by 53k stars and serious engineering. Cadre takes that frame, inverts the scheduling model, rescopes for solo operators, and keeps its own vocabulary — **Firm / Member / Unit / Gate / Contract runtime** — because the products are different and the language should be too.
