# Cadre

*Coordinated Agent Deployment Runtime Engine*

**Run your AI agents like a company.**

Cadre is a framework for orchestrating a persistent roster of AI Members with roles, ownership, atomic work assignment, and board-level approval gates. Built for Claude Code, designed runtime-agnostic from v1 via a formal Contract interface (swap to OpenClaw, Codex, or any agent runtime that can execute a prompt).

```bash
pip install cadre        # (once published to PyPI; dev install below)
cadre init . --demo --install-hooks
```

Open the directory in Claude Code. The session-start hook injects your active roster, pending approvals, and goal health into every session.

---

## Why this exists

Every Claude Code session starts cold. You re-explain context, re-assign work, re-approve the same decisions. The moment you try to run more than one agent or split work across sessions, the whole thing falls apart: who owns what? which Unit is claimed? who's overloaded? who decides when to hire?

Cadre gives you a persistent structure that survives between sessions — a Firm of Members, each with a Contract (runtime), claiming Units (atomic work) under Operations (departments) toward Goals (KPIs), with Gates (your approval) for significant actions. The framework tracks it. The session hook surfaces it. You stop hand-driving and start governing.

---

## Core vocabulary

| Entity | Analogy | What it does |
|---|---|---|
| **Firm** | Your company | One Cadre install = one Firm. Has a name (e.g., `chrisai`). |
| **Member** | Employee | An AI agent with a role (Writer, Editor, CMO). Named (Quill, Sterling, Sage). |
| **Contract** | Employment agreement | How a Member executes: which runtime (Claude Code / OpenClaw / Codex), which skills/commands they can use. |
| **Operation** | Department | Long-running business function ("Content Publishing"). |
| **Project** | Initiative | Scoped deliverable inside an Operation ("Blog Cadence — First 8 Posts"). |
| **Unit** | Ticket | Atomic work item. Claimed atomically — no two Members can grab the same one. |
| **Goal** | KPI | Measurable outcome attached to any entity. Hooks surface stale goals. |
| **Gate** | Manager approval | You (the Board) approve or reject significant actions (hires, policy changes). |
| **Member Run** | Timesheet entry | One execution of a Member against a Unit. Immutable audit trail. |
| **Records** | Event log | Immutable append-only history of every state transition. |

All 14 entity types and schemas are documented in [`ENTITY-DESIGN.md`](ENTITY-DESIGN.md).

---

## What it does in practice

### 1. Persistent roster (session-pulse hook)

Every Claude Code session starts with an injection showing:
- `<active-roster>` — who's in the Firm, role, current load
- `<pending-gates>` — what's waiting on your approval
- `<goal-health>` — which Goals are stale or off-track

No more re-explaining who does what. The context is always there.

### 2. Autonomous delegation

Sterling (CMO) reads pending work and queues Units for Quill (Writer). Quill runs `/quill:run full` to execute. The reports-to hierarchy enforces who can delegate to whom. You approve the big things; the team handles the rest.

### 3. Gap detection

```bash
firm detect-gaps
```

Surfaces unclaimed Units, overloaded Members, stale Goals, and coverage gaps ("no one on the team covers video editing"). Sterling can then `propose_hire`, which opens a hire-member Gate for your approval. The Firm grows itself under your direction.

### 4. MCP server

33 entity tools exposed over MCP. Members can create Units, update Goals, and request Gates programmatically mid-session — not just through slash commands.

---

## Install (development)

```bash
cd apps/agent-company-architecture
pip install -e ".[dev]"
cadre --help
pytest        # 548 tests across 8 phases
```

Both `cadre` and `firm` console scripts route to the same CLI. The import package is `firm`; the distribution name is `cadre`. (Divergent dist/import names are standard Python — see `bs4`/`beautifulsoup4`.)

---

## Quickstart

### Bootstrap a demo Firm

```bash
mkdir /tmp/my-firm && cd /tmp/my-firm
cadre init . --demo --install-hooks
```

