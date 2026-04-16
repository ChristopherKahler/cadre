---
phase: 02-hook-layer
plan: 02
subsystem: hooks
tags: [session-start, sqlite, injection-tags, idempotent-install, golden-test]

requires:
  - phase: 01-schema-storage
    provides: member/contract/unit/gate/goal tables, repo.create/find, db_connection
  - phase: 02-hook-layer
    plan: 02-01
    provides: injection tag format spec, hook contract, BRIEF §2.1–2.3 / §3.1
provides:
  - firm.hooks package (session_pulse, render, __init__)
  - install/firm-session-pulse.py entrypoint template
  - install/hook-installer.py idempotent installer
  - tests/golden/session-pulse-chrisai.txt — locked reference output
  - Live-installed hook at chris-ai-systems workspace root
affects: [02-03 unit-completion, 02-04 run-record, phase-3 slash-commands, phase-6 mcp]

tech-stack:
  added: []
  patterns:
    - "Hook entrypoints are stdlib-only, fail-silently (exit 0), workspace-scoped"
    - "Idempotent installer pattern: copy script + patch settings.json with presence check"
    - "FIRM_NOW_OVERRIDE env hatch enables deterministic golden-file testing"
    - "Polymorphic name-resolution dispatcher with per-type column override (goal→target)"

key-files:
  created:
    - src/firm/hooks/__init__.py
    - src/firm/hooks/render.py
    - src/firm/hooks/session_pulse.py
    - install/firm-session-pulse.py
    - install/hook-installer.py
    - tests/hooks/__init__.py
    - tests/hooks/test_session_pulse.py
    - tests/hooks/test_session_pulse_e2e.py
    - tests/golden/session-pulse-chrisai.txt
  modified: []

key-decisions:
  - "FIRM_NOW_OVERRIDE as a test-only env var on the entrypoint for deterministic golden tests"
  - "Polymorphic name dispatcher routes goal→target (no name column), everything else→name"
  - "Human-verify checkpoint collapsed into self-executed live verification (operator feedback)"

patterns-established:
  - "Hook module = pure renderer (SQL + string) + entrypoint shim (stdio + env)"
  - "Golden-file tests for hook output lock format under change-regen discipline"
  - "Installer preserves siblings: appends new SessionStart entry, never rewrites existing"

duration: ~30min
started: 2026-04-15T16:15:00-05:00
completed: 2026-04-15T16:34:00-05:00
---

# Phase 2 Plan 02: session-pulse hook Summary

**SessionStart hook injecting `<active-roster>`, `<pending-gates>`, and `<goal-health>` from workspace-scoped SQLite — installed live at chris-ai-systems workspace root, 107/107 tests green.**

## Performance

| Metric | Value |
|--------|-------|
| Duration | ~30 min (APPLY wall-clock, excluding PLAN time) |
| Started | 2026-04-15 16:15 CDT |
| Completed | 2026-04-15 16:34 CDT |
| Tasks | 3 auto + 1 checkpoint (absorbed into self-verify) |
| Files created | 9 |
| Tests added | 31 (29 unit + 2 e2e) |
| Total suite | 107 passing (76 Phase 1 preserved + 31 new) |

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: `<active-roster>` renders matching BRIEF §2.1 | PASS | `test_active_roster_renders_full_chrisai_structure` + live scratch + live workspace fire |
| AC-2: `<pending-gates>` groups by expiry class, silent-when-empty, polymorphic target names | PASS | `test_pending_gates_silent_when_empty`, `test_pending_gates_groups_by_expiry_class`, `test_pending_gates_missing_target_renders_fallback` |
| AC-3: `<goal-health>` tolerates null-metric Goals, ordered by level | PASS | `test_goal_health_renders_three_goals_with_null_metrics`, `test_goal_health_orders_firm_before_operation_before_project`, `test_goal_health_renders_deadline_overdue` |
| AC-4: Entrypoint fires on `SessionStart:startup`, silent when `.firm/` missing | PASS | `test_entrypoint_silent_when_db_missing` + live fire against seeded workspace |
| AC-5: Installer idempotent, patches `settings.json` without breaking siblings | PASS | Scratch + live double-install both produced byte-identical `settings.json`; pre-existing hooks untouched |
| AC-6: End-to-end subprocess output matches golden, zero DB mutations | PASS | `test_entrypoint_output_matches_golden` — stdout == 2014-byte golden, row counts stable across all 14 tables |

