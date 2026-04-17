# Cadre vs Paperclip

*Last updated: 2026-04-17*

Paperclip gets compared to Cadre a lot, so let's be explicit about the differences. This isn't a feature checklist — it's a category distinction. The two frameworks solve related but structurally different problems.

## TL;DR

**Paperclip** is a multi-operator control plane that bundles scheduling, execution, UI, governance, and audit into a persistent Node server backed by Postgres. The scheduler is in-process; kill the server and agents stop firing. Enterprise-shaped.

**Cadre** is a local-first framework that decouples scheduling from execution. `firm pulse` is a stateless function — any trigger (cron, session hook, CLI, CI, systemd, webhook) fires it. SQLite + Python, no daemon, no server process. Integrations come through the MCP ecosystem, scoped per-Member via `Contract.skill_loadout`. Solo-operator-shaped.

They aren't the same product at different scales. Different users, different assumptions about what running an AI company means, and — critically — different answers to "who owns the scheduler."

## The category distinction

| | Paperclip | Cadre |
|---|---|---|
| **Mental model** | Salesforce for AI companies | Git for AI companies |
| **User** | Multi-operator team | Solo builder |
| **Activation** | Scheduler-bound (cron inside a persistent server) | Stateless PULSE — trigger-agnostic (cron, session hook, CLI, CI, or any caller) |
| **Operator surface** | React dashboard at localhost:3100 | Claude Code session context (no separate UI) |
| **Persistence** | Postgres (embedded PGlite or hosted) | SQLite (stdlib, single file) |
| **Process model** | Persistent Node server + workers | Python package, no daemon |
| **Deployment** | Docker / hosted / self-hosted server | `pip install` |
| **Governance** | Approvals as DB state machine rows | Rules-as-code (CARL integration) + Gate entities |
| **Platform integrations** | Custom plugins via TS SDK | MCP servers scoped per-Member via `Contract.skill_loadout` |
| **Runtime swap** | Adapter + adapter_config JSONB | `ContractRuntime` Protocol (Python, 3 methods) |
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
| Recurring work | `Routine` + `RoutineTrigger` (in-process cron) | `contract.pulse_config.cron` + stateless `firm pulse` | PC owns the scheduler. Cadre stores the cron string and lets the caller (system cron, CI, hook) fire it. |
| Approval | `Approval` (rich state machine) | `Gate` (pending/approved/rejected + payload) | Cadre's Gate is simpler; CARL rules handle the "when do we need approval" logic |
| Budget | `BudgetPolicy` + `BudgetIncident` (separate enforcement service) | `contract.budget_config` + `budget_period` table (hard/soft limits, period rollups) | PC: multi-table budget subsystem. Cadre: two tables, enforced at pre-flight and mid-run. |
| Cost tracking | `CostEvent` (per-model, per-token, denormalized) | `UsageEvent` + `budget_period` rollup | PC: per-event granularity. Cadre: per-run events + period aggregate. |
| Execution history | `HeartbeatRun` + `ExecutionWorkspace` + run events | `member_run` (with `invocation_source`, `retry_of_run_id`, `prompt_snapshot`, `validation_result`) + immutable `Records` | PC: fragmented across several tables. Cadre: run lifecycle in `member_run`, audit replays from `Records` |
| Skills | `CompanySkill` (uploaded via UI) | `Contract.skill_loadout` (list reference) | PC: mutable skill library. Cadre: skills are part of the Contract, versioned with it |
| Comments | `IssueComment` (scoped to issues) | `Comment` (polymorphic `parent_ref`) | Cadre's polymorphic refs mean new entities don't need new comment tables |
| Secrets | `CompanySecret` + `CompanySecretVersion` (rotation built in) | Env-bound via Contract config (metadata only) | PC: production secret store. Cadre: offload to env/keychain |

**Interpretation.** Paperclip's 65 tables are not bloat — they're what you need for a hosted, multi-tenant, audited, governed agent platform. Cadre's 14 tables aren't "lean" — they're what you need when one operator owns the whole thing and Records can serve as the unified truth stream.

## Architectural assumptions

### Scheduling

**Paperclip** runs a 24/7 in-process scheduler. `RoutineTrigger` rows specify cron expressions. The Node server polls, matches triggers, spawns agent invocations through the scheduler. Background workers execute. The scheduler is load-bearing — kill the server process and no agents fire.

