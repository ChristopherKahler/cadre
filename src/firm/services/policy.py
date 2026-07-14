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
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.services._records import log_event

POLICY_FILE = "policy.json"
DENIAL_LOG = "policy-denials.jsonl"
DENIAL_CURSOR = "policy-denials.cursor"


def _parse_vc(contract: dict[str, Any] | None) -> dict[str, Any]:
    if not contract:
        return {}
    raw = contract.get("validation_config")
    try:
        vc = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        return {}
    return vc if isinstance(vc, dict) else {}


def member_denies(conn: sqlite3.Connection, firm_id: str) -> dict[str, list[dict[str, str]]]:
    """member_id -> deny rules, read from each Member's Contract."""
    contracts = {c["id"]: c for c in repo.find(conn, "contract", firm_id=firm_id)}
    out: dict[str, list[dict[str, str]]] = {}
    for m in repo.find(conn, "member", firm_id=firm_id):
        vc = _parse_vc(contracts.get(m.get("contract_id")))
        rules = [
            {"match": str(r["match"]).strip(), "reason": str(r.get("reason") or "").strip()}
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


def ingest_denials(conn: sqlite3.Connection, workspace: Path, firm_id: str) -> int:
    """Turn new denial-log lines into Records + deduped escalations.

    The hook can only append to a file — it must never open the DB. The
    pulse carries the lines the rest of the way. Cursor = line count already
    ingested, in a sidecar; malformed lines are skipped (the log is boundary
    input written by a hook, not trusted internal state).
    """
    log_path = workspace / ".firm" / DENIAL_LOG
    if not log_path.exists():
        return 0
    cursor_path = workspace / ".firm" / DENIAL_CURSOR
    try:
        done = int(cursor_path.read_text().strip() or 0)
    except (OSError, ValueError):
        done = 0

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    new = lines[done:]
    if not new:
        return 0

    from firm.services import escalation as escalation_svc
    members = {m["id"]: m for m in repo.find(conn, "member", firm_id=firm_id)}
    ingested = 0
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
        log_event(
            conn,
            firm_id=firm_id,
            event_type="policy.denied",
            actor={"type": "member", "id": member_id},
            target_ref={"type": "member", "id": member_id},
            details={"rule": match, "tool": tool,
                     "reason": str(evt.get("reason") or "")[:200]},
        )
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
        ingested += 1

    cursor_path.write_text(str(len(lines)) + "\n", encoding="utf-8")
    return ingested
