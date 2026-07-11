---
ontology: true
type: doc
domain: cadre
summary: The definitive guide for scaffolding a new Cadre firm from zero — anatomy, primitives, member specs, org shape, loadouts, full walkthrough, and the complete gotcha catalog
tags: [cadre, firm, scaffolding, guide, new-firm, checklist, walkthrough]
status: active
relatedTo: [cadre-engineering-handoff, lab-firm-remediation]
---

# Cadre Firm Scaffolding Guide — standing up a new firm from zero

Written 2026-07-10 by robin, immediately after remediating the `lab` firm end-to-end in a live boardroom session. Every CLI invocation, schema, and gotcha below was verified against the running framework (`~/ops-sys/toolbox/frameworks/05-exp-cadre`) or observed live — nothing is guessed. Companion doc: `docs/ENGINEERING.md` (system internals; when the two disagree, the code wins, then fix the docs).

A firm = a workspace folder + a SQLite entity DB + AI Members spawned as headless `claude --print` subprocesses on a pulse heartbeat, governed by a human Board through Gates/Escalations, with every action audited through one service layer.

---

## 1. Firm anatomy

```
~/firms/<folder>/                  # folder name is operator-facing ONLY — the firm id lives in the DB firm row
├── CLAUDE.md                      # charter — binds every session opened in the workspace (§ below)
├── .venv/                         # WSL-only venv, editable install of the cadre framework
├── .mcp.json                      # the ONLY MCP servers members get (spawn passes --strict-mcp-config)
├── .claude/
│   ├── settings.json              # SessionStart hook → session-pulse.sh
│   ├── hooks/                     # session-pulse trio (roster/gates/goals injection) — copy from chrisai, sed paths
│   └── commands/pulse.md          # /pulse — the Board Proxy governance protocol
├── .firm/
│   ├── firm.db                    # ALL entities (SQLite, WAL). WSL-only access. Never raw-UPDATE state.
│   ├── last-pulse.json            # outcome of the last pulse (ran/skipped/errors/skip_reasons)
│   ├── protocols/                 # *.md here injects into EVERY member's prompt
│   │   ├── 10-squad.md            # (optional) squad protocol, if the squad tool is installed
│   │   └── _member/<MEM-ID>/*.md  # per-member fragments (e.g. 50-squad-contract.md) — that member only
│   └── dashboard/views.json       # (optional) custom boardroom views — see ENGINEERING.md §5
├── scripts/seed_<firm>.py         # idempotent seed — THE definition of the firm (pattern below)
├── reports/                       # convention: members write run reports here
├── .gitignore                     # .venv/, .firm/*.db, .firm/*.db-*, __pycache__/, .env
└── .gitattributes                 # * text=auto eol=lf
```

Plus one folder OUTSIDE the workspace, on the Windows side: `/mnt/c/Users/Chris/Claude/Projects/<name>-boardroom/` with a seeded `PULSE-LOG.md` — the board-pack export target and the firm's field-failure log.

**What `cadre init .` creates:** `.firm/` with `firm.db` and all migrations applied. Migrations are idempotent and run on every connect path, so old firms upgrade transparently. Everything else (charter, seed, mcp config, hooks) is yours to add.

**Hub discovery is automatic:** the hub (`systemd --user` unit `cadre-hub`, port 8484) scans `~/firms/*/.firm/firm.db` and reads the firm id from each DB's firm row. A new firm folder appears on the portfolio with zero registration. Corollary: NEVER keep a backup copy of a firm under `~/firms` — a duplicate firm id shadows the real one (use `~/firms-archive/`).

---

## 2. Primitives — what exists and how it composes

Everything is an entity row in `firm.db` with a `PREFIX-NNN` id minted by `services/_id.next_id` (custom ids like `UNT-DEV-INFRA` are also legal when you create rows in a seed). **Writes go through the service layer only**: members write via the firm MCP, the Board writes via dashboard actions (`perform_action`), seeds use `firm.core.repo.create/update` (column-validated). Records/usage events/comments are immutable.

