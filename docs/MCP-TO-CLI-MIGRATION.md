# Firm write-surface: MCP → CLI migration

**Status:** scoped, build started 2026-07-07 (Board verdict, /boardroom).
**Owner:** Board engineering seat.
**Why now:** ESC-010 (dnd-table) — the firm MCP server launches via a `wsl.exe`
Windows→WSL hop in `.mcp.json` that fails *silently* on WSL-native member
spawns (no `mcp__firm__*` tools load). Same root cause as the wastelander
UNIT-023 "member couldn't self-register" incident (ENGINEERING.md field log).
A native-launch patch is shipped as the stopgap; this migration is the durable
fix that lets us **delete the firm MCP server and every firm `.mcp.json`.**

## Thesis

The firm MCP is a process + handshake + per-firm `.mcp.json` wrapping **local
service code** that a CLI calls directly. It has nothing external in it (even
Slack notify is service-layer). It is accidental complexity, and its failure
mode is silent-absent. A CLI over the same `services/*` functions is:

- **Loud on failure** — `command not found` / non-zero exit shows in the run
  transcript, vs. MCP tools silently missing.
- **One write-surface** — game state already writes via a CLI (`engine.py`,
  which *survived* the outage). Firm governance joining it means one mental
  model, one test story, one thing contracts teach.
- **Testable in a shell / CI** — no live server to stand up.
- **Footgun-free** — the CLI resolves `firm_id` from `FIRM_ID`/cwd
  (`__main__.py` already does `args.firm_id or os.environ["FIRM_ID"]`), killing
  the MCP `firm_id="chrisai"` default that bit Dorn.

Trade accepted: members lose typed tool schemas and construct commands from
contract docs instead. `engine.py` already proves these exact members do this
well; `--help` + JSON I/O + contract examples close the gap.

## Scope — the surface to convert (21 MCP tools → `firm` verbs)

All in `src/firm/mcp/tools.py`, each a thin marshaller over `services/*`:

| Domain | MCP tools | CLI verbs |
|---|---|---|
| Members | list/view/create/update, get_direct_reports | `firm member list|view|create|update|reports` |
| Units | list/view/create/checkout/release/complete | `firm unit list|view|create|checkout|release|complete` (`complete` exists) |
| Gates | list/view/request/approve/reject | `firm gate list|view|request|approve|reject` |
| Escalations | list/view/raise/resolve | `firm escalation list|view|raise|resolve` |

`unit complete` already exists in `cli/unit.py` — proof the pattern fits.

## Conventions (mirror `engine.py` + `cli/unit.py`)

- Each verb: `run_<verb>(workspace, *, ..., firm_id)` → `connect(get_db_path(
  workspace))` → `services/*` call → `print(json.dumps(result, default=str))`
  → return `0`/`1`.
- `firm_id` from `--firm-id` else `FIRM_ID` env else error (do **not** default
  to chrisai in member-facing verbs — a wrong-firm write is worse than a stop).
- Structured args as `--payload-json` where a tool takes list/dict fields
  (acceptance_criteria, depends_on), matching `engine.py`.
- JSON to stdout, human errors to stderr, exit codes honest.

## Phases

1. **Proof verb (this session):** `firm escalation raise` end-to-end — the tool
   that failed, has the notify side effect, exercises firm_id resolution.
   Validate against a scratch DB (notify nulled). ← *stop, Board blesses pattern.*
2. **Convert the remaining 20** — mechanical, mirror the proof. pytest per verb.
3. **Contract migration** — the real work: rewrite every member prompt/contract
   that teaches `mcp__firm__*` to the `firm <verb>` command; update the prompt
   assembler. Grep `mcp__firm__` across contracts + `services`/prompt code.
4. **Retire MCP** — delete `firm/mcp/server.py` + `tools.py`, drop the `firm`
   server from every `.mcp.json` (keep external MCPs like Slack). Update the
   new-firm checklist in ENGINEERING.md (no more `.mcp.json` firm block).
5. **Docs** — ENGINEERING.md write-surface section rewritten; field report added.

## Definition of done

- Every former MCP tool has a `firm` verb with a pytest.
- No contract references `mcp__firm__*`; a full pulse on each firm completes with
  members writing via CLI (escalate, gate, complete all observed in Records).
- `firm/mcp/` deleted; no `.mcp.json` carries a `firm` server; full suite green.
- ENGINEERING.md reflects CLI-only write-surface.

## Not in scope

- External MCPs (Slack, web) stay MCP — that is what MCP is for.
- The Board write path (`perform_action`) is unchanged; it already bypasses MCP.