Creates:
- `.firm/firm.db` — SQLite store, all entities
- `.claude/hooks/cadre-session-pulse.py` — session-start hook
- `.claude/settings.json` — hook registered
- Demo Firm with Members `Pen` (Writer) + `Edit` (Editor), one Operation, one Project, one unclaimed Unit

Open `/tmp/my-firm` in Claude Code. The first session pulse will show your roster.

### Run the end-to-end smoke test

```bash
bash scripts/e2e-test.sh
```

One command, ~30 seconds. It's a real integration test, not a unit-test suite. In order:

1. Cleans `/tmp/cadre-e2e-*` and `dist/`
2. Builds a wheel from source (`python -m build`)
3. Creates a fresh venv at `/tmp/cadre-e2e-venv` and `pip install`s the wheel there (no PYTHONPATH tricks — real install path)
4. Verifies `cadre` + `firm` console scripts resolve and `--version` prints correctly
5. Runs `cadre init /tmp/cadre-e2e-workspace --demo --install-hooks`
6. Asserts `.firm/firm.db`, the session-pulse hook, and `settings.json` all exist and the hook is registered
7. Opens the DB, verifies demo Firm structure (2 Members named Pen + Edit, 1 op, 1 project, 1 unclaimed unit)
8. Runs `detect_gaps` against the demo DB, confirms it surfaces the unclaimed Unit
9. Re-runs `cadre init` to verify idempotence (no duplicate entities, "already installed" message)
10. Imports the MCP module and asserts 33 tools are registered

Green checkmarks on every step = installer path works end-to-end for any user starting from a clean machine. Red X = it bails loudly at the first failure with the failing command's output.

To actually test the hook in a live Claude Code session, open the e2e workspace:

```bash
cd /tmp/cadre-e2e-workspace
# open this directory in Claude Code
# session start should inject <active-roster> with Pen + Edit
```

---

## CLI reference

| Command | Purpose |
|---|---|
| `cadre init <workspace>` | Create `.firm/firm.db` and run migrations |
| `cadre init . --demo` | Seed generic demo Firm |
| `cadre init . --install-hooks` | Install session-pulse hook into `.claude/hooks/` |
| `cadre unit complete <id> --member <id>` | Mark a Unit done; writes Records row |
| `cadre run end <run_id> --status completed` | Finalize a Member Run; writes usage + records |
| `firm pulse` | Stateless PULSE orchestrator — runs due Members per frequency/budget/validation |

Run `cadre --help` for full flags.

---

## Hooks

| Hook | Fires on | Injects / writes |
|---|---|---|
| `session-pulse` | `SessionStart:startup` | `<active-roster>`, `<pending-gates>`, `<goal-health>` tags |
| `unit-completion` | CLI call (not yet auto-hooked) | Records row + Project AC update |
| `run-record` | CLI call | RUN + USG rows with credential redaction |

`unit-completion` and `run-record` are callable functions in v1 rather than Claude Code hooks because v1 has no auto-trigger surface for them. Phase 6 MCP is the layer they'll auto-hook through in v0.2.

---

## MCP surface (33 tools)

Python FastMCP server wrapping all 10 `firm.services` modules. Full entity CRUD: `firm_*`, `member_*`, `contract_*`, `operation_*`, `project_*`, `unit_*`, `gate_*`, `goal_*`, `comment_*`, `document_*`, plus `detect_gaps` and `propose_hire`.

Members can manipulate their own Firm's state during Runs — create sub-Units, close Gates, update Goal metrics — without the operator being in the loop for every write.

---

## Contract runtimes (runtime-agnostic design)

Member (identity) and Contract (runtime binding) are separable entities in Cadre. Cadre ships with one reference Contract runtime: `firm.contracts.claude_code`. To target another runtime, implement the 3-method `ContractRuntime` Protocol:

