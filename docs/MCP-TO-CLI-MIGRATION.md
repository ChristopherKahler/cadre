# Firm write-surface: MCP → CLI migration

**Status:** partially migrated. Measured against the tree 2026-09-10, not
estimated — counts below come from `src/firm/mcp/tools.py` and from each verb's
own `--help`, because the check for whether a verb exists is `--help` and not
this document (ENGINEERING.md:259).

| Phase | State | What is actually true |
|---|---|---|
| 1. Proof verb | **done** | `firm escalation raise` shipped and is in use. |
| 2. Convert the remaining writes | **partial, 6 of 18** | `tools.py` carries 37 `@mcp.tool()` functions: 19 read, 18 write. Six write tools have a Member-facing CLI equivalent today — `firm_escalate`, `firm_create_unit`, `firm_complete_unit`, `firm_create_document`, `firm_request_gate`, `firm_update_goal_metric`. **Twelve remain MCP-only** (checkout/release unit, create comment/member/operation/project, propose goal, propose hire, resolve escalation, update document, update goal, update member). The old line here said "convert the remaining 20" and read as though it had run; it had not. |
| 3. Contract migration | **partial — the framework surface is done, firm databases are not** | Every Member-facing text the framework ships now names CLI verbs: the execution directive (`pulse/prompt.py`), the charter the wire step writes (`dashboard/wiring.py`), the boardroom template, and the `authority.py` / `gate.py` hint strings. Pinned by `tests/test_prompt_verbs_resolve.py` and `tests/test_member_facing_text_names_real_verbs.py`, which resolve every verb those surfaces name through `--help`. **Not done:** contracts already written into the twelve firm databases are untouched. |
| 4. Retire MCP | **not started** | `firm/mcp/` is intact and six of twelve firms still declare a `firm` server. |
| 5. Docs | **not started** | The ENGINEERING.md write-surface section still describes the MCP-era surface. |

**The one gap that still bites a Member.** `firm_propose_goal`
(`mcp/tools.py:378`) calls `gate.request_gate` with action `create-goal`, so
proposing a Goal is a **second** member-facing route into the Gate service and
it has no CLI verb. A Member in a firm without the firm MCP can now escalate,
queue a Unit, register a deliverable and request a Gate, but still cannot
propose a Goal. That verb (`firm goal propose`) is deliberately out of the
member-write-surface lane and is named here so the next reader finds it without
re-deriving it.

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
2. **Convert the remaining writes** — mechanical, mirror the proof. pytest per
   verb. Six of the eighteen write tools are done; the twelve still outstanding
   are listed in the status table at the top of this file. (This line used to
   read "convert the remaining 20", which was both the wrong count and phrased
   as though the phase had run.)
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
