# CADRE Pulse Server Spec

> Refactoring the activation model from session-start hooks to a persistent Python server with cron-driven Member execution, hard budget enforcement, one-shot rich prompts, and output validation hooks.

**Created:** 2026-04-15
**Status:** Draft spec - pending review before implementation
**Supersedes:** PLANNING.md Phase 2 (Hook Layer) activation model, ENTITY-DESIGN.md principle #6 (session-pulse activation), LANDSCAPE.md Section 12 (Scheduler / Activation Model)

---

## Problem

The current design fires Members only on session-start hooks. This means:

- Members never run unless Chris opens a terminal
- No background work happens during business hours
- Every Member invocation requires operator presence
- The framework can't demonstrate autonomous team behavior

The goal is a persistent server that runs Members on schedule, autonomously, within business-hours constraints, with hard budget stops and output quality validation.

---

## Architecture Decision: Python

**Decision:** The Pulse server is Python.

**Rationale:**
- All existing framework code is Python (hooks, installer, core, services, migrations)
- pyproject.toml already defines the `firm` package with Python 3.11+
- SQLite access via stdlib `sqlite3` - no ORM translation layer needed
- asyncio + subprocess for spawning `claude --print` child processes
- FastAPI or bare asyncio event loop for the server - no heavy framework required
- Every person who installs CADRE and already runs the framework has Python. Adding Node.js as a runtime dependency for a single component fractures the install story.

