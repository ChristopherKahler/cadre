# Members Design — ChrisAI Firm Roster

**Purpose:** Concrete Member instances and the Operations / Goals / Projects they own. Companion to `ENTITY-DESIGN.md` (which defines the schemas).
**Status:** Active. Updated as Members come online or scope shifts.
**Last updated:** 2026-04-14

---

## Firm Record

```json
{
  "id": "chrisai",
  "name": "ChrisAI",
  "description": "Chris Kahler's personal AI-operated firm.",
  "operator": { "name": "Chris Kahler", "role": "Board / Founder" },
  "north_star": "$7k+/month on 25 hours/week or less — no obligatory night work by Sept 2026",
  "values": ["Balance", "Integrity", "Experience", "Humility", "Alignment"],
  "vision": "Life moves at the pace I choose — spontaneous, present, unrestricted."
}
```

---

## Active Roster

### Sterling (MEM-002) — CMO (Chief Marketing Officer)

**Scope:** Owns OPS-001 Content Publishing. Sets strategic direction, queues Units for producer Members, identifies team gaps, proposes hires.

**Critical responsibility:** **Identifies Member gaps.** Sterling is expected to surface role needs ("we need a data analyst to wire GA," "we need an SEO specialist") and propose new Member hires via Gates to Board. Chris should NOT need to identify these gaps — that's Sterling's job.

**Operating principle reminder:** Board (Chris) is yes/no authority, NOT scope-definer. Sterling runs the function.

```json
{
  "id": "MEM-002",
  "firm_id": "chrisai",
  "name": "Sterling",
  "role": "CMO",
  "description": "Owns Content Publishing operation. Sets strategy, queues Units for producer Members, identifies team gaps, proposes hires.",
  "status": "active",
  "reports_to": null,
  "contract_id": "CON-002",
  "suggested_skills": ["blog:strategy", "blog:audit", "blog:surface"],
  "suggested_domains": ["content", "projects"],
  "budget": {
    "enforcement": "off",
    "limits": {
      "api_monthly_usd": null,
      "window_percent_cap": null,
      "plan": "claude_pro_100"
    }
  }
}
```

### Sage (MEM-003) — Content Strategist

**Scope:** Owns topic ideation from the 4 pillars. Analyzes pillar coverage, surface opportunities, hands curated topics to Sterling for assignment. Reports to Sterling.

```json
{
  "id": "MEM-003",
  "firm_id": "chrisai",
  "name": "Sage",
  "role": "Content Strategist",
  "description": "Owns topic ideation from pillars. Analyzes pillar coverage, identifies opportunities, curates topics for Sterling to assign.",
  "status": "active",
  "reports_to": "MEM-002",
  "contract_id": "CON-003",
  "suggested_skills": ["blog:ideate", "blog:research", "blog:surface"],
  "suggested_domains": ["content"],
  "budget": {
    "enforcement": "off",
    "limits": {
      "api_monthly_usd": null,
      "window_percent_cap": null,
      "plan": "claude_pro_100"
    }
  }
}
```

### Quill (MEM-001) — Blog Author

**Scope:** Owns the blog authoring pipeline end-to-end. Ideate → research → outline → draft → humanize → editorial → publish. Receives Units from Sterling; executes production.

**Does NOT own:** Social repurposing (reserved for Echo), longform video (reserved for Harbor).

**Quill is NOT the blog skill — Quill USES the blog skills.** Entry command `/quill:run <stage>` dispatches to `/blog:*` skills per stage.

**Split signal:** If Quill catches themselves needing to switch mental modes (e.g., writing tweets vs blog), that's the boundary — promote to a new Member.

```json
{
  "id": "MEM-001",
  "firm_id": "chrisai",
  "name": "Quill",
  "role": "Blog Author",
  "description": "Owns the blog authoring pipeline end-to-end. USES blog skills, is not the blog skill itself.",
  "status": "active",
  "reports_to": "MEM-002",
  "contract_id": "CON-001",
  "suggested_skills": ["blog:ideate", "blog:research", "blog:write", "humanizer", "blog:publish"],
  "suggested_domains": ["content"],
  "budget": {
    "enforcement": "off",
    "limits": {
      "api_monthly_usd": null,
      "window_percent_cap": null,
      "plan": "claude_pro_100"
    }
  }
}
```

