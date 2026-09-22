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
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.core.db import connect, get_db_path
from firm.core.proc import popen_utf8
from firm.pulse.spawn import resolve_claude_bin
from firm.services.base_domain import session_spawn

# Mirrors the hardened Member spawn flags (firm/pulse/spawn.py). --strict-mcp-config
# with NO --mcp-config is load-bearing and deliberate: strict means the run gets
# exactly the servers named in an explicit config, and naming none gives it none.
# Without strict, a headless run under --dangerously-skip-permissions inherits the
# operator's entire personal MCP fleet (387 tools incl. Gmail/Slack/Drive, measured
# 2026-07-10). A founding agent needs to read two docs and write JSON. It gets files.
# What the firm-founding agents run on. Not in the Member vocabulary by
# default — Board 2026-09-09: fable is wired in and selectable, opus is the
# working ceiling for Members. Must stay above _FOUNDING_FLAGS, which
# reads it at import time.
_TOP_TIER = "opus"

_FOUNDING_FLAGS = [
    "--print",
    # Board ruling 2026-07-13: the firm only gets created once — architect it
    # on the top tier at max effort, never the operator's session default. The
    # stage choreography absorbs the latency; quality is the point. Shared by
    # the wiring and Co-Board briefing agents (they import these flags).
    # Alias, never a pinned id: a pin goes stale silently the next time
    # Anthropic ships a tier (it sat on claude-opus-4-8 through Opus 5).
    "--model", _TOP_TIER,
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

# #135: `commit` and `_validate` live in `services/founding.py` now, so the
# CLI can reach the one founding path without importing the dashboard. The
# names are bound HERE, into this module's globals, and that spelling is
# load-bearing rather than a matter of taste: `_run_founding` below resolves
# `_validate` as a global of this module at call time, and
# `tests/test_child_output_is_decoded_as_utf8.py` patches it by that name. A
# re-export that held the module instead and called `_svc._validate(...)`
# would leave that patch pointing at nothing and the test green over an
# unpatched run.
from firm.services.founding import (  # noqa: F401  (re-exported on purpose)
    _DEFAULT_MODEL,
    _FIRM_ID_RE,
    _MODEL_TIERS,
    _validate,
    commit,
)
_TIMEOUT_SEC = 300

# The tiers a Member may run on, cheapest last. CLI aliases, not pinned ids —
# an alias tracks the account's current model of that tier, so a founded firm
# upgrades with the account instead of fossilizing on a version string.
# (Board ruling 2026-07-14: Cooper's single run — 30 min, $13.99, 7.6M cache
# reads — because no contract set a model and all four Members inherited Opus.
# The founding slate now staffs the model like it staffs the org.)
# Aliases only. Claude Code resolves each to the current model in that tier,
# so a new release re-points them with no edit here.

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
      "model": "opus, sonnet, or haiku — the Claude tier this Member runs on (fable exists above opus; do not use it unless the Board asks)",
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
    """Repo root — where the house docs and the framework tree are READ from.

    It is not where the founding agent runs, and the sentence that said so was
    wrong even before #143 moved that working directory: `_house_rules()` reads
    both documents through `root / rel`, an absolute path, and inlines their
    TEXT into the prompt, so the agent is never handed a path to open. The old
    sentence is what made the tier fix look as though it would break doc
    resolution, and it put a wrong fact into a design document before anyone
    read the code it described.
    """
    return Path(__file__).resolve().parents[3]


def _scratch_session(job_id: str) -> tuple[str, str]:
    """A tier of founding's own, recorded on the job so `_finish` removes it.

    Founding and reshuffle run BEFORE there is a firm, so there is no firm
    workspace to isolate them into and the shape the other two spawn sites use
    does not apply (#143, G0 ruling R-2). They get a scratch home under TEMP
    instead: base's global tier is that directory and the working directory is
    the one path base returns without walking, so nothing in the session climbs
    out into the operator's own workspace.

    THE `cadre-founding-` PREFIX IS LOAD-BEARING, not decoration. The leg that
    proves this (R11) passed twice before it was keyed on this string — once
    off an ambient `BASE_HOME`, once off the suite's own TEMP fence — because
    both of those are also "a BASE_HOME under TEMP". A name only this function
    can produce is what makes the assertion be about the fix.
    """
    scratch = tempfile.mkdtemp(prefix="cadre-founding-")
    cwd, base_home = session_spawn(home=scratch)
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["scratch_tier"] = scratch
    return cwd, base_home


def _remove_scratch_tier(scratch: Path) -> tuple[bool, str]:
    """Best effort, and it SAYS which it was. #143, leg R11.

    A scratch tier left behind is a directory of graph data in TEMP after every
    founding run. Removing it can genuinely fail — on Windows base's own hook
    can still hold a file open for a moment after the session ends — so the
    outcome is recorded rather than assumed. `tier_removed: false` with the
    reason is a true job record; a silent failure is a directory nobody knows
    about.
    """
    try:
        shutil.rmtree(scratch)
    except OSError as exc:
        return (not scratch.exists()), str(exc)
    return True, ""


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
    cwd, env["BASE_HOME"] = _scratch_session(job_id)

    try:
        proc = popen_utf8(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
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
        scratch = job.get("scratch_tier")
    if not scratch:
        return
    # OUTSIDE THE LOCK, deliberately. Removing a directory tree is filesystem
    # work and the hub answers `status` from this same lock on another thread.
    removed, why = _remove_scratch_tier(Path(scratch))
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job["tier_removed"] = removed
        if not removed:
            job["tier_removed_error"] = why


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
    # #143: a proposal is not a firm either, so reshuffle gets a scratch tier
    # of its own rather than borrowing one.
    cwd, env["BASE_HOME"] = _scratch_session(job_id)

    try:
        proc = popen_utf8(argv, cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env)
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
    # CANCEL REMOVES THE SCRATCH TIER TOO (#143). This pops the job record, so
    # `_finish` finds nothing and its cleanup never runs — a cancelled founding
    # run is the one path on which the tier would stay in TEMP for good.
    if job and job.get("scratch_tier"):
        _remove_scratch_tier(Path(job["scratch_tier"]))
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
            servers = list((json.loads(mcp.read_text(encoding="utf-8")).get("mcpServers") or {}))
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
    # Read-only, every time this screen is drawn. `check` installs nothing: a
    # screen that repairs the machine while rendering makes it impossible to
    # say afterwards what the machine looked like before anybody looked at it.
    # The repair belongs to `commit`, which is where founding happens.
    from firm.services import base_ready
    base_state = base_ready.check(ws)
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
        # Both non-blocking. A firm with no base is an ordinary firm -- a
        # licensee may not carry base at all -- which is the same ruling this
        # screen already makes about MCP servers. What is not acceptable is
        # silence: an absent base, or a `base cadre` that does not run, is
        # named here rather than left for the Board to discover mid-run (#118).
        {"key": "base", "label": "base — where the firm keeps what it learns",
         "ok": bool(base_state.get("base_present") and base_state.get("base_runs")),
         "blocking": False,
         "detail": (f"base at {base_state.get('base_path')}"
                    if base_state.get("base_runs")
                    else str(base_state.get("reason") or "not found")),
         "fix": "Equip"},
        # The row follows check's OWN verdict, never `extension_runs` alone.
        # `base cadre` exits 0 for a base that resolves the command from outside
        # the firm's tier, with no manifest in it, and this row used to read
        # "installed and runs" over exactly that (PR 127 G2, F2).
        {"key": "base_cadre", "label": "base cadre — the command every Member runs",
         "ok": bool(base_state.get("ok")),
         "blocking": False,
         "detail": ("the cadre extension is installed and runs"
                    if base_state.get("ok")
                    else str(base_state.get("reason") or "not installed")),
         "fix": "Equip"},
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


