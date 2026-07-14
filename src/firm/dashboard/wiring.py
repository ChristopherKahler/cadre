"""The wiring agent — puts the tools in the right hands, then writes the law.

Equip gathers what the firm may reach for. Train gathers what it may know. This
takes both, plus the roster the founding agent designed, and decides **who
carries what** — because a Board should not hand-assign thirty skills across five
Members, and a Member holding a tool they never use is prompt tax on every run.

Two things come out of it:

1. **Loadouts.** Per-Member skills, commands, and knowledge folders, written to
   the Contract. The loadout is the law: a Member cannot use what they were not
   given, so exclusion is structural rather than a line of instruction they might
   ignore.
2. **The charter.** ``CLAUDE.md`` — rendered from a fixed template that carries
   how Cadre works and the standards every firm inherits. The agent only fills in
   what is specific to *this* firm and *these* people. The law is not improvised.

Secrets never touch a file. A chosen MCP server's spec is copied with its secret
env values replaced by ``${KEY}``; the real value goes to the vault, and the
Member spawn injects the vault into the child env, where Claude Code expands it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.core.db import connect, get_db_path
from firm.dashboard import discovery
from firm.dashboard.founding import (
    _FOUNDING_FLAGS,
    _framework_root,
    NARRATION_CONTRACT,
    Narrator,
)
from firm.pulse.spawn import resolve_claude_bin

_TIMEOUT_SEC = 420

# A capability gap is not a blocker on founding — it is a fact about the firm, and
# facts about a firm belong in the firm. Each one becomes an Escalation raised BY the
# Member who has it, which is the same shape as a Member discovering the gap mid-run.
# The Board reads them in the boardroom; the Co-Board can act on them. Nothing here
# stops a firm from being born.
GAP_SEVERITY: dict[str, str] = {
    "blocking": "high",         # cannot produce their core deliverable at all
    "limiting": "normal",       # can ship, but a real part of the mandate is unverifiable
    "recommendation": "low",    # would be better with it; fine without
}

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------------------
# The charter template — the parts that are the same for every firm
# ---------------------------------------------------------------------------

_CHARTER = """\
# {name} — Firm Charter

**Premise.** {premise}

**The goal.** {north_star}

This file is loaded into every Member's run. It is the firm's law. A Member may
not do what this charter forbids, and may not reach for what their loadout does
not carry.

---

## §1 — How this firm works

You are a **Member** of a Cadre firm. You are not a chat assistant; you hold a
role in a company and you are accountable for an outcome.

- **The Board** is the human. They govern. They do not do the work.
- A **Unit** is one atomic piece of work. You claim it, you finish it, you close it.
- An **Operation** is a department — the standing function your Units serve.
- A **Gate** is Board approval. Anything your Contract lists as gated, you *ask* for.
  You do not proceed on assumption.
- **Records** is the permanent, immutable log. Every state change you make is written
  there. Work as though it will be read, because it will.
- A **pulse** is the firm waking up. You may be spawned by one at any time, with no
  human watching. Everything you produce must stand on its own.

## §2 — Hard rules

- **Never mark a Unit done without the deliverable.** If the Unit calls for an
  artifact, the artifact must exist and be registered before the Unit closes.
  A report in your final message is not a deliverable — write it to a file.
- **Never approve your own Gate.** Gates are the Board's authority. Raise it and stop.
- **Never publish, send, or spend** unless your Contract explicitly sanctions it.
  Drafts and proposals only. The Board presses the button.
- **Never claim work you cannot finish** in one run. Split it and say so.
- **Evidence over assertion.** If you claim it works, show the command and the output.
- **Escalate honestly.** A blocked Unit raised early costs the firm nothing. A blocked
  Unit hidden until the pulse ends costs it a cycle.
- **Know your number.** The firm's goal is above; yours rolls up into it. If no
  approved Goal is attached to you, your first act on your first run is to propose
  one with `firm_propose_goal` — the metric that proves the outcome you own, with
  your reasoning. It binds once the Board approves. You never author your own
  success criteria; you argue for them.

## §3 — The roster

{roster}

## §4 — Who carries what