---

## Reserved Member Names (Future)

Activate when Quill's scope needs splitting:

- **Echo** — blog-to-social repurposing (different voice, different platforms)
- **Harbor** — longform video production (YouTube scripts, adaptations)

---

## Operations

### OPS-001 "Content Publishing"

**Owner:** Sterling (MEM-002, CMO)
**Status:** active
**Scope:** Ongoing business function covering all published content streams. Sterling owns strategy and team. Quill produces blog content under Sterling's direction. Future: Echo (social repurposing), Harbor (video).

```json
{
  "id": "OPS-001",
  "firm_id": "chrisai",
  "name": "Content Publishing",
  "description": "Ongoing business function covering all published content streams. Sterling (CMO) owns; Quill (and future Echo/Harbor) execute.",
  "owner_member_id": "MEM-002",
  "status": "active",
  "goal_ids": ["GOAL-001", "GOAL-002", "GOAL-003"]
}
```

**Goals (3) — designed to remove gaming vectors:**

**GOAL-001 — Throughput**
```json
{
  "id": "GOAL-001",
  "level": "operation",
  "parent_ref": { "type": "operation", "id": "OPS-001" },
  "target": "Publish 2 longform blog posts per week",
  "metric": { "type": "publish_rate", "value": 2, "unit": "posts_per_week" }
}
```

**GOAL-002 — Reach (SEO / GEO)**
```json
{
  "id": "GOAL-002",
  "level": "operation",
  "parent_ref": { "type": "operation", "id": "OPS-001" },
  "target": "Monthly unique visitors trending upward",
  "metric": { "type": "unique_visitors", "value": null, "unit": "per_month", "trend": "growing" }
}
```

**GOAL-003 — Quality (conversion)**
```json
{
  "id": "GOAL-003",
  "level": "operation",
  "parent_ref": { "type": "operation", "id": "OPS-001" },
  "target": "Unique-visitor-to-subscriber ratio held or growing",
  "metric": { "type": "conversion_ratio", "value": null, "unit": "subs_per_unique", "trend": "stable_or_growing" }
}
```

**Failure modes caught by this goal set:**
- Low throughput → stagnation flag (GOAL-001)
- Flat traffic → discovery failure, SEO/GEO not working (GOAL-002)
- Dropping conversion → quality loss, keyword-stuffing, clickbait (GOAL-003)

All three must track positively for Content Publishing to be considered healthy.

**Open flags:** GOAL-002 and GOAL-003 values are `null` pending analytics baseline at chrisai.cv. Populate when analytics are live.

---

## Projects

### PROJ-001 "Quill Cadence — First 8 Posts"

**Owner:** Quill (MEM-001)
**Operation:** OPS-001 Content Publishing
**Status:** in_progress
**Due date:** 2026-05-12 (4 weeks from 2026-04-14 start)
**Cadence:** 2 longform blog posts / week
**Goals served:** GOAL-001 directly; establishes baseline for GOAL-002 and GOAL-003

```json
{
  "id": "PROJ-001",
  "firm_id": "chrisai",
  "operation_id": "OPS-001",
  "name": "Quill Cadence — First 8 Posts",
  "description": "First bounded publishing sprint establishing Quill's rhythm. Uses blog-post-master pipeline. Success = 8 posts live on chrisai.cv at 2/week cadence held, each meeting per-post AC.",
  "owner_member_id": "MEM-001",
  "status": "in_progress",
  "goal_ids": ["GOAL-001"],
  "priority": "high",
  "due_date": "2026-05-12",
  "tags": ["blog", "content", "first-sprint"]
}
```

**Acceptance Criteria:**
- 8 posts published to chrisai.cv
- Cadence held at 2/week (no gap > 5 days)
- Each post meets canonical Blog Unit AC (see §Canonical AC — Blog Unit below)

