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
- **Spawn** = headless `claude --print --dangerously-skip-permissions --strict-mcp-config [--mcp-config <workspace>/.mcp.json] [--model X]`, binary resolved `CADRE_CLAUDE_BIN` → PATH (must be the WSL binary; login-shell PATH only). Preflight aborts `runtime-not-wired` rather than writing doomed failed rows. MCP is explicit and exclusive: the firm's `.mcp.json` is passed as `--mcp-config` (headless project-config auto-loading depends on per-project trust state in `~/.claude.json` — never rely on it), and `--strict-mcp-config` is unconditional so Members never inherit the operator's user-scope/plugin MCP fleet. A firm with no `.mcp.json` = Members get no MCP servers, by design.
- **Run forensics on the row**: the runner persists the final message text into `member_run.outputs` (`[{"type":"final_text","text":…}]`) at every terminal transition — completed, failed, validation-failed-then-retried, timed out — and the MCP startup guard writes an `mcp_degraded` warning JSON into `member_run.notes` (plus `mcp_degraded` in the pulse `ran_details`) when a server declared in `.mcp.json` shows NO evidence of having connected. Evidence hierarchy (any one clears): init status `connected` / its tools in the init index → an `mcp__<server>__*` tool call in the stream → a `Successfully connected` line for THIS session in claude's per-project MCP debug log (`~/.cache/claude-cli-nodejs/<cwd with / → ->/mcp-logs-<server>/*.jsonl`; entries carry `sessionId`). Init `pending` alone NEVER flags — the init event is a snapshot that races ahead of MCP connects under systemd-run pulse timing (§9).
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
4. Seed script (`scripts/seed_*.py`, pattern: wastelander's `seed_novel.py`): firm row (incl. `notify_config` with `token_env`), contracts (**set `pulse_config.model` AND `timeout_sec` AND `budget_config.limits`**), members w/ `reports_to_member_id` hierarchy, operation→projects (due_date NOT NULL)→units (`depends_on` chains; `claimed_by` via post-create update), goals (metric JSON in `target`). Idempotent: guard every create with `repo.get`. The discipline family (PAUL-extracted quality packs) is installed by `cadre init` automatically; after seeding contracts, attach the role packs: `cadre templates apply discipline --map lead=<CON> --map dev=<CON>`.
5. Charter `CLAUDE.md`: §0 WSL/Windows runtime preface (copy verbatim — it's host-detection law), firm table, Board-Proxy hard rules, the firm's structural NEVERs, accuracy tiers.
6. `.mcp.json`: firm MCP server with `FIRM_ID`, `FIRM_WORKSPACE`, `CADRE_SLACK_TOKEN` env (dashboard `_slack_token_from_workspace` greps it from here too). **Launch native, not via `wsl.exe`:** `command:"bash", args:["-lc","FIRM_ID=<id> FIRM_WORKSPACE=<abs> CADRE_SLACK_TOKEN=<tok> exec <abs>/.venv/bin/python -m firm.mcp.server"]`. Pulses run WSL-native, so a `wsl.exe -d Ubuntu -e …` hop fails silently there (ESC-010, §9). Being retired entirely — see `docs/MCP-TO-CLI-MIGRATION.md`.
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
- **Unit marked done but no artifact exists / a blocked run "completed"** → the contract shipped `validation_config: None`, so `validate_output` passed vacuously and seam-4 flipped the unit to done regardless of output (wastelander Wren RUN-017: refused to draft pre-gate, still closed UNT-021). Fixed c137b65: always-on `_nonempty_floor` (no text + no tools never completes) + `file_exists`'s `require_written` for always-produce-a-file contracts. When a firm forgets validation config, the floor still holds; set `require_written` on drafter/writer contracts explicitly.
- **A revision destroyed the prior version** → `request_revision` used to say "revise in place (same content_path)." Never-overwrite is now policy: it targets the next `-vN` via `_next_version_path` and its ACs forbid touching the original (c137b65). Hand-edits must copy-to-vN too.
- **Member told to register a revised deliverable as a new version, but there was no audited path — hand-patches firm.db** → `update_document` (service) has always done the version-bump + Records log, but it was never exposed: no `firm_update_document` MCP tool, no CLI `document` verb. A Member handed a "register v2 of DOC-001" unit had to either escalate or raw-`UPDATE` the DB (chief-of-staff ESC-014, 2026-07-14; Cooper did the latter — correct escalation, wrong hands). Fixed 2026-07-14: `firm_update_document(document_id, member_id, content_path/name/status)` added to the MCP surface, and `update_document` takes an `actor` so Records carries the *Member* who revised, not a default Board actor. Also: `_register_deliverables` now detects a `-vN` file whose de-versioned path matches an existing Document and bumps that row instead of forking a sibling DOC — a member who ships v2 through the normal deliverable path no longer needs the tool at all. Diagnostic tell: a `document.revised`/`document.updated` Records row with no matching service call in the run, or a raw firm.db mutation in a member's run forensics.
- **`firm` shim dead everywhere: `ModuleNotFoundError: No module named 'firm'`** → the global `~/.local/bin/firm` is an editable-install entry point whose `__editable__.firm-0.1.0.pth` pointed at `chris-ai-systems/apps/agent-company-architecture/src` — the pre-toolbox source location, deleted when Cadre moved to `ops-sys/toolbox/frameworks/05-exp-cadre`. Every `firm` command from any shell outside a firm's own `.venv` silently failed (surfaced via chief-of-staff ESC-014, 2026-07-14). Fix: repoint the one-line `.pth` at the live `…/05-exp-cadre/src`. When the framework repo moves, the editable `.pth` does NOT follow — grep `~/.local/lib/python*/site-packages/__editable__.firm*.pth` and any firm `.venv`'s equivalent after any relocation.
- **Unit done, file on disk, but nothing in the Board's deliverables to review** → the run wrote + passed `require_written`, but no Document row was ever created (member couldn't self-register — firm MCP not wired in the spawn; wastelander UNIT-023 ch18). Fixed 7adff12: seam-4 registers the artifact it verified — `_register_deliverables` creates a unit-parented Document for each written file when the contract opts in via `require_written`, idempotent by content_path. If a firm's completed units still show no deliverable, check the contract HAS `file_exists require_written`.
- **`mcp__firm__*` tools silently absent from a member's toolset** → `.mcp.json` launched the firm MCP via `wsl.exe -d Ubuntu -e bash -lc '…'`, a Windows→WSL hop that assumes Claude Desktop on Windows. Pulse-spawned members are WSL-native (`uname -s = Linux`; systemd --user), where `wsl.exe` isn't on PATH → the server never starts, no tools load, and the member silently improvises or no-ops (dnd-table ESC-010; same root cause as the UNIT-023 "couldn't self-register" symptom above). Stopgap fix 2026-07-07: all four firms' `.mcp.json` converted to native `command:"bash", args:["-lc","<inline env> exec …python -m firm.mcp.server"]` (wsl.exe hop dropped, inline `CADRE_SLACK_TOKEN=` format preserved for the dashboard grep). Durable fix scoped: `docs/MCP-TO-CLI-MIGRATION.md` retires the firm MCP entirely for CLI verbs. New firms: use the native launch (checklist §6), never the wsl.exe hop.
- **Member runs "complete" but firm MCP tools were never there; a text-only deliverable evaporates** → wastelander ESC-004 (2026-07-08, all `mcp__firm__*` absent, member improvised via CLI) and RUN-051 (2026-07-10, $4.09 canon check "completed"+validated, `create_document` never happened, the 1,853-char final text existed only in pulse stdout and was dropped — `member_run.outputs` NULL). THREE stacked causes, all invisible: (1) the `mcp` Python SDK was never in `pyproject dependencies`, so `pip install -e cadre` built firm venvs whose `python -m firm.mcp.server` died at import (`ModuleNotFoundError: mcp`) with stderr swallowed by claude — 3 of 4 firms shipped broken; only chrisai had a manual install. (2) The spawn passed no `--mcp-config`, leaving headless project-`.mcp.json` loading to per-project trust state in `~/.claude.json` (`hasTrustDialogAccepted:false` on wastelander) — version- and state-dependent, not deterministic. (3) With nothing strict, Members inherited the operator's ENTIRE personal MCP fleet under `--dangerously-skip-permissions` — measured 420 tools / 387 MCP (Gmail, Slack, Drive, Canva…) in a wastelander member spawn: a loadout-discipline violation and a giant prompt tax. Fixed 2026-07-10 at framework tier: `mcp>=1.0` declared as a dependency (existing firms: re-run `.venv/bin/pip install -e <cadre>`); spawn passes `--mcp-config <workspace>/.mcp.json` + unconditional `--strict-mcp-config`; parser captures the init event's `tools`/`mcp_servers`; runner's MCP startup guard marks the run row `mcp_degraded` (notes JSON + pulse summary) when an expected server never connected; final message text always persists to `member_run.outputs` so a deliverable can never again exist only as process stdout. Verified end-to-end on wastelander: firm `connected`, 38 `mcp__firm__*` tools, personal fleet absent. Diagnostic tell: a member "worked around" missing tools, or a completed run whose `outputs` is NULL while the pulse summary shows `text_length > 0`. **Post-fix postmortem (RUN-053/054/055, same day): the init event is a snapshot that races ahead of MCP connects under systemd-run pulse timing** — all three runs showed `firm: pending` at init yet the server connected ~500ms later and the members created Documents via MCP (claude's own log: "Successfully connected in 506ms" then `firm_create_document` OK); the first guard version trusted the init snapshot and false-flagged three healthy production runs as degraded. The guard's evidence model since then: a server is flagged missing only on affirmative failure (init `failed`/`needs-auth`/absent-from-init, or this session's MCP debug-log entries existing WITHOUT a connect line) and never on `pending` alone — `pending` with no consultable log is indeterminate, and a guard that false-flags healthy runs trains the Board to ignore it. When investigating any "MCP absent" suspicion, claude's per-project `mcp-logs-<server>/*.jsonl` (sessionId-stamped, in `~/.cache/claude-cli-nodejs/`) is the authoritative record — read it before trusting the init snapshot.
- **`firm pulse` bounces `pulse-already-running` but nothing is pulsing; `--abort` says "No active processes"** → a killed pulse (systemctl stop, OOM, crash) leaves its `pulse_lock` row for the full 10-min steal TTL, and the old `--abort` only SIGTERMed subprocesses tracked in its own fresh CLI process — a guaranteed no-op that never looked at the DB (novel-house 2026-07-11: pulse killed mid-run for a canon change, next pulse silently refused, abort reported nothing to abort). Fixed 0f32637: `_handle_abort(workspace, firm_id)` is DB-aware — SIGTERMs a live local holder with a grace window for its own release, clears a dead local holder's stale row, reports a remote holder and leaves it to the TTL. Manual unwedge (older installs): verify the holder pid in the lock row is dead, then delete the row.
- **`firm run end` / `on_run_end` crashes `UNIQUE constraint failed: usage_event.id`** → two stacked causes (novel-house RUN-065, 2026-07-11): (1) `_next_usage_event_id`/`_next_records_id` used firm-scoped `COUNT(*)+1` against globally-unique PK id columns — the count lags the real ceiling whenever another firm_id shares the table or rows are deleted, minting a duplicate; (2) the CLI firm-id default is `$FIRM_ID or 'chrisai'`, so a bare `firm run end` in any other firm's workspace scopes the count to a firm with zero rows → instant `USG-001` duplicate plus a wrong-firm row. Fixed 0f32637: all id generation (`services/_id.py next_id` + both hook variants) is `MAX(numeric suffix)+1` over the actual id space (UNIT/SUB still share one sequence). Diagnostic tell post-fix: `FOREIGN KEY constraint failed` from `run end` means you forgot `--firm-id`/`$FIRM_ID` — the FK is correctly rejecting the wrong-firm write. OPEN footgun: the silent `'chrisai'` fallback still writes to the wrong firm wherever a `chrisai` firm row exists; the default should derive from the workspace DB (single-firm DBs are unambiguous) — scoped for a later pass.
- **The Table: DM plays forward but the Board's move box never appears** → the DM narrated the turn to `game_story_log` but never logged the closing `your_move` (no new your-move escalation raised), so the move box (which renders the latest `your_move` row) has nothing fresh; the run still "completed" because the DM contract had `validation_config: None` (dnd-table RUN-017, 2026-07-07). Fixed: generic `sql_guard` validator (`pulse/validate.py`) — runs a configured query against the firm DB (honours `CADRE_DB_URL`) and fails when it returns the wrong row-shape; Dorn's contract requires a `your_move` newer than the last board move, so a dangling turn fails validation and Ralph-Wiggum-retries into closing. Config-driven → game-agnostic, reusable by any firm invariant.
