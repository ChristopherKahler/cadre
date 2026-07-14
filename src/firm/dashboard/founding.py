"""The founding agent — a firm designs its own org from one paragraph.

The Board describes a business in prose. A headless ``claude --print`` run
(same hardened flag set Members spawn under) reads the scaffolding knowledge
Cadre already ships and returns a structured org proposal: operations,
contracts, members, and what each one needs Board approval for.

The Board reviews the slate, cuts and edits candidates, and approves. Only
then does anything touch disk — ``commit`` scaffolds the workspace, runs the
real ``run_init``, and writes the approved org through the same service
functions Members use, so Records read identically to a hand-seeded firm.

Jobs are held in memory. A founding run is a single interactive act, not a
durable pipeline; if the hub dies mid-thought, the Board starts over — which
costs one paragraph.
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
from firm.pulse.spawn import resolve_claude_bin

# Mirrors the hardened Member spawn flags (firm/pulse/spawn.py). --strict-mcp-config
# with NO --mcp-config is load-bearing and deliberate: strict means the run gets
# exactly the servers named in an explicit config, and naming none gives it none.
# Without strict, a headless run under --dangerously-skip-permissions inherits the
# operator's entire personal MCP fleet (387 tools incl. Gmail/Slack/Drive, measured
# 2026-07-10). A founding agent needs to read two docs and write JSON. It gets files.
_FOUNDING_FLAGS = [
    "--print",
    # Board ruling 2026-07-13: the firm only gets created once — architect it
    # with Opus 4.8 at max effort, never the operator's session default. The
    # stage choreography absorbs the latency; quality is the point. Shared by
    # the wiring and Co-Board briefing agents (they import these flags).
    "--model", "claude-opus-4-8",
    "--effort", "max",
    "--output-format", "stream-json",
    "--verbose",
    # Token-level deltas. Without this a `--print` run is one silent assistant turn:
    # the agent reads two docs, thinks for ninety seconds, and dumps a JSON blob. The
    # Board stares at a bar. With it — plus a prompt that tells the agent to narrate —
    # they watch it reason about their business in real time, which is the difference
    # between a spinner and a deliberation.
    "--include-partial-messages",
    "--dangerously-skip-permissions",
    "--strict-mcp-config",
]

# Every agent prompt ends with this. The narration is the loading experience.
NARRATION_CONTRACT = """\

## Narrate as you work

Think out loud where the Board can see it. Write lines beginning with `· ` (middot,
space). The Board reads these while they wait — they are the only window into what
you're doing, so make them worth reading.

**Write your first line before you do anything else** — before you read a file, before
you plan. The Board is staring at an empty screen until you speak.

**Four to six lines. No more.** Each one is a *conclusion you reached*, stated whole —
not a step in a checklist. Consolidate: if you made five small decisions that all serve
one judgment, that is ONE line about the judgment. A line that only makes sense as item
three of a list is the wrong line.

Good: `· They record the talking head themselves — so nobody films, nobody directs.
That kills two roles I'd otherwise have staffed, and it means the whole org sits
downstream of the camera.`

Bad (too granular, too many, reads as a checklist):
`· Reviewing the brief.` `· Three platforms noted.` `· Deciding on operations.`
`· Assigning gates.` `· Naming members.`

Write them as you genuinely arrive at each judgment, not all at once at the end. Then
output what you were asked for. Nothing between or after the `· ` lines except the
output itself.
"""

_FIRM_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_TIMEOUT_SEC = 300

# The tiers a Member may run on, cheapest last. CLI aliases, not pinned ids —
# an alias tracks the account's current model of that tier, so a founded firm
# upgrades with the account instead of fossilizing on a version string.
# (Board ruling 2026-07-14: Cooper's single run — 30 min, $13.99, 7.6M cache
# reads — because no contract set a model and all four Members inherited Opus.
# The founding slate now staffs the model like it staffs the org.)
_MODEL_TIERS = ("opus", "sonnet", "haiku")
_DEFAULT_MODEL = "sonnet"

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------

_FOUNDING_PROMPT = """\
You are the founding agent for a new Cadre firm. The Board has described a
business in their own words. Design the organization that runs it.

## The Board's brief

{brief}

## What Cadre is

A Firm is a company of AI Members. Each Member has a Contract (what they may
run and which skills they carry) and claims Units (atomic work) inside
Operations (departments) toward Goals. A Gate is Board approval, required for
anything significant. The Board is the human. They govern; they do not do the
work.

## House rules on org design

The full house docs are inlined below — they are already in front of you.
Do NOT read any files; everything you need is here.

__HOUSE_RULES__

## The operator's arsenal

Everything below actually exists on the operator's machine. When you design
the org, ALSO design its starting loadout — which of these this firm needs,
and why. Recommend ONLY names that appear here, spelled exactly as written;
anything invented is silently discarded. Be lean: recommend what the work
needs, usually three to ten items across all three lists. The Board reviews
your picks with your rationale next to each — the rationale is what they read.

__INVENTORY__

## How to design this org

- Start from the work, not from a template. What must happen every week for
  this business to move? Those are your Operations.
- Staff the smallest org that covers the work. Three to six Members. A firm
  with a Member who has nothing to claim is a firm that wastes the Board's money.
