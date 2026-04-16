# Task 2 Notes — Paperclip Reference Mining

**Sources:**
- `z-dump/references/paperclip/docs/agents-runtime.md` (user-facing runtime guide)
- `z-dump/references/paperclip/docs/guides/execution-policy.md` (review/approval runtime)
- `z-dump/references/paperclip/docs/adapters/overview.md` (+ claude-local, creating-an-adapter)
- `z-dump/references/paperclip/server/src/services/activity-log.ts` (94 lines — Records equivalent)
- `z-dump/references/paperclip/server/src/services/heartbeat-run-summary.ts` (84 lines)
- `z-dump/references/paperclip/server/src/services/costs.ts` (364 lines — cost events + budgets)
- `z-dump/references/paperclip/server/src/services/heartbeat.ts` (4707 lines — scanned structurally, not full-read)

**Purpose:** Derive `KEEP / ADAPT / REJECT` decisions per pattern. These ride into the BRIEF as-is.

---

## 1. Patterns Extracted

### 1.1 Heartbeat wakeup taxonomy (`agents-runtime.md §2`)

Paperclip wakes agents four ways: `timer | assignment | on_demand | automation`. Wakeups coalesce if an agent is already running.

**KEEP the coalesce invariant.**
Reason: if Quill is already in a Run and a second pulse fires (e.g., user opens two terminals), we should not spawn a duplicate Run. Enforce "one active Run per Member" via `member_run WHERE member_id=? AND status='running'` guard. Matches our single-assignee Unit checkout philosophy.

**REJECT the four-way taxonomy.**
Reason: Our design (PROJECT.md Key Decisions, ENTITY-DESIGN §Principle 6) pivoted to pulse-only. Session-start hook is our `timer+assignment+on_demand` unified. `automation` is deferred past v1. Don't import vocabulary we don't use.

### 1.2 `LogActivityInput` shape (`activity-log.ts` lines 25-35)

Paperclip's `logActivity()` takes:
```ts
{ companyId, actorType: "agent"|"user"|"system", actorId, action: string,
  entityType, entityId, agentId?, runId?, details? }
```
Maps almost 1:1 to our `records` table: `firm_id / actor_type / actor_id / event_type / target_entity_type / target_entity_id / details / run_id`.

**KEEP the shape — we already have it.**
Reason: Our Phase 1 `records` schema is a near-exact match. Validates the design. No change needed.

**REJECT the separate `agentId` field.**
Reason: Paperclip carries `agentId` alongside `actorId` because actor can be user-acting-on-behalf-of-agent. Our `actor_id` IS the member/board — simpler, no double-bookkeeping. Our way wins.

**ADAPT the live-events broadcast.**
Reason: Paperclip calls `publishLiveEvent()` + `_pluginEventBus.emit()` inside `logActivity()`. We have no realtime layer; we have a **hook injection layer**. The ADAPT: hook-layer reads recent `records` rows on session start and surfaces deltas ("Quill completed UNIT-003 since last session") in a new `<recent-activity>` tag. v1 or v2 call. Flag in brief for consideration.

### 1.3 Cost event shape (`costs.ts` lines 49-77)

Paperclip's `costEvents` row: `{companyId, agentId, occurredAt, costCents, billingType: "metered_api"|"subscription_included"|"subscription_overage", cachedInputTokens, provider, biller, ...}`. Aggregation via SUM over UTC-month windows.

**KEEP the per-Run cost event pattern.**
Reason: Our `usage_event` is ONE row per Run (richer detail). Paperclip may have multiple cost events per heartbeat — they log per API call. Our granularity is better for v1; simpler.

**ADAPT the `billingType` enum.**
Reason: We have `plan: claude_pro_100 | claude_pro_200 | api | custom`. Paperclip's three-value billing_type is lower-cardinality but cleaner semantically (does a dollar count against included subscription or overage?). Consider adding a `billing_type` column post-v1 for reporting clarity. Not a Phase 2 blocker.

**REJECT the live budget-enforcement flow.**
Reason: Paperclip's `costService.createEvent()` computes agent+company month spend on every event and calls `budgetService` hooks to pause agents at thresholds. Our budget is **scaffolded-off** in v1 (ENTITY-DESIGN §Member budget). Don't wire enforcement yet. Read the pattern; skip the code.

### 1.4 Heartbeat run summary (`heartbeat-run-summary.ts`)

Paperclip's `mergeHeartbeatRunResultJson()` and `buildHeartbeatRunIssueComment()` funnel run output into two sinks: structured summary JSON + a required issue comment. Fields parsed: `summary, result, message, error, total_cost_usd`.

**KEEP the "run outputs get denormalized back to the unit" instinct.**
Reason: Our `member_run.outputs` and `unit.outputs` both exist. `run-record` hook should roll the Run's outputs up to its Unit on completion. Matches our ENTITY-DESIGN §Entity 8 note: "A Run's outputs get rolled up to its Unit on completion."

**REJECT the required-issue-comment invariant (for v1).**
Reason: Paperclip forces every run to post an issue comment, with retry-once-then-fail. Our Comments are polymorphic and optional; forcing a comment per Run is ceremony we don't need yet. Consider for v2 if audit trails feel thin.

### 1.5 Execution policy (`execution-policy.md`)

Paperclip has a runtime-enforced review/approval stage system. Executor completes → status forced to `in_review` → reviewer assigned → approver assigned → status transitions to `done`. Changes-requested loop routes back to executor.

**ADAPT — weak claim on v1 Gates, strong claim on v2.**
Reason: Our `gate` entity supports this conceptually. v1 Gate is "Board decides yes/no"; no runtime enforcement of stages. Phase 2 hooks should NOT implement execution-policy enforcement. But the SHAPE (stage list with participants, `executionState` tracking current stage) is a blueprint if/when we add multi-stage gates. Record in brief's "Post-v2 patterns to remember" section.