**Cadre** decouples trigger from execution. `firm pulse` is a stateless function that loads the firm DB, checks which Members are due (frequency + budget + business hours), dispatches them, and exits. Any caller can invoke it:

- **Cron:** `crontab -e` → `*/15 * * * * firm pulse` (user or system cron, not an in-process scheduler)
- **Session hook:** Claude Code `SessionStart:startup` calls it (read-only roster injection by default; optional dispatch mode)
- **CLI:** `firm pulse` by hand when you want to manually tick the Firm
- **CI:** GitHub Actions cron calls it to run content pipelines on a server schedule
- **Any other trigger:** webhooks, systemd timers, launchd, whatever fires a shell command

Same handler, any trigger. No daemon, no PID file, no supervisor process to restart. Bug in the pulse logic? `pip install -U cadre` and the next tick picks up the fix; no server to bounce.

**Why this matters.** Paperclip bundles scheduling with execution. You get both or neither — to run agents autonomously, you run their server. Cadre says the scheduler is not a framework concern. Use the one your ops stack already trusts. Your laptop has cron. Your dev server has cron. GitHub Actions has cron. Systemd has timers. Cadre doesn't compete with those; it plugs into them.

This is the same inversion Unix took with `mail` and `cron` instead of building a scheduler into the mail daemon. Simpler primitive, more composition.

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

### Integrations and extensions

This is where a Firm actually becomes useful. Members need real platform access — Meta Ads Manager, Google Analytics, CRMs, email providers, ad accounts, project trackers, whatever the business runs on. "Can your AI team actually do the work?" reduces to "can they reach the tools?"

Cadre has three integration surfaces, each serving a different concern:

**1. MCP servers — platform integrations.** This is the primary answer for "how does Sterling check Meta Ads performance" or "how does Quill post to Contentful." Any MCP-compatible platform (Meta Ads, Google Analytics, HubSpot, Slack, Notion, GitHub, etc.) plugs in through the standard Model Context Protocol. Cadre itself exposes 33 MCP tools for its own entities. Adding a CRM integration means pointing a Member's runtime at that CRM's MCP server — no Cadre code changes required.

**2. Contract.skill_loadout — per-Member tool subscription.** Members aren't given universal tool access. Each Contract declares which skills and commands that Member can use via `skill_loadout` (a JSON array on the `contract` row). Quill's Contract might list `/blog:*` skills and the Contentful MCP. Sterling's Contract lists `/sterling:*`, the Meta Ads MCP, and the GA MCP. This is the role-based-access-control layer: identity (Member) is separate from tooling (Contract.skill_loadout).

**3. ContractRuntime Protocol — runtime swaps.** Separate concern from integrations. This is how you swap Claude Code for OpenClaw or Codex. Three methods (invoke, status, cancel), ~50 lines of Python. Not about platform integrations — about which agent runtime executes the Member.

**Agent-driven tool procurement (design direction).** When a Member hits a capability gap mid-run ("I could not complete this Unit because I do not have access to the HubSpot contacts API"), they report it. The report surfaces as a procurement Unit — "add HubSpot MCP to Sterling's Contract skill_loadout." The Board approves (or rejects) via a Gate. On approval, Sterling's Contract gains the new tool and the blocked Unit unblocks. This closes the loop: agents surface gaps, the team proposes the fix, the Board approves additions, Contracts grow with the work. Not yet implemented — design direction for v0.2.

**Why this matters.** Paperclip's answer to integrations is a TypeScript plugin SDK (~65k-character spec). That serves enterprise customization — build once, ship to many tenants. Cadre's answer is MCP + skill_loadout — use the ecosystem's standard integration protocol, scoped per-Member via Contract. No Cadre-specific SDK to learn. If you can point a Claude Code agent at an MCP server today, your Cadre Member can use it tomorrow.

Paperclip asks: "what plugins have we authored for our platform?" Cadre asks: "which MCP servers does this Member have in their Contract?"

### Governance

**Paperclip** governance is data-driven. An `Approval` row with type=`hire` sits pending until someone clicks approve in the UI. If you want different approval logic, you write a plugin or extend the schema.

**Cadre** governance is code-driven. CARL domain rules encode the policy ("HIRING domain requires Board.yes before agent_hire completes"). The rule is readable, versionable, composable. Gates are just the runtime state of a governance rule firing — not the source of truth for governance itself.

