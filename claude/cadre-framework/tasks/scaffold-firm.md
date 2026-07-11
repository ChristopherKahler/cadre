<purpose>
Stand up a new Cadre firm from an empty folder to a green dry-run pulse. Ends at evidence; the first LIVE pulse is the Board's call, never this task's.
</purpose>

<context>
@~/.base-frameworks/cadre-framework/checklists/scaffold-checklist.md (the exit gate — load at the end)
@~/.base-frameworks/cadre-framework/frameworks/firm-anatomy.md (load if you don't already know the entity model)
</context>

<steps>

<step name="locate-framework">
Find the cadre checkout: `ls ~/ops-sys/toolbox/frameworks/05-exp-cadre` or `pip show cadre` inside an existing firm's `.venv`, or ask the operator. Call it `$CADRE` below.
</step>

<step name="workspace">
```bash
mkdir -p ~/firms/<name> && cd ~/firms/<name>
python3 -m venv .venv
.venv/bin/pip install -e $CADRE
.venv/bin/cadre init .        # creates .firm/firm.db + migrations + discipline family
mkdir -p reports scripts
```
Folder name is operator-facing only — the firm id lives in the DB firm row. NEVER keep firm backups under `~/firms/` (duplicate ids shadow; use `~/firms-archive/`).
</step>

<step name="charter">
Route to the charter task: Read `~/.base-frameworks/cadre-framework/tasks/charter.md` and produce the firm's CLAUDE.md before any seeding — the charter's NEVERs shape the contracts.
</step>

<step name="wiring">
1. `.mcp.json` — NATIVE launch, never a `wsl.exe` hop:
```json
{"mcpServers": {"firm": {"command": "bash", "args": ["-lc",
  "FIRM_ID=<id> FIRM_WORKSPACE=<abs-workspace> CADRE_SLACK_TOKEN=<tok> exec <abs-workspace>/.venv/bin/python -m firm.mcp.server"]}}}
```
2. `.claude/` — copy an existing firm's session-pulse hook trio + `commands/pulse.md`, sed the firm name/paths.
3. `.gitignore` (`.venv/`, `.firm/*.db`, `.firm/*.db-*`, `__pycache__/`, `.env`) + `.gitattributes` (`* text=auto eol=lf`).
4. Boardroom folder on the Windows side: `mkdir -p /mnt/c/Users/<user>/Claude/Projects/<name>-boardroom` + seed a `PULSE-LOG.md` (newest-first pulse entries).
</step>

<step name="seed">
Route to the seed task: Read `~/.base-frameworks/cadre-framework/tasks/seed.md`. Run it. Then attach role packs — Read `~/.base-frameworks/cadre-framework/tasks/disciplines.md`.
</step>

<step name="git">
```bash
git init && git add -A && git commit -m "<name>: firm scaffold + seed"
```
</step>

<step name="verify">
Route to the verify task: Read `~/.base-frameworks/cadre-framework/tasks/verify.md`. Then load the scaffold checklist and give every item a verdict with evidence.
</step>

</steps>

<output>
A firm workspace that passes every scaffold-checklist item, with a green dry-run pulse and NO live pulse fired.
</output>

<acceptance-criteria>
- [ ] `FIRM_ID=<id> .venv/bin/firm pulse --dry-run` → 0 errors; skips are explainable (load=0 only where intended)
- [ ] Every scaffold-checklist item has a verdict backed by a command output
- [ ] No live pulse fired — explicitly confirmed to the operator
</acceptance-criteria>
