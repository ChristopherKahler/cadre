# PAUL Session Handoff — Phase 2 Plan 02-03 Shipped

**Session:** 2026-04-15, ~17:00–17:30 CDT (~30min, post-02-02 handoff)
**Satellite:** `apps/agent-company-architecture/`
**Phase:** 2 — Hook Layer (3 of 4 plans complete)
**Context:** Resume → plan 02-03 → full APPLY → UNIFY. `firm.hooks.unit_completion.on_unit_done()` + `firm unit complete` CLI shipped with atomic two-write transactions and full dry-run preview. 129/129 tests green.

---

## Session Accomplishments

- Resumed from 02-02 handoff (consumed + archived to `.paul/handoffs/archive/`).
- Created `.paul/phases/02-hook-layer/02-03-PLAN.md` — standard track, 2 autonomous tasks, 6 ACs, fully autonomous (no checkpoints), depends_on `02-02`.
- Executed APPLY end-to-end:
  - **Task 1:** `firm.hooks.unit_completion.on_unit_done()` — transactional records INSERT + `project.acceptance_criteria` JSON flip. 12 unit tests covering happy path, idempotency, no-match AC, null AC, unit-not-found, project-missing, mid-transaction rollback via DB-level trigger, details payload shape, run_id threading, deterministic `now` override.
  - **Task 2:** `firm.cli.unit.run_unit_complete` + nested `unit complete` subparser in `firm.__main__`. 10 CLI tests via subprocess covering happy path, records-write, no-AC-flipped output, `--dry-run` (row-count + AC-bytes pre/post snapshot), unit-not-found, db-missing, argparse surface, help regression guard, FIRM_ID env override, `--firm-id` flag precedence over env.
- Ran UNIFY to close the loop:
  - `02-03-SUMMARY.md` with full template (performance, AC table, 5 decisions, 1 deviation w/ rationale, 1 scope addition, next-phase readiness)
  - `STATE.md` refreshed — loop ✓✓✓, 4 new decisions rolled up, resume points to BRIEF §5.3
  - `paul.json` bumped `timestamps.updated_at`; loop → IDLE; phase still `in_progress`; id preserved
  - `ROADMAP.md` updated — 02-03 ✅, phase count 3/4
- Full test suite: **129/129 passing** (107 prior + 12 unit + 10 CLI).
- Live smoke: `firm unit complete UNIT-NONEXISTENT --dry-run --workspace <chris-ai-systems>` against the real `.firm/firm.db` returns `unit-not-found` with no writes.

---

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Raw SQL + manual `try/commit/except+rollback` instead of `repo.create` / `repo.update` for multi-row hook writes | `repo.*` calls `conn.commit()` internally. Inside a `with conn:` or try/except block that commit defeats rollback — records row would persist even if project UPDATE raised. AC-4 required genuine both-or-neither atomicity. | **02-04 plan author: start with raw-SQL transactions from the outset — run-record writes 4 rows atomically, same issue amplified.** Option to add `repo.create_no_commit` / `repo.update_no_commit` variants in a future cleanup plan. |
| `LOG-NNN` record ids generated sequentially via `SELECT COUNT(*) FROM records WHERE firm_id = ?` | Simplest readable scheme; safe because `records` is immutable (no deletes shrink the count). | v1 single-operator assumption. Not concurrency-safe — two simultaneous writers could collide on `COUNT(*)+1`. Flag if framework grows concurrent writers (Phase 6 MCP?). Document in 02-04 so run-record keeps the pattern. |
| Caller owns `unit.status` mutation; `on_unit_done` records the transition but does not flip the column itself | Keeps handler pure; prevents double-writes when Phase 3 slash commands sequence status update + handler call explicitly; decouples from caller flow. | CLI `firm unit complete` today does NOT flip `unit.status` — only writes records + flips AC. Phase 3 `/unit:complete` will orchestrate the full flow (status update → handler). Noted in CLI docstring. Similar convention will likely apply to `on_run_end` (02-04). |
| DB-level trigger (`BEFORE UPDATE ... RAISE(ABORT, ...)`) as the AC-4 rollback-test fixture | `sqlite3.Connection.execute` is read-only on CPython — can't `patch.object(conn, "execute", ...)`. A pre-update trigger fires inside the real SQL and produces a genuine `sqlite3.IntegrityError`, which is a cleaner simulation of real mid-transaction failure. | Reusable pattern for any future test that needs to force a genuine mid-transaction SQL failure. 02-04 run-record tests should use this pattern. |
| **Commit cadence: DEFER bundle to Phase 2 transition (after 02-04) — option C** | Operator preference at 02-03 UNIFY. Bundle now spans Phase 1 (3 plans) + 02-01 + 02-02 + 02-03 — 4 loops. Waiting one more loop keeps the log cleaner: single commit `feat(firm): phase 1 foundation + phase 2 hook layer`. Trade-off accepted: larger pending-bundle for fewer commits. | Commit fires at Phase 2 → Phase 3 transition, not at 02-03 UNIFY. Pre-transition sanity: the `/paul:transition` workflow will stage and commit everything automatically. |
| Scope addition: `test_complete_help_includes_all_flags` (~15 LOC subprocess test) | Lightweight guard — any future PR that accidentally drops `--dry-run` or renames `--member` trips this test. | Regression protection on the CLI surface. Pattern reusable for 02-04's `firm run end` CLI. |

