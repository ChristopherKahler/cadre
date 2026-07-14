"""The Co-Board briefing — what your Boardroom seat learns about a firm.

The ``/boardroom`` skill knows how to *operate* any Cadre firm: the hub API, the
gate protocol, run forensics, the fix-triage tiers. What it has none of is
**judgment about a particular firm** — what good looks like here, what this Board
actually cares about, what is worth waking them for and what is not.

Today that gap gets filled by cramming firm-specific knowledge into the shared
skill file (``boardroom.md`` carries The Table's whole DB-era architecture
inline). That does not scale past a handful of firms and is nonsense for a
licensee who does not own them.

So the brief is **firm-owned**: ``<workspace>/.firm/boardroom/BRIEF.md``. It
travels with the firm, the Co-Board loads it when scoped to that firm, and it is
a living document — the Board and the Co-Board keep it current together.

Written at founding by an agent that has read everything the firm is: its
premise, its roster, its loadouts, the gaps in its toolchain, and how the Board
said they want the floor run.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.core.db import connect, get_db_path
from firm.dashboard.founding import (
    _FOUNDING_FLAGS,
    _framework_root,
    NARRATION_CONTRACT,
    Narrator,
)
from firm.pulse.spawn import resolve_claude_bin

_TIMEOUT_SEC = 420

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()

BRIEF_REL = Path(".firm") / "boardroom" / "BRIEF.md"


_BRIEF_PROMPT = """\
You are writing the operating brief for a Cadre firm's **Co-Board** — the seat a
human Board member sits beside when they govern this firm from a terminal, Slack,
or Telegram.

The Co-Board already knows how Cadre works: the hub API, gate and escalation
protocol, run forensics, spend triage. It knows none of that is enough. What it
does not know is **this firm** — what good looks like here, what this Board cares
about, what is worth an interruption and what is beneath one.

Write what it needs.

## The firm

**{name}** — {premise}

## The floor

{roster}

## What each of them carries

{loadouts}

## Known gaps in the toolchain

{gaps}

## How the Board said they want the floor run

- Pulse: {cadence}
- Reached by: {channel}
- In their own words: {voice}

## What to write

A markdown document. These sections, in this order, with these exact H2 headings:

## What this firm is for
Two or three sentences. Not the premise restated — the *point*. What changes in
the Board's life if this firm works. What it means for this firm to be winning
versus merely busy.

## The Board
Who they are to this firm and what they actually care about. Their bar for
quality. What they will always want to decide themselves, and what they would be
annoyed to be asked about. Infer this honestly from what they said and from what
they chose to gate — do not flatter them and do not invent traits.

## The floor
Who to commission for what kind of work, and who NOT to. Where the seams between
Members are, and which handoffs are the fragile ones. If two Members could both
plausibly take a piece of work, say who gets it and why.

## What good looks like here
Concrete. What a strong week of output from this firm actually contains. What a
weak one looks like, so the Co-Board can spot the difference before the Board does.

## When to interrupt
The hardest section and the most valuable. What genuinely warrants reaching the
Board, and what the Co-Board should handle, park, or simply report at the next
brief. A Co-Board that escalates everything is a pager; one that escalates nothing
is a liability. Draw the line for THIS firm.

## Known weak points
The toolchain gaps above, in operating terms: what will fail, how it will present,
and what the Co-Board should do when it does. Plus any structural fragility you can
see in the org itself — a Member with too much surface, a dependency with no backup.

## Standing notes
Leave this section with a single line: `_Nothing yet — the Board and the Co-Board
keep this current._` It is the living part of the document; it fills up over time.

## Rules

- Write to be *used mid-decision*, not read once. Short paragraphs. No preamble.
- Be specific to this firm. A sentence that would be true of any firm is wasted.
- Loadouts and deny rules drift after founding — the Board can equip and
  unequip from the dashboard. Treat the loadout facts above as founding-day
  truth and say so where it matters; the live read is `/f/<firm>/api/floor`.
- Do not invent facts about the Board or the business. Where you are inferring,
  the inference must be visibly grounded in something above.