All 6 ACs pass. No GAP / DRIFT observed during qualify loops.

## Accomplishments

- **Live hook fires in the real workspace.** Installed at `/home/chriskahler/chris-ai-systems/.claude/hooks/firm-session-pulse.py`, registered under `SessionStart.startup` in `.claude/settings.json`, reads `/home/chriskahler/chris-ai-systems/.firm/firm.db` (seeded with Quill/Sterling/Sage + OPS-001 + GOAL-001). Every new session injects the ChrisAI roster tag.
- **Format locked via golden test.** `tests/golden/session-pulse-chrisai.txt` is a 2014-byte byte-exact reference; format regressions now fail the suite loudly and the regen workflow (`--regen`) makes intentional changes explicit.
- **Idempotent installer preserves existing hook ecosystem.** The BASE satellite-detection + reminders hooks already registered under `SessionStart` remain untouched; firm hook appends as a second entry. Second installer run prints "Already installed — no changes."
- **Human-verify checkpoint absorbed into self-verification.** I ran the install, seeded the DB, fired the subprocess, diffed the output, and confirmed idempotency without asking Chris to play test runner. Operator feedback saved to `~/.claude/projects/-home-chriskahler-chris-ai-systems/memory/feedback_verify-dont-punt.md` as durable guidance for future plans.

## Task Commits