---

## Gap Analysis with Decisions

### Gap 1: Pyright src-layout import resolution (from 02-02 handoff, still open)
**Status:** DEFER
**Notes:** Pyright reports `firm.hooks.*` imports as unresolved across source + tests. Runtime is clean (129/129 tests green). Fix is `[tool.pyright] extraPaths = ["src"]` in `pyproject.toml` or a `pyrightconfig.json`. Not blocking any work.
**Effort:** ~5min
**Reference:** `apps/agent-company-architecture/pyproject.toml`

### Gap 2: PROJECT.md Key Decisions table append still pending
**Status:** INTENTIONAL (scheduled in 02-04 Task 3 per BRIEF §6)
**Notes:** Pending count now totals ~15 decisions: 8 from BRIEF §6 + 3 from 02-02-SUMMARY + 4 from 02-03-SUMMARY. All land together at 02-04 Task 3.
**Effort:** ~15min when 02-04 runs (up from ~10min — more rows to write)
**Reference:** `.paul/phases/02-hook-layer/02-01-BRIEF.md` §6 + 02-02-SUMMARY §Decisions + 02-03-SUMMARY §Decisions

### Gap 3: Uncommitted bundle — Phase 1 + 02-01 + 02-02 + 02-03
**Status:** DEFER to Phase 2 transition (after 02-04)
**Notes:** Operator decision at 02-03 UNIFY — keep the log clean with one bundled Phase-1+Phase-2 commit at transition rather than splitting at 02-03. Bundle will include: Phase 1 foundation (migrations, repo, units, db) + Phase 2 hooks (session-pulse live-installed + unit-completion + run-record + CLI verbs) + hook installer + tests + all `.paul/` artifacts.
**Effort:** Auto-handled by `/paul:transition` at phase close — ~5min operator time
**Reference:** `git status` in `apps/agent-company-architecture/`

### Gap 4: `repo.create_no_commit` / `repo.update_no_commit` variants for transactional callers
**Status:** DEFER (post-Phase-2 cleanup)
**Notes:** 02-03 proved that `repo.*` internal-commit semantics break transactional callers. 02-04 will hit the same issue (4-row atomic write). Instead of adding the variants now (would widen 02-04 scope), each hook continues using raw SQL inside a manual transaction. Revisit after Phase 2 is closed — maybe a small dedicated cleanup plan between Phase 2 and Phase 3, or fold into Phase 6 (MCP) when the repo surface gets its next round of hardening.
**Effort:** ~30min to add + tests
**Reference:** `src/firm/core/repo.py` lines 152, 205 (the `conn.commit()` calls)

### Gap 5: CLI doesn't flip `unit.status` during `firm unit complete`
**Status:** INTENTIONAL — Phase 3 concern
**Notes:** Per decision above, caller owns status mutation. Today running the CLI directly records the transition but doesn't actually update the column. When `/unit:complete` slash command lands in Phase 3, it will sequence `repo.update(conn, "unit", id, {"status": "done"}) → on_unit_done(...)`. Users testing manually today need to know this.
**Effort:** N/A (design choice; doc added to CLI docstring)
**Reference:** `src/firm/cli/unit.py` module docstring; 02-03-SUMMARY §Decisions

### Gap 6: `LOG-NNN` concurrency safety
**Status:** DEFER (v1 single-operator; tag for Phase 6 MCP)
**Notes:** Count-based id generation races under concurrent writers. Not a v1 concern. When firm MCP exposes concurrent write endpoints (Phase 6+), switch to a properly atomic scheme — either `RETURNING id` with an autoincrement column, or a UUID/ULID.
**Effort:** ~20min schema migration + handler updates when needed
**Reference:** `src/firm/hooks/unit_completion.py::_next_records_id`

---

## Open Questions

1. **02-04 Task 3 scope:** Should the PROJECT.md Key Decisions table append land INSIDE 02-04 as currently planned (BRIEF §6), or get its own tiny plan before transition? Current plan: keep it in 02-04 Task 3 for consistency with BRIEF. Flag if the ~15-decision append feels too large to bundle with run-record work.
2. **Phase 2 transition: single commit or split Phase 1 vs Phase 2?** Operator chose single bundle at Phase 2 close (option C). Confirm at transition time, or change your mind earlier if the bundle starts feeling unwieldy.
3. **`unit.outputs` rollup timing:** 02-04 will merge `run.outputs` into `unit.outputs` when a Run has a unit_id. Does that happen unconditionally, or only when the Run is status=completed (not failed/cancelled)? BRIEF §3.3 implies unconditional. Verify in 02-04 planning.