- No praise, no filler, no "in today's fast-paced world." Operator to operator.
- After your narration, output the markdown document and nothing else. No fence.
"""


def _finish(job_id: str, *, brief: str | None = None, error: str | None = None) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job["proc"] = None
        job["status"] = "ready" if brief else "failed"
        job["brief"] = brief
        job["error"] = error


def _context(workspace: Path, firm_id: str) -> dict[str, Any]:
    conn = connect(get_db_path(workspace))
    try:
        firm = repo.get(conn, "firm", firm_id) or {}
        contracts = {c["id"]: c for c in repo.find(conn, "contract", firm_id=firm_id)}
        members = []
        for m in repo.find(conn, "member", firm_id=firm_id):
            c = contracts.get(m.get("contract_id")) or {}
            raw = c.get("skill_loadout")
            lo = json.loads(raw) if isinstance(raw, str) else (raw or {})
            vraw = c.get("validation_config")
            vc = json.loads(vraw) if isinstance(vraw, str) else (vraw or {})
            members.append({
                "name": m.get("name") or m["id"],
                "role": m.get("role") or "",
                "owns": m.get("description") or "",
                "leads": not m.get("reports_to_member_id"),
                "loadout": lo,
                "gates": vc.get("gates_required") or [],
            })
    finally:
        conn.close()
    return {"firm": firm, "members": members}


def _fmt_roster(members: list[dict[str, Any]]) -> str:
    out = []
    for m in members:
        lead = " **(leads)**" if m["leads"] else ""
        out.append(f"- **{m['name']}** — {m['role']}{lead}. Owns: {m['owns']}")
        if m["gates"]:
            out.append(f"  - Must ask the Board before: {'; '.join(m['gates'])}")
    return "\n".join(out) or "- (none)"


def _fmt_loadouts(members: list[dict[str, Any]]) -> str:
    out = []
    for m in members:
        lo = m["loadout"]
        bits = []
        for key, label in (("skills", "skills"), ("commands", "commands"),
                           ("mcp", "servers"), ("cli", "CLI tools")):
            if lo.get(key):
                bits.append(f"{label}: {', '.join(lo[key])}")
        for k in lo.get("knowledge") or []:
            if isinstance(k, dict):
                bits.append(f"knows {Path(str(k.get('path') or '')).name}"
                            f" — {k.get('teaches', '')}")
            elif k:
                bits.append(f"knows {k}")
        out.append(f"- **{m['name']}**: " + ("; ".join(bits) if bits
                                             else "nothing beyond the firm's standing tools"))
    return "\n".join(out) or "- (none)"


def _run_brief(job_id: str, workspace: Path, firm_id: str,
               extras: dict[str, Any]) -> None:
    claude_bin, detail = resolve_claude_bin()
    if not claude_bin:
        _finish(job_id, error=f"claude runtime not wired: {detail}")
        return

    ctx = _context(workspace, firm_id)
    firm, members = ctx["firm"], ctx["members"]

    gaps = extras.get("gaps") or []
    gaps_txt = "\n".join(
        f"- **{g.get('member','')}** lacks {g.get('missing','')} — recommended: "
        f"{g.get('recommend','')} ({g.get('why','')})"
        for g in gaps
    ) or "- None identified. The firm is fully equipped for the work it was hired to do."

    prompt = _BRIEF_PROMPT.format(
        name=firm.get("name") or firm_id,
        premise=firm.get("description") or "",
        roster=_fmt_roster(members),
        loadouts=_fmt_loadouts(members),
        gaps=gaps_txt,
        cadence=extras.get("cadence") or "manual only — the Board wakes it by hand",
        channel=extras.get("channel") or "nothing — the Board checks the boardroom themselves",
        voice=(extras.get("voice") or "").strip() or "(they said nothing further)",
    )

    argv = [claude_bin, *_FOUNDING_FLAGS, "-p", prompt + NARRATION_CONTRACT]
    env = dict(os.environ)
    env.pop("CADRE_DB_URL", None)
    env.pop("CADRE_DB_TOKEN", None)

    try:
        proc = subprocess.Popen(argv, cwd=str(_framework_root()),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, text=True)
    except OSError as exc:
        _finish(job_id, error=f"could not spawn the briefing agent: {exc}")
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
        _finish(job_id, error="The briefing agent took too long.")
        return
    except Exception as exc:
        _finish(job_id, error=str(exc))
        return

    if not final:
        err = (proc.stderr.read() if proc.stderr else "").strip()
        tail = err.splitlines()[-1][:200] if err else f"exit code {proc.returncode}"
        _finish(job_id, error=f"The briefing agent returned nothing — {tail}")
        return

    body = final.strip()
    fence = re.match(r"^```(?:markdown|md)?\s*(.*?)\s*```$", body, re.S)
    if fence:
        body = fence.group(1).strip()

    # The narration (`· ` lines) rides in front of the document now. The brief starts
    # at its first heading; anything before that was the agent thinking out loud.
    head = body.find("## What this firm is for")
    if head < 0:
        _finish(job_id, error=(
            "The brief came back without its operating sections. "
            f"It started: {body[:120]!r}"))
        return
    _finish(job_id, brief=body[head:].strip())


def start(workspace: Path, firm_id: str, extras: dict[str, Any]) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"status": "thinking", "narration": ["Reading everything this firm is…"], "brief": None,
                         "error": None, "proc": None}
    threading.Thread(target=_run_brief, args=(job_id, workspace, firm_id, extras),
                     daemon=True).start()
    return {"ok": True, "job_id": job_id}


def status(job_id: str, cursor: int = 0) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "unknown briefing run"}
        narration = job["narration"][cursor:]
        return {"ok": True, "status": job["status"], "narration": narration,
                "cursor": cursor + len(narration),
                "brief": job["brief"], "error": job["error"]}


def commit(root: Path, firm_id: str, brief: str) -> dict[str, Any]:
    """Write the brief into the firm. The Co-Board picks it up from there."""
    workspace = (root / firm_id).resolve()
    if workspace.parent != root.resolve() or not get_db_path(workspace).exists():
        return {"ok": False, "error": "unknown firm"}
    if "## What this firm is for" not in (brief or ""):
        return {"ok": False, "error": "that brief is missing its operating sections"}

    target = workspace / BRIEF_REL
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(brief.rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"could not write the brief: {exc}"}

    return {"ok": True, "firm_id": firm_id, "brief_path": str(target)}


def briefed_firms(root: Path) -> list[str]:
    """Which firms the Co-Board has been briefed on. The real level-up number."""
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and (d / BRIEF_REL).is_file()
    )