- Every Member owns an outcome, not a tool. "Grows the audience" is a role.
  "Uses Instagram" is not.
- Name them like people, because the Board will talk to them like people.
  One word. Distinct. No cute AI puns, no "Bot", no "AI" in the name.
- Give exactly one Member the lead. They report to the Board; everyone else
  reports to them.
- Be explicit about what needs a Gate. Anything published, anything spent,
  anything sent to another human. Default to gating; trust is earned later.
- Staff the model like you staff the org. Every run bills the Board, and the
  Member's model is the cost lever. Default to "sonnet". Reserve "opus" for a
  role whose whole job is judgment — usually the lead, sometimes nobody. Use
  "haiku" for mechanical, high-frequency work. A four-Member firm running
  all-Opus bills like a law firm.
- The firm gets ONE goal, not a list. Pick the single measurable outcome
  that, if true at the end of a quarter, means this firm worked. A firm with
  no number cannot fail — it can only be busy, which is worse. Give the Board
  a number to argue with, not prose to admire.

## Output

Return ONLY a JSON object, no prose before or after, no code fence:

{{
  "firm_id": "kebab-case-slug, max 32 chars, letters/digits/hyphens, starts with a letter",
  "name": "The firm's display name, title case",
  "premise": "One sentence: what this company exists to do. The Board's words, sharpened.",
  "north_star": {{
    "target": "The firm's ONE goal — a sentence with a number in it. If it is true at the end of the quarter, the firm worked.",
    "metric_value": 5,
    "metric_unit": "what the number counts, e.g. pages/week — '' if the target has no clean unit",
    "why": "One line: why THIS number proves the premise."
  }},
  "operations": [
    {{"name": "Department name", "purpose": "One line — what this department is accountable for."}}
  ],
  "members": [
    {{
      "name": "Onename",
      "role": "Their title",
      "owns": "One sentence: the outcome they are accountable for.",
      "operation": "The name of the Operation they work in — must match one above exactly",
      "leads": true or false,
      "model": "opus, sonnet, or haiku — the Claude tier this Member runs on",
      "skills": ["skill or command names they'd carry — [] if none obvious"],
      "gates": ["what this Member must get Board approval for, in plain words"]
    }}
  ],
  "first_units": [
    {{"name": "The first real piece of work", "member": "Onename", "why": "One line."}}
  ],
  "reroll_tips": [
    "Advice to the Board on how to brief me better, if they don't like this org."
  ],
  "loadout": {{
    "mcp": [{{"name": "exact server name from the arsenal", "why": "who uses it, for what — one line"}}],
    "skills": [{{"name": "exact skill name from the arsenal", "why": "one line"}}],
    "commands": [{{"name": "exact command name from the arsenal", "why": "one line"}}]
  }}
}}

Exactly one Member has "leads": true. Every Member's "operation" matches an
Operation name exactly. Every Member's "model" is one of opus, sonnet, haiku.
Give two to four first_units — real work this firm could start on tonight,
not setup chores.