**Why this matters.** Rules-as-code scales better for solo operators who change their minds. "I trust Sterling to hire engineers but not marketing folk" → add a rule. In Paperclip that's a plugin.

## What both frameworks get right (shared foundations)

Some patterns are load-bearing for any serious AI firm framework. Cadre and Paperclip both implement them, with different architectural styles.

- **Atomic work checkout.** Cadre: `UPDATE unit SET claimed_by = ? WHERE id = ? AND claimed_by IS NULL RETURNING *` (single SQL statement, no race window). Paperclip: `agent_task_sessions` with persistent resumable context. Different trade-offs (Cadre = fresh context each Pulse, Paperclip = resumed session), same correctness.
- **Goal ancestry.** Cadre: polymorphic `parent_ref` — Goals attach to Firm, Operation, Project, Unit, or Member; `goal_ids` denormalized on parents for fast lookup; pulse prompt assembly includes parent chain + goal health. Paperclip: explicit `parent_id` on issues + goals, full ancestor materialization on read. Cadre's approach is lighter (one pattern, any entity); Paperclip's is stricter (typed hierarchy). Both solve "every Unit traces back to Firm mission."
- **Budget enforcement.** Cadre: `contract.budget_config` with hard/soft limits, pre-flight check before spawn, mid-run token tracking, period rollups in `budget_period`. Paperclip: `BudgetPolicy` + `BudgetIncident` as separate entities. Same outcome; Cadre keeps it in fewer tables.
- **Output validation.** Cadre: Ralph Wiggum pattern — validators run post-invoke, one retry with error context, then flag for supervisor review. Paperclip: heartbeat evaluators + agent self-reports.

## Paperclip patterns worth studying

1. **Multi-runtime ecosystem maturity.** 8 production runtimes (claude-local, codex-local, cursor, gemini-local, openclaw-gateway, opencode, pi, http, process). Proves the identity/runtime split works at scale. Cadre's `ContractRuntime` Protocol is shaped by this precedent.
2. **Routine triggers with timezone + DST handling.** `/server/src/services/routines.ts` handles 4-year search windows, leap years, DST transitions. When Cadre users wire `firm pulse` into cron, they inherit whatever their cron daemon supports — but if Cadre ever adds its own scheduling helper, Paperclip's cron library is the reference.
3. **Workspace isolation for multi-agent CI-like work.** `execution_workspaces` + per-run git clones. Overkill for solo operators; correct for any setup running multiple Members in parallel on overlapping files.
4. **Explicit cost-event table granularity.** Per-model, per-token accounting in `CostEvent`. Cadre uses a generic `UsageEvent`; when deep LLM accounting becomes a requirement, the Paperclip shape is well-designed.

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
4. **Don't build an in-process scheduler into Cadre.** Cadre Members can run 24/7 if you want them to — wire `firm pulse` into system cron or GitHub Actions. But don't make Cadre itself the scheduler daemon. The whole point of the stateless trigger-agnostic model is that the operator's ops stack owns scheduling, not the framework.
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
- You want the framework itself to own scheduling (in-process cron inside the server)
- You need a web dashboard because non-engineers need to see what's happening
- You have ops infrastructure (Postgres, hosting, auth) already
- Approval workflows, RBAC, and audit compliance are requirements

**Use Cadre if:**
- You're a solo builder or small technical team
- You want the scheduler to be *your* scheduler (cron, systemd, CI, hooks) — not a framework's daemon
- You live in Claude Code / terminal and want the Firm state in your session context
- You want `pip install cadre` and nothing else to think about — no Postgres, no Node server
- Your platform integrations come from the MCP ecosystem, scoped per-Member via Contract.skill_loadout
- You want governance-as-code (CARL rules), not governance-as-DB-state

---

## Acknowledgment

Paperclip's entity vocabulary (Company / Agent / Operation / Issue / Approval) was formative for Cadre's thinking. The "AI-operated company" mental model is theirs, validated by 53k stars and serious engineering. Cadre keeps its own vocabulary — **Firm / Member / Unit / Gate / Contract runtime** — because the products are different and the language should be too. Where Paperclip owns the scheduler, Cadre delegates it. Where Paperclip ships a plugin SDK, Cadre points at MCP. Where Paperclip governs via DB state, Cadre governs via rules-as-code. Same north star, different shape.