### The hierarchy

```
Firm ─┬─ Operation (department-scale, has owner_member_id)
      │    └─ Project (due_date NOT NULL)
      │         └─ Unit (the work atom — assigned, chained, validated)
      ├─ Member ── Contract (how the member runs)
      ├─ Goal (metric attached to firm/operation/project)
      ├─ Gate / Escalation (Board decision surface)
      └─ Records / Documents / Comments / member_run / usage_event (audit + deliverables)
```

### Firm row (identity — set ALL of this in the seed)

```python
repo.create(conn, "firm", {
    "id": "acme", "name": "Acme Lab",
    "description": "...", "operator": "Chris Kahler",
    "north_star": "one sentence the whole firm optimizes for",
    "core_values": json.dumps(["...", "..."]),
    "vision": "...",
    "notify_config": json.dumps({"provider": "slack", "slack_user_id": "U0B7LJZSZ5J",
                                 "token_env": "CADRE_SLACK_TOKEN", "remind_hours": 24}),
})
```

`notify_config` is what makes Gates/Escalations DM the Board (deduped, 24h reminders). Skip it and the firm goes mute — decisions rot silently.

### Contract (the behavior levers — every field matters)

```python
repo.create(conn, "contract", {
    "id": "CON-ENG", "firm_id": "acme", "name": "Acme Engineer Contract",
    "member_id": None,                          # None = shared by several members; or bind to one member
    "runtime_type": "claude_code",
    "runtime_config": json.dumps({"cwd": "."}),
    "pulse_config": json.dumps({"timeout_sec": 900, "model": "sonnet"}),   # MUST set model — unset silently runs account default
    "budget_config": json.dumps({"limits": {"max_runs_per_period": 40,
                                            "max_total_cost_per_period_usd": 5}}),
    "validation_config": json.dumps({"enabled": True, "max_retries": 1,
        "validators": [{"name": "file_exists", "require_written": True}]}),
    "skill_loadout": json.dumps({ ... }),       # see §5 — ONLY stages/tools/duties/policies render into prompts
})
```

- **`pulse_config`**: `timeout_sec` per run (leads/orchestrators 900–1200s, heavy drafters up to 2400s, cheap sims 600s) and `model` (`opus`/`sonnet`/`haiku` or full id). Tier models to the work: opus for the one member whose output quality is the product, haiku for high-frequency cheap roles, sonnet default.
- **`budget_config.limits`**: run-count and USD ceilings per budget period (periods auto-create on first run). No config = unbounded spend. Real-world per-run cost runs ~$0.50–2, heavy drafting $4+.
- **`validation_config`**: without it only the always-on `_nonempty_floor` holds (no text + no tools never completes — but a *refusal* with text can). Validators: `file_exists` with `require_written: true` (run must write a file; seam-4 then auto-registers each written file as a Document, parent = the unit — this is how deliverables become Board-visible) and `sql_guard` (arbitrary query against the firm DB must return the configured row-shape; failure message is fed back and the member retries — use for firm invariants like "the turn must be closed").
- **Timeouts interact with the reaper**: rows stuck `running` past 2× contract timeout + 600s grace get reaped to failed/orphaned at the next pulse.

### Member

```python
repo.create(conn, "member", {
    "id": "MEM-NOVA", "firm_id": "acme", "name": "Nova", "role": "Next.js Engineer",
    "description": "2-4 sentences — this IS the member's identity in its prompt; make it specific",
    "reports_to_member_id": "MEM-LEAD",   # None = reports to the Board directly
    "contract_id": "CON-ENG",
    "can_self_assign": 0,                 # 1 for the director/triage role, else units route by assignment
    "status": "active",
    # "frequency": optional pulse-gating cadence; None = every pulse it has load
})
```

A member's composed prompt = **Identity** (name/role/id/reports-to/description) + **Standing Notes** (Board comments attached to the member — persistent direction, applied every run until archived) + **Contract** (name + the rendered loadout, §5) + unit briefing. Verify any member's actual prompt with `GET /f/<firm>/api/member/<MEM-ID>` → `prompt_preview`. A healthy member previews at 2–4k chars; ~350 chars means the loadout is empty and the member will improvise.

