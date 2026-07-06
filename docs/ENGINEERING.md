# Cadre Engineering Handoff — how this system actually works

Written 2026-07-06 as a model-to-model handoff (Fable 5 → whoever works on this next). Everything here was verified against the running system, not inferred. Read this BEFORE changing framework source, building a dashboard view, or standing up a firm. When this doc and the code disagree, the code wins — then fix this doc.

**Operating discipline for any model working here:** read the existing pattern before writing a new one; copy the neighboring idiom exactly; make the smallest diff that solves the problem; run `pytest tests/` (653+ tests, ~20s) before claiming done; commit to this repo with a message that explains the WHY. The suite green is the definition of "didn't break Cadre."

---

## 1. System map

```
src/firm/
├── core/          repo.py (generic entity CRUD w/ column validation), db.py (connect,
│                  get_db_path), migrate.py (apply_migrations — idempotent, runs everywhere)
├── migrations/    numbered SQL; every connect path applies them (old firms upgrade transparently)
├── services/      THE ONLY WRITE PATH. unit.py, gate.py, escalation.py, goal.py, member.py,
│                  comment.py, document.py, _records.py (log_event), _id.py (next_id)
├── pulse/         orchestrator.py (gates+topo+loop), spawn.py (claude subprocess),
│                  runner.py (make_runner: prompt→spawn→parse→validate→budget),
│                  budget.py, validate.py
├── contracts/     claude_code.py (runtime adapter; _get_model reads pulse_config.model),
│                  dispatch.py (stage resolution from skill_loadout)
├── dashboard/     server.py (stdlib HTTP; single-firm + hub) + index.html (the whole SPA)
├── mcp/           firm MCP server (Members' write surface — same services underneath)
├── cli/           init, pulse, unit, goal, notify, roll, run
├── commands/      member_dispatch.py (preflight/postflight for skill-driven members)
└── hooks/         session-start roster/gates/goals injection
```

**Deployment model:** every firm venv does `pip install -e` against THIS directory. One source tree, all firms live instantly. `server.py` changes need a hub/dashboard process restart; `index.html` does NOT (read from disk per request — edit, refresh browser). Framework-source changes affect every firm: that's the power and the blast radius. Suite green before restart.

---

## 2. Constitutional invariants (violating these breaks the philosophy, not just the code)

1. **Everything is an entity row in `.firm/firm.db`** (SQLite, WAL). IDs are `PREFIX-NNN` (`MEM-001`, `UNT-012`, `GATE-003`) minted by `services/_id.next_id`. `repo.create/update` validate column names against the live schema — unknown column = ValueError, so schema drift fails loudly.
2. **Services are the only write path.** Members write through the firm MCP; the Board writes through dashboard actions (`perform_action`); both call the same `services/*` functions, so Records/audit behavior is identical regardless of who acted. NEVER raw-`UPDATE` state transitions; NEVER "helpfully" bypass a service.
3. **Records are immutable.** `log_event` appends; nothing edits or deletes. Same for comments and usage events. The audit trail is the product of governance.
4. **The harness owns completion, never the model** ("seam-4"): a validated run flips its Unit to done in the runner — a Member claiming "done" in prose means nothing.
5. **Structural rules beat behavioral rules.** Capability lives in Contract loadouts (files, tools, stages), not in prompt instructions. A member that must not see X simply doesn't get X in its loadout (Reader-sim blindness, no-social-credentials). When asked to make a member behave differently, first ask whether the fix is structural.
6. **Gates are the Board's alone.** No autonomous session resolves one. The `/boardroom` co-board seat executes the human's explicit verdict only.
7. **NOT NULL fields that bite during seeding/tests:** `project.due_date`, `escalation.dedupe_key`, `gate.target_entity_type`+`target_entity_id`. `create_unit` does NOT accept `claimed_by` — create, then `repo.update`. `raise_escalation` returns `{"escalation": row, "deduped", "notified", ...}` — a wrapper, not the row. Escalation's raiser field is `raised_by_member_id`.