**Trade-off acknowledged:** Node.js has marginally better stream-json parsing ergonomics (Paperclip's reference implementation is TypeScript). Python's `json.loads` per line is adequate for `--output-format stream-json` since each line is a complete JSON object.

---

## Execution Model

### How a Member Run Works

When the scheduler fires a Member:

1. **Pre-flight checks**
   - Member status is `active`
   - No existing run `in_progress` for this Member (max 1 concurrent)
   - Budget check passes (see Budget Enforcement below)
   - Business-hours gate passes
   - Member has at least one assigned Unit in `pending` or `in_progress` status

2. **Prompt assembly** (one-shot, no session resume)
   - Member instructions file (personality, voice rules, constraints)
   - Firm context snapshot (roster, goals, pending gates)
   - Unit context (the specific Unit being executed, its AC, dependencies, parent Project/Operation/Goal chain)
   - Skill directives (which `/skill:command` to invoke and how)
   - Output format requirements (what files to produce, where to write them)

3. **Spawn child process**
   ```
   claude --print - \
     --output-format stream-json \
     --verbose \
     --dangerously-skip-permissions \
     --append-system-prompt-file <member-instructions.md> \
     --model <contract.model> \
     --max-turns <contract.max_turns> \
     --add-dir <workspace-root>
   ```

4. **Stream parsing**
   - Read stream-json lines as they arrive
   - Capture token usage from usage events
   - Capture session_id from init event
   - Capture result text and tool calls
   - Enforce timeout (kill process if exceeded)

5. **Output validation** (Ralph Wiggum pattern - see below)

6. **Post-run recording**
   - Write `member_run` record (RUN-*)
   - Write `usage_event` record (USG-*)
   - Write `records` entry (LOG-*)
   - Update Unit status if work completed
   - Update budget tracking

### No Session Resume

**Decision:** Every Member Run is a fresh context window. No `--resume`.

**Rationale:**
- Resume requires storing session IDs and matching cwd/prompt bundles across runs - fragile
- A well-assembled one-shot prompt with rich context produces better results than a stale resumed session with accumulated drift
- Resumed sessions can't update their system prompt (Claude Code rejects `--append-system-prompt-file` on resume)
- Fresh sessions get clean tool permissions, clean context, clean cache
- Cost is marginally higher (no prompt cache hits across runs) but predictability is worth it

**Implication:** The prompt assembly phase is critical. The prompt IS the entire context the Member operates with. It must be rich, strategic, and self-contained. Every run should read as if the Member is being briefed by their manager for a specific task.

---

## Prompt Assembly Strategy

Since every run is one-shot, the assembled prompt is the single most important artifact. Structure:

### 1. Member Instructions File (static per Member)

Written to `.firm/instructions/<member-id>.md` during Member creation. Contains:
- Member identity (name, role, personality, voice)
- Standing orders (what this Member always does and never does)
- Skill loadout documentation (which skills to use, in what order)
- Output format expectations
- Quality standards

### 2. Firm Context Snapshot (dynamic per run)

Generated at dispatch time by reading `.firm/firm.db`:
- Active roster (who else is on the team, their roles)
- Goal health for this Member's Operation
- Pending gates relevant to this Member
- Recent Records entries (last 5 relevant log entries for situational awareness)

### 3. Unit Briefing (dynamic per run)

The specific work assignment:
- Unit details (name, description, AC, priority)
- Parent chain context (Project name/description, Operation, inherited Goals)
- Dependencies status (all `depends_on` Units and their current state)
- Previous attempts (if this Unit was reset by a supervisor - include the supervisor's feedback)
- Expected outputs (what files/artifacts the run should produce)

### 4. Execution Directive

The actual instruction:
```
You are {member.name}, {member.role} at {firm.name}.

Your assignment: {unit.name}
{unit.description}

Acceptance criteria:
{formatted_ac_list}

Execute using the following skills: {skill_directives}

When complete, write your outputs to: {output_paths}
```

---

## Output Validation (Ralph Wiggum Pattern)

### Concept

A Claude Code Stop hook fires when the `claude --print` process completes its first response. The hook inspects the output against the Unit's acceptance criteria. If validation fails, the hook injects a continuation message that forces one retry within the same session.

### Implementation

The Pulse server doesn't use Claude Code's hook system directly (since it spawns `--print` processes, not interactive sessions). Instead, validation is built into the server's post-run pipeline:

```
spawn claude --print → parse output → validate → (retry once if failed) → record
```

### Validation Flow

1. **Run completes.** Server has full output.
2. **Validator checks:**
   - Did the Member produce the expected output files?
   - Do outputs meet minimum size/format requirements?
   - Did the Member report any AC as unresolvable?
   - Did the process exit cleanly (exit code 0)?
3. **If validation passes:** Record run as `completed`, update Unit.
4. **If validation fails AND retry_count < 1:**
   - Spawn a NEW `claude --print` process (fresh session - no resume)
   - Prompt includes: original briefing + "Your previous attempt failed validation. Issues: {validation_errors}. Produce corrected output."
   - `retry_count` increments
5. **If validation fails AND retry_count >= 1:**
   - Record run as `completed_with_issues`
   - Flag Unit for supervisor review
   - The output from attempt 2 is what it is

### Configurable Per Contract

```json
{
  "runtime_config": {
    "validation": {
      "enabled": true,
      "max_retries": 1,
      "validators": ["file_exists", "min_word_count", "ac_self_report"],
      "on_final_failure": "flag_for_review"
    }
  }
}
```

### Supervisor Pattern (Post-V1)

Supervisors (Sterling) review completed Units from their reports (Quill, Sage). A supervisor run:
1. Reads the completed Unit's outputs
2. Evaluates against AC and strategic fit
3. Either approves (Unit stays `done`) or resets (Unit goes back to `pending` with supervisor feedback attached as a Comment)
4. Reset Units get re-queued for the original Member's next Pulse

This is distinct from the Ralph Wiggum retry pattern:
- **Retry:** Same session, same Member, mechanical validation (did you produce the file?)
- **Supervisor review:** Separate session, different Member, strategic evaluation (is this good enough?)

---

## Business Hours Gate

### Configuration

```json
{
  "schedule": {
    "timezone": "America/Chicago",
    "business_hours": {
      "start": "07:00",
      "end": "17:00",
      "days": ["mon", "tue", "wed", "thu", "fri"]
    },
    "override_open": false
  }
}
```

Stored in `.firm/firm.db` on the `firm` table (new column: `schedule`).

### Behavior

- Pulses that fire outside business hours are silently skipped
- The cron expression still ticks, but the business-hours gate blocks dispatch
- `override_open: true` allows manual override for one-month-day sprints or weekend pushes
- Server logs skipped Pulses for transparency

---

## Budget Enforcement

### Decision: Hard Enforcement from V1

Budget enforcement is ON by default, not scaffolded-off. Every Member has limits.

### Budget Schema (Contract-level)

```json
{
  "budget": {
    "enforcement": "hard",
    "period": "monthly",
    "limits": {
      "max_runs_per_period": 60,
      "max_input_tokens_per_run": 200000,
      "max_output_tokens_per_run": 16000,
      "max_total_cost_per_period_usd": 50.00
    },
    "on_limit": "pause_member",
    "alert_threshold_pct": 80
  }
}
```

### Enforcement Points

1. **Pre-flight (before spawn):**
   - Count runs this period. If `>= max_runs_per_period`: block, set Member status to `budget_paused`.
   - Sum cost this period. If `>= max_total_cost_per_period_usd`: block, pause.
   - At 80% threshold: log a warning Record and surface in next `<goal-health>` injection.

2. **Mid-run (stream parsing):**
   - Track cumulative tokens as stream-json events arrive.
   - If `input_tokens > max_input_tokens_per_run`: kill the process (SIGTERM, then SIGKILL after grace period).
   - Note: this is a safety valve, not normal operation. A well-scoped prompt shouldn't hit this.

3. **Post-run (recording):**
   - Write `usage_event` with actual token counts and computed cost.
   - Update running period totals.

### Cost Calculation

Token costs derived from model pricing. Stored in server config, not in the DB:

```python
MODEL_COSTS = {
    "claude-sonnet-4-6": {"input_per_1m": 3.00, "output_per_1m": 15.00},
    "claude-opus-4-6": {"input_per_1m": 15.00, "output_per_1m": 75.00},
    "claude-haiku-4-5-20251001": {"input_per_1m": 0.80, "output_per_1m": 4.00},
}
```

**Note:** These costs apply to API usage. Subscription plan runs (Pro) don't have per-token costs but DO have rate limits. Budget enforcement for subscription plans tracks `max_runs_per_period` and window percentage, not USD.

### Budget Status in Session Pulse

The existing `<goal-health>` injection gains a `<budget-health>` sibling:

```
<budget-health>
  MEM-001 Quill: 12/60 runs (20%), $8.40/$50.00 (17%) — healthy
  MEM-002 Sterling: 3/30 runs (10%), $2.10/$30.00 (7%) — healthy
  MEM-003 Sage: 28/30 runs (93%), $18.50/$20.00 (93%) — WARNING: approaching limit
</budget-health>
```

---

## Schema Changes Required

### New Columns on `contract`

```sql
ALTER TABLE contract ADD COLUMN Pulse_config TEXT;  -- JSON
ALTER TABLE contract ADD COLUMN validation_config TEXT;  -- JSON
ALTER TABLE contract ADD COLUMN budget_config TEXT;      -- JSON (migrated from member.budget)
```

**Pulse_config** shape:
```json
{
  "cron": "0 */1 * * MON-FRI",
  "timeout_sec": 300,
  "grace_sec": 30,
  "model": "claude-sonnet-4-6",
  "max_turns": 25,
  "cwd": "/home/chriskahler/chris-ai-systems",
  "instructions_file": ".firm/instructions/MEM-001.md",
  "extra_args": [],
  "env": {}
}
```

**validation_config** shape:
```json
{
  "enabled": true,
  "max_retries": 1,
  "validators": ["file_exists", "min_word_count", "ac_self_report"],
  "on_final_failure": "flag_for_review"
}
```

**budget_config** shape: (see Budget Schema above)

### Budget Migration

`member.budget` currently holds budget data. Migration moves it to `contract.budget_config` since budget is per-runtime-configuration, not per-identity. A Member reassigned to a cheaper runtime gets different budget parameters.

### New Column on `member_run`

```sql
ALTER TABLE member_run ADD COLUMN retry_of_run_id TEXT REFERENCES member_run(id);
ALTER TABLE member_run ADD COLUMN invocation_source TEXT DEFAULT 'manual'
    CHECK (invocation_source IN ('manual', 'Pulse', 'supervisor', 'retry'));
ALTER TABLE member_run ADD COLUMN prompt_snapshot TEXT;  -- the assembled prompt (for debugging/audit)
ALTER TABLE member_run ADD COLUMN validation_result TEXT;  -- JSON: {passed: bool, errors: [...]}
```

### New Column on `firm`

```sql
ALTER TABLE firm ADD COLUMN schedule TEXT;  -- JSON (business hours config)
```

### New Table: `budget_period`

Rolling budget tracking per Member per period:

```sql
CREATE TABLE budget_period (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firm(id) ON DELETE CASCADE,
    member_id TEXT NOT NULL REFERENCES member(id) ON DELETE CASCADE,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    run_count INTEGER NOT NULL DEFAULT 0,
    total_input_tokens INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'closed', 'limit_reached')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX idx_budget_period_member ON budget_period(member_id, period_start);
```

---

## Server Architecture

### Package Structure

New module: `firm.server`

```
src/firm/
  server/
    __init__.py
    app.py              # Server entrypoint, asyncio event loop
    scheduler.py        # Cron engine, business-hours gate, Member dispatch queue
    dispatcher.py       # Reads Contract, assembles prompt, spawns claude --print
    parser.py           # Parses stream-json output into structured results
    validator.py        # Output validation (Ralph Wiggum pattern)
    budget.py           # Budget check/update/enforcement
    prompt_builder.py   # Assembles the one-shot prompt from DB state + templates
    config.py           # Server configuration (model costs, defaults)
```

### Server Lifecycle

```
firm server start
  → Load firm.db, read all active Members + Contracts
  → Parse Pulse_config.cron for each Member with a Contract
  → Register cron jobs in scheduler
  → Start asyncio event loop
  → Log: "CADRE server running. 3 Members scheduled. Business hours: 07:00-17:00 CDT"

[cron tick]
  → scheduler checks business-hours gate
  → for each due Member:
      → budget.pre_flight_check(member)
      → dispatcher.dispatch(member, unit)
      → parser.parse_stream(process)
      → validator.validate(output, unit.ac)
      → (retry once if needed)
      → budget.record_usage(member, usage)
      → repo.create(member_run)

firm server stop
  → Graceful shutdown: wait for in-flight runs (up to grace_sec)
  → Kill remaining processes
  → Log final status
```

### CLI Surface

```bash
firm server start                    # Start the Pulse server (foreground)
firm server start --daemon           # Start in background (writes PID file)
firm server stop                     # Graceful shutdown
firm server status                   # Show running state, next scheduled fires
firm server fire <member-id>         # Manual one-off dispatch (bypasses cron, respects budget)
firm server fire <member-id> --force # Bypass budget check too
```

### How to Run

**Development (Chris's workstation):**
```bash
cd ~/chris-ai-systems/apps/agent-company-architecture
pip install -e ".[dev]"
firm server start
```

Or as a VS Code task in `.vscode/tasks.json`:
```json
{
  "label": "CADRE Server",
  "type": "shell",
  "command": "firm server start",
  "isBackground": true,
  "problemMatcher": []
}
```

**Production (public release):**
```bash
pip install cadre  # or whatever the published package name becomes
cadre server start --daemon
```

---

## Dual Activation Model

The Pulse server does NOT replace the session-start pulse hook. Both coexist:

| Surface | Purpose | Cost | Activation |
|---------|---------|------|------------|
| **Session pulse hook** | Injects `<active-roster>`, `<pending-gates>`, `<goal-health>`, `<budget-health>` into Chris's interactive sessions | Zero (read-only DB query) | SessionStart hook (existing) |
| **Pulse server** | Fires Members autonomously on schedule to execute Units | Tokens per run | Persistent server process |

The pulse hook gives Chris Board-level visibility. The server gives Members autonomy. Both read from the same `.firm/firm.db`.

---

## Existing Code Impact

### Preserve (no changes)

- `install/firm-session-pulse.py` - session-start hook entrypoint
- `install/hook-installer.py` - hook registration
- `src/firm/hooks/session_pulse.py` - pulse renderer (add `<budget-health>` tag)
- `src/firm/core/db.py` - SQLite connection helpers
- `src/firm/core/repo.py` - Generic CRUD
- `src/firm/core/units.py` - Atomic checkout + cycle detection
- `src/firm/services/` - Validation helpers

### Modify

- `src/firm/hooks/session_pulse.py` - Add `render_budget_health()` function and wire into `render()`
- `src/firm/core/repo.py` - Add `budget_period` to `ALL_TABLES`, `JSON_COLUMNS`
- `src/firm/migrations/` - New migration `003_Pulse.sql` for schema changes
- `pyproject.toml` - Add server dependencies (none expected beyond stdlib for v1; maybe `croniter` for cron parsing)

### Create

- `src/firm/server/` - Entire new module (see Package Structure above)
- `.firm/instructions/` - Directory for Member instruction files
- Migration `003_Pulse.sql`

---

## Dependencies

Minimal. The server should run on stdlib + one cron library:

```toml
dependencies = [
    "croniter>=2.0",  # Cron expression parsing + next-fire calculation
]
```

Everything else is stdlib:
- `asyncio` for the event loop
- `subprocess` for spawning `claude --print`
- `sqlite3` for DB access
- `json` for stream-json parsing
- `signal` for process management
- `logging` for server logs

No web framework needed. This is not an HTTP server. It's a scheduler + process supervisor.

---

## What This Spec Does NOT Cover

- MCP server (Phase 6 in PLANNING.md - separate concern)
- Supervisor agent orchestration (Phase 5 - Leadership Layer, builds on top of Pulse)
- Multi-Firm support
- Web dashboard
- Public release packaging (Phase 8)

---

## Open Questions

1. **Process isolation:** Should each Member run in a git worktree (like Paperclip supports) or in the main workspace? Worktrees prevent file conflicts between concurrent Members but add complexity. V1 recommendation: main workspace, max 1 concurrent run across ALL Members (not per-Member). Revisit when concurrency is needed.

2. **Prompt snapshot storage:** Storing the full assembled prompt in `member_run.prompt_snapshot` is valuable for debugging but could bloat the DB. Alternative: write to `.firm/runs/<run-id>/prompt.md` on disk, store only the path in DB.

3. **Log streaming:** Should the server stream Member output to a log file in real-time (for live tailing) or only capture on completion? Real-time is more useful for debugging but adds file I/O.

4. **Subscription vs API billing:** Members on Chris's Pro subscription don't have per-token costs but DO have rate limits (5-hour rolling windows). Budget enforcement for subscription plans needs a different model than USD tracking. Possible approach: track window percentage via Claude's usage reporting in stream-json events.

5. **Instructions file generation:** Should `firm member create` auto-generate a starter instructions file from the Member's role/description/skills, or require manual creation? Auto-generation with manual refinement is likely the right call.

6. **Server restart recovery:** If the server crashes mid-run, the child `claude --print` process may still be running. On restart, the server should detect orphaned processes (via PID tracking) and clean up.

---

*Spec written 2026-04-15. Implementation pending review.*