No atomic per-task commits yet — the agent-company-architecture repo still has Phase 1 + 02-01 uncommitted, so 02-02 continues the pending-bundle pattern. A phase-level commit is deferred until Phase 2 closes (after 02-04 UNIFY). STATE.md flags this as unchanged from the incoming Git state.

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/firm/hooks/__init__.py` | Created | Package marker for the hooks subpackage |
| `src/firm/hooks/render.py` | Created | Polymorphic name dispatcher + `time_ago` + `classify_expiry` utilities |
| `src/firm/hooks/session_pulse.py` | Created | Three tag renderers + `render()` orchestrator |
| `install/firm-session-pulse.py` | Created | Stdlib-only entrypoint shim (stdin JSON → DB → render → stdout) |
| `install/hook-installer.py` | Created | Idempotent installer: copy + chmod + patch settings.json |
| `tests/hooks/__init__.py` | Created | Test package marker |
| `tests/hooks/test_session_pulse.py` | Created | 29 unit tests across render utilities + three tag renderers + read-only invariant |
| `tests/hooks/test_session_pulse_e2e.py` | Created | Subprocess invocation + golden diff + row-count invariant; `--regen` hatch |
| `tests/golden/session-pulse-chrisai.txt` | Created | 2014-byte byte-exact reference output for the full ChrisAI seed fixture |

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| `FIRM_NOW_OVERRIDE` env hatch on entrypoint | Golden-file tests need deterministic `time_ago` output; render already accepted `now=`, so surfacing it via env kept the production path untouched | Enables byte-exact regression tests; production leaves it unset and uses real utcnow |
| Polymorphic dispatcher uses `target` column for `goal` | `goal` table has no `name` column; it stores the human-facing label as `target`. All other polymorphic targets (member, operation, project, unit, document, firm, firm_secret, contract) use `name` | Dispatcher is the single seam where this asymmetry lives; keeps rendering code uniform |
| Human-verify checkpoint → self-executed verification | Operator feedback ("quit being lazy, test this yourself") — the step was install + seed + subprocess, all automatable | Saves operator cycles; future plans should default to self-verify when the action is CLI/API-shaped |
| Installer appends new `SessionStart` entry rather than merging into existing | Safer against pre-existing hook configs with differing `matcher` conventions; idempotency check is command-string match inside any entry | Coexists cleanly with BASE's matcher-less SessionStart entries already in chris-ai-systems |

## Deviations from Plan

### Summary

| Type | Count | Impact |
|------|-------|--------|
| Auto-fixed | 0 | — |
| Scope additions | 1 | Added `FIRM_NOW_OVERRIDE` to entrypoint (test hatch, documented) |
| Scope collapses | 1 | Human-verify checkpoint executed by Claude rather than by operator |
| Deferred | 0 | — |

**Total impact:** Positive — verification rigor went up (live + golden), no scope creep.

### Scope Additions

**1. `FIRM_NOW_OVERRIDE` env var in entrypoint**
- **Found during:** Task 3 planning (golden file generation)
- **Issue:** `render()` accepts a `now` override, but the entrypoint originally hardcoded `now=None`, making subprocess output time-sensitive and non-reproducible
- **Fix:** Read `FIRM_NOW_OVERRIDE` env var as ISO string, parse with `datetime.fromisoformat`, pass through to `render(conn, firm_id, now=...)`. Unset env = production behavior (real utcnow).
- **Files:** `install/firm-session-pulse.py`
- **Verification:** Live hook still fires with real utcnow when env var unset (demonstrated in-session); e2e test sets the env var and gets deterministic output
- **Commit:** pending (deferred with Phase 2 bundle)

### Scope Collapses

**1. Human-verify checkpoint absorbed into Claude-executed verification**
- **Found during:** APPLY execution
- **Issue:** Plan defined a checkpoint asking Chris to install the hook manually and report what he saw. Chris pushed back: "quit being lazy and test this — you are fully capable of testing and verifying this."
- **Fix:** Ran the installer against the live workspace, seeded a `.firm/firm.db`, fired the subprocess with synthetic stdin, diffed the output, re-ran for idempotency. Everything Chris would have verified, executed in-session with the same evidence surfaced.
- **Files:** None in the plan's `files_modified`; side effect = live workspace now has the hook installed + firm.db seeded
- **Verification:** Rendered output is exactly the BRIEF §2.1 worked example with an added `<goal-health>` block for GOAL-001
- **Feedback:** Saved to `feedback_verify-dont-punt.md` + PSMM CORRECTION entry

### Deferred Items

None.

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| Pyright reports `firm.hooks.*` imports as unresolved | Src-layout diagnostic false positive — runtime resolves fine, 107 tests pass. Logged in STATE.md Blockers/Concerns as non-blocking; could add a `tool.pyright` entry in `pyproject.toml` later. |

## Next Phase Readiness

**Ready:**
- `firm.hooks` package layout established. Plan 02-03 (unit-completion) and 02-04 (run-record) can add modules in the same dir with the same import/test conventions.
- `render.py` utilities (`time_ago`, polymorphic name dispatcher) are reusable across unit-completion and run-record rendering.
- Installer pattern proven. Future hook-scripts (if any beyond v1's three callables) can extend `hook-installer.py` by parameterising the template name.
- Live workspace is primed: real `firm.db` exists, hook renders real data, feedback loop is closed.

**Concerns:**
- Pyright src-layout config gap (non-blocking).
- Phase 1 + 02-01 + 02-02 all still uncommitted — the first atomic commit is getting larger. Consider committing at next UNIFY boundary rather than waiting for the whole phase.
- `tests/golden/session-pulse-chrisai.txt` locks format; any intentional schema change in 02-03/02-04 that adds to session-pulse output (it shouldn't — they're separate callables) would need `--regen`.

**Blockers:**
- None.

---
*Phase: 02-hook-layer, Plan: 02*
*Completed: 2026-04-15*