`reroll_tips`: two or three specific things the Board could have told you that
would have produced a sharper org. Name what you had to *guess* at — the thing
you inferred because they didn't say. "You didn't say whether you publish or
just draft, so I gated everything" is a useful tip. "Be more specific" is not.
Write them as instructions to the Board, not observations about yourself.
"""


# ---------------------------------------------------------------------------
# Founding run
# ---------------------------------------------------------------------------

def _framework_root() -> Path:
    """Repo root — the founding agent runs here so its doc paths resolve."""
    return Path(__file__).resolve().parents[3]


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the proposal object out of the agent's final message."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("founding agent returned no JSON object")
    return json.loads(text[start:end + 1])


def _validate(proposal: dict[str, Any],
              inv: dict[str, set[str]] | None = None) -> dict[str, Any]:
    """Shape the agent's output into something the Board can act on.

    Fails loudly on the two things that would corrupt a firm — a bad id, or a
    Member assigned to an Operation that doesn't exist. Everything else is
    coerced, because a missing "skills" list is not worth losing the org over.

    *inv* is the arsenal index the prompt was built from; loadout picks that
    don't resolve against it are dropped — the Board never reviews a ghost.
    With inv=None (commit-time revalidation) the loadout passes through as-is.
    """
    def _loadout(kind: str) -> list[dict[str, str]]:
        seen: set[str] = set()
        out = []
        for it in (proposal.get("loadout") or {}).get(kind) or []:
            if not isinstance(it, dict) or not it.get("name"):
                continue
            name = str(it["name"]).strip()
            if name in seen or (inv is not None and name not in inv.get(kind, set())):
                continue
            seen.add(name)
            out.append({"name": name, "why": str(it.get("why") or "").strip()[:200]})
        return out

    fid = str(proposal.get("firm_id") or "").strip().lower()
    if not _FIRM_ID_RE.match(fid):
        raise ValueError(f"invalid firm_id {fid!r}")

    ops = [
        {"name": str(o["name"]).strip(), "purpose": str(o.get("purpose") or "").strip()}
        for o in proposal.get("operations") or []
        if isinstance(o, dict) and o.get("name")
    ]
    if not ops:
        raise ValueError("proposal has no operations")
    op_names = {o["name"] for o in ops}

    # The roster pills are promises to the Board — scrub them against the same
    # arsenal the loadout is held to, or the agent decorates candidates with
    # tools that don't exist (or that no Member may ever carry).
    allowed = (inv.get("skills", set()) | inv.get("commands", set())
               ) if inv is not None else None

    members = []
    for m in proposal.get("members") or []:
        if not isinstance(m, dict) or not m.get("name"):
            continue
        op = str(m.get("operation") or "").strip()
        if op not in op_names:
            raise ValueError(
                f"member {m['name']!r} assigned to unknown operation {op!r}")
        model = str(m.get("model") or "").strip().lower()
        members.append({
            "name": str(m["name"]).strip(),
            "role": str(m.get("role") or "").strip(),
            "owns": str(m.get("owns") or "").strip(),
            "operation": op,
            "leads": bool(m.get("leads")),
            # Coerced, never fatal — a missing model is not worth losing the
            # org over, and sonnet is the tier a role must argue its way off.
            "model": model if model in _MODEL_TIERS else _DEFAULT_MODEL,
            "skills": [str(s) for s in (m.get("skills") or [])
                       if s and (allowed is None or str(s) in allowed)],
            "gates": [str(g) for g in (m.get("gates") or []) if g],
        })
    if not members:
        raise ValueError("proposal has no members")

    leads = [m for m in members if m["leads"]]
    if len(leads) != 1:  # the agent gets this wrong occasionally; the org can't be headless
        for m in members:
            m["leads"] = False
        members[0]["leads"] = True

    # The firm's ONE goal. Coerced to shape here, REQUIRED at commit — a firm
    # with no number cannot fail, only be busy, and the Board must see and
    # own the number before the hire. None (agent omitted it) is survivable
    # on the roster screen, where the Board writes one; not past it.
    ns = proposal.get("north_star")
    north_star = None
    if isinstance(ns, dict) and str(ns.get("target") or "").strip():
        mv = ns.get("metric_value")
        north_star = {
            "target": str(ns["target"]).strip()[:300],
            "metric_value": mv if isinstance(mv, (int, float)) else None,
            "metric_unit": str(ns.get("metric_unit") or "").strip()[:60],
            "why": str(ns.get("why") or "").strip()[:300],
        }

    return {
        "firm_id": fid,
        "name": str(proposal.get("name") or fid).strip(),
        "premise": str(proposal.get("premise") or "").strip(),
        "north_star": north_star,
        "operations": ops,
        "members": members,
        "loadout": {"mcp": _loadout("mcp"), "skills": _loadout("skills"),
                    "commands": _loadout("commands")},
        "first_units": [
            {"name": str(u["name"]).strip(),
             "member": str(u.get("member") or "").strip(),
             "why": str(u.get("why") or "").strip()}
            for u in proposal.get("first_units") or []
            if isinstance(u, dict) and u.get("name")
        ],
        # Held back until the Board asks to reroll — advice they haven't earned the
        # right to need yet, and noise on a draft they're about to accept.
        "reroll_tips": [str(t).strip() for t in (proposal.get("reroll_tips") or []) if t][:3],
    }


class Narrator:
    """Turns a `claude --print` stream into lines the Board can watch arrive.

    Two sources, both real:

    - **Tool calls** — `Reading org-design.md`. Activity. Proves it's working.
    - **Token deltas** — the agent's own prose, streamed as it is written. The
      prompt (see NARRATION_CONTRACT) tells it to reason out loud in `· ` lines
      before emitting JSON, so what arrives is a train of actual decisions rather
      than a progress bar pretending to be one.

    Stateful because deltas arrive mid-word: text is buffered and only released
    at a newline. Narration stops the moment the JSON begins — the Board should
    watch the agent think, not watch it type a data structure.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._done = False       # the JSON has started; nothing after it is prose

    def feed(self, line: str) -> list[str]:
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            return []

        kind = evt.get("type")

        if kind == "assistant":
            out = []
            for block in (evt.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    target = (block.get("input") or {}).get("file_path") or ""
                    out.append(f"Reading {Path(target).name}" if target
                               else str(block.get("name") or "working"))
            return out

        if kind != "stream_event" or self._done:
            return []

        ev = evt.get("event") or {}
        if ev.get("type") != "content_block_delta":
            return []
        delta = ev.get("delta") or {}
        if delta.get("type") != "text_delta":
            return []

        self._buf += delta.get("text") or ""
        out = []
        while "\n" in self._buf:
            line_out, self._buf = self._buf.split("\n", 1)
            note = self._line(line_out)
            if self._done:
                return out
            if note:
                out.append(note)
        return out

    def _line(self, raw: str) -> str | None:
        text = raw.strip()
        if not text:
            return None
        if text.startswith("{") or text.startswith("```"):
            self._done = True       # JSON has begun — stop narrating
            return None
        if text.startswith("·"):
            return text.lstrip("·").strip()[:200] or None
        return None


def _inventory() -> tuple[str, dict[str, set[str]]]:
    """The operator's real arsenal, compacted for the founding prompt.

    The founding agent recommends the firm's starting loadout — so it must
    see what actually exists, and _validate drops anything it invents.
    Skills carry a line of description; commands ride as names only (there
    are ~150 of them — the whole point is that the Board stops scrolling
    through that list). Returns (prompt_text, validation_index).
    """
    from firm.dashboard import discovery, exclusions, inventory

    # The Armory is the survey of record (machine tier, shared with Train and
    # the Floor's equip picker) — founding demands fresh CLI identity probes
    # because its prompt promises "probed just now". The operator's global
    # exclusion list is a hard boundary: an excluded item never enters the
    # agent's head, and the validation index drops it even if the agent
    # hallucinates the name.
    ex = exclusions.load()
    inv = inventory.ensure(max_cli_age_sec=3600)
    mcp = [s for s in inv.get("mcp") or []
           if s.get("available") and s["name"] not in set(ex["mcp"])]
    skills = [sk for sk in inv.get("skills") or []
              if sk["name"] not in set(ex["skills"])]
    commands = [c for c in inv.get("commands") or []
                if c["name"] not in set(ex["commands"])]
    clis = [c for c in inv.get("cli") or []
            if c["present"] and c["name"] not in set(ex["clis"])]

    lines = ["### MCP servers (firm-wide armory — every Member shares these)",
             "BASE is NOT in this list and never will be: it is a CLI tool with "
             "its own card, and its graph is read and written through the `base` "
             "CLI. Never describe any MCP server as the surface for BASE or its "
             "graph."]
    for s in mcp:
        keys = f" (needs {', '.join(s['needs_keys'])})" if s.get("needs_keys") else ""
        lines.append(f"- {s['name']}{keys}")
    lines.append("")
    lines.append("### CLI tools (host machine — every Member can shell out to these)")
    lines.append(
        "Probed on this machine moments ago; this list is ground truth. A tool "
        "marked LIVE is installed AND signed in — design the org around it. "
        "Never treat a capability as absent when a LIVE tool below provides it: "
        "a firm was once founded believing it had no email while a signed-in "
        "Google Workspace CLI sat right here. Do not repeat that.")
    for c in clis:
        lines.append(f"- {discovery.cli_prompt_line(c)}")
    lines.append("")
    lines.append("### Skills (attachable per Member)")
    for sk in skills:
        desc = (sk.get("description") or "").strip().replace("\n", " ")[:90]
        lines.append(f"- {sk['name']} — {desc}" if desc else f"- {sk['name']}")
    lines.append("")
    lines.append("### Commands (attachable per Member; names only)")
    lines.append(", ".join(c["name"] for c in commands))

    index = {
        "mcp": {s["name"] for s in mcp},
        "skills": {sk["name"] for sk in skills},
        "commands": {c["name"] for c in commands},
    }
    return "\n".join(lines), index


def _house_rules() -> str:
    """The org-design house docs, inlined verbatim into the prompt.

    Identical input to what the agent used to fetch itself — but each Read
    was a full model round-trip, and the two of them were most of the silent
    first minute. Inlining is lossless: same text, zero tool turns.
    """
    root = _framework_root()
    parts = []
    for rel in ("docs/FIRM-SCAFFOLDING-GUIDE.md",
                "claude/cadre-framework/frameworks/org-design.md"):
        try:
            parts.append(f"### {rel}\n\n{(root / rel).read_text(encoding='utf-8')}")
        except OSError:
            parts.append(f"### {rel}\n\n(unavailable — design from the rules above)")
    return "\n\n".join(parts)


def _run_founding(job_id: str, brief: str) -> None:
    claude_bin, detail = resolve_claude_bin()
    if not claude_bin:
        _finish(job_id, error=f"claude runtime not wired: {detail}")
        return

    # Prompt rides argv as -p, the way every Member run passes it. An explicit
    # env, never ambient inheritance — a hub started from a shell that sourced a
    # firm's .env would otherwise point the founding run at that firm's database.
    # House rules and the arsenal are token-swapped AFTER .format — the inlined
    # docs contain literal braces that str.format would choke on.
    arsenal, inv = _inventory()
    argv = [claude_bin, *_FOUNDING_FLAGS, "-p",
            _FOUNDING_PROMPT.format(brief=brief)
                .replace("__HOUSE_RULES__", _house_rules())
                .replace("__INVENTORY__", arsenal)
            + NARRATION_CONTRACT]
    env = dict(os.environ)
    env.pop("CADRE_DB_URL", None)   # a founding run has no firm yet
    env.pop("CADRE_DB_TOKEN", None)

    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(_framework_root()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
    except OSError as exc:
        _finish(job_id, error=f"could not spawn the founding agent: {exc}")
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
                        return          # cancelled
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
        _finish(job_id, error="The founding agent took too long. Try a shorter brief.")
        return
    except Exception as exc:
        _finish(job_id, error=str(exc))
        return

    if not final:
        # A mute failure is the worst kind. Say what the runtime actually said.
        err = (proc.stderr.read() if proc.stderr else "").strip()
        detail = err.splitlines()[-1][:200] if err else f"exit code {proc.returncode}"
        _finish(job_id, error=f"The founding agent returned nothing — {detail}")
        return
    try:
        _finish(job_id, proposal=_validate(_extract_json(final), inv=inv))
    except (ValueError, json.JSONDecodeError) as exc:
        _finish(job_id, error=f"The founding agent's org did not hold up: {exc}")


def _finish(job_id: str, *, proposal: dict | None = None, error: str | None = None) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job["proc"] = None
        job["status"] = "ready" if proposal else "failed"
        job["proposal"] = proposal
        job["error"] = error
        job["ended_at"] = datetime.now(tz=timezone.utc).isoformat()


_RESHUFFLE_PROMPT = """\
You designed this org for a Cadre firm. The Board has read it and pushed back.

## The org you proposed

{current}

## What the Board said

{note}

## What to do

Patch it. Not a rewrite — a *response*. Keep everything they didn't object to,
exactly as it was, including names and each Member's "model", so they aren't
re-reading a whole new org to find the one thing they asked for. Change what
they asked you to change, and anything that must move as a consequence.

If they asked for something you think is a mistake, do it anyway and say why you
disagree in `pushback`. They are the Board. But an org architect who never says
"that will cost you" is not worth having.

Return ONLY the same JSON object shape you returned before — `firm_id`, `name`,
`premise`, `north_star`, `operations`, `members`, `first_units`, `reroll_tips` —
patched. Plus one extra key:

  "pushback": "One or two sentences, or empty string if you agree with them."

Same rules as before: exactly one Member leads, every Member's `operation`
matches an Operation name exactly.
"""


def _run_reshuffle(job_id: str, proposal: dict[str, Any], note: str) -> None:
    claude_bin, detail = resolve_claude_bin()
    if not claude_bin:
        _finish(job_id, error=f"claude runtime not wired: {detail}")
        return

    prompt = _RESHUFFLE_PROMPT.format(
        current=json.dumps(proposal, indent=2), note=note.strip()) + NARRATION_CONTRACT
    argv = [claude_bin, *_FOUNDING_FLAGS, "-p", prompt]
    env = dict(os.environ)
    env.pop("CADRE_DB_URL", None)
    env.pop("CADRE_DB_TOKEN", None)

    try:
        proc = subprocess.Popen(argv, cwd=str(_framework_root()),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, text=True)
    except OSError as exc:
        _finish(job_id, error=f"could not spawn the founding agent: {exc}")
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
        _finish(job_id, error="The founding agent took too long.")
        return
    except Exception as exc:
        _finish(job_id, error=str(exc))
        return

    if not final:
        _finish(job_id, error="The founding agent returned nothing.")
        return
    try:
        raw = _extract_json(final)
        patched = _validate(raw)
        patched["pushback"] = str(raw.get("pushback") or "").strip()
        # The reshuffle agent argues about the ORG, not the arsenal — it
        # usually omits loadout. Carry the founding recommendations forward
        # rather than losing them to a roster argument.
        if not any(patched["loadout"].values()) and any(
                (proposal.get("loadout") or {}).values()):
            patched["loadout"] = proposal["loadout"]
        _finish(job_id, proposal=patched)
    except (ValueError, json.JSONDecodeError) as exc:
        _finish(job_id, error=f"The patched org did not hold up: {exc}")


def reshuffle(proposal: dict[str, Any], note: str) -> dict[str, Any]:
    """The Board argues with the draft; the agent answers with a patched one."""
    note = (note or "").strip()
    if len(note) < 5:
        return {"ok": False, "error": "Tell me what you'd change."}
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"status": "thinking", "narration": ["Reading the house rules…"], "proposal": None,
                         "error": None, "proc": None,
                         "started_at": datetime.now(tz=timezone.utc).isoformat()}
    threading.Thread(target=_run_reshuffle, args=(job_id, proposal, note),
                     daemon=True).start()
    return {"ok": True, "job_id": job_id}


def start(brief: str) -> dict[str, Any]:
    """Kick off a founding run. Returns immediately with a job id."""
    brief = (brief or "").strip()
    if len(brief) < 20:
        return {"ok": False, "error": "Tell me a little more about the business."}
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "thinking",
            "narration": [],
            "proposal": None,
            "error": None,
            "proc": None,
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
        }
    threading.Thread(target=_run_founding, args=(job_id, brief), daemon=True).start()
    return {"ok": True, "job_id": job_id}