```python
invoke(conn, contract, member, unit, *, cwd) -> InvokeResult
status(handle) -> RunStatus
cancel(handle) -> bool
```

Stub templates live in [`templates/contracts/`](templates/contracts/) (OpenClaw, Codex). Full authoring guide in [`docs/contracts.md`](docs/contracts.md).

> Cadre calls these **Contract runtimes**, not *adapters*. "Adapter" is Paperclip vocabulary. See [CADRE-VS-PAPERCLIP.md](CADRE-VS-PAPERCLIP.md) for the terminology and architectural differences.

---

## What's shipped vs. not yet

**Shipped (v0.1, 548 tests green):**
- 14 entity types with full CRUD, SQLite store, atomic Unit checkout, dependency cycle detection
- PULSE handler (stateless orchestrator with frequency/budget/validation gating)
- 10 service modules, 33 MCP tools, gap detection + propose-hire flow
- Demo Firm seed, one-command installer, session-pulse hook with auto-registration
- Public docs, Contract runtime authoring guide, OpenClaw + Codex stub templates

**Not yet:**
- PyPI publish (install from source for now)
- UI / dashboard — CLI + hook injection is the interface
- Real OpenClaw / Codex Contract runtimes (stubs only; implement when those runtimes are live for you)
- Scheduled `cadre pulse` cron — Members activate on trigger, not on a timer
- Multi-operator governance — v0.1 is single-Board

---

## Architecture at a glance

```
Board (human, yes/no authority)
   ↓
Firm (chrisai)
   ├─ Operation (Content Publishing)
   │    └─ Project (Blog Cadence — First 8 Posts)
   │         └─ Units (atomic, claimed by one Member)
   ├─ Members
   │    ├─ Sterling (CMO) ──reports_to──> Board
   │    │    ├─ Quill (Writer) ──reports_to──> Sterling
   │    │    └─ Sage  (Strategist) ──reports_to──> Sterling
   │    └─ (future: Echo — social repurposing, Harbor — video)
   ├─ Contracts (one per Member: runtime + skill loadout)
   ├─ Goals (attached polymorphically via parent_ref)
   └─ Gates (Board approval queue)
```

| Layer | Tech |
|---|---|
| Data store | SQLite (`.firm/firm.db`) — ACID, stdlib only, zero deps |
| Core / hooks / CLI / MCP | Python (one language, zero serialization boundary) |
| Contract runtimes | Formal `ContractRuntime` Protocol (structural typing) |
| Operator interface | Slash commands + session-pulse injection + MCP tools |

---

## How Cadre differs from Paperclip

