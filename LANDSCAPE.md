# Agent Company Architecture — Landscape Analysis

**Source:** Paperclip reference clone (`z-dump/references/paperclip/`) mined for architectural signals and mapped against the current Chris AI Systems stack.
**Date:** 2026-04-13
**Status:** Pre-planning research artifact for PRJ-050. Do not implement from this yet.

---

## 1. Paperclip in One Paragraph

Paperclip is a **control plane** — it orchestrates, it does not execute. Companies are first-order entities. Agents are employees with adapters (how they run), roles, reporting lines, and budgets. All work traces from the company goal down through projects, issues (tasks), and comments. Heartbeats fire agents on schedule; adapters translate "invoke this agent" into either a shell process or an HTTP callback. Every mutation hits an activity log. Cost events roll up to enforce hard-stop budget caps. Board (human) approves hiring and strategy. That is the entire mental model.

The rest is plumbing around those primitives.

---

## 2. Paperclip Architecture — The 8 Essentials

1. **Company as first-order container.** Everything scoped to `company_id`. One deployment, many companies possible.
2. **Agent = adapter_type + adapter_config + role + reports_to + budget.** Five fields define an employee. Adapter config IS the agent's identity (could point at CLAUDE.md, SOUL.md, or CLI args — Paperclip doesn't care).
3. **Goal hierarchy.** Company → team → agent → task. Every issue must trace up to a goal. "Why am I doing this?" is always answerable.
4. **Issues (tasks) with atomic checkout.** Single SQL UPDATE with status + assignee guard. Conflict = 409. No double-assignment possible.
5. **Tasks + comments = the ONLY communication.** Explicitly not a chatbot. Explicitly not a code review tool. Work objects own all conversation.
6. **Heartbeats drive execution.** Scheduler fires agents on interval (min 30s). Two modes: spawn process, or fire HTTP. Max 1 concurrent run per agent in V1.
7. **Cost events → budget enforcement.** Every agent action ingests cost. Monthly rollup. 80% soft alert, 100% hard-stop → auto-pause agent.
8. **Board governance.** Approvals required for hiring and CEO strategy. Board can override anything. Agents can delegate and request but cannot bypass.

---

## 3. Entity Mapping — Paperclip → Your Stack

| Paperclip Entity | Your Current Equivalent | Status | Notes |
|---|---|---|---|
| `companies` | BASE workspace (singular) | **UPGRADE NEEDED** | You have one workspace. Their model supports many. Decision: singular "Chris Inc" with C&C + ChrisAI as divisions, OR multi-company model? |
| `agents` | Skills + CARL domains (implicit) | **NEW ENTITY NEEDED** | You have skills and domains but no named "agent" entity with role, reports_to, or budget. This is THE missing primitive. |
| `agent.adapter_type` + `adapter_config` | Skill invocation + domain loadout | **REPURPOSE** | Your skills already define invocation behavior. Formalize as adapter config. |
| `agent.reports_to` | Nothing | **NEW** | No org hierarchy exists in your stack. |
| `agent.capabilities` | Skill descriptions | **REPURPOSE** | You already write these — just attach to agents. |
| `agent.budget_monthly_cents` | ccusage (global, manual) | **UPGRADE** | ccusage tracks total spend. Need per-agent tagging. |
| `agent.last_heartbeat_at` | Nothing | **DEFERRED** | You said no 24/7 autonomous. Manual invocation suffices for v1. |
| `goals` | Active-awareness `NEXT:` strings + operator North Star | **UPGRADE** | You have implicit goals in project fields. Needs formalization as a hierarchy. |
| `goals.level` (company\|team\|agent\|task) | None explicit | **NEW** | Hierarchy levels don't exist in your data. Informal in your head. |
| `projects` | BASE projects | **REUSE** | 1:1 match. Your `projects.json` already does this. Just link to goals. |
| `issues` (tasks) | BASE projects + tasks | **PARTIAL** | You have tasks but they don't have single-assignee, atomic checkout, or parent-trace-to-goal invariants. |
| `issues.parent_id` | Your `parent_id` on tasks | **REUSE** | You already have hierarchical tasks. |
| `issues.assignee_agent_id` | Not present | **NEW** | Tasks aren't assigned to anyone. You're implicit assignee. |
| `issue_comments` | Notes field + conversation | **UPGRADE** | Ad-hoc now. Could be formalized. |
| `heartbeat_runs` | Your session files (`.carl/sessions/*.json`) | **REPURPOSE** | You already record session runs with timestamps. Just need agent attribution. |
| `cost_events` | ccusage JSONL parsing | **UPGRADE** | Exists at session level. Need agent attribution. |
| `approvals` | Your implicit self-approval | **NEW (LITE)** | You are sole Board. Approval might be lightweight: "show me before commit." |
| `activity_log` | PSMM logs + decisions log | **UPGRADE** | You have PSMM and decisions.json. Unify into one mutation audit. |
| `company_secrets` | `.env` files (ad-hoc) | **UPGRADE** | You don't have workspace-level secret management. |
| `documents` (plan, design, notes keys) | PAUL STATE.md + PLANNING.md | **REUSE** | PAUL already treats plans as versioned documents. |
| `adapters` (process \| http) | Skill invocation | **REPURPOSE** | Your slash commands ARE process adapters. Formalize the contract. |