def status(job_id: str, cursor: int = 0) -> dict[str, Any]:
    """Poll a founding run. *cursor* is how much narration the Board has seen."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "unknown founding run"}
        narration = job["narration"][cursor:]
        return {
            "ok": True,
            "status": job["status"],
            "narration": narration,
            "cursor": cursor + len(narration),
            "proposal": job["proposal"],
            "error": job["error"],
        }


def cancel(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.pop(job_id, None)
    if job and job.get("proc"):
        job["proc"].kill()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Commit — the hire
# ---------------------------------------------------------------------------

def readiness(root: Path, firm_id: str) -> dict[str, Any]:
    """What a freshly-hired firm still lacks before it should be let loose.

    Hiring is not outfitting. A firm with an org but no charter, no tools, and
    no loadouts will happily burn a pulse producing nothing — so the Board sees
    this list before it sees a heartbeat control.
    """
    ws = (root / firm_id).resolve()
    protocols = ws / ".firm" / "protocols"
    mcp = ws / ".mcp.json"

    servers: list[str] = []
    if mcp.is_file():
        try:
            servers = list((json.loads(mcp.read_text()).get("mcpServers") or {}))
        except (OSError, json.JSONDecodeError):
            servers = []

    loadouts = 0
    try:
        conn = connect(get_db_path(ws))
        try:
            for c in repo.find(conn, "contract", firm_id=firm_id):
                raw = c.get("skill_loadout")
                pack = json.loads(raw) if isinstance(raw, str) else (raw or {})
                if pack.get("skills"):
                    loadouts += 1
        finally:
            conn.close()
    except Exception:
        pass

    # Exactly ONE thing may block a firm from living: its law. A firm with no MCP
    # servers is an ordinary firm — plenty of operators work almost entirely in CLI.
    # A firm with bare loadouts still has the shell and the filesystem. Gating the
    # pulse on those turned "you could have more" into "you may not start", which
    # is a different sentence and the wrong one.
    charter = (ws / "CLAUDE.md").is_file()
    checks = [
        {"key": "charter", "label": "A charter (CLAUDE.md) — the firm's law",
         "ok": charter, "blocking": True,
         "fix": "Train"},
        {"key": "armory", "label": "An armory (.mcp.json) — MCP servers the firm owns",
         "ok": bool(servers), "blocking": False,
         "detail": ", ".join(servers) if servers else "none — CLI and filesystem only",
         "fix": "Equip"},
        {"key": "loadouts", "label": "Loadouts — what each member is permitted to carry",
         "ok": loadouts > 0, "blocking": False,
         "detail": f"{loadouts} contract(s) carry skills" if loadouts else "none assigned",
         "fix": "Train"},
        {"key": "protocols", "label": "Protocols — law injected into every member run",
         "ok": protocols.is_dir() and any(protocols.glob("*.md")), "blocking": False,
         "fix": "Train"},
    ]
    blocking = [c["key"] for c in checks if c["blocking"] and not c["ok"]]
    # The roster rides along so the readiness screen is reachable at any time —
    # not only in the seconds after a commit, while it happens to be in memory.
    name, hired = firm_id, []
    try:
        conn = connect(get_db_path(ws))
        try:
            firm = repo.get(conn, "firm", firm_id)
            name = (firm or {}).get("name") or firm_id
            hired = [{"id": m["id"], "name": m.get("name") or m["id"],
                      "role": m.get("role") or ""}
                     for m in repo.find(conn, "member", firm_id=firm_id)]
        finally:
            conn.close()
    except Exception:
        pass

    return {"ok": True, "firm_id": firm_id, "name": name, "workspace": str(ws),
            "hired": hired, "checks": checks,
            "wired": charter,        # the loadouts + charter have been written once
            "blocking": blocking,
            "ready": not blocking}


def set_pulse(root: Path, firm_id: str, interval: str,
              enable: bool = True) -> dict[str, Any]:
    """Start or stop the firm's pulse. One click, no terminal.

    The Board's vocabulary is PULSE — "heartbeat" is Paperclip's word and must
    not surface anywhere a user can read it. The CLI module still carries the
    old noun internally (``firm.cli.heartbeat``); renaming that public verb is a
    separate, deliberate change. This delegates to it rather than forking the
    logic, capturing its stdout because it prints JSON instead of returning it.
    """
    import contextlib
    import io

    from firm.cli.heartbeat import run_disable, run_enable, validate_interval

    ws = (root / firm_id).resolve()
    if ws.parent != root.resolve():
        return {"ok": False, "error": "unknown firm"}

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            if enable:
                validate_interval(interval)
                run_enable(ws, firm_id, interval)
            else:
                run_disable(firm_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"could not set the heartbeat: {exc}"}

    try:
        return json.loads(buf.getvalue() or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": buf.getvalue().strip()[:200] or "no response"}


def set_manifest(root: Path, firm_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """The Board's Manifest — how this Board runs the floor.

    Manual pulse is never configured here because it is never optional: the CLI
    and the dashboard's own control are always available, to every Board, on
    every firm. Everything else is a choice the Board makes and can unmake.
    """
    from firm.sysconfig import service as sysconfig_svc

    ws = (root / firm_id).resolve()
    if ws.parent != root.resolve() or not get_db_path(ws).exists():
        return {"ok": False, "error": "unknown firm"}

    done: list[str] = []
    conn = connect(get_db_path(ws))
    try:
        notify = manifest.get("notify") or {}
        channel = str(notify.get("channel") or "")
        token = str(notify.get("token") or "")
        target = str(notify.get("target") or "")
        preset = notify.get("preset") or {}
        if preset and not token:
            # One-click reuse: the token value is resolved HERE, from the
            # preset's source on this machine — it never crossed the browser.
            src = preset.get("source") or {}
            token = _preset_token_value(root, str(src.get("kind") or ""),
                                        str(src.get("ref") or ""),
                                        str(preset.get("token_env") or ""))
            if not target:
                target = str(preset.get("target") or "")

        if channel in ("telegram", "slack") and target:
            token_env = ("CADRE_TELEGRAM_TOKEN" if channel == "telegram"
                         else "CADRE_SLACK_TOKEN")
            if token:
                sysconfig_svc.vars_set(conn, firm_id, ws, token_env, token, "firm")
                done.append(f"vault:{token_env}")
            cfg: dict[str, Any] = {"provider": channel, "remind_hours": 24}
            if channel == "telegram":
                cfg["telegram_chat_id"] = target
                cfg["telegram_token_env"] = token_env
            else:
                cfg["slack_user_id"] = target
                cfg["slack_token_env"] = token_env
            conn.execute("UPDATE firm SET notify_config = ? WHERE id = ?",
                         (json.dumps(cfg), firm_id))
            done.append(f"notify:{channel}")

        # Trust posture — full-load firms spawn members without
        # --strict-mcp-config (see firm.pulse.spawn.full_load). File presence
        # IS the setting; the default — no file — is lean: the loadout is the
        # law. Board-made, Board-unmade.
        spawn_cfg = ws / ".firm" / "spawn.json"
        if manifest.get("full"):
            spawn_cfg.write_text(json.dumps({"full": True}, indent=2) + "\n",
                                 encoding="utf-8")
            done.append("spawn:full-load")
        elif spawn_cfg.exists():
            spawn_cfg.unlink()
            done.append("spawn:lean")
        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.close()
        return {"ok": False, "error": f"could not write the manifest: {exc}"}
    finally:
        try:
            conn.close()
        except Exception:
            pass

    cadence = manifest.get("cadence")
    if cadence:
        res = set_pulse(root, firm_id, str(cadence), enable=True)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error") or res.get("reason")
                    or "could not start the pulse", "partial": done}
        done.append(f"pulse:{cadence}")

    return {"ok": True, "firm_id": firm_id, "did": done, "cadence": cadence}


def pulse_state(firm_id: str) -> dict[str, Any]:
    """The firm's current pulse cadence, read from the platform scheduler.

    The manifest UI needs the truth, not session memory — a refreshed page
    must show the real cadence and be able to change it.
    """
    from firm.cli.heartbeat import _UNIT_PREFIX, _sched
    st = _sched().status(f"{_UNIT_PREFIX}{firm_id}")
    if not st.get("installed"):
        return {"ok": True, "enabled": False, "interval": None}
    return {"ok": True, "enabled": True, "interval": st.get("interval")}


def _preset_token_value(root: Path, kind: str, ref: str, key: str) -> str:
    """Resolve a notify preset's token from its source — server-side only.

    kind 'firm' / 'firm-env' reads a sibling firm's vault (then its .env);
    'channel-env' reads the operator's ~/.claude/channels/<ref>/.env.
    """
    if not key:
        return ""
    from firm.sysconfig.service import _parse_env_file, vars_reveal
    try:
        if kind in ("firm", "firm-env"):
            ws = (root / ref).resolve()
            if ws.parent != root.resolve():
                return ""
            try:
                return str(vars_reveal(ws, key)["value"])
            except ValueError:
                return _parse_env_file(ws / ".env").get(key, "")
        if kind == "channel-env":
            path = Path.home() / ".claude" / "channels" / ref / ".env"
            return _parse_env_file(path).get(key, "")
    except OSError:
        return ""
    return ""


def _scaffold_base_graph(workspace: Path) -> bool:
    """Give the newborn firm its BASE workspace — the firm's own memory.

    Every founded firm gets `.base/` (graph, domains.toml, global registry
    entry) so Members have an institutional memory from day one; the charter's
    §6 charges them with using and maintaining it. BASE absent is not an
    error — licensees may not carry it — and a scaffold failure degrades the
    firm, it never aborts a founding.
    """
    from firm.sysconfig.service import which_base
    base = which_base()
    if not base:
        return False
    try:
        # Explicit env, never ambient — a systemd-spawned hub's PATH is bare.
        proc = subprocess.run(
            [base, "scaffold", str(workspace)],
            capture_output=True, text=True, timeout=120,
            env={"HOME": str(Path.home()),
                 "PATH": os.environ.get("PATH") or "/usr/bin:/bin"},
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def commit(root: Path, proposal: dict[str, Any]) -> dict[str, Any]:
    """Scaffold the workspace and write the approved org.

    Everything before this point was a conversation. This is the moment the
    firm exists. Routed through the same ``run_init`` + service layer a
    hand-seeded firm uses, so nothing about this firm's Records betrays that
    it was born in a browser.
    """
    from firm.cli.init import run_init
    from firm.services import contract as contract_svc
    from firm.services import member as member_svc
    from firm.services import operation as operation_svc

    try:
        proposal = _validate(proposal)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    fid = proposal["firm_id"]
    # A firm cannot finish founding without its goal (fork 003: four firms
    # shipped with goal-count zero and north_star null — unfalsifiable by
    # construction). The roster screen guarantees one is present; a commit
    # without one is a bug or a bypass, and both should stop here.
    ns = proposal.get("north_star")
    if not (isinstance(ns, dict) and str(ns.get("target") or "").strip()):
        return {"ok": False, "error": "the firm has no goal — write the north "
                                      "star before hiring; a firm with no "
                                      "number cannot fail, only be busy"}
    root = root.resolve()
    workspace = (root / fid).resolve()
    if workspace.parent != root:   # a firm_id with slashes must not escape the root
        return {"ok": False, "error": "refusing to found a firm outside the firms root"}
    if get_db_path(workspace).exists():
        return {"ok": False, "error": f"a firm already lives at {workspace}"}

    workspace.mkdir(parents=True, exist_ok=True)
    if run_init(workspace, force=False, demo=False, install_hooks_flag=False) != 0:
        return {"ok": False, "error": "could not initialize the firm workspace"}
    base_graph = _scaffold_base_graph(workspace)

    conn = connect(get_db_path(workspace))
    try:
        repo.create(conn, "firm", {
            "id": fid,
            "name": proposal["name"],
            "description": proposal["premise"],
            "north_star": ns["target"],
        })

        # The number, as a Goal row — the denominator every drift verdict,
        # brief, and goal-health banner divides by. Board-authored: the Board
        # read and could edit it on the roster screen, so committing IS the
        # approval. Members propose theirs later via firm_propose_goal.
        from firm.services import goal as goal_svc
        metric: dict[str, Any] = {}
        if ns.get("metric_value") is not None:
            metric["value"] = ns["metric_value"]
        if ns.get("metric_unit"):
            metric["unit"] = ns["metric_unit"]
        goal_svc.create_goal(conn, fid, {
            "target": ns["target"],
            "parent_entity_type": "firm",
            "parent_entity_id": fid,
            "level": "firm",
            **({"metric": metric} if metric else {}),
        })

        # Members before Operations: an Operation names its owner, not the reverse.
        # Lead first — everyone else reports to them, so they must exist to be pointed at.
        lead_id: str | None = None
        by_name: dict[str, str] = {}
        hired: list[dict[str, Any]] = []
        for m in sorted(proposal["members"], key=lambda m: not m["leads"]):
            con = contract_svc.create_contract(conn, fid, {
                "name": f"{m['name']} — {m['role']}",
                "runtime_type": "claude_code",
                "skill_loadout": {"skills": m["skills"]},
                # Trust is earned. A new hire's gates are declared up front and
                # relaxed later — this list is what the Board must sign off on.
                "validation_config": {"gates_required": m["gates"]},
                # Generous on purpose: the orchestrator's 300s fallback kills a
                # first real run mid-work, and a founded firm's first
                # experience must never be a timeout to troubleshoot. The
                # Board tightens this per contract later if they want to.
                # (pulse_config is the key _contract_timeout_sec reads.)
                # `model` is the slate's tier pick, Board-approved on the
                # roster screen; a contract with no model inherits the
                # operator's session default — Opus, at Opus prices.
                "pulse_config": {"timeout_sec": 1800, "model": m["model"]},
            })
            mem = member_svc.create_member(conn, fid, {
                "name": m["name"],
                "role": m["role"],
                "description": m["owns"],
                "contract_id": con["id"],
                "suggested_skills": m["skills"],
                "reports_to_member_id": None if m["leads"] else lead_id,
            }, cwd=str(workspace))
            repo.update(conn, "contract", con["id"], {"member_id": mem["id"]})
            if m["leads"]:
                lead_id = mem["id"]
            by_name[m["name"]] = mem["id"]
            hired.append({"id": mem["id"], "name": m["name"], "role": m["role"]})

        first_op_id: str | None = None
        for op in proposal["operations"]:
            owner = next(
                (m for m in proposal["members"]
                 if m["operation"] == op["name"] and m["leads"]),
                next((m for m in proposal["members"]
                      if m["operation"] == op["name"]), None),
            )
            op_row = operation_svc.create_operation(conn, fid, {
                "name": op["name"],
                "description": op["purpose"],
                "owner_member_id": by_name.get(owner["name"]) if owner else None,
            })
            if first_op_id is None:
                first_op_id = op_row["id"]

        # First units — the reason the first pulse DOES something. Firms used
        # to be born with an empty queue, so the inaugural pulse answered
        # "load=0 (no queued Units)" and the whole ceremony ended in silence.
        if proposal.get("first_units") and first_op_id:
            from datetime import date, timedelta

            from firm.services import project as project_svc
            from firm.services import unit as unit_svc
            proj = project_svc.create_project(conn, fid, {
                "name": "First shift",
                "description": "The founding slate — the work this firm was born holding.",
                "operation_id": first_op_id,
                "owner_member_id": lead_id,
                "due_date": (date.today() + timedelta(days=7)).isoformat(),
            })
            for u in proposal["first_units"]:
                unit_svc.create_unit(conn, fid, {
                    "name": u["name"],
                    "project_id": proj["id"],
                    "description": u.get("why") or "",
                    # An unassigned pending unit is invisible to compute_load —
                    # a name that no longer matches (rename races) falls to the
                    # lead rather than falling out of the firm's working set.
                    "assignee_member_id": by_name.get(u.get("member")) or lead_id,
                    "status": "pending",
                })

        conn.commit()
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": f"the org did not take: {exc}"}
    finally:
        conn.close()

    return {
        "ok": True,
        "firm_id": fid,
        "name": proposal["name"],
        "workspace": str(workspace),
        "hired": hired,
        "base_graph": base_graph,
    }