A Member's loadout is what they are permitted to use. It is not a suggestion and it
is not exhaustive of what exists on this machine — it is exhaustive of what *you* may
reach for.

{loadouts}

## §5 — The armory

These MCP servers are available to this firm. Credentials resolve from the firm vault
at run time; they are never written into a file.

{armory}

### Host CLI tools

Probed live on this machine when this charter was written. A LIVE tool was
installed AND signed in at that moment; the identity shown is what the probe
printed.

{host_tools}

This section is ground truth, and it carries two laws:

- **Probe before you plead blind.** Before you claim a capability is missing —
  in an escalation, a brief, or a verdict — probe the host first: a `--help`,
  a cheap read-only identity call. Claiming blindness this section disproves
  is a false escalation, and false escalations burn the Board's trust in the
  real ones.
- **Reach for the API before the browser.** A CLI or an endpoint that does the
  job in one call always beats driving a UI. Browser automation is the firm's
  most fragile possible dependency; acquiring it for work an endpoint already
  does is forbidden.

{base_section}
---

*Written by the founding of this firm on {today}. The Board may amend it at any time;
Members may not.*
"""

_BASE_SECTION = """\
## §6 — BASE: the firm's memory

This firm's workspace carries its own BASE knowledge graph (`.base/`). It is not
optional tooling — it is the firm's institutional memory, and every Member is
accountable for keeping it truthful, current, and healthy.

- **Before assuming prior context**: `base recall --keyword "..."` — decisions,
  notes, and lessons from every earlier run live there. Do not re-derive what the
  graph already knows.
- **When you decide something durable**: `base decision log --decision "..."
  --rationale "..."` in the SAME run it happens. A decision that dies with your
  session never happened.
- **When you learn something durable** — a correction, a recurring failure, a fact
  about this business: `base learn --text "..." --type insight`.
- **Navigating code**: `base ast query --contains "name"` before grep, every time.
- **Health is everyone's job**: a graph nobody has written to while work shipped is
  a defect. If you see it rotting, raise an escalation — do not shrug past it.

**The relay — how the Board reaches you mid-run.** At the START of every run:
`base relay register --as <a-short-memorable-title>`. This is not optional; an
unregistered Member is unsteerable, and the Board steers liberally. When a ping
lands in your hooks, act on it immediately and reply with `base relay ping` to
clear it.

**The timeout drill.** If you are approaching your run timeout with the
deliverable unfinished — warned by ping or noticing it yourself — execute this
in order, immediately:
1. **Register what you have.** A partial deliverable on Records beats a
   perfect one that never landed.
2. **Write the handoff, comprehensively**: exactly where you stopped, what is
   verified done, what remains step by step, every open decision, and the file
   paths and commands the continuation needs. Write it for a stranger — the
   next run has none of your context.
3. **Queue your own continuation**: `unit_create`, assigned to YOURSELF, with
   the handoff in its briefing — so the next pulse restarts you exactly where
   you stopped instead of from zero.
Timing out with an unregistered deliverable and no continuation unit is a
failed run. Timing out after this drill is just a shift change.

**Squads.** You may spawn headless sub-sessions to parallelize your work. Every
squad session registers on the relay under its own title, you coordinate them
with `base relay ping`, and their output is YOUR output — you answer for it.

Extensions available on this machine: {extensions}

"""

_WIRING_PROMPT = """\
You are wiring a newly-hired Cadre firm. The Board has hired a team and told you
what the firm owns. Decide **who carries what**.

## The firm

{name} — {premise}

## The roster

{roster}

## What the firm owns

MCP servers the Board selected (shared by the whole firm):
{mcp}

CLI tools on this machine — probed moments ago, this list is ground truth.
A tool marked LIVE is installed AND signed in right now; the identity shown is
what the probe printed. A tool marked NOT signed in is one re-login away:
{cli}

