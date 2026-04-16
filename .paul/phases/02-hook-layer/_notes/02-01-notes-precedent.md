# Task 3 Notes — Workspace Injection Precedent Audit

**Sources:**
- `chris-ai-systems/.base/hooks/{base-pulse-check.py, active-hook.py, backlog-hook.py, apex-insights.py, operator.py, psmm-injector.py, reminders-hook.py, satellite-detection.py}`
- `chris-ai-systems/.claude/hooks/{carl-hook.py, get-calendar-today.py, get-current-time-cst.py}`
- `~/.claude/hooks/{carl-hook.py, dynamic-rules-loader.py, get-current-time-cst.py, get-machine-context.py, post-compact-recovery.py}`
- Live injection samples visible in THIS session's `<system-reminder>` payload (ground truth for rendered output)

**Purpose:** Framework hooks must feel consistent with BASE/CARL look-and-feel so a session doesn't see two foreign visual dialects.

---

## 1. Observed Tag Catalog (Live Session Ground Truth)

Tags actually present in this session's injection payload:

| Tag | Source hook | Trigger | Purpose |
|-----|-------------|---------|---------|
| `<base-pulse>` | `.base/hooks/base-pulse-check.py` | UserPromptSubmit | Workspace health alert (silent when clean) |
| `<active-awareness items="11">` | `.base/hooks/active-hook.py` | UserPromptSubmit | Active project summary |
| `<backlog-awareness items="28">` | `.base/hooks/backlog-hook.py` | UserPromptSubmit | Backlog summary |
| `<carl-rules>` | `.claude/hooks/carl-hook.py` | UserPromptSubmit (fresh) | Rule injection |
| `<carl-status dedup="true" prompt="N" domains="...">` | `.claude/hooks/carl-hook.py` | UserPromptSubmit (dedup) | Dedup marker |
| `<decisions>` | (within carl output) | UserPromptSubmit | Decision log summary |
| `<operator>` | `.base/hooks/operator.py` | UserPromptSubmit | Operator profile |
| `<calendar imminent="false">` | `.claude/hooks/get-calendar-today.py` | UserPromptSubmit | Today's calendar |
| `<current-time>` | `.claude/hooks/get-current-time-cst.py` | UserPromptSubmit | Timestamp |
| `<machine>` | `~/.claude/hooks/get-machine-context.py` | UserPromptSubmit | Host machine type |
| `<base-satellites>` | `.base/hooks/satellite-detection.py` | SessionStart:startup | PAUL satellite registry |

All rendered at the top of user-prompt submissions. Claude Code concatenates hook stdout into the injection payload.

---

## 2. Conventions to Respect

| Convention | Observed in | Rule for firm hooks |
|------------|-------------|---------------------|
| **Tag naming** | `<active-awareness>`, `<backlog-awareness>`, `<base-pulse>`, `<carl-rules>` | Kebab-case. Use suffix `-awareness` for lists, `-pulse` for alerts, `-rules` for rules. Framework-specific prefix recommended: `<firm-*>` or `<active-roster>` without prefix if name is already scoped (like `<base-pulse>`). |
| **Count attribute** | `<active-awareness items="11">` | Include `items="N"` when surface is a list. |
| **Dedup attribute** | `<carl-status dedup="true" prompt="3">` | If hook emits the same content twice in a session, track signature and emit short dedup marker. Not required v1. |
| **Body format** | Plain text + markdown bullets with `-` prefix | Do NOT nest XML. Keep it flat and readable. |
| **Priority/group headers** | `[URGENT]`, `[HIGH]`, `[MEDIUM]` uppercase in brackets | Use for grouping lists. Consistent with active-hook + backlog-hook. |
| **Inline staleness markers** | `STALE: 5d since update (threshold: 3d)` | Same format for time-sensitive data. |
| **Behavior directive** | active-hook closes with `BEHAVIOR: This context is PASSIVE AWARENESS ONLY. Do NOT proactively mention these items unless: ...` | Surfaces that are informational (not action-triggering) MUST include a behavior directive so Claude doesn't monologue about them. |
| **Silent-when-empty** | `base-pulse-check.py` prints nothing when drift=0 and no reminders | Hook should `sys.exit(0)` silently when no content is warranted — avoid empty tags. |
| **Entry command hint** | `<base-pulse>` ends with `Run /base:carl-hygiene when ready` | When hook detects actionable state, name the specific command. |