### Unit (the work atom)

```python
repo.create(conn, "unit", {
    "id": "UNT-APP", "firm_id": "acme", "project_id": "PRJ-001",
    "name": "imperative, one-run-sized task",
    "description": "scope notes; what this unit is NOT is as valuable as what it is",
    "assignee_member_id": "MEM-NOVA",     # THE routing mechanism — unassigned + nobody can_self_assign = firm deadlock
    "status": "pending", "priority": "medium",
    "depends_on": json.dumps(["UNT-INFRA"]),          # topo-sorted within a pulse; fully-blocked members skip
    "acceptance_criteria": json.dumps(["verifiable criterion", "evidence requirement"]),
    "parent_unit_id": "UNT-EPIC",         # optional epic/child structure
})
# create_unit (service) does NOT accept claimed_by — create, then repo.update if needed
```

Scope units to ONE member-run each (fits inside `timeout_sec`). Enforce review/ship gates **structurally** — a final audit unit chained via `depends_on`, not a sentence asking members to remember.

### Goal

```python
repo.create(conn, "goal", {
    "id": "GL-001", "firm_id": "acme", "level": "operation",
    "parent_entity_type": "operation", "parent_entity_id": "OP-001",
    "metric": json.dumps({"type": "demos_validated", "current": 0}),
    "target": json.dumps({"type": "count", "value": 1, "unit": "demos", "current": 0}),
    "status": "active",
})
# Board updates progress via the goal-metric dashboard action
```

### Gate / Escalation (the Board surface)

- **Gate**: member (or Board Proxy) requests a decision; `target_entity_type` + `target_entity_id` are NOT NULL. ONLY the human Board resolves gates — `gate-approve`/`gate-reject` dashboard actions with the verdict in the comment. Dismissing a notification is an *acknowledge*, never a reject.
- **Escalation**: member raises a blocker/question via the firm MCP (`firm_escalate`); `dedupe_key` NOT NULL (same key = deduped, no spam); `raise_escalation` returns a wrapper `{"escalation": row, "deduped", "notified", ...}`, not the row. Board resolves via `escalation-resolve` with `queue_followup: true` to convert the answer into a commissioned unit for the raiser (essential for turn-based loops — a resolved escalation with no follow-up unit strands the loop).
- Both notify over Slack when `notify_config` is set.

### Run + records

Every spawn writes a `member_run` row: status, timing, `outputs` (the final message text persists at every terminal transition — a deliverable can never exist only in process stdout), `notes` (e.g. `mcp_degraded` warnings), cost via `usage_event`. **Seam-4: the harness owns completion** — a validated run flips its unit to done in the runner; a member *claiming* done in prose means nothing.

### Pulse (the heartbeat)

```
reap stale runs → business-hours gate → filter members (active, load>0, frequency)
→ [--only MEM-ID] → topo-sort by unit depends_on → sequential spawn loop
```

- `load>0` means: has claimed units OR assigned-unclaimed pending units. **`skip_reasons: {"load=0": N}` is the "create/assign work" signal, not a bug.**
- Spawn = `claude --print --dangerously-skip-permissions --strict-mcp-config --mcp-config <ws>/.mcp.json [--model X]`, binary from `CADRE_CLAUDE_BIN` → login-shell PATH.
- A live pulse holds `pulse.lock` (flock) and blocks until the slowest member finishes (20–40 min): from a session, detach with `systemd-run --user --collect` — nohup/setsid/disown die with `wsl.exe` teardown. Dry-run is read-only and lock-free.
- Chained units CAN complete in one pulse: the topo-sorted sequential loop runs upstream members first; downstream run in the same pulse once their dependency is done.

---

## 3. Member spec — filled example (verified live on the lab firm)

Contract loadout (shared by 3 demo members; note the name-prefixed duty pattern for shared contracts):

