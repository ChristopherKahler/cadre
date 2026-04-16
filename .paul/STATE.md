# Project State

## Project Reference

See: .paul/PROJECT.md (updated 2026-04-15 after Phase 1 completion)

**Core value:** Solo operators can treat their AI workflows as a company — with Members, Goals, and autonomous delegation — instead of hand-driving every session.
**Current focus:** Phase 3 — Core Slash Commands — 03-01 research complete. Ready for 03-02 scaffold.

## Current Position

Milestone: v0.1 Initial Release (2 of 8 phases complete, Phase 3 in progress)
Phase: 3 of 8 — Core Slash Commands — IN PROGRESS
Plan: 03-03 loop closed — service infrastructure complete
Status: Wave 1 complete (03-02 scaffold + 03-03 service infra). Wave 2 ready.
Last activity: 2026-04-15 ~21:25 CDT — 03-03 SUMMARY written

Progress:
- Milestone: [██░░░░░��░░] ~25%
- Phase 3: [░░░░░░░��░░] 0% (plan 03-01 in design)

## Loop Position

Current loop state:
```
PLAN ──▶ APPLY ──▶ UNIFY          [03-03 closed]
  ✓        ✓        ✓
```
Next: Wave 2 — 03-04 (Firm/Member/Operation) through 03-07 (Comment/Contract/Doc) — all parallel

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
- Phase 1 + Phase 2 committed as bundle (4ac7cc6 + c5df0e2). Phase 3 research committed (6bd4c58).
- `LOG-NNN` id generation is not concurrency-safe (count-based). v1 single-operator; flag if the framework grows concurrent writers.

### Git State
Last commit: 6bd4c58 — feat(03-core-slash-commands): Phase 3 entered — 03-01 research complete
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

Last session: 2026-04-15 ~21:25 CDT
Stopped at: 03-03 complete — service infrastructure shipped (181/181 tests)
Next action: Wave 2 — /paul:plan for 03-04 (Firm/Member/Operation services)
Resume file: .paul/phases/03-core-slash-commands/03-03-SUMMARY.md
Resume context: Wave 1 done. Skill directory scaffolded (03-02). Service infra shipped: _id (next_id, global count), _validate (require_exists, validate_status, validate_parent_ref, validate_fk), _records (log_event). 181 tests green. Wave 2 ready: 03-04 through 03-07 are all parallel candidates.

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