**Post-completion handoff:** Baseline data established for GOAL-002 (traffic) and GOAL-003 (conversion). Populate null metric values from observed performance. Assess cadence fit before Project #2.

---

## Units

### UNIT-000 — "The CLAUDE.md Strategy" (retroactive, pre-Project)

**Status:** done
**Assignee:** Quill (MEM-001) — retroactively attributed
**Pre-Project:** Does NOT count toward PROJ-001's 8-post requirement. Published before Quill was formalized; attributed for Quill's history and to provide a live post as baseline for GOAL-002 / GOAL-003 metric tracking.

```json
{
  "id": "UNIT-000",
  "firm_id": "chrisai",
  "project_id": null,
  "parent_unit_id": null,
  "name": "The CLAUDE.md Strategy",
  "description": "First blog post produced end-to-end via blog-post-master pipeline. Published before framework formalization. Retroactively attributed to Quill as pre-Project baseline.",
  "assignee_member_id": "MEM-001",
  "status": "done",
  "priority": "high",
  "rank": null,
  "goal_ids": [],
  "acceptance_criteria": [
    { "id": "AC-1", "condition": "Humanizer gate passed", "resolved": true, "resolved_by": "pipeline" },
    { "id": "AC-2", "condition": "Editorial checklist passed", "resolved": true, "resolved_by": "pipeline" },
    { "id": "AC-3", "condition": "Strategy alignment confirmed", "resolved": true, "resolved_by": "retroactive" },
    { "id": "AC-4", "condition": "Output logged", "resolved": true, "resolved_by": "manual" }
  ],
  "depends_on": [],
  "due_date": null,
  "outputs": [
    { "type": "url", "url": "https://chrisai.cv/blog/claude-md-strategy" }
  ],
  "tags": ["blog", "pre-project", "retroactive", "pillar-claude-code-mastery"],
  "created_at": "2026-04-14",
  "notes": "Metrics baseline to be captured at next session-pulse. This post's traffic/conversion performance establishes GOAL-002 and GOAL-003 baselines."
}
```

### UNIT-001 through UNIT-008 — Project #1 deliverables

*To be created one at a time as Quill takes them on. First Unit definition pending.*

---

## Firm-Level Principle: Earn-the-Pace Rule

**Binding principle for throughput decisions across all Members and Operations:**

> Every cadence bump gets earned by prior Project performance. Members cannot unilaterally increase throughput; they propose increases at Project boundaries, and the increase is approved only if quality and conversion goals held during the prior Project.

**Applied to Quill:**
- Project #1 locked at 2 posts/week
- End of Project #1, Quill can propose 3/week for Project #2 IF:
  - GOAL-003 (conversion) held or grew during Project #1
  - Chris's editorial time wasn't strained
  - Pillar coverage stayed clean (no repetitive cannibalization)
- Default carries forward at 2/week if any condition unmet

**Why this rule exists:** Prevents AI throughput creep. Quill optimizing for volume hurts quality; the rule forces earning the next gear.

---

## Canonical AC — Blog Unit

Every Quill-produced blog Unit inherits this AC list. Delegates to existing quality infrastructure; surfaces critical strategic gates on the Unit record.

```
AC-1: Humanizer gate passed
  Delegates to: apps/blog-post-master/src/plugins/blog/tasks/humanizer-gate.md
  Pass criteria: 0 high-severity findings, ≤3 total findings, soul assessment = pass

AC-2: Editorial checklist passed
  Delegates to: apps/blog-post-master/src/plugins/blog/tasks/editorial-checklist.md
  Pass criteria: all 26 items across Factual Accuracy, Voice, Structure, SEO, CTA, Final Quality

AC-3: Strategy alignment confirmed
  Delegates to: apps/blog-post-master/STRATEGY.md
  Sub-checks:
    - Declared pillar (1 of 4: Claude Code Mastery / AI Automation Frameworks / Solo Builder Stack / Zero to Dangerous)
    - Declared segment (1 of 8 from STRATEGY.md §Content Segments)
    - 7-part AI-citation structure present (TLDR → Context → Content → Takeaways → FAQ → Related → CTA)
    - Lead magnet matches segment (per §Email Capture Strategy)
    - Pillar not over-concentrated (≤3 posts in same pillar in rolling 2-week window)

AC-4: Output logged
  Unit's `outputs` field populated with:
    - Live URL on chrisai.cv
    - Baseline metrics snapshot at t=0 (page views start, subscriber count start)
```