---

## 3. Wire-Format Examples to Mirror

### Example A — List surface (active-hook.py output)
```
<active-awareness items="11">
Load: 11 active | 1 blocked | 0 ongoing | 0 deferred
[URGENT]
- [PRJ-001] (community) Skool Recalibration — CC Strategic AI (in_progress)
  REV: $97/$997 tiers (recurring)
  NEXT: One-month-day sprint Sunday Apr 12 to finish final polish
  DUE: 2026-04-12
  STALE: 5d since update (threshold: 3d)
[HIGH]
- [PRJ-003] (saas) CaseGate - Legal Intake SaaS (in_review)
  PAUL: Phase 25/None (UAT Bug Fixes) | IDLE | plan 15d ago | HANDOFF
  ...

BEHAVIOR: This context is PASSIVE AWARENESS ONLY.
Do NOT proactively mention these items unless:
  - User explicitly asks (e.g., "what should I work on?", "what's next?")
  - A deadline is within 24 hours AND user hasn't acknowledged it this session
For details on any item, use base_get_project(id).
</active-awareness>
```

**Pattern features:** count attribute, summary line ("Load: N..."), priority groups, bullet + 2-space-indent sub-lines, inline staleness, behavior directive.

### Example B — Alert surface (base-pulse output)
```
<base-pulse>
CARL hygiene never run. Run /base:carl-hygiene when ready | 3 staged proposals pending
</base-pulse>
```

**Pattern features:** short, actionable, no count attribute, lists alerts joined by ` | `, names command to run.

### Example C — Config surface (current-time output — full example from live session)
```
<current-time>
Today: Wednesday, April 15, 2026
Current Time: 2026-04-15 15:28:11 CDT
ISO: 2026-04-15T15:28:11-05:00
Timezone: America/Chicago (CDT)
</current-time>
```

**Pattern features:** fixed-shape key-value, no attributes, small payload. Good template for `<firm-config>` or static-ish info.

### Example D — Rule-injection surface (carl-hook output, dedup variant)
```
<carl-status dedup="true" prompt="7" domains="GLOBAL">
CARL rules were NOT injected this prompt (dedup: signature unchanged).
Prior injection is still in your context window — operate on those rules.
...
</carl-status>
```

**Pattern features:** dedup flag, prompt counter, brief explanation of why body is skipped, preserves attributes for audit.

### Example E — Satellite detection (SessionStart startup hook)
```
SessionStart:startup hook success: <base-satellites>
Stale (not found on disk): core-cal, casegate-v2, awesome-intake, remotion-videos, hipshot-media
</base-satellites>
```

**Pattern features:** prefixed with hook success line, minimal body, drift reporting.

---

## 4. Python Hook Structural Conventions

Observed in every BASE/CARL hook file:

```python
#!/usr/bin/env python3
"""One-line purpose, one-line source-of-truth file reference."""

import sys
import json
from pathlib import Path
from datetime import date, datetime

# Constants at top
HOOK_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = HOOK_DIR.parent.parent   # .claude/hooks/ → workspace root
DATA_FILE = WORKSPACE_ROOT / ".base" / "data" / "projects.json"

def main():
    # Try to read stdin JSON (hook input); swallow errors
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        pass

    # Exit silently if data missing
    if not DATA_FILE.exists():
        sys.exit(0)

    try:
        data = json.loads(DATA_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        sys.exit(0)

    # ... compute output ...

    if output:
        print(f"<tag-name>\n{output}\n</tag-name>")

    sys.exit(0)

if __name__ == "__main__":
    main()
```

