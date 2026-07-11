<overview>
How to design a firm that can actually run: routing, org shape, contract differentiation, economics. The grading standards the audit task applies.
</overview>

<routing-law>
- Work routes by **unit assignment** (`assignee_member_id`). Unassigned units + `can_self_assign=0` everywhere = the firm can never start (every pulse skips all members at `load=0`).
- Exactly ONE triage role (director) gets `can_self_assign=1`; its duty is keeping the unit board honest.
- `depends_on` chains are the ONLY real enforcement of sequence (review gates, ship gates). A rule that lives in prose ("nothing ships without X") is cosmetic until it's the final chain link.
- One unit = one member run inside the contract timeout. Split at >3 tasks, multiple subsystems, or >5 files. Vertical slices over horizontal layers.
</routing-law>

<org-shape>
- `reports_to_member_id` builds the hierarchy; the top role reports to the Board (`None`). Two-department pattern (e.g. dev lead + demo lead under a director) maps to two Operations with `owner_member_id` set.
- Idle headcount costs nothing — members only cost money when they run. Roster breadth is free; **contract emptiness is not**: specialists with identical blank contracts are name tags, not specialization. Differentiation lives in `skill_loadout` duties/policies.
- Shared contracts: name-prefix the duty lines ("Nova: ...", "Rail: ...") with a first line telling members to apply the lines addressed to their name. Per-member contracts (wastelander idiom) when roles diverge hard.
</org-shape>

<economics>
- Real-world cost ≈ $0.50–2 per member run; heavy drafting $4+. Budget every contract per period: engineers ~$5 / 40 runs, leads ~$10 / 30 runs are sane starting points — tune from observed spend.
- Model tiers: sonnet default; opus only where output quality IS the product (e.g. the novelist); haiku for high-frequency cheap roles (reader-sims, player-characters).
- Timeouts: leads/orchestrators 900–1200s; engineers doing real deploys 900s; heavy drafters up to 2400s; cheap sims 600s. The reaper closes zombies at 2× timeout + 600s grace.
</economics>

<validation-selection>
- File-producing roles (code, prose, reports): `file_exists` + `require_written: true` — also auto-registers deliverables as Documents.
- DB-invariant roles (turn-based games, state machines): `sql_guard` with a query that fails when the invariant is broken; the message coaches the retry.
- Coordination-only roles (leads): the always-on nonempty floor may suffice — but make that an explicit decision, not an omission.
- Prompts persuade, validators enforce. Every "must" in a loadout policy should have a validator or chain behind it where mechanically possible.
</validation-selection>

<containment>
Structural beats behavioral (constitutional invariant #5): a member that must not see X doesn't get X in its loadout; a member that must not spend can't reach a paid surface without a Gate; missing capability = procurement unit (Gate → loadout update), never a prompt hack or borrowed credential. `.mcp.json` + `--strict-mcp-config` is the hard tool boundary — members get exactly those servers, nothing else.
</containment>
