# Project State

## Project Reference

See: .paul/PROJECT.md (updated 2026-04-15 after Phase 1 completion)

**Core value:** Solo operators can treat their AI workflows as a company — with Members, Goals, and autonomous delegation — instead of hand-driving every session.
**Current focus:** Phase 2 — Hook Layer — Plan 02-03 CLOSED. Ready for 02-04 (run-record + PROJECT.md decisions append).

## Current Position

Milestone: v0.1 Initial Release (1 of 8 phases complete → Phase 2 transition pending)
Phase: 2 of 8 — Hook Layer — COMPLETE (4 of 4 plans closed)
Plan: 02-04 loop closed — SUMMARY written
Status: Phase 2 complete. Transition required.
Last activity: 2026-04-15 ~20:30 CDT — 02-04 UNIFY complete

Progress:
- Milestone: [██░░░░░░░░] ~25%
- Phase 2: [██████████] 100% (4 of 4 plans complete)

## Loop Position

Current loop state:
```
PLAN ──▶ APPLY ──▶ UNIFY          [02-04 loop closed]
  ✓        ✓        ✓
```
Next: Phase 2 transition (commit bundle + ROADMAP update + route to Phase 3)

## Accumulated Context

### Recent Decisions (full log to land in PROJECT.md at 02-04)
- **SQLite data store** supersedes JSON files (01-01)
- **Python single-language stack** (01-01)
- **Per-migration explicit transaction control** (01-01)
- **SQL splitter handles BEGIN/END + inline `--`** (01-02)
- **Polymorphic refs as `*_entity_type` + `*_entity_id` + CHECK** (01-02)
- **`member_run` mutable; immutable = comment, records, usage_event** (01-02)
- **`repo.find` not `list`** (01-03)
- **Atomic Unit checkout via RETURNING** (01-03)
- **session-pulse on SessionStart:startup** (02-01)
- **unit-completion + run-record = callable functions in v1** (02-01)
- **Hook install path `<workspace>/.claude/hooks/firm-*.py`** (02-01)
- **FIRM_ID env var default `chrisai`** (02-01)
- **Goal health read-only v1** (02-01)
- **`<pending-gates>` silent-when-empty** (02-01)
- **Credential-regex redaction on `run-record`** (02-01, applies in 02-04)
- **`FIRM_NOW_OVERRIDE` env hatch on entrypoint for deterministic golden tests** (02-02)
- **Polymorphic dispatcher: `goal`→`target`, others→`name`** (02-02)
- **Human-verify checkpoints should default to Claude-executed when action is automatable** (02-02, ops principle)
- **Raw SQL + manual transaction for multi-row hook writes** (02-03, `repo.*` commits internally; reuse in 02-04)
- **`LOG-NNN` record ids via `COUNT(*)` per firm_id** (02-03; v1 single-operator; concurrency caveat)
- **Caller owns `unit.status` mutation; `on_unit_done` records the transition only** (02-03)
- **DB-trigger fixture for mid-transaction failure tests** (02-03, replaces monkeypatch since `Connection.execute` is read-only)
- Full list + rationale: BRIEF §6 + 02-02-SUMMARY §Decisions + 02-03-SUMMARY §Decisions — to land in PROJECT.md at 02-04 Task 3

### Deferred Issues
None.

### Blockers/Concerns
- Pyright cannot resolve `firm.hooks.*` imports (src-layout config gap). Runtime + tests fine. Non-blocking.
- Phase 1 + 02-01 + 02-02 + 02-03 all uncommitted. Operator decision at 02-03 UNIFY: DEFER — bundle commit will fire at Phase 2 transition (after 02-04). Bundle growing to 4 loops; still manageable.
- `LOG-NNN` id generation is not concurrency-safe (count-based). v1 single-operator; flag if the framework grows concurrent writers.

### Git State
Last phase: Phase 1 + 02-01 + 02-02 + 02-03 (all pending bundle commit)
Branch: main
Feature branches merged: none

### 02-02 Execution Summary
- 3/3 auto tasks PASS + 1 human-verify collapsed into self-verification
- 107/107 tests green (76 Phase 1 + 29 unit + 2 e2e)
- Live install at `/home/chriskahler/chris-ai-systems/.claude/hooks/firm-session-pulse.py`
- `/home/chriskahler/chris-ai-systems/.firm/firm.db` seeded (Quill, Sterling, Sage, OPS-001, GOAL-001)
- Golden file: `tests/golden/session-pulse-chrisai.txt` (2014 bytes)
- SUMMARY: `.paul/phases/02-hook-layer/02-02-SUMMARY.md`

## Session Continuity

Last session: 2026-04-15 ~20:30 CDT
Stopped at: 02-04 UNIFY complete — loop closed, Phase 2 transition pending
Next action: Phase 2 transition — bundle commit (Phase 1 + Phase 2 all uncommitted), ROADMAP update, route to Phase 3
Resume file: .paul/phases/02-hook-layer/02-04-SUMMARY.md
Resume context: Phase 2 (Hook Layer) complete — 4/4 plans shipped. 154/154 tests green. Deliverables: session-pulse hook (live-installed), unit-completion handler + CLI, run-record handler + CLI, _redact utility, PROJECT.md with 34 decisions. Bundle commit covers Phase 1 (foundation: scaffold + migrations + repo) + Phase 2 (hooks: session-pulse + unit-completion + run-record + CLI verbs). Operator chose single-bundle commit at 02-03 UNIFY — confirm at transition or split.

### 02-03 Execution Summary
- 2/2 auto tasks PASS
- 22 new tests (12 unit-completion + 10 CLI) + 107 prior = 129/129 green
- Files added: `src/firm/hooks/unit_completion.py`, `src/firm/cli/unit.py`, `tests/hooks/test_unit_completion.py`, `tests/cli/__init__.py`, `tests/cli/test_unit.py`
- Files modified: `src/firm/hooks/__init__.py` (export `on_unit_done`), `src/firm/__main__.py` (`unit complete` subcommand)
- Deviation from PLAN: raw SQL inside single manual transaction for records INSERT + project UPDATE — required for AC-4 atomicity since `repo.create`/`repo.update` commit internally
- Scope addition: `test_complete_help_includes_all_flags` (CLI help regression guard, ~15 LOC)
- Manual smoke: `firm unit complete UNIT-NONEXISTENT --dry-run` against live workspace DB returns `unit-not-found` (read-only confirmed)
- SUMMARY: `.paul/phases/02-hook-layer/02-03-SUMMARY.md`

---
*STATE.md — Updated after every significant action*