**Failure modes this AC catches:**
- AI-sounding writing → AC-1 blocks
- Missing CTAs, weak structure, factual sloppiness → AC-2 blocks
- Off-strategy content (random topic not in pillars) → AC-3 blocks
- Wrong segment-to-lead-magnet pairing → AC-3 blocks
- Pillar cannibalization (5 hooks posts in a row) → AC-3 blocks
- "Marked done" without actually being live → AC-4 blocks

---

## Reference Documents

Documents attached to ChrisAI Firm entities. Pointers to authoritative source files — content lives on disk, framework tracks attribution and lifecycle.

### DOC-001 — Content Publishing Strategy
```json
{
  "id": "DOC-001",
  "firm_id": "chrisai",
  "parent_ref": { "type": "operation", "id": "OPS-001" },
  "type": "strategy",
  "name": "Content Publishing Strategy",
  "content_path": "apps/blog-post-master/STRATEGY.md",
  "author": { "type": "board", "id": null },
  "version": 1,
  "status": "active"
}
```
Authoritative strategy for Content Publishing Operation. Defines pillars, segments, cadence, structure, conversion funnel, and distribution.

### DOC-002 — Humanizer Gate Specification
```json
{
  "id": "DOC-002",
  "firm_id": "chrisai",
  "parent_ref": { "type": "member", "id": "MEM-001" },
  "type": "quality_gate",
  "name": "Humanizer Gate Specification",
  "content_path": "apps/blog-post-master/src/plugins/blog/tasks/humanizer-gate.md",
  "author": { "type": "board", "id": null },
  "version": 1,
  "status": "active"
}
```
AI-pattern detection gate Quill runs before editorial. 24 pattern categories + soul assessment.

### CON-001 — Quill on Claude Code

```json
{
  "id": "CON-001",
  "firm_id": "chrisai",
  "name": "Quill on Claude Code",
  "member_id": "MEM-001",
  "runtime_type": "claude_code",
  "runtime_config": {
    "model": "claude-opus-4-6",
    "working_dir": "/home/chriskahler/chris-ai-systems",
    "entry_command": "/quill:run",
    "entry_command_args": ["stage"],
    "stage_to_skill_map": {
      "ideate": "/blog:ideate",
      "research": "/blog:research",
      "draft": "/blog:write",
      "humanize": "humanizer",
      "editorial": "/blog:editorial",
      "publish": "/blog:publish",
      "full": "full pipeline chain"
    },
    "plan": "claude_pro_100"
  },
  "skill_loadout": ["blog:ideate", "blog:research", "blog:write", "humanizer", "blog:publish"],
  "domain_loadout": ["content"]
}
```

Quill is invoked via `/quill:run <stage>`. Stage argument dispatches to the appropriate `/blog:*` skill. Supports individual stage invocation (from Sterling for targeted work) or full pipeline chain.

**CON-002, CON-003** (Sterling, Sage) — pending definition when those Members need to execute. For v1, both can be invoked via `/member:run <name> <directive>` placeholder pattern until their Contracts are formalized.

---

### DOC-003 — Editorial Checklist Specification
```json
{
  "id": "DOC-003",
  "firm_id": "chrisai",
  "parent_ref": { "type": "member", "id": "MEM-001" },
  "type": "quality_gate",
  "name": "Editorial Checklist Specification",
  "content_path": "apps/blog-post-master/src/plugins/blog/tasks/editorial-checklist.md",
  "author": { "type": "board", "id": null },
  "version": 1,
  "status": "active"
}
```
26-item editorial checklist Quill runs after humanizer passes. Covers factual accuracy, voice, structure, SEO, CTA, final quality.