---

## 3. The pulse (heartbeat) — order matters

`pulse()` in `pulse/orchestrator.py`, called by `cli/pulse.py run_pulse()`:

```
reap_stale_runs           # zombie 'running' rows past 2×contract timeout + 600s grace → failed/orphaned
→ business-hours gate     # firm-wide
→ filter_members          # active status, load>0 (claimed OR assigned-unclaimed pending units), frequency gate
→ [--only MEMBER_ID]      # Board-targeted: restrict to one member; frequency waived, other gates stand
→ topo_sort_members       # inter-member unit dependencies; fully-blocked members skip
→ sequential loop         # make_runner: assemble prompt → spawn → parse → validate(+1 retry) → budget → completion
```

Operational facts:
- **Live pulses hold `pulse.lock`** (flock, auto-released on process death) — a second live pulse exits `pulse-already-running`. Dry-run is read-only and lock-free.
- **Spawn** = headless `claude --print --dangerously-skip-permissions [--model X]`, binary resolved `CADRE_CLAUDE_BIN` → PATH (must be the WSL binary; login-shell PATH only). Preflight aborts `runtime-not-wired` rather than writing doomed failed rows.
- **Model selection** = `contract.pulse_config.model` → `--model` (aliases opus/sonnet/haiku or full ids). Unset = account default. The dashboard Settings page + `contract-model` action edit this; takes effect next spawn. **Seed scripts must set it** — the wastelander seed forgot and every member silently ran default.
- **Detachment**: a pulse blocks until its slowest member finishes (20-40 min). From sessions, ALWAYS `systemd-run --user --collect` — the only detach that survives `wsl.exe` teardown (nohup/setsid/disown die with the session; proven 2026-07-03). The dashboard's `_fire_pulse` does this and routes output to `.firm/last-pulse.json` for `/api/pulse-status`.
- **A 0-ran pulse is usually correct**: `skip_reasons` in the summary says why (`load=0` = nothing queued — the firm needs work created, not a louder pulse).

---

## 4. Dashboard architecture (`dashboard/server.py` + `index.html`)

**Design constraints (deliberate):** stdlib-only (ThreadingHTTPServer, no deps, no build step), one HTML file SPA, one comprehensive `/api/state` payload, brand via CSS custom properties (`--brand-*`, Caddy design system; light+dark themes both defined at the top of index.html).

**Server shape:** module-level pure functions. `assemble_state(conn, firm_id)` builds the entire read payload (roster+load+current units, gates/escalations with related-doc resolution, goals, docs, last-30 runs with cost + `stale` flag, records, comments, spend, budget periods, `contract_settings`). `perform_action(conn, action, entity_id, body)` is the write switchboard — every Board button lands here (or in the few workspace-needing actions handled in `_firm_post`: `pulse`, `member-commission`, `escalation-resolve`+`queue_followup`). `_firm_get`/`_firm_post` are shared request handlers parameterized by `(workspace, db_path, firm_id, path, base)`.

**Hub (multi-tenancy):** `make_hub_handler(root)` — `discover_firms` scans `<root>/*/.firm/firm.db` and reads firm id from each DB's firm row (**folder name ≠ firm id**). Routes: `/` portfolio page (`_HUB_HTML`, inline), `/api/hub` health cards, `/f/<firm-id>/...` → shared handlers with `base="/f/<firm-id>"`. Unknown firm triggers a lazy rescan, so a new firm folder appears live with zero registration. Single-firm `cadre dashboard` still works — same handlers, empty base.

**BASE tenancy rule (the one that bites):** the server stamps `<html data-base="/f/<id>">` when serving under the hub; the SPA reads it once into `const BASE` and EVERY `fetch`/`EventSource` URL must be written `BASE + '/api/...'`. A new fetch call written without BASE works standalone and silently 404s under the hub. Same contract in `_VIEW_PAGE_TEMPLATE` via `__BASE__` substitution.

