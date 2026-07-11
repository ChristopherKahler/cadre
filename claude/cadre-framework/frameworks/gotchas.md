<overview>
The complete bite list — every item cost something once. Consult per failing check; the canonical, always-current version is `docs/FIRM-SCAFFOLDING-GUIDE.md` §7 + `docs/ENGINEERING.md` §9 in the cadre checkout.
</overview>

<environment>
1. WSL is the firm's world: venv, DB access, pulses — all WSL-only. Never touch `firm.db` over `\\wsl.localhost`, never pulse from a Windows shell.
2. Detached work from sessions: `systemd-run --user --collect` only — nohup/setsid die with the terminal.
3. 3-second member deaths, returncode 1, empty stderr = spawn environment (PATH, `CADRE_CLAUDE_BIN`, wrong host world) — never member quality.
4. `sqlite3` CLI may be absent — diagnose with `.venv/bin/python` + the `sqlite3` module, SELECTs only.
</environment>

<seeding>
5. Folder name ≠ firm id; the hub reads the id from the DB. Duplicate ids under `~/firms` shadow each other — backups go to `~/firms-archive/`.
6. THE deadlock: unassigned units + `can_self_assign=0` everywhere → every pulse skips everyone, forever.
7. Seeds MUST set `pulse_config.model`, `timeout_sec`, `budget_config.limits` — a forgotten model silently runs the account default.
8. NOT NULL traps: `project.due_date`, `gate.target_entity_type`+`target_entity_id`, `escalation.dedupe_key`. `create_unit` rejects `claimed_by` (create, then update).
9. Only `stages`/`tools`/`duties`/`policies` in `skill_loadout` reach prompts — `scope`/`files` are inert docs. A ~350-char prompt preview = empty loadout.
10. No `notify_config` = silent firm: gates and escalations never reach the Board.
</seeding>

<mcp>
11. `.mcp.json` launches natively (`bash -lc "... exec .venv/bin/python -m firm.mcp.server"`) — a `wsl.exe` hop silently fails for WSL-native pulse spawns.
12. Spawn passes `--mcp-config` + `--strict-mcp-config`: members get the firm's servers only; none declared = no MCP tools, by design.
13. `mcp_degraded` in run notes = expected server showed no connect evidence. Authoritative record: claude's per-project `mcp-logs-<server>/*.jsonl` in `~/.cache/claude-cli-nodejs/` — the init snapshot races connects; `pending` alone is not failure.
</mcp>

<validation-completion>
14. `validation_config: None` = vacuous completion — a refusal with text can close a unit. File-producing contracts get `file_exists` + `require_written` (which also registers Documents).
15. `sql_guard` turns any firm-DB invariant into a retryable validator.
16. Completed run at $0 = the model never worked — investigate prompt and validation.
17. A file never registered as a Document is invisible to the Board; final message text always persists in `member_run.outputs`.
</validation-completion>

<operations>
18. `ran: 0` + `load=0` skips usually means create/assign work — not a louder pulse.
19. A resolved escalation without `queue_followup` strands turn-based loops.
20. Business-hours gate is firm-wide — a "dead" firm at night may just be gated.
21. Zombie `running` rows reap at next pulse (2× timeout + 600s grace); `stale: true` marks them meanwhile.
22. First live pulse = first spend = the Board's explicit call. Scaffolding ends at a green dry-run.
23. Member "can't do X" = contract/loadout gap = procurement unit — never a prompt hack, never borrowed credentials.
</operations>