```json
{
  "scope": "demo-staging",
  "duties": [
    "Apply the duty lines addressed to YOUR name (see Your Identity); unprefixed lines bind all demo engineers",
    "Casey: stage realistic demo data in GoHighLevel and HubSpot SANDBOX accounts — contacts, pipelines, opportunities",
    "Wade: stage realistic demo data in Monday.com and Notion SANDBOX accounts — boards, items, databases, pages",
    "Coco: connect staged sandbox accounts into Cowork, run the demo flows end-to-end, validate before the Board presents",
    "Write every run's outcome to reports/<unit-id>-<slug>.md — what was done, verified evidence, gaps"
  ],
  "policies": [
    "SANDBOX-ONLY: stage data exclusively in accounts explicitly designated SANDBOX; ambiguous = STOP and escalate",
    "MONEY GATE: any action with a price tag needs a Board Gate FIRST",
    "Report only verified facts: 'staged' = counts read back from the API; unverified is reported as unverified"
  ]
}
```

Rendered member prompt (from `prompt_preview`, 2.1k chars): `## Your Identity` (name, role, id, reports-to, description) → `## Your Contract` (contract name) → `### Duties` → `### Binding policies — non-negotiable`. Add per-member standing direction as Board comments on the member (Standing Notes) or per-member protocol fragments (`.firm/protocols/_member/<MEM-ID>/`).

## 4. Org shape — how work routes and where the Board sits

