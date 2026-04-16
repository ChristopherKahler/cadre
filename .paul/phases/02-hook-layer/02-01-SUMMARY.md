---
phase: 02-hook-layer
plan: 01
type: research
completed: 2026-04-15T15:55:00-05:00
duration: ~30min
---

# Phase 2 Plan 01: Hook Layer Research — Summary

**Produced a research brief that locks injection tag format + hook contracts before any hook code is written.**

## Objective

Lock Phase 2's open architectural questions (injection tag specifics, hook triggers, Paperclip borrow decisions, plan split) so plans 02-02/03/04 can be written without rediscovery.

## What Was Built

| File | Purpose | Lines |
|------|---------|-------|
| `.paul/phases/02-hook-layer/02-01-BRIEF.md` | Research brief — 7 sections locking format, contracts, borrows, plan split, decisions | 414 |
| `.paul/phases/02-hook-layer/_notes/02-01-notes-context.md` | Task 1 output — entity field facts, SQL shapes, schema gaps | 173 |
| `.paul/phases/02-hook-layer/_notes/02-01-notes-paperclip.md` | Task 2 output — 16 KEEP/ADAPT/REJECT decisions against Paperclip reference | 186 |
| `.paul/phases/02-hook-layer/_notes/02-01-notes-precedent.md` | Task 3 output — BASE/CARL injection conventions audit | 186 |

**No source code, migrations, or PROJECT.md edits** — strictly within boundaries (research-only plan).

## Acceptance Criteria Results

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| AC-1 | Injection tag format fully specified (SQL + rendered + example + policy per tag) | **PASS** | BRIEF §2.1 (`<active-roster>`), §2.2 (`<pending-gates>`), §2.3 (`<goal-health>`) |
| AC-2 | Hook contracts unambiguous (trigger / input / reads / writes / output / failure modes per hook) | **PASS** | BRIEF §3.1–3.3 — table format covers all required dimensions |
| AC-3 | Paperclip borrow decisions explicit with rationale | **PASS** | BRIEF §4 — 16-pattern keep/adapt/reject table |
| AC-4 | Plan split actionable (02-02/03/04 named, tasks outlined, deps + checkpoints flagged) | **PASS** | BRIEF §5 — three plans with 3/2/3 tasks respectively |
| AC-5 | Decisions surfaced for logging (≥3 PROJECT.md-format rows ready to paste) | **PASS** | BRIEF §6 — 8 rows in Decision \| Rationale \| Date \| Status format |

## Verification Results

```
$ grep -c "^## " .paul/phases/02-hook-layer/02-01-BRIEF.md
7                                                          # ≥6 sections required — PASS

$ wc -l .paul/phases/02-hook-layer/02-01-BRIEF.md
414                                                        # <600 target — PASS

$ grep -c "ENTITY-DESIGN.md" .paul/phases/02-hook-layer/_notes/02-01-notes-context.md
8                                                          # ≥5 citations required — PASS

$ grep -cE "^\*\*(KEEP|ADAPT|REJECT)" .paul/phases/02-hook-layer/_notes/02-01-notes-paperclip.md
16                                                         # ≥6 decisions required — PASS

$ grep -c "<[a-z-]\+" .paul/phases/02-hook-layer/_notes/02-01-notes-precedent.md
26                                                         # ≥5 tag citations required — PASS
```

All task verify commands green.

## Deviations

None. Plan executed as specified:
- All 4 tasks completed in order
- No checkpoints triggered (plan was fully autonomous)
- No scope changes
- Notes archived under `_notes/` subfolder per plan's recommended approach

## Key Decisions Locked (full list in BRIEF §6)

1. `session-pulse` triggers on `SessionStart:startup`, not `UserPromptSubmit` (one per session; avoids v1 dedup complexity)
2. `unit-completion` + `run-record` ship as callable Python functions in v1; auto-hooking deferred to Phase 6 MCP
3. Hook install path: `<workspace>/.claude/hooks/firm-*.py`
4. Firm ID resolution via `FIRM_ID` env var with default `"chrisai"`
5. Goal health is read-only v1 (no metric computation)
6. Silent-when-empty rendering for `<pending-gates>` and `<goal-health>`
7. Credential-regex redaction on `run-record` writes before immutable insert
8. Goal inheritance rendering deferred to v2

**Recording location:** Decisions stay in BRIEF until Plan 02-04 Task 3 appends them to `.paul/PROJECT.md` Key Decisions table. This avoids premature mutation during an early research plan.

## Key Patterns Emerged

- **"Callable module + CLI verb + future hook"** pattern — v1 ships logic as pure functions callable from CLI; Phase 6 re-wraps them as tool-triggered hooks. Avoids building hook infra before the MCP surface exists to drive them.
- **Paperclip's `logActivity` shape validated our `records` design** — Phase 1 schema was independently derived; external reference confirms we got it right.
- **Silent-when-empty** is the idiomatic firm-hook behavior (matches `<base-pulse>`), NOT forced zero-state tags.

## Skill Audit

No `.paul/SPECIAL-FLOWS.md` configured — skill audit skipped (workflow per `unify-phase.md` §audit_skill_invocations).

## Next Phase

**Phase 2 continues — 3 execute plans remain:**
- **02-02** (session-pulse hook, 3 tasks, human-verify checkpoint after install)
- **02-03** (unit-completion handler, 2 tasks, autonomous)
- **02-04** (run-record handler + PROJECT.md decisions append, 3 tasks, autonomous)

**Immediate next action:** Run `/paul:plan` in this satellite to enter PLAN for 02-02 using the BRIEF §5.1 scope as the starting point.

---
*Plan 02-01 loop closed: PLAN ✓ APPLY ✓ UNIFY ✓*