---

## 4. Invariants — What They Enforce vs What You Enforce

| Invariant | Paperclip | You | Gap |
|---|---|---|---|
| Every entity scoped to company | Yes (SQL constraint) | No (implicit — one workspace) | Design decision: needed if multi-company |
| Single-assignee per task | Yes (SQL) | No (you're implicit) | Needed when agents exist |
| Atomic task checkout | Yes (409 conflict) | No (you never double-assign yourself) | Needed when multiple agents run in parallel |
| Task traces to goal | Yes (required chain) | No (implicit via project → North Star) | Strong principle worth adopting |
| Every mutation logged | Yes (activity_log) | Partial (PSMM + decisions) | Unify |
| Budget hard-stop | Yes (auto-pause) | No (ccusage reports, doesn't stop) | Cannot enforce per-agent on Claude Code |
| Agent/manager same company | Yes | N/A | N/A if single workspace |
| No reporting cycles | Yes | N/A | N/A currently (no hierarchy) |
| Approvals cannot be bypassed | Yes | N/A | Lightweight version useful for you |
| Auth boundary enforced per entity | Yes | N/A | You're sole operator |

**Takeaway:** Paperclip's invariants exist because they run multi-agent, multi-company, multi-operator. You run solo. Most invariants become best-practices rather than enforced constraints. The **task-traces-to-goal** principle is the highest-value borrow.

---

## 5. The Adapter Pattern — Your Biggest Transferable Win

Paperclip's single most important abstraction: **every agent is swappable because the adapter boundary is narrow.**

```ts
interface AgentAdapter {
  invoke(agent: Agent, context: InvocationContext): Promise<InvokeResult>
  status(run: HeartbeatRun): Promise<RunStatus>
  cancel(run: HeartbeatRun): Promise<void>
}
```

Three methods. That's it.

**Mapping to your world:**

- Your `/paul:plan`, `/skillsmith`, `/base:pulse`, `/seed` are already invocations.
- Each one takes context (domain loadout, project state) and produces output.
- You already have status (PAUL phase_status, BASE active-awareness).
- Cancel is trivial — you just stop.

**What formalizing this gives you:**

1. Any skill becomes "hire-able" as an agent by defining its adapter config
2. You can swap a Claude Code agent for a local OpenClaw agent by changing adapter type — same role, same tasks, different runtime. This is your $4k-rig transition path.
3. External runtimes (Codex, Cursor, Gemini) slot in by implementing the three-method interface
4. Plugins become trivial: register a new adapter, done

**This is the single piece I'd copy verbatim from Paperclip's design.**

---

## 6. Concept Hierarchy — Side-by-Side

**Paperclip:**
```
Company
  └─ Goal (level: company)
       └─ Goal (level: team)
            └─ Goal (level: agent)
                 └─ Issue (task)
                      ├─ Issue (parent_id) — subtask
                      └─ Comments
Project (optional grouping) → Goal
Agent (assignee) ← Issue
```

**You (current):**
```
Workspace
  └─ Initiative
       └─ Project
            └─ Task
                 └─ (subtask via parent_id)
                      └─ Notes (inline)
Satellite (PAUL project) → Project
North Star → Initiative (implicit alignment)
```

**Collisions to resolve:**

- Their **Issue** = your **Task**. Naming — adopt "task" (already yours) and ignore "issue."
- Their **Project** = your **Project**. Clean alignment.
- Their **Goal** levels = your **Initiative + North Star**. You're missing mid-level goals.
- Their **Company** = your **Workspace** OR your **Venture** (C&C vs ChrisAI). Decision needed.
- Their **Agent** = brand new concept for you.

**Proposed reconciliation:**
```
Workspace (your "Chris Inc")
  └─ Venture (C&C Strategic | Chris AI Systems) ← new abstraction layer
       └─ North Star Goal (level: venture)
            └─ Initiative (level: team)
                 └─ Goal (level: agent)  ← optional intermediate
                      └─ Project
                           └─ Task
                                └─ Subtasks + Comments
Agent (assignee) ← Task
Satellite (PAUL project) = executable Project
```

---

## 7. Gaps Requiring New Frameworks

Things Paperclip has that you fundamentally lack:

### Gap A: The Agent Entity
You have skills, domains, commands. You do NOT have a unified "agent" concept that aggregates: name + role + adapter config + skill loadout + domain access + capabilities description + reports-to + budget.

**Required:** A new framework or BASE entity type. Working name candidates below.

### Gap B: Org Hierarchy
No reports_to. No team structure. No delegation model. Flat.

**Required:** Schema addition + visualization pattern. Could be minimal: JSON tree + Mermaid render.

### Gap C: Goal-Chain Enforcement
Your tasks are not required to trace to a goal. Strings like `NEXT: "audit content"` are goals in disguise.

**Required:** A goal entity type in BASE + a `goal_id` foreign key on tasks (already supported in your projects schema as a free field).

### Gap D: Per-Agent Cost Attribution
ccusage reports total. You cannot say "Content Agent spent $40 this month."

**Required:** Session tagging. Every Claude Code session needs to declare which agent owns it. Then ccusage parser + aggregation per agent. This is the enforcement backbone for budget governance.

### Gap E: Adapter Contract
Your slash commands have no shared interface. Each is ad-hoc. This works for one operator driving manually but breaks when you want substitution (Claude Code vs Gemini vs OpenClaw running the same role).

**Required:** A formal adapter interface. Three methods: invoke, status, cancel. Apply it to existing skills incrementally.

### Gap F: Approval Gates (Lightweight)
You already de facto approve everything yourself. A formal gate framework lets you say "this agent can do X autonomously, Y requires my review, Z is forbidden." The governance signal matters more than the UI.

**Required:** Per-agent permission flags + a review queue. Could be a BASE entity.

### Gap G: The Meta-Layer Name
You have BASE, PAUL, CARL, SEED, AEGIS. What do you call the layer that ties them into a company?

**Required:** Brainstorm session. Acronym + word. Must not be "Forge/Helm/Atlas" (your banned list). Candidates to explore: GUILD, COHORT, HELIX, ORBIT, BRIDGE, CHORUS. The name drives the metaphor.

---

## 8. What You Already Have That Paperclip Would Envy

- **JIT context injection via CARL domains.** Paperclip uses `context_mode: thin|fat` — binary. You have dynamic per-intent domain loading. Strictly more powerful.
- **Satellite auto-registration.** Your PAUL projects auto-register in BASE workspace on session start. Paperclip requires manual agent creation.
- **Grooming cadence.** Your BASE areas have audit strategies (staleness, classify, cross-reference, pipeline-status). Paperclip has no equivalent — they have activity logs but no rhythmic workspace health cycle.
- **Dedup-aware hooks.** Your `<carl-status dedup="true">` avoids re-injecting unchanged signatures. Paperclip has no prompt-caching hygiene layer.
- **Named personas (ZERO).** Paperclip's agents are functional. You have identity/character primitives that give agents personality. This is a content differentiator.
- **Meta commands (*zero, *one, *meta).** Your persona switching has no Paperclip equivalent.

---

## 9. The Landscape — Where You Are vs Where You're Going

### Where You Are (Current State — 2026-04-13)

You operate a **single-operator studio** with sophisticated workspace hygiene. Your frameworks address:
- **BASE:** workspace health, project state, grooming rhythm
- **CARL:** dynamic rule injection, domain-scoped context
- **PAUL:** per-project plan/apply/unify execution
- **SEED:** typed ideation → graduation
- **AEGIS:** codebase audits
- **Skillsmith:** skill authoring

What's missing is the **human/organizational abstraction layer**. You think of your work in terms of projects, phases, and rules — not in terms of roles, responsibilities, and employees. Every task implicitly routes to "you, holding the wheel."

### Where You're Going (Target State — Q4 2026 and beyond)

A **named company** (or companies) where:
- Each business function is an explicit agent with a role, skill loadout, and budget
- Work flows through assignable tasks that trace to goals
- You operate at board level — reviewing, approving, redirecting — not driving every keystroke
- Agents are runtime-swappable (Claude Code today, OpenClaw on local rig later)
- Per-agent cost attribution lets you see which functions are efficient vs bleeding tokens
- Content pillar: "I run a company of AI agents" becomes literal, not metaphorical

### The Bridge (What Must Be Built)

Five new primitives must exist before the target state is reachable:

1. **Agent entity type** in BASE (Gap A)
2. **Org hierarchy / reports-to model** (Gap B)
3. **Goal-chain formalization** (Gap C)
4. **Per-agent session tagging for cost attribution** (Gap D)
5. **Formal adapter interface** for skill invocation (Gap E)

Plus two optional quality-of-life additions:
6. Lightweight approval gates (Gap F)
7. Meta-layer name + conceptual framing (Gap G)

---

## 10. Plan of Action (Suggested Phases)

**Phase 0 — Naming + Framing (1 session, 1 hour)**
Name the meta-layer. Decide: one company or multiple. Decide: where does ZERO live in the hierarchy (chief of staff? embedded in every agent? separate?). These decisions drive everything else.

**Phase 1 — Agent Entity Spec (2-3 sessions over a week)**
Define the agent schema. Write down what "an agent" is in your world. Do NOT build yet. Produce a SPEC.md in `projects/agent-company-architecture/`.

**Phase 2 — Roster Exercise (1 session)**
Define your first 3-5 agents using the spec. Names, roles, skill loadouts, budgets. This surfaces schema gaps fast.

**Phase 3 — Adapter Interface (1-2 sessions)**
Formalize the three-method contract. Pilot it against ONE existing skill (probably SEED or PAUL). Verify the abstraction holds.

**Phase 4 — Session Tagging (1 session — tactical)**
Add a `CHRIS_AGENT_ID` env var convention. Update ccusage parser or wrap it to group by agent. This unlocks budget visibility.

**Phase 5 — BASE Integration (2-3 sessions)**
Add agents as BASE entity type. Update hooks to surface agent context. Small, incremental merge.

**Phase 6 — First Orchestration (ongoing)**
Use the new abstractions in actual work. "I'm running ScribeBot on the blog post today." Observe what breaks. Refine.

**NOT in this plan:**
- Dashboard / UI (skip entirely — CLI + hooks + active-awareness is your UI)
- Multi-operator governance
- 24/7 heartbeats
- Plugin marketplace
- Cloud deployment

**Total commitment:** ~8-12 focused sessions across 4-8 weeks before anything works end-to-end. Treat as research-backed evolution, not a sprint.

---

## 11. Open Questions (Must Be Answered)

1. Meta-layer name?
2. Single company ("Chris Inc") or multi (C&C + ChrisAI)?
3. Where does ZERO sit in the hierarchy?
4. Is "agent" a new BASE entity type, or its own satellite framework?
5. Do agents own projects, or do projects assign agents?
6. Is the goal hierarchy 3 levels (venture/initiative/agent) or 4 (+ task-level)?
7. Does approval gating ship in v1 or deferred?
8. How do Skills relate to Agents — is a skill an agent capability, or is every skill invocation an agent call?
9. Does this REPLACE your current workflow or LAYER on top?
10. Content implications — when do you announce this publicly? Before or after v1?

---

## 12. Scheduler / Activation Model

Paperclip runs 24/7 cron polling for heartbeats. That model doesn't fit you. Your scheduler is **operator-driven, business-hours, session-scoped.**

### 12.1 Four Activation Modes

| Mode | When | Trigger | Example |
|---|---|---|---|
| **Session-boot** | Terminal opens | SessionStart hook | "Briefing Agent" fires on first session of day, loads calendar + active projects + yesterday's PSMM |
| **Manual hire** | You invoke | Slash command `/agent:name` | "Run ScribeBot on the blog post" |
| **Calendar-triggered** | Event nears | Calendar hook + threshold | 11:15am → "Standup Prep Agent" fires 15 min before 11:30am standup |
| **Scheduled routine** | Wall clock | CronCreate via schedule skill | 9am daily → "Skool Triage Agent" runs automatically |

### 12.2 Session Boot Flow

1. Terminal opens → Claude Code session starts → existing SessionStart hook chain fires
2. New hook `shift-boot.py` reads `.base/agents.json`, checks for agents with `activation: session-start`
3. Runs "first-session-of-day?" check against `.base/shifts/YYYY-MM-DD.json`
4. If first session: starts the "shift," logs active agents, injects `<shift-roster>` tag
5. Operator sees: "Shift started. On roster: Briefing Agent (active), ScribeBot (on-call), Ops Agent (on-call)"
6. Briefing Agent runs immediately — replaces existing pulse/briefing with attribution to a named agent
7. Others wait idle until invoked, calendar-triggered, or cron-fired

### 12.3 Cron Layer (Optional)

- Use existing `schedule` skill (CronCreate)
- Agent config gets optional `cron: "0 9 * * MON-FRI"` field
- Schedule skill registers the job on agent creation
- When fired, invokes `/agent:name run` as RemoteTrigger with scoped context
- Cron schedules constrained to business hours only — never 24/7

### 12.4 End-of-Shift

- Calendar's 4:45pm EOD Reporting = shift end signal
- StopAgent or session-end hook runs "close the shift" routine
- Agents report summary, unfinished tasks roll forward to tomorrow's shift roster
- PSMM entries consolidated, decisions logged, shift archived

### 12.5 Agent Lifecycle States

```
off-shift → on-shift:idle → on-shift:running → on-shift:idle → off-shift
                                  ↓
                              on-shift:error
```

Terminal: `terminated` (board-only, irreversible — matches Paperclip invariant).

---

## 13. Naming Candidates

Pattern established: acronym + pronounceable word + mythological/functional weight. Banned: Forge, Helm, Atlas, Matrix, Nexus (per operator preference / overuse).

### 13.1 Meta-Framework Names (the "agent company" layer)

| Candidate | Acronym Expansion | Metaphor | Notes |
|---|---|---|---|
| **GUILD** | Gathered Unit for Intelligent Labor & Delegation | Medieval trade guild — skilled craftspeople with roles, master directs | Mythological weight parallel to AEGIS. Matches builder identity. |
| **CREW** | Coordinated Runtime for Employed Workers | Nautical — ship's crew, captain directs, specialized hands | Works with "running a shift." Warm, small-team feel. |
| **HIVE** | Hierarchical Intelligence + Virtual Employees | Colony, distributed work, productive swarm | Productivity association. Maybe too generic. |
| **CHORUS** | Coordinated Heterogeneous Orchestration of Role Units & Skills | Musical — voices in harmony, conductor directs | Pairs elegantly with scheduler name TEMPO. |
| **CORPS** | Coordinated Operations with Role-Specific Personnel | Military battalion, formation discipline | Precision feel. Harder to say. |
| **CADRE** | Core Agents Delivering Results Effectively | Elite trained unit, small sharp team | Small scale appropriate for solo operator. |

**Leading candidates: GUILD or CREW.**

### 13.2 Scheduler Sub-System Names

| Candidate | Metaphor | Notes |
|---|---|---|
| **SHIFT** | Business-hours punch-in/punch-out | Nails the "not 24/7" linguistic distinction. "Shift starts," "on shift," "end of shift." |
| **TEMPO** | Rhythm, cadence | Pairs with CHORUS. Musical. |
| **BEAT** | Heartbeat-adjacent but terser | Short, works as verb: "the beat fires" |
| **CLOCK** | Literal punch-clock | On-the-nose. Risk of boring. |

**Leading candidate: SHIFT.** Linguistically distances you from Paperclip's "heartbeat" and accurately describes the business-hours model.

### 13.3 Composite Proposal

- **Meta-framework:** GUILD (or CREW)
- **Scheduler:** SHIFT
- **Agent file:** `.base/guild.json` (or `.base/crew.json`, or `.base/roster.json`)
- **Shift log:** `.base/shifts/YYYY-MM-DD.json`
- **Commands:** `/guild:roster`, `/guild:hire`, `/shift:start`, `/shift:end`, `/agent:{name}`

Content angle: "I run a GUILD on SHIFT from my desk." Or: "Running my CREW on SHIFT this morning."

### 13.4 Content Resonance Test

| Test Phrase | GUILD | CREW |
|---|---|---|
| "I run a [X] of AI agents" | Works | Works |
| "The [X] audit surfaced three idle agents" | Slightly formal | Natural |
| "Hiring a new [X] member" | Works | Works |
| "[X]master" (your title as orchestrator) | GUILDMASTER (strong) | CREW... Captain? |
| "The [X] roster" | Natural | Natural |
| "[X] governance" | Works | Works |
| T-shirt reading "Chris Kahler — [X]master" | Strong | Weaker |

**GUILD has stronger content extensibility.** The "Guildmaster" title is gold.

---

## 14. MVP Starter Implementation (When Ready — NOT Now)

Minimum working version after naming locks:

1. **`shift-boot.py`** — SessionStart hook that reads roster, starts/resumes shift, injects context
2. **`.base/guild.json`** (or equivalent) — agent roster with 3 hardcoded agents to start
3. **`.base/shifts/YYYY-MM-DD.json`** — daily shift log (who's on-shift, when started, when ended)
4. **`/agent:{name}`** command skill — loads agent's adapter config, activates their skill+domain loadout
5. **`shift-end.py`** — session-end hook that closes shift, consolidates output

No server, no database, no dashboard, no cron yet. Pure hook + file + command. Entire v1 buildable in one focused deep-work block once naming lands.

**First 3 agents to define** (tactical — defers philosophical "what's an agent?" until you have 3 concrete examples to compare):

1. **Briefing Agent** — fires at shift-start, gives daily rundown (already conceptually exists as your pulse/briefing)
2. **ScribeBot** — content writer, activated manually on content tasks
3. **Ops Agent** — handles BASE hygiene, project status updates, decisions logging

Watch what breaks. Refine the agent schema based on what those three need but don't have. Then expand.