### 1.6 Adapter 3-method shape (`adapters/overview.md` + LANDSCAPE.md §5)

Paperclip adapter: `execute(context)` + `parse(output)` + `test(env)`. We specified `invoke / status / cancel` in PROJECT.md Key Decision 7.

**REJECT Paperclip's naming; KEEP our naming.**
Reason: `invoke / status / cancel` is the canonical async-task-runner shape (Kubernetes, Celery, Temporal). Paperclip's `execute` is their own. We locked `invoke / status / cancel` in Phase 0. Don't relitigate. Note divergence in brief.

**ADAPT the `test()` method for v2.**
Reason: Paperclip's `test()` runs adapter-specific diagnostics before saving agent config ("is Claude CLI installed and authenticated?"). Not a hook-layer concern, but worth recording for Phase 6 MCP or Phase 8 installer. Flag in brief's "Deferred patterns worth remembering" list.

### 1.7 Session resume (`agents-runtime.md §4`)

Paperclip stores adapter session IDs for resumable runtimes (Claude supports `--resume`). Next heartbeat reuses the saved session.

**REJECT for v1.**
Reason: Our Member Runs are session-scoped. Claude Code sessions already handle their own continuity. Adding framework-layer session-resume tracking is duplication. Revisit only if multi-Run-per-Unit use cases surface.

### 1.8 Plugin event bus (`activity-log.ts` lines 72-93)

Paperclip forwards significant activity events to registered plugins via `_pluginEventBus.emit()`.

**REJECT for Phase 2.**
Reason: Phase 6 MCP is the analog for us; adding plugin hooks in Phase 2 is out-of-scope creep. LANDSCAPE.md §7 Gap B/E already captures this pattern.

### 1.9 Redaction in activity log (`activity-log.ts` line 41-44)

Paperclip sanitizes activity `details` via `sanitizeRecord()` + `redactCurrentUserValue()` before insert.

**ADAPT lightly.**
Reason: Our `records.details` is freeform JSON. Hook-layer writers should NOT dump full prompt text, secret values, or credential shapes. Add a simple redaction pass to the `unit-completion` and `run-record` hooks — strip fields matching `/token|key|secret|password/i`. One-function utility, not a framework. Record in brief's Decisions section.

---

## 2. Keep / Adapt / Reject Summary Table

| # | Pattern | Decision | Rationale |
|---|---------|----------|-----------|
| 1 | Wakeup coalesce ("one active Run per agent") | KEEP | Prevents duplicate Runs on double-pulse; matches single-assignee Unit checkout |
| 2 | `LogActivityInput` shape | KEEP | Already matches our `records` schema |
| 3 | Four-way wakeup taxonomy | REJECT | Pulse-only design already locked |
| 4 | Separate `agentId` + `actorId` on activity | REJECT | Our `actor_id` is simpler and correct |
| 5 | Live events / plugin bus on activity | ADAPT (v2) | Consider `<recent-activity>` tag for session-pulse in v2 |
| 6 | Per-Run cost event granularity | KEEP | Our `usage_event` is richer than Paperclip's per-call events |
| 7 | `billingType` enum (metered/subscription) | ADAPT (v2) | Cleaner reporting; consider adding column post-v1 |
| 8 | Live budget-enforcement flow | REJECT (v1) | Budget scaffolded off; wire with ccusage integration later |
| 9 | Run outputs → Unit outputs rollup | KEEP | Already in ENTITY-DESIGN note; `run-record` hook implements |
| 10 | Required-issue-comment invariant | REJECT (v1) | Ceremony we don't need; reconsider if audit feels thin |
| 11 | Execution policy (review/approval stages) | ADAPT (v2+) | v1 Gate is yes/no only; multi-stage for later |
| 12 | `invoke / status / cancel` vs `execute/parse/test` | REJECT PCL naming | Our naming is canonical; Paperclip's is bespoke |
| 13 | `test()` adapter diagnostic method | ADAPT (Phase 6/8) | Phase 8 installer concern |
| 14 | Session resume via adapter session IDs | REJECT (v1) | Claude Code handles its own; no need for framework tracking |
| 15 | Plugin event bus | REJECT (Phase 2) | Phase 6 MCP is the right layer |
| 16 | Redaction of activity details on write | ADAPT | Simple regex redaction in hook writers; credential-hygiene matters |

**Total decisions: 16** (spec required ≥6).

---

## 3. Pattern Gaps — Paperclip Has, We Don't

Listed for awareness, not action:

- **Real-time live-events broadcast** — Paperclip pushes UI updates; we have no UI.
- **Adapter plugin marketplace** — external npm adapters; we have v1-internal adapters only.
- **Multi-company / companyId scoping enforcement** — we have `firm_id` but single-Firm v1.
- **Budget hard-stop auto-pause** — Paperclip pauses agents at 100% budget; we defer enforcement.
- **Session-ID resume for adapters** — Claude Code handles internally.

---

## 4. Pattern Gaps — We Have, Paperclip Doesn't

Worth remembering for content angle:

- **Polymorphic Goal/Comment** — Paperclip goals are hierarchical-level only; ours attach anywhere.
- **AC inline on parent entity** — Paperclip has no concept; we have `acceptance_criteria` arrays on Operation/Project/Unit.
- **Earn-the-pace throughput rule** (MEMBERS-DESIGN §Firm-Level Principle) — no Paperclip analog.
- **Board = yes/no authority, not scope-definer** — Paperclip Board can create/edit; our principle is narrower and healthier.