**Realtime:** SSE (`/api/events`) watches SQLite `PRAGMA data_version` (bumped by any other connection's commit) at 0.5s, pushes `change`, client re-polls `/api/state`; 15s poll is the fallback. Render is idempotent: `render()` re-renders everything from `STATE`; per-view `renderX(S)` functions; skip re-render while a modal is open or (settings) while a `<select>` is focused — never clobber an in-progress interaction on the poll cadence.

**UI conventions:** `esc()` every interpolated value (XSS + layout safety). Status semantics via `STATUS_PILL` map + `pill()`/`sdot()` helpers. Fixed-height cards (roster `.mcard` 110px, null fields render placeholders — no jagged grids). Honest-state styling: green pulsing = actually running, amber static = stale/presumed-dead; never let a dead thing glow green. **No nested `<a>`** — browsers eject the inner anchor mid-parse and the debris floats outside the card (portfolio bug, fixed by click-handling `<div role="link">`). **Never fit/measure a hidden (`display:none`) element** — 0×0 measurements lock in garbage transforms (roster org-chart bug; fit on first visible frame instead).

---

## 5. Custom views — the per-firm app seam

A firm extends its boardroom without forking anything. Manifest at `<workspace>/.firm/dashboard/views.json`:

```json
{"views": [{
  "id": "table",                          // [a-z0-9-]{1,32}
  "title": "The Table",
  "fragment": "dashboard/views/table.html", // resolved under .firm/ — escapes rejected
  "files":   {"game_state": "game/game_state.json"},   // GET /api/views/<id>/file/<key>
  "dirs":    {"art": "game/art"},                       // GET /api/views/<id>/dir/<key>/<basename> (whitelisted extensions)
  "actions": {"roll": {"cmd": [".venv/bin/cadre", "roll", "{json}"], "timeout": 60}}
}]}
```

- The boardroom shell mounts each view as a nav item (embedded) and serves a chrome-free full page at `/view/<id>` (hub: `/f/<firm>/view/<id>`) — same fragment both ways.
- Fragments talk to the shell ONLY via **`window.CadreShell`**: `state()` (latest /api/state), `post(path, body)` (Board actions), `viewFile(viewId, key)`, `viewAction(viewId, key, body)`. Both the embedded shell and the full-page template implement this identically — write fragments against CadreShell and they work everywhere.
- `innerHTML` never executes scripts — the shell re-creates `<script>` nodes after mounting. Fragments should re-render on the `cadre:state` document event.
- **Security boundary:** `_firm_file` refuses path escapes from `.firm/`; view actions run manifest-declared argv only (the `{json}` placeholder carries the request body as ONE argument, never through a shell). The manifest lives inside `.firm/` — same trust domain as the DB itself.
- Reference implementation: `~/firms/dnd-table/.firm/dashboard/` (views.json + table.html).

---

## 6. Standing up a new firm (the checklist that actually works)

Reference: `~/firms/the-wastelander-novel` (built 2026-07-05) and `~/firms/chrisai` (first firm; its PULSE-LOG.md in the boardroom folder is the failure catalog — read it).

1. `mkdir ~/firms/<name> && cd` — folder name is operator-facing; the firm id lives in the DB.
2. `python3 -m venv .venv && .venv/bin/pip install -e ~/ops-sys/toolbox/frameworks/05-exp-cadre`
3. `.venv/bin/cadre init .`
4. Seed script (`scripts/seed_*.py`, pattern: wastelander's `seed_novel.py`): firm row (incl. `notify_config` with `token_env`), contracts (**set `pulse_config.model` AND `timeout_sec` AND `budget_config.limits`**), members w/ `reports_to_member_id` hierarchy, operation→projects (due_date NOT NULL)→units (`depends_on` chains; `claimed_by` via post-create update), goals (metric JSON in `target`). Idempotent: guard every create with `repo.get`.
5. Charter `CLAUDE.md`: §0 WSL/Windows runtime preface (copy verbatim — it's host-detection law), firm table, Board-Proxy hard rules, the firm's structural NEVERs, accuracy tiers.
6. `.mcp.json`: firm MCP server with `FIRM_ID`, `FIRM_WORKSPACE`, `CADRE_SLACK_TOKEN` env (dashboard `_slack_token_from_workspace` greps it from here too).
7. `.claude/` hooks (copy chrisai's session-pulse trio, sed the paths), `/pulse` command file, `.gitignore`/`.gitattributes` (LF), git init + commit.
8. Boardroom folder on the Windows side (`/mnt/c/Users/Chris/Claude/Projects/<name>-boardroom/`), seeded PULSE-LOG.md.
9. Verify: `FIRM_ID=<id> .venv/bin/firm pulse --dry-run` (expect clean ran/skipped/0-errors), session hook renders roster, hub portfolio shows the firm automatically.
10. **Do NOT fire a live pulse** — first spend is the Board's call. Model tiers via Settings page if the seed missed them.

---

## 7. Where things run (ops quick reference)

| Thing | How |
|---|---|
| Hub (all boardrooms) | systemd user unit `cadre-hub` → `cadre hub --firms-root ~/firms --port 8484`; portfolio `/`, firm at `/f/<id>/` |
| Board pulse button | POST `/f/<id>/api/action/pulse/now` → systemd-run detached → outcome at `/api/pulse-status` |
| Board commission | POST `member-commission/<MEM-id>` → real Unit + Records + `pulse --only` targeted dispatch |
| Resolve→followup | `escalation-resolve` with `queue_followup:true` → resolves + commissions the raiser (turn-based firms' game loop) |
| Hourly governance | per-firm Board Proxy desktop routine → firm's `/pulse` command (dry-run, detached live fire, board-pack export, ledgered DMs) |
| Slack notify | service-layer at gate/escalation creation, deduped, 24h reminders; `firm notify` for manual |

## 8. Testing discipline

- Pattern: in-memory or tmp-path SQLite + `apply_migrations(conn)` + `repo.create` fixtures (see `tests/test_hub.py::_make_firm`). HTTP tests: real `ThreadingHTTPServer` on port 0 in a thread. Dispatch tests: `monkeypatch.setattr(srv.subprocess, "run", ...)` — never spawn real members in tests. Remember pulse argv now nests inside `bash -c`, so assert against `" ".join(cmd)`.
- Map: `test_hub.py` (hub/commission/only/settings), `test_dashboard.py` (state+actions), `test_member_dispatch.py`, `test_cli_pulse.py`, golden/ fixtures.
- The suite is the contract: green before every commit, no exceptions, and add a test with every behavior change (the reviewer after you is a model too — tests are how it knows what you meant).

## 9. Field-failure catalog (learned the expensive way — check here before debugging)

- **3-second member deaths, returncode 1, empty stderr** → spawn environment (PATH/CADRE_CLAUDE_BIN/wrong host world), never member work quality.
- **Run "running" for hours, no process** → orphaned row from a dead pulse process; reaper closes it at next pulse; `stale:true` in state marks it meanwhile.
- **Pulse spawns nobody** → read `skip_reasons`; `load=0` means create work (a resolved escalation with no follow-up unit strands turn loops — hence `queue_followup`).
- **Completed run, $0 cost** → model never actually worked; investigate the prompt and validation.
- **Member "can't do X"** → check the Contract loadout before touching prompts; capability gaps are procurement Units (Gate → loadout update), not prompt hacks.
- **Windows-hosted session weirdness** → §0 preface violated somewhere: firm.db touched over `\\wsl.localhost`, pulse fired from a Windows shell, or a non-login shell missing the claude binary.
- **Duplicate work/spend** → two live pulses overlapped before pulse.lock existed, or a completion bug resurrected (fixed at f0080a0; verify unit status column, not member prose).