**Key patterns:**
- **Graceful no-op on missing data** — `sys.exit(0)` instead of error. Hook chain must not break session startup if `.firm/firm.db` doesn't exist.
- **Absolute-path resolution from `__file__`** — `HOOK_DIR.parent.parent` assumes hook lives in `<workspace>/.claude/hooks/`. For `apps/agent-company-architecture/`, hooks install into the PARENT workspace's `.claude/hooks/`, resolving via a workspace-root search or explicit config.
- **Swallow JSON decode errors** — never let a malformed data file crash the hook.
- **stdout IS the injection channel** — `print()` on valid output; `sys.exit(0)` otherwise.

---

## 5. Hook Install Path Decision

Two options for where firm hooks land:

**Option P1 (workspace-scoped):** `<workspace>/.claude/hooks/firm-*.py`
- Matches BASE pattern (`.base/hooks/` → registered in `.claude/settings.json`)
- Works because `.firm/firm.db` is workspace-scoped too

**Option P2 (user-global):** `~/.claude/hooks/firm-*.py`
- Hook exists once per machine, reads whichever workspace's `.firm/firm.db` from cwd
- Matches user-global CARL pattern

**Recommendation for BRIEF:** Option P1. Firm is workspace-scoped (`.firm/` is workspace-root); hooks should be too. Installer can symlink or copy into `~/.claude/hooks/` for user-scoped needs later.

---

## 6. Hook Event Selection

| Event | Fires on | Good for | Hook candidates |
|-------|----------|----------|-----------------|
| `SessionStart:startup` | Terminal opens / new session | One-time boot scans | satellite-detection.py pattern. Consider for firm **session-pulse** (v1). |
| `UserPromptSubmit` | Before each prompt | Per-turn context injection | Most BASE/CARL hooks. Also valid for session-pulse if we want it every turn. |
| `PostToolUse` | After each tool call | Reactive logging | Candidate for **unit-completion** if a `base_update_unit` MCP tool exists post-Phase 6. |
| `Stop` | Session ends | Finalization writes | Candidate for **run-record** (write usage_event + finalize member_run). |

**Trigger recommendations (brief should lock these):**
- `session-pulse` → `SessionStart:startup` (runs once per session) OR `UserPromptSubmit` (every turn, with aggressive dedup). v1 recommendation: SessionStart. Matches BASE satellite detection precedent.
- `unit-completion` → v1: manually invoked via slash command (Phase 3). Hook-triggered unit-completion requires Phase 6 MCP to fire PostToolUse. Defer auto-hook to Phase 6.
- `run-record` → `Stop` event (session end) OR manual invoke at end of a Member Run via `/member:run` wrapper. v1: manual wrap. Chris's existing sessions aren't framework-scoped, so SessionStop would write spurious runs.

**Implication:** Phase 2 may only deliver `session-pulse` fully. The other two hooks get scaffolded contracts + manual-invoke wrappers; auto-triggering lands in Phase 6. Brief should call this out as a scope-tightening recommendation.

---

## 7. Dedup Strategy

CARL computes signature: `bracket | devmode | always_on | matched | commands`. When signature unchanged prompt-to-prompt, emits `<carl-status dedup="true" prompt="N">` instead of full body.

**Applied to firm hooks:**
- `session-pulse` signature: `firm_id | active_member_count | pending_gate_count | active_goal_count | most_recent_record_ts`
- If all five unchanged since last prompt → dedup marker.
- Re-emit every N=5 prompts regardless to avoid long-horizon drift (matches CARL's `FORCE_EMIT_EVERY_N`).

v1 can SKIP dedup if SessionStart-only trigger (fires once anyway). Add dedup only if trigger moves to UserPromptSubmit.

---

## 8. Citation Index

- `.base/hooks/base-pulse-check.py` (full read — 217 lines)
- `.base/hooks/active-hook.py` (full read — 179 lines)
- `.claude/hooks/carl-hook.py` (first 100 lines read; signature + dedup logic confirmed)
- Live session injection payload (empirical ground truth for rendered format)

**Example tag references in this doc:** 12 distinct tags cited.
