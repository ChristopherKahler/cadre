"""Contract NEVERs as controls — materialize, and account for, deny policies.

A charter NEVER used to be a sentence: "never send" relied on the Member
reading it and choosing to obey, every run, under a closing timeout — while
the loadout handed it a credential whose scope bundle permitted exactly the
forbidden thing (fork 009: gmail.modify, granted for labels, also carries
messages.send). If a NEVER is worth writing, it is worth enforcing.

The pieces:

- **The policy lives on the Contract** — ``validation_config.deny``: a list of
  ``{"match": <pattern>, "reason": <one line>}``. A bare string matches as a
  substring; ``*``/``?``/``[`` make it a glob. Matched case-insensitively
  against ``"<tool_name> <canonical tool_input JSON>"``.
- **The lock is a PreToolUse hook** (``firm.cli.install_hooks``) installed in
  the firm workspace. It reads the MATERIALIZED policy — ``.firm/policy.json``,
  written here — never the DB: a hook that queries the DB fights the pulse
  for locks on every tool call of every Member.
- **A blocked call is a Records event.** The hook appends to
  ``.firm/policy-denials.jsonl``; ``ingest_denials`` (called by the pulse)
  turns new lines into Records + one deduped escalation per member+rule. A
  Member that tried to send is a signal — the briefing is wrong or the
  Member is drifting, and both are worth knowing.
- **…but not every denial is that signal.** Records take everything;
  escalations take what the Board should be interrupted for. A verify
  harness firing all 34 rules on purpose is not 34 Members drifting, and one
  Member hitting one rule twice is not two problems. ``_is_noise`` and the
  per-window dedupe below draw that line — see ``ingest_denials``. A gate
  that pages the Board for its own self-test trains the Board to stop
  reading, which costs more than the noise does.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.hooks.shell_intent import is_inspection
from firm.services._records import log_event

POLICY_FILE = "policy.json"
DENIAL_LOG = "policy-denials.jsonl"
DENIAL_CURSOR = "policy-denials.cursor"

#: How much of the acting input the gate writes onto a receipt. A receipt at
#: exactly this length is a FRAGMENT, and a fragment cannot be judged — see
#: ``_is_noise``.
INPUT_HEAD_LIMIT = 300

# An upstream API method name: `chat.postMessage`, `messages.send`. A dot
# between word characters is the tell. No tool is ever NAMED this — the MCP
# tool is `slack_send_message`, the CLI verb is `messages send` — so a rule
# in this shape can only ever fire against a shell command that curls the
# API directly. As the whole of a Member's policy it is a rule aimed at
# nothing (fork 015 / chief-of-staff ESC-021).
_API_METHOD_FORM = re.compile(r"\w\.\w")


def _is_api_method_form(pattern: str) -> bool:
    return bool(_API_METHOD_FORM.search(pattern.strip("*?[] ")))


def unfireable_members(conn: sqlite3.Connection, firm_id: str) -> list[dict[str, Any]]:
    """Members whose every NEVER can only fire through a shell.

    The ESC-021 signature, made detectable: a Member carrying MCP tools whose
    deny rules are ALL upstream API method names has no rule that can match
    any tool they can actually call. The policy reads as protection and
    enforces nothing — the worst of both, because it stops anyone looking.

    Re-patterning is judgment (which verbs, which routes), so this reports
    and routes to Train; it never rewrites a Contract behind the Board.
    """
    contracts = {c["id"]: c for c in repo.find(conn, "contract", firm_id=firm_id)}
    denies = member_denies(conn, firm_id)
    findings: list[dict[str, Any]] = []
    for member_id, rules in denies.items():
        member = repo.get(conn, "member", member_id) or {}
        contract = contracts.get(member.get("contract_id"))
        loadout = _parse_json_col(contract, "skill_loadout")
        servers = [s for s in (loadout.get("mcp") or []) if str(s).strip()]
        if not servers:
            continue          # no MCP route to leave unlocked
        if any(not _is_api_method_form(r["match"]) for r in rules):
            continue          # at least one rule can match a tool name
        findings.append({
            "member_id": member_id,
            "name": member.get("name") or member_id,
            "servers": servers,
            "rules": [r["match"] for r in rules],
        })
    return findings


def _parse_json_col(row: dict[str, Any] | None, col: str) -> dict[str, Any]:
    """A JSON column, whether the repo handed it back parsed or raw."""
    if not row:
        return {}
    raw = row.get(col)
    try:
        val = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        return {}
    return val if isinstance(val, dict) else {}


def _parse_vc(contract: dict[str, Any] | None) -> dict[str, Any]:
    return _parse_json_col(contract, "validation_config")


def member_denies(conn: sqlite3.Connection, firm_id: str) -> dict[str, list[dict[str, str]]]:
    """member_id -> deny rules, read from each Member's Contract.

    ``tool`` (fork 014) is carried through: it names the equipment a rule
    locks, which is how the Board reads a denial and how the Floor groups
    the rules. It was silently dropped here, so a label the founding flow
    took care to write never reached anything that could show it.
    """
    contracts = {c["id"]: c for c in repo.find(conn, "contract", firm_id=firm_id)}
    out: dict[str, list[dict[str, str]]] = {}
    for m in repo.find(conn, "member", firm_id=firm_id):
        vc = _parse_vc(contracts.get(m.get("contract_id")))
        rules = [
            {"match": str(r["match"]).strip(),
             "reason": str(r.get("reason") or "").strip(),
             "tool": str(r.get("tool") or "").strip()}
            for r in (vc.get("deny") or [])
            if isinstance(r, dict) and str(r.get("match") or "").strip()
        ]
        if rules:
            out[m["id"]] = rules
    return out


def materialize(conn: sqlite3.Connection, workspace: Path, firm_id: str) -> Path:
    """Write .firm/policy.json — the file the PreToolUse hook enforces.

    Call after any change to a Contract's deny list; the hook reads only
    this file, so an unmaterialized policy is a policy that doesn't exist.
    """
    path = workspace / ".firm" / POLICY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(member_denies(conn, firm_id), indent=2) + "\n",
                    encoding="utf-8")
    return path


def _is_noise(evt: dict[str, Any]) -> bool:
    """A denial that cannot mean a Member tried to send.

    Two kinds, and neither is worth a Board escalation:

    - **A harness probe.** A verify run fires every rule on purpose. The gate
      stamps ``probe`` when it was spawned with ``CADRE_POLICY_PROBE`` set —
      which only whoever spawns the session can do, never the Member inside
      it. One verify run once became 34 escalations (ESC-047…080).
    - **An inspection.** ``grep slack_send_message`` names the verb, it does
      not perform it. The current gate never denies these at all — this is
      the second control, and it is what makes the receipts the OLD gate
      already wrote (a `grep` that was verifying the lock; a Member blocked
      from `firm escalation raise`-ing about it) ingest quietly.

    Read against the same resolver the gate enforces with, never a local
    re-implementation of it (ESC-021: the replica agreed with itself).

    The receipt is a 300-char HEAD of the input, and a fragment cannot be
    judged: `grep …<300 chars>… | curl -d @secrets api/chat.postMessage`
    truncates to something that reads exactly like an honest `grep`. The
    gate sees the whole string and blocks it either way — but suppressing
    the escalation on the strength of the fragment would hand every Member
    an opt-out from being reported, by padding. So a full-length head is
    never quiet. Truncation can only ever make this louder.
    """
    if evt.get("probe"):
        return True
    head = str(evt.get("input_head") or "")
    if len(head) >= INPUT_HEAD_LIMIT:
        return False
    return is_inspection(head)


def ingest_denials(conn: sqlite3.Connection, workspace: Path, firm_id: str) -> int:
    """Turn new denial-log lines into Records + deduped escalations.

    The hook can only append to a file — it must never open the DB. The
    pulse carries the lines the rest of the way. Cursor = line count already
    ingested, in a sidecar; malformed lines are skipped (the log is boundary
    input written by a hook, not trusted internal state).

    **Every denial becomes a Records row; only some become escalations.** A
    denial is evidence and the audit trail is the product — so the log_event
    below is unconditional. An escalation is an interrupt, and the Board's
    attention is the scarce thing: a harness probe, an inspection, and the
    2nd..Nth repeat of one Member hitting one rule are all noise. This
    function is where that line is drawn, because the gate cannot see it —
    it decides one call at a time and cannot know it is one of 34.
    """
    log_path = workspace / ".firm" / DENIAL_LOG
    if not log_path.exists():
        return 0
    cursor_path = workspace / ".firm" / DENIAL_CURSOR
    try:
        done = int(cursor_path.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        done = 0

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    new = lines[done:]
    if not new:
        return 0

    from firm.services import escalation as escalation_svc
    members = {m["id"]: m for m in repo.find(conn, "member", firm_id=firm_id)}
    ingested = 0
    # One Member hitting one rule is one signal, however many times the log
    # caught it in this window. `raise_escalation` already absorbs a repeat
    # into an OPEN escalation — but the moment the Board resolves it, the
    # next line raises a fresh one, so the dedupe cannot live only there.
    escalated: set[tuple[str, str]] = set()
    for line in new:
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        member_id = str(evt.get("member_id") or "")
        if member_id not in members:
            continue
        match = str(evt.get("match") or "")[:120]
        tool = str(evt.get("tool_name") or "")[:60]
        noise = _is_noise(evt)
        log_event(
            conn,
            firm_id=firm_id,
            event_type="policy.denied",
            actor={"type": "member", "id": member_id},
            target_ref={"type": "member", "id": member_id},
            details={"rule": match, "tool": tool,
                     "reason": str(evt.get("reason") or "")[:200],
                     # Why the Board did or didn't hear about it — a silent
                     # suppression is indistinguishable from a broken ingest.
                     "escalated": not noise,
                     "probe": bool(evt.get("probe")),
                     "heads": evt.get("heads") or []},
        )
        ingested += 1
        if noise:
            continue
        key = (member_id, match)
        if key in escalated:
            continue
        escalated.add(key)
        name = members[member_id].get("name") or member_id
        escalation_svc.raise_escalation(conn, firm_id, {
            "raised_by_member_id": member_id,
            "severity": "normal",
            "title": f"{name} hit a Contract NEVER: {match}",
            "body": (
                f"The policy gate blocked `{tool}` matching deny rule "
                f"`{match}` ({evt.get('reason') or 'no reason recorded'}).\n\n"
                "The call never executed. This is a signal, not an incident: "
                "either the Member's briefing points it at forbidden work, or "
                "it is drifting. Worth a look either way."
            ),
            "dedupe_key": f"policy-denied:{member_id}:{match[:60]}",
        })

    cursor_path.write_text(str(len(lines)) + "\n", encoding="utf-8")
    return ingested