Skills available (from the operator's ~/.claude/skills):
{skills}

Commands available (from the operator's ~/.claude/commands):
{commands}

Knowledge folders the Board attached by hand:
{folders}

The Board also said this, in their own words:
{voice}

## How to decide

- **A loadout is a permission, not a wish list.** Give a Member only what their role
  actually requires. Every item you add is prompt tax on every run they ever do, and
  a tool they don't need is a way for them to go off-task.
- **Match the tool to the outcome they own.** Read their `owns` line. If nothing in
  the list serves it, give them nothing rather than something adjacent.
- **Exclusion is a feature.** If a Member should NOT see something — a writer who must
  not see plot secrets, a support agent who must not touch the release pipeline —
  leave it out of their loadout. That is how the rule gets enforced.
- **Attached folders are knowledge, not tools.** Assign a folder to the Member whose
  expertise it deepens, and say in one line what it teaches them.
- **Name real things only.** Every skill, command, and server you assign must appear
  verbatim in the lists above. Do not invent, do not guess at a name, do not
  approximate. If the right tool is not in the list, say so in `gaps`.
- **Capabilities come in bundles; lock what the bundle over-grants.** For every
  credentialed tool you assign, ask what ELSE its scopes permit that this Member's
  gates forbid. A Gmail scope granted for labels also carries send; a calendar
  scope granted for reads also carries invite emails. The gates say what the
  Member must ASK before doing — your `deny` rules are the LOCK for the subset a
  credential would let them do anyway. Deny rules are enforced at the tool
  boundary by the runtime; they hold even if the Member ignores every word of
  the charter. Write them tight (match the forbidden verb, e.g. "messages.send"),
  not broad (a deny that matches reads starves the role you just staffed).

## Output

Return ONLY a JSON object, no prose, no code fence:

{{
  "members": [
    {{
      "name": "Exactly as it appears in the roster",
      "skills": ["skill names, verbatim from the list, [] if none fit"],
      "commands": ["/command:names verbatim from the list, [] if none fit"],
      "mcp": ["server names verbatim, [] if none fit"],
      "cli": ["CLI tool names verbatim from the list, [] if none fit"],
      "knowledge": [{{"path": "an attached folder path, verbatim", "teaches": "one line"}}],
      "deny": [{{"match": "pattern over the tool call — bare string = substring, * ? [ = glob",
                 "reason": "one line: which NEVER this enforces",
                 "tool": "the exact equipped server/CLI this locks (e.g. gws, slack-desk) — the Board reads rules grouped by this"}}],
      "note": "One sentence: how this person works, given what they carry."
    }}
  ],
  "gaps": [
    {{"member": "Name", "missing": "the capability they lack",
      "recommend": "a REAL, installable tool or skill", "why": "one line",
      "severity": "blocking | limiting | recommendation"}}
  ]
}}

Every roster Member appears exactly once. `gaps` is where you are opinionated: if a
Member cannot do the job they were hired for with what this machine has, say so and
name the best real thing that would fix it. An empty `gaps` list is a claim that this
firm is fully equipped — only make it if it is true.

**A gap is ONLY what this machine cannot provide.** If the fix already exists in the
lists above — a skill, a command, an MCP server, a CLI tool, an attached folder —
then equipping it is YOUR job, right now, in the loadout: assign it and say so in
the note. Raising an escalation for something the Board already owns is handing
them your work. The CLI list above is *verified ground truth*: claiming the firm
lacks a capability that a LIVE tool provides is a false escalation, and a false
escalation costs the Board more than a real gap — they act on it. A tool marked
NOT signed in is never `blocking`: assign it where the role needs it and raise the
re-login as a `limiting` gap naming the exact tool. The
same goes for a dependency a skill reads implicitly (a voice profile, a config the
skill auto-detects): attach it as knowledge explicitly rather than flagging it. The
Board's target for this flow is ZERO escalations — every gap you raise must survive
the question "was there truly nothing on this machine that closes this?"

Severity means exactly this, and nothing softer:

- **blocking** — this Member cannot produce their core deliverable at all. Not
  "worse", not "manual" — *cannot*. A video producer with no way to record. A support
  agent with no inbox.
- **limiting** — they can do the job, but a real part of their mandate is unenforceable
  or unverifiable without it. They will ship, and nobody will know if it worked.
- **recommendation** — it would make them better. They are fine without it.

Be honest about the difference. Every gap becomes an escalation the Board actually
reads; calling a nice-to-have "blocking" is how a Board learns to ignore you.
"""


# ---------------------------------------------------------------------------

def _finish(job_id: str, *, plan: dict | None = None, error: str | None = None) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job["proc"] = None
        job["status"] = "ready" if plan else "failed"
        job["plan"] = plan
        job["error"] = error


def _roster_lines(members: list[dict[str, Any]]) -> str:
    out = []
    for m in members:
        lead = " (leads the firm)" if m.get("leads") else ""
        out.append(f"- **{m['name']}** — {m.get('role','')}{lead}. "
                   f"Owns: {m.get('owns') or m.get('description') or ''}")
    return "\n".join(out) or "- (none)"


def _bullets(items: list[Any], empty: str = "- (none)") -> str:
    if not items:
        return empty
    return "\n".join(f"- {i}" for i in items)


def _load_roster(workspace: Path, firm_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    conn = connect(get_db_path(workspace))
    try:
        firm = repo.get(conn, "firm", firm_id) or {}
        members = [
            {"id": m["id"], "name": m.get("name") or m["id"],
             "role": m.get("role") or "", "owns": m.get("description") or "",
             "leads": not m.get("reports_to_member_id"),
             "contract_id": m.get("contract_id")}
            for m in repo.find(conn, "member", firm_id=firm_id)
        ]
    finally:
        conn.close()
    return firm, members


def _validate(plan: dict[str, Any], members: list[dict[str, Any]],
              allowed: dict[str, set[str]]) -> dict[str, Any]:
    """Reject anything the agent invented. A loadout naming a tool that does not
    exist is worse than an empty one — it reads as a permission and resolves to
    nothing at run time."""
    names = {m["name"] for m in members}
    out_members = []
    for m in plan.get("members") or []:
        if not isinstance(m, dict) or m.get("name") not in names:
            continue
        out_members.append({
            "name": m["name"],
            "skills": [s for s in (m.get("skills") or []) if s in allowed["skills"]],
            "commands": [c for c in (m.get("commands") or []) if c in allowed["commands"]],
            "mcp": [s for s in (m.get("mcp") or []) if s in allowed["mcp"]],
            "cli": [c for c in (m.get("cli") or []) if c in allowed.get("cli", set())],
            "knowledge": [
                {"path": k["path"], "teaches": str(k.get("teaches") or "")}
                for k in (m.get("knowledge") or [])
                if isinstance(k, dict) and k.get("path") in allowed["folders"]
            ],
            # NEVERs as controls (fork 009): sanitized here, enforced by the
            # PreToolUse policy gate. Capped — a 50-rule denylist is a sign
            # the loadout is wrong, not a policy.
            "deny": [
                {"match": str(d["match"]).strip()[:120],
                 "reason": str(d.get("reason") or "").strip()[:200],
                 "tool": str(d.get("tool") or "").strip()[:60]}
                for d in (m.get("deny") or [])
                if isinstance(d, dict) and str(d.get("match") or "").strip()
            ][:12],
            "note": str(m.get("note") or ""),
        })
    seen = {m["name"] for m in out_members}
    for m in members:                      # nobody falls off the roster
        if m["name"] not in seen:
            out_members.append({"name": m["name"], "skills": [], "commands": [],
                                "mcp": [], "cli": [], "knowledge": [],
                                "deny": [], "note": ""})

    gaps = []
    for g in plan.get("gaps") or []:
        if not isinstance(g, dict) or not g.get("missing"):
            continue
        sev = str(g.get("severity") or "limiting").lower()
        if sev not in GAP_SEVERITY:
            sev = "limiting"
        gaps.append({
            "member": str(g.get("member") or ""),
            "missing": str(g.get("missing") or ""),
            "recommend": str(g.get("recommend") or ""),
            "why": str(g.get("why") or ""),
            "severity": sev,
        })
    return {"members": out_members, "gaps": gaps}


def _run_wiring(job_id: str, workspace: Path, firm_id: str,
                picks: dict[str, Any]) -> None:
    claude_bin, detail = resolve_claude_bin()
    if not claude_bin:
        _finish(job_id, error=f"claude runtime not wired: {detail}")
        return

    firm, members = _load_roster(workspace, firm_id)
    sv = discovery.survey(workspace, picks.get("folders") or [])

    skills = sv["knowledge"]["skills"]
    commands = sv["knowledge"]["commands"]
    folders = list(picks.get("folders") or [])

    # Only servers the firm can actually declare in its own .mcp.json. A Board can
    # tick a plugin-provided server in a stale UI; the agent must never be told it
    # exists, or it will hand out a permission that resolves to nothing at run time.
    offerable = {s["name"] for s in sv["mcp"]["servers"] if s.get("available")}
    # Already-equipped servers are ALWAYS in the plan. The Equip screen renders
    # them checked but only sends fresh ticks — on a re-Train, a firm's whole
    # armory would silently vanish from every loadout (the entries stay in
    # .mcp.json, but the loadout is the law, so Members lose permission to
    # tools the firm still carries). Equip has no unequip affordance; removal
    # is a deliberate sysconfig act, never a side effect of a reroll.
    equipped = [n for n in sv["mcp"]["equipped"] if n in offerable]
    mcp_names = sorted(
        {n for n in (picks.get("mcp") or []) if n in offerable} | set(equipped))

    # Host CLIs ride the same exclusion list founding honors — excluded means
    # never offered, here as there.
    from firm.dashboard import exclusions
    excluded_clis = exclusions.excluded_set("clis")
    clis = [c for c in sv["cli"]
            if c["present"] and c["name"] not in excluded_clis]

    allowed = {
        "skills": {s["name"] for s in skills},
        "commands": {c["name"] for c in commands},
        "mcp": set(mcp_names),
        "cli": {c["name"] for c in clis},
        "folders": set(folders),
    }

    prompt = _WIRING_PROMPT.format(
        name=firm.get("name") or firm_id,
        premise=firm.get("description") or "",
        roster=_roster_lines(members),
        mcp=_bullets(mcp_names, "- (none selected)"),
        cli=_bullets([discovery.cli_prompt_line(c) for c in clis]),
        skills=_bullets([f"{s['name']} — {s['description']}" for s in skills]),
        commands=_bullets([c["name"] for c in commands]),
        folders=_bullets([f"{f['path']} ({f['files']} files: {', '.join(f['sample'])})"
                          for f in sv["knowledge"]["attached"]], "- (none attached)"),
        voice=(picks.get("voice") or "").strip() or "(they said nothing further)",
    )

    argv = [claude_bin, *_FOUNDING_FLAGS, "-p", prompt + NARRATION_CONTRACT]
    env = dict(os.environ)
    env.pop("CADRE_DB_URL", None)
    env.pop("CADRE_DB_TOKEN", None)

    try:
        proc = subprocess.Popen(
            argv, cwd=str(_framework_root()),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, text=True,
        )
    except OSError as exc:
        _finish(job_id, error=f"could not spawn the wiring agent: {exc}")
        return

    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["proc"] = proc

    final = ""
    narrator = Narrator()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            notes = narrator.feed(line)
            if notes:
                with _jobs_lock:
                    job = _jobs.get(job_id)
                    if job is None:
                        return
                    job["narration"].extend(notes)
            try:
                evt = json.loads(line)
                if evt.get("type") == "result":
                    final = evt.get("result") or ""
            except json.JSONDecodeError:
                pass
        proc.wait(timeout=_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        proc.kill()
        _finish(job_id, error="The wiring agent took too long.")
        return
    except Exception as exc:
        _finish(job_id, error=str(exc))
        return

    if not final:
        err = (proc.stderr.read() if proc.stderr else "").strip()
        tail = err.splitlines()[-1][:200] if err else f"exit code {proc.returncode}"
        _finish(job_id, error=f"The wiring agent returned nothing — {tail}")
        return

    try:
        text = final.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if fence:
            text = fence.group(1)
        raw = json.loads(text[text.find("{"):text.rfind("}") + 1])
        plan = _validate(raw, members, allowed)
    except (ValueError, json.JSONDecodeError) as exc:
        _finish(job_id, error=f"The wiring agent's plan did not hold up: {exc}")
        return

    plan["firm_id"] = firm_id
    plan["name"] = firm.get("name") or firm_id
    plan["mcp"] = mcp_names
    plan["keys_needed"] = discovery.keys_needed(mcp_names)
    _finish(job_id, plan=plan)


def start(workspace: Path, firm_id: str, picks: dict[str, Any]) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"status": "thinking", "narration": ["Reading the roster and what you have…"], "plan": None,
                         "error": None, "proc": None}
    threading.Thread(target=_run_wiring,
                     args=(job_id, workspace, firm_id, picks), daemon=True).start()
    return {"ok": True, "job_id": job_id}


def status(job_id: str, cursor: int = 0) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "unknown wiring run"}
        narration = job["narration"][cursor:]
        return {"ok": True, "status": job["status"], "narration": narration,
                "cursor": cursor + len(narration),
                "plan": job["plan"], "error": job["error"]}


# ---------------------------------------------------------------------------
# Commit — loadouts to Contracts, servers to the armory, law to CLAUDE.md
# ---------------------------------------------------------------------------

def _render_charter(firm: dict[str, Any], members: list[dict[str, Any]],
                    plan: dict[str, Any], base: dict[str, Any],
                    today: str) -> str:
    by_name = {m["name"]: m for m in plan["members"]}

    loadouts = []
    for m in members:
        p = by_name.get(m["name"], {})
        lines = [f"### {m['name']} — {m.get('role','')}"]
        if p.get("note"):
            lines.append(f"\n{p['note']}\n")
        if p.get("skills"):
            lines.append("- **Skills:** " + ", ".join(f"`{s}`" for s in p["skills"]))
        if p.get("commands"):
            lines.append("- **Commands:** " + ", ".join(f"`{c}`" for c in p["commands"]))
        if p.get("mcp"):
            lines.append("- **Servers:** " + ", ".join(f"`{s}`" for s in p["mcp"]))
        if p.get("cli"):
            lines.append("- **CLI:** " + ", ".join(f"`{c}`" for c in p["cli"]))
        for k in p.get("knowledge") or []:
            lines.append(f"- **Knowledge:** `{k['path']}` — {k['teaches']}")
        for d in p.get("deny") or []:
            lines.append(f"- **Never (enforced):** `{d['match']}` — {d['reason']}")
        if not any(p.get(f) for f in ("skills", "commands", "mcp", "cli", "knowledge")):
            lines.append("- Carries nothing beyond the firm's standing tools.")
        loadouts.append("\n".join(lines))

    armory = "\n".join(f"- `{s}`" for s in plan.get("mcp") or []) or \
             "- The firm has no MCP servers. Members work with the filesystem and CLI."

    # Host ground truth, probed at render time — the charter must never carry
    # a staler claim about the machine than the survey can make right now.
    host_tools = "\n".join(
        f"- {discovery.cli_prompt_line(c)}"
        for c in discovery.cli_survey() if c["present"]
    ) or "- (nothing recognized on PATH)"

    base_section = ""
    if base.get("present"):
        base_section = _BASE_SECTION.format(
            extensions=", ".join(f"`{e['name']}`" for e in base["extensions"]) or "none")

    return _CHARTER.format(
        name=firm.get("name") or plan["firm_id"],
        premise=firm.get("description") or "",
        north_star=firm.get("north_star")
        or "(not set — the Board owes this firm a number)",
        roster=_roster_lines(members),
        loadouts="\n\n".join(loadouts),
        armory=armory,
        host_tools=host_tools,
        base_section=base_section,
        today=today,
    )


def commit(root: Path, firm_id: str, plan: dict[str, Any],
           keys: dict[str, str] | None = None) -> dict[str, Any]:
    """Write the armory, the loadouts, the keys, and the law."""
    from firm.services import contract as contract_svc
    from firm.services import escalation as escalation_svc
    from firm.sysconfig import service as sysconfig_svc

    workspace = (root / firm_id).resolve()
    if workspace.parent != root.resolve() or not get_db_path(workspace).exists():
        return {"ok": False, "error": "unknown firm"}

    firm, members = _load_roster(workspace, firm_id)
    by_name = {m["name"]: m for m in plan.get("members") or []}
    wrote: list[str] = []

    conn = connect(get_db_path(workspace))
    try:
        # 1. Keys to the vault, before the armory that references them.
        for key, value in (keys or {}).items():
            if value:
                sysconfig_svc.vars_set(conn, firm_id, workspace, key, value, "firm")
                wrote.append(f"vault:{key}")

        # 2. The armory. Specs carry ${KEY} placeholders, never the key itself.
        #    A server we cannot resolve a spec for must ABORT, not be skipped — a
        #    silent skip leaves a loadout naming a tool the firm doesn't carry, and
        #    the failure only surfaces mid-run, in a Member, with nobody watching.
        #    A server the firm ALREADY carries resolves from its own .mcp.json —
        #    re-equipping what you own must never abort a re-Train just because
        #    the spec's original source left the operator's config.
        chosen = list(plan.get("mcp") or [])
        specs = discovery.raw_specs(chosen)
        try:
            own = json.loads((workspace / ".mcp.json").read_text()).get("mcpServers") or {}
        except (OSError, json.JSONDecodeError):
            own = {}
        for n in chosen:
            if n not in specs and isinstance(own.get(n), dict):
                specs[n] = own[n]
        unresolved = [n for n in chosen if n not in specs]
        if unresolved:
            raise ValueError(
                "no spec found for " + ", ".join(unresolved)
                + " — these cannot be written into the firm's .mcp.json"
            )
        for name, spec in specs.items():
            sysconfig_svc.mcp_set(conn, firm_id, workspace, name, spec)
            wrote.append(f"mcp:{name}")

        # 3. Loadouts onto Contracts. The loadout is the law.
        for m in members:
            p = by_name.get(m["name"])
            if not p or not m.get("contract_id"):
                continue
            updates: dict[str, Any] = {
                "skill_loadout": {
                    "skills": p["skills"],
                    "commands": p["commands"],
                    "mcp": p["mcp"],
                    "cli": p.get("cli") or [],
                    "knowledge": p["knowledge"],
                },
            }
            # Deny rules ride validation_config next to gates_required —
            # merged, not replaced, so the founding gates survive a rewire.
            from firm.services.policy import _parse_vc
            contract_row = repo.get(conn, "contract", m["contract_id"]) or {}
            vc = _parse_vc(contract_row)
            vc["deny"] = p.get("deny") or []
            updates["validation_config"] = vc
            contract_svc.update_contract(conn, m["contract_id"], updates)
            wrote.append(f"loadout:{m['name']}")

        # 3b. The tools themselves. A loadout that names a skill nobody
        #     installed is a promise, not a capability — Members run in this
        #     workspace, so every named skill and command is symlinked in from
        #     the operator's own library (live: operator updates flow through).
        #     An unresolvable name ABORTS, exactly like an unresolvable MCP spec.
        know = discovery.knowledge_survey(None)
        skill_src = {s["name"]: Path(s["path"]) for s in know["skills"]}
        cmd_src = {c["name"]: Path(c["path"]) for c in know["commands"]}
        want_skills: set[str] = set()
        want_cmds: set[str] = set()
        for p in (plan.get("members") or []):
            want_skills.update(p.get("skills") or [])
            want_cmds.update(p.get("commands") or [])
        missing = ([n for n in want_skills if n not in skill_src]
                   + [n for n in want_cmds if n not in cmd_src])
        if missing:
            raise ValueError(
                "no installed source for " + ", ".join(sorted(missing))
                + " — these cannot be handed to a Member")
        for n in sorted(want_skills):
            dest = workspace / ".claude" / "skills" / n
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.symlink_to(skill_src[n])
            wrote.append(f"skill:{n}")
        for n in sorted(want_cmds):
            rel = Path(*n.lstrip("/").split(":")).with_suffix(".md")
            dest = workspace / ".claude" / "commands" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.symlink_to(cmd_src[n])
            wrote.append(f"command:{n}")

        # 4. Gaps become Escalations, raised by the Member who has the gap. They do
        #    NOT block the firm — the Board reads them on the floor and decides.
        ids = {m["name"]: m["id"] for m in members}
        for g in plan.get("gaps") or []:
            mem_id = ids.get(g.get("member"))
            if not mem_id:
                continue
            sev = GAP_SEVERITY.get(g.get("severity") or "limiting", "normal")
            escalation_svc.raise_escalation(conn, firm_id, {
                "raised_by_member_id": mem_id,
                "severity": sev,
                "title": f"I lack {g['missing']}",
                "body": (
                    f"{g.get('why', '')}\n\n"
                    f"Recommended: {g.get('recommend', '')}\n\n"
                    f"Raised at founding by the wiring agent. This does not stop the firm "
                    f"from working — it is what I cannot do until the Board equips me."
                ),
                "dedupe_key": f"founding-gap:{mem_id}:{g['missing'][:60]}",
            })
            wrote.append(f"escalation:{g['member']}")

        # 5. The proving run. Equipping used to end at assertion — a survey
        #    said the tools work and nobody ever tested them from inside a
        #    Member run. The lead's first unit is to PROVE the toolchain with
        #    cheap read-only calls and file a grounded capability report, so
        #    the firm's first pulse establishes ground truth instead of
        #    shipping confident blindness. Idempotent across Train rerolls.
        from firm.services import unit as unit_svc
        cli_assigned = sorted({c for p in (plan.get("members") or [])
                               for c in (p.get("cli") or [])})
        already = any(u.get("name") == "Prove the armory"
                      for u in repo.find(conn, "unit", firm_id=firm_id))
        lead = next((m for m in members if m.get("leads")), None)
        projects = sorted(repo.find(conn, "project", firm_id=firm_id),
                          key=lambda p: p.get("created_at") or "")
        if (chosen or cli_assigned) and not already and lead and projects:
            probes = "\n".join(
                [f"- MCP `{s}`: list its tools, then make ONE cheap read-only call"
                 for s in chosen]
                + [f"- `{c}`: run ONE cheap read-only identity or list call"
                   for c in cli_assigned])
            unit_svc.create_unit(conn, firm_id, {
                "name": "Prove the armory",
                "project_id": projects[0]["id"],
                "assignee_member_id": lead["id"],
                "status": "pending",
                "description": (
                    "Your firm was equipped at founding. Prove it before you "
                    "trust it. For each item below, run ONE cheap, read-only "
                    "probe — an identity call, a one-item list — and record "
                    "the exact command and what it returned:\n\n"
                    f"{probes}\n\n"
                    "Deliverable: a capability report, registered as a "
                    "document artifact, stating for each tool VERIFIED "
                    "WORKING or FAILED, with the evidence inline. Raise an "
                    "escalation only for what actually failed — a probe that "
                    "works is a fact, not a finding. Read-only means "
                    "read-only: no sends, no writes, no spends."
                ),
            })
            wrote.append("unit:prove-the-armory")

        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.close()
        return {"ok": False, "error": f"could not wire the firm: {exc}"}
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # 4. The charter — rendered from the template, not improvised.
    try:
        charter = _render_charter(
            firm, members, plan, discovery.base_survey(),
            datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
        )
        (workspace / "CLAUDE.md").write_text(charter, encoding="utf-8")
        wrote.append("CLAUDE.md")
    except OSError as exc:
        return {"ok": False, "error": f"could not write the charter: {exc}"}

    # 5. The lock. The deny rules just written are prose until they are
    #    materialized where the PreToolUse gate reads them and the gate is
    #    installed in the workspace. If a NEVER is worth writing, it is
    #    worth enforcing (fork 009).
    try:
        from firm.cli.install_hooks import install_policy_hook
        from firm.services import policy as policy_svc
        conn = connect(get_db_path(workspace))
        try:
            policy_svc.materialize(conn, workspace, firm_id)
        finally:
            conn.close()
        _, hook_msgs = install_policy_hook(workspace)
        wrote.append("policy:materialized")
        wrote.extend(f"policy:{m}" for m in hook_msgs[:1])
    except Exception as exc:
        return {"ok": False, "error": f"could not arm the policy gate: {exc}"}

    return {"ok": True, "firm_id": firm_id, "wrote": wrote,
            "charter": str(workspace / "CLAUDE.md")}