Cadre and [Paperclip](https://github.com/) both encode the "AI-operated company" mental model, but they target different operators with different assumptions. Paperclip bundles scheduling, execution, UI, governance, and audit into a persistent Node + Postgres server — Salesforce-shaped. Cadre decouples scheduling from execution and delegates platform integration to the MCP ecosystem — Git-shaped.

|  | Paperclip | Cadre |
|---|---|---|
| **Mental model** | Salesforce for AI companies | Git for AI companies |
| **Activation** | Scheduler-bound (cron inside a persistent server) | Stateless PULSE — trigger-agnostic (cron, session hook, CLI, CI, systemd, any caller) |
| **Operator surface** | React dashboard at localhost:3100 | Claude Code session context (no separate UI) |
| **Persistence** | Postgres (embedded PGlite or hosted) | SQLite (stdlib, single file) |
| **Process model** | Persistent Node server + workers | Python package, no daemon |
| **Identity vs runtime** | `agent.adapter_config` JSONB | Member and Contract as separate entities |
| **Platform integrations** | Custom plugins via TS SDK | MCP servers scoped per-Member via `Contract.skill_loadout` |
| **Runtime swap** | Adapter + adapter_config JSONB | `ContractRuntime` Protocol (Python, 3 methods) |
| **Audit** | `activity_log` + `cost_events` + `heartbeat_run_events` + `budget_incidents` | `member_run` + immutable `Records` stream |
| **Governance** | Approvals as DB state machine rows | Rules-as-code (CARL) + Gate entities |
| **Goal ancestry** | Explicit `parent_id` + full materialization | Polymorphic `parent_ref` (any entity) + denormalized `goal_ids` |
| **Tables** | ~65 | ~14 |

**Use Paperclip** for multi-operator teams that want the framework to own the scheduler, a web dashboard for non-engineers, and enterprise governance/audit needs.

**Use Cadre** if you want your scheduler to be *your* scheduler (cron, systemd, CI, hooks), MCP as the platform-integration surface, zero-infra deployment (`pip install`), and governance-as-code via CARL rules.

These aren't the same product at different scales. They occupy different categories — most critically, they give different answers to "who owns the scheduler" and "how do Members reach real platforms." Full breakdown with entity tables, architectural assumptions, shared foundations, integration architecture, and anti-patterns to avoid: [CADRE-VS-PAPERCLIP.md](CADRE-VS-PAPERCLIP.md).

---

## Key design principles

1. **Standalone** — independent of BASE/CARL/PAUL. Own `.firm/`, own hooks, own MCP.
2. **Identity/runtime split** — Member and Contract separable. Swap runtimes without rewriting Members.
3. **Firm-scoped from day one** — `firm_id` on every row. Multi-Firm migration is ~1-2 hours.
4. **Trigger-agnostic PULSE** — `firm pulse` is a stateless function. Cron, session hooks, CLI, CI, systemd — any trigger fires the same handler. Cadre does not own the scheduler.
5. **Polymorphic modifiers** — Goals and Comments both attach via `parent_ref` (entity_type + entity_id). Every Unit traces back to the Firm via Project → Operation → Firm; Goals attach at any level of the chain.
6. **MCP-first platform integrations** — Members reach Meta Ads, Google Analytics, CRMs, and any SaaS through MCP servers declared in `Contract.skill_loadout`. No Cadre-specific plugin SDK.
7. **Immutable where it matters** — Comments, Records, Usage Events never rewritten.
8. **Hard-gated dependencies** — Units can't run until `depends_on` is complete.
9. **Hybrid priority** — categorical bucket + decimal stack rank for deterministic AI ordering.
10. **Atomic Unit checkout** — single SQL statement, no race window: `UPDATE unit SET claimed_by = ? WHERE id = ? AND claimed_by IS NULL RETURNING *`.
11. **Board = yes/no authority, not scope-definer** — the team runs the Firm; the Board approves or rejects.

---

## References

- [`PLANNING.md`](PLANNING.md) — original planning artifact
- [`ENTITY-DESIGN.md`](ENTITY-DESIGN.md) — all 14 entity schemas + full design decision history
- [`MEMBERS-DESIGN.md`](MEMBERS-DESIGN.md) — concrete roster (Quill, Sterling, Sage), Operations, Goals, Projects, Contracts
- [`NAMING.md`](NAMING.md) — Cadre vocabulary map (public brand vs. internal package)
- [`PULSE-SPEC.md`](PULSE-SPEC.md) — stateless pulse orchestrator spec
- [`docs/contracts.md`](docs/contracts.md) — Contract runtime authoring guide
- [`CADRE-VS-PAPERCLIP.md`](CADRE-VS-PAPERCLIP.md) — architectural and positioning differences vs Paperclip

---

## License

MIT.

---

## Acknowledgments

The "AI-operated company" mental model was formatively explored by [Paperclip](https://github.com/), a 53k-star project validating the concept for multi-operator orgs on 24/7 cron heartbeats. Cadre takes that frame, inverts the scheduling model, rescopes for solo operators, and keeps its own vocabulary — **Firm / Member / Unit / Gate / Contract runtime** — because the products are different and the language should be too. See [CADRE-VS-PAPERCLIP.md](CADRE-VS-PAPERCLIP.md) for the full architectural and positioning breakdown.