- **Reporting lines** = `reports_to_member_id` (briefing/hierarchy context; the top role reports to the Board with `None`).
- **Routing** = unit assignment. A lead's *duty* is decomposition: turn intake into assigned, `depends_on`-chained units. Give exactly one role (director/triage) `can_self_assign=1` so the firm can bootstrap its own work; everyone else receives assignments.
- **Departments** = Operations with `owner_member_id`; the owner lead sequences that department's projects/units.
- **The Board** sits above everything and touches the firm only through audited actions: `gate-approve/reject`, `escalation-resolve` (+`queue_followup`), `member-commission` (one-shot unit + targeted `pulse --only` dispatch), `unit-create`, `comment-create` (steering + standing notes), `doc-revision`, `goal-metric`, `contract-model`, `pulse/now`. Everything lands in Records.
- **Board visibility** = registered Documents (deliverables tab), the hub card (`/f/<firm>/`), Slack notifies, and the board-pack export to the boardroom folder every governance pulse.
- **Board Proxy** (the charter's role for unattended sessions) runs pulses, watches, steers HOW — and NEVER resolves gates, never spends, never widens loadouts.

## 5. Loadouts — capability is structural, never behavioral

**Constitutional rule: a member that must not do X simply doesn't get X.** Prompts persuade; loadouts enforce.

Three mechanisms, narrowest wins:

1. **MCP surface**: `.mcp.json` is passed with `--strict-mcp-config` — members get those servers and NOTHING else (no personal-fleet inheritance; that leak is fixed framework-tier). No `.mcp.json` = no MCP tools at all, by design. The firm MCP server (`python -m firm.mcp.server`, native `bash -lc` launch — see gotchas) is what gives members `firm_*` write tools (documents, escalations, records). Add external-service MCP servers here ONLY when a role needs them — that's a procurement decision.
2. **Contract `skill_loadout`** — the prompt-rendered grant. Rendered keys (verified in `pulse/prompt.py`): `stages` (dict → "Sanctioned commands — use these, do not improvise tooling"; also drives skill-dispatch preflight), `tools` (list), `duties` (list), `policies` (list → "Binding policies — non-negotiable"). **Any other key (`scope`, `files`, `exclusions`) is inert documentation** — fine for humans, invisible to members.
3. **Structural blindness**: what a member must NOT see stays out of its loadout files/duties entirely (e.g. a reader-sim gets published text only, never the bibles). Never "helpfully" widen a loadout — that proposal is itself a Gate.

Constraints that are law across firms: no social/production credentials in loadouts; missing capability = procurement unit (Gate → loadout update), never a prompt hack; squad spawn rights (if the squad tool is installed) are per-member grants — `squad contract set <MEM-ID> --quota 5 --anchor "mon 00:00" --budget 1 --depth 1` writes the fragment that renders in that member's every run.

## 6. Walkthrough — zero to first pulse (hypothetical firm `acme`, 4 members, 2 chained units)

```bash
# 1. Workspace + framework
mkdir -p ~/firms/acme && cd ~/firms/acme
python3 -m venv .venv
.venv/bin/pip install -e ~/ops-sys/toolbox/frameworks/05-exp-cadre   # editable: framework fixes flow live
.venv/bin/cadre init .                                               # creates .firm/firm.db, migrations applied

# 2. Scaffolding files
#   CLAUDE.md          — charter: §0 WSL runtime preface COPIED VERBATIM from an existing firm (host-detection law),
#                        firm table, Board-Proxy hard rules, the firm's structural NEVERs, accuracy tiers, cadence
#   .mcp.json          — NATIVE launch (never wsl.exe):
#                        {"mcpServers": {"firm": {"command": "bash", "args": ["-lc",
#                          "FIRM_ID=acme FIRM_WORKSPACE=/home/<user>/firms/acme CADRE_SLACK_TOKEN=<tok> exec /home/<user>/firms/acme/.venv/bin/python -m firm.mcp.server"]}}}
#   .claude/           — copy chrisai's session-pulse hook trio + commands/pulse.md, sed the firm name/paths
#   .gitignore/.gitattributes — from any existing firm (LF enforced)
mkdir -p reports scripts /mnt/c/Users/Chris/Claude/Projects/acme-boardroom

# 3. Seed script (scripts/seed_acme.py) — idempotent: guard EVERY create with repo.get
```

```python
from firm.core import repo
import json, sqlite3
conn = sqlite3.connect("/home/<user>/firms/acme/.firm/firm.db"); conn.row_factory = sqlite3.Row

# firm row: identity + north_star + core_values + notify_config     (§2 schema)
# contracts: CON-LEAD (900s) + CON-ENG (900s) — model, budgets, validation, loadout ALL set
# members:   MEM-DIR (lead, reports_to None, can_self_assign=1)
#            MEM-LEAD (reports_to MEM-DIR) · MEM-ENG-A, MEM-ENG-B (report_to MEM-LEAD)
# operation OP-001 (owner MEM-LEAD) → project PRJ-001 (due_date NOT NULL — it bites)
# units:     UNT-BUILD (assignee MEM-ENG-A, depends_on [])
#            UNT-SHIP  (assignee MEM-ENG-B, depends_on ["UNT-BUILD"], acceptance = evidence requirements)
# goal GL-001 on OP-001
conn.commit()
```

```bash
.venv/bin/python scripts/seed_acme.py

# 4. Verify BEFORE any spend
FIRM_ID=acme .venv/bin/firm pulse --dry-run
#   expect: ran=<members with load>, errors=0; load=0 skips are members without assigned units
curl -s http://127.0.0.1:8484/api/hub | python3 -m json.tool          # firm card appears automatically
curl -s http://127.0.0.1:8484/f/acme/api/member/MEM-ENG-A            # prompt_preview must show duties+policies (2k+ chars)

# 5. Git + boardroom log
git init && git add -A && git commit -m "acme: firm scaffold + seed"
#   seed PULSE-LOG.md in the boardroom folder (newest-first pulse entries)

# 6. FIRST LIVE PULSE IS THE BOARD'S CALL — do not fire it as part of scaffolding.
#   When the Board says go:  dashboard /f/acme → Pulse   (systemd-run detached, outcome at /api/pulse-status)
#   or attended:             the /pulse command in the workspace
```

**Board-gated deliverable, end to end:** pulse spawns MEM-ENG-A on UNT-BUILD → run writes `reports/UNT-BUILD-report.md` → `file_exists/require_written` validates → seam-4 flips UNT-BUILD done and auto-registers the file as a Document (Board's Deliverables tab) → same pulse's topo order now unblocks MEM-ENG-B on UNT-SHIP → member hits a decision it may not make → `firm_escalate` (or requests a Gate) → Slack DM to the Board → Board resolves in the dashboard with the verdict in the comment (`queue_followup: true` commissions the follow-up automatically) → Records carries the whole chain immutably.

## 7. Gotchas — the complete bite list (each one cost something once)

**Environment / runtime**
1. WSL is the firm's world: venv, DB access, pulses — all WSL-only. Never touch `firm.db` over `\\wsl.localhost`, never pulse from a Windows shell. Charter §0 preface is law; copy it verbatim.
2. Detached work from sessions: `systemd-run --user --collect` only. nohup/setsid die with the terminal.
3. 3-second member deaths, returncode 1, empty stderr = spawn environment (PATH, `CADRE_CLAUDE_BIN`, wrong host world) — never member quality.
4. `sqlite3` CLI may not be installed — diagnose with `.venv/bin/python` + the `sqlite3` module (read-only). Writes: services only, always.

**Seeding / config**
5. Folder name ≠ firm id. The hub reads the id from the DB. Duplicate ids under `~/firms` shadow each other — backups live in `~/firms-archive/`.
6. THE deadlock: units unassigned + everyone `can_self_assign=0` → every member `load=0` → every pulse skips everything, forever. Assign units in the seed and give the triage role self-assign.
7. Seeds MUST set `pulse_config.model`, `timeout_sec`, and `budget_config.limits` — the wastelander seed forgot `model` and every member silently ran the account default.
8. NOT NULL traps: `project.due_date`, `gate.target_entity_type`+`target_entity_id`, `escalation.dedupe_key`. `create_unit` rejects `claimed_by` (create, then update).
9. Only `stages`/`tools`/`duties`/`policies` in `skill_loadout` reach the member's prompt. `scope`/`files` are inert. Check `prompt_preview` after seeding — ~350 chars = empty loadout = the member will improvise.
10. No `notify_config` = silent firm: gates and escalations never reach the Board.

**MCP**
11. `.mcp.json` launches natively (`command: "bash", args: ["-lc", "... exec .venv/bin/python -m firm.mcp.server"]`) — a `wsl.exe` hop silently fails for WSL-native pulse spawns and the member improvises without firm tools.
12. Spawn passes `--mcp-config` + `--strict-mcp-config` unconditionally: members get the firm's servers only. No `.mcp.json` = no MCP tools, by design.
13. `mcp_degraded` in run notes = an expected server showed no evidence of connecting. The authoritative record is claude's per-project MCP debug log (`~/.cache/claude-cli-nodejs/<cwd>/mcp-logs-<server>/*.jsonl`) — the init snapshot races ahead of connects; `pending` alone is NOT a failure.

**Validation / completion**
14. `validation_config: None` = vacuous completion: a run that refused the work can still close its unit (only the nonempty-floor holds). Always-produce-a-file contracts get `file_exists` + `require_written: true` — which also auto-registers deliverables as Documents.
15. `sql_guard` turns any firm-DB invariant into a retryable validator (config-driven, reusable — e.g. "the turn must actually be closed before the run completes").
16. Completed run at $0 cost = the model never worked. Investigate prompt + validation, not the member.
17. A file on disk that was never registered as a Document is invisible to the Board. `require_written` closes this; text-only deliverables persist in `member_run.outputs` regardless.

**Operations**
18. `ran: 0` with `load=0` skips is usually CORRECT — the firm needs work created/assigned, not a louder pulse.
19. A resolved escalation without `queue_followup` strands turn-based loops — nobody gets commissioned to act on the answer.
20. Business-hours gate is firm-wide in the pulse; a "dead" firm at night may just be gated.
21. Zombie `running` rows (dead pulse process) are reaped at next pulse (2× timeout + 600s grace); `stale: true` marks them in state meanwhile.
22. First live pulse = first spend = the Board's explicit call. Scaffold ends at a green dry-run.
23. Member "can't do X" = Contract/loadout gap = procurement unit (Gate → loadout update). Never a prompt hack, never borrowed credentials.