---

## Reference Files for Next Session

**Primary reads on resume:**
```
@.paul/phases/02-hook-layer/02-03-SUMMARY.md    # what just shipped
@.paul/phases/02-hook-layer/02-01-BRIEF.md      # §3.3 = run-record contract, §5.3 = 02-04 scope outline
@.paul/STATE.md                                  # current loop + next action
```

**Code context for 02-04 (run-record + PROJECT.md decisions):**
```
@src/firm/hooks/unit_completion.py              # 02-03 reference — raw-SQL transaction pattern to mirror
@src/firm/hooks/__init__.py                     # package exports
@src/firm/cli/unit.py                           # CLI verb pattern to mirror for `firm run end`
@src/firm/__main__.py                           # nested subparser pattern — extend for `run end`
@src/firm/core/repo.py                          # reminder: repo.create/update commit internally
@src/firm/migrations/002_entities.sql           # member_run (mutable), usage_event (immutable), records, unit
```

**Tests to pattern-match:**
```
@tests/hooks/test_unit_completion.py            # 12 tests; fixture + trigger-based rollback test
@tests/cli/test_unit.py                         # 10 subprocess-based CLI tests
```

**BRIEF sections for 02-04:**
```
@.paul/phases/02-hook-layer/02-01-BRIEF.md      # §3.3 run-record contract
                                                 # §5.3 plan outline (3 tasks)
                                                 # §6 decisions to append to PROJECT.md
```

**Decisions to append to PROJECT.md in 02-04 Task 3:**
- 8 from BRIEF §6 (session-pulse trigger, callable functions, install path, FIRM_ID, goal-health read-only, silent-when-empty, redaction, no-inheritance-rendering)
- 3 from 02-02-SUMMARY (FIRM_NOW_OVERRIDE, polymorphic dispatcher, human-verify default)
- 4 from 02-03-SUMMARY (raw-SQL transaction, LOG-NNN ids, caller-owns-status, DB-trigger test fixture)
- Total: ~15 rows to append

---

## Prioritized Next Actions

| Priority | Action | Effort | Notes |
|----------|--------|--------|-------|
| 1 | `/paul:plan` for 02-04 (run-record + PROJECT.md decisions append) | ~10min | Scope in BRIEF §5.3: 3 tasks, autonomous. (a) `firm.hooks.run_record.on_run_end()` + `_redact.py`, (b) `firm run end <id>` CLI verb, (c) PROJECT.md Key Decisions append (~15 rows from BRIEF §6 + 02-02/03 decisions). Start with raw-SQL transaction pattern from 02-03. |
| 2 | APPLY 02-04 | ~60–90min | 4 atomic writes per call: member_run UPDATE (mutable) + usage_event INSERT (immutable) + unit.outputs merge (conditional on unit_id) + records INSERT. Regex-based credential redaction on error/notes before write. Fixtures: completed run with full usage / failed run with error / no-unit-id run / redaction on notes-with-api_key. |
| 3 | UNIFY 02-04 + Phase 2 transition | ~10min + transition | SUMMARY + STATE + paul.json + ROADMAP update. **Bundle commit fires here** — `/paul:transition` handles git staging. Phase 2 → Phase 3. |
| 4 | `/paul:plan` for Phase 3 (first plan) | ~10min | Phase 3 = Core Slash Commands. First plan likely `/unit:complete`, `/unit:claim`, `/gate:decide`, `/firm:status`. |

---

## State Summary

**Current position:**
- Milestone: v0.1 Initial Release (1 of 8 phases complete)
- Phase: 2 — Hook Layer (3 of 4 plans complete)
- Loop: `PLAN ✓ APPLY ✓ UNIFY ✓` for 02-03
- `paul.json`: `phase.status: in_progress`, `loop.position: IDLE`
- Tests: 129/129 green
- Commit bundle: DEFERRED to Phase 2 transition

**Next:**
- `/paul:plan` for 02-04 (run-record + PROJECT.md decisions append)

**Resume:**
- `/paul:resume` in `apps/agent-company-architecture/` — will auto-detect this handoff
- Primary read: `02-03-SUMMARY.md` for context, then `02-01-BRIEF.md` §3.3 + §5.3 for 02-04 scope
- Bring raw-SQL transaction pattern from `src/firm/hooks/unit_completion.py` into the 02-04 plan from the outset

---

*Handoff created: 2026-04-15 ~17:30 CDT*
*Sizes: SUMMARY 199 lines / PLAN 166 lines / unit_completion.py 150 lines / 22 new tests*
