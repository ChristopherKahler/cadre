"""Credential preflight — the firm proves it can see before it spawns.

A Member whose tool quietly 401s does the honest thing, works around the
missing data, and ships a deliverable that looks complete and covers nothing.
The pulse reports ``ran: 1, errors: 0`` and the dashboard stays green — the
operator finds out from a human, weeks later (fork 007: a Testing-status OAuth
app whose refresh tokens die every 7 days would have produced exactly this).

So, before Members spawn, every credentialed CLI surface named in the firm's
loadouts is probed with the same cheap read-only identity call the discovery
survey uses. A dead surface raises ONE deduped escalation and blocks exactly
the Members who carry it — a Member that cannot read its inputs must say so
instead of producing output. Members whose loadouts don't touch the dead tool
still run; blindness is per-surface, not firm-wide.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from typing import Any

from firm.core import repo


def _absent_reason(name: str) -> str:
    """Resolution failures name the PATH they failed against. "not installed"
    once sent the Board hunting a missing binary that sat in ~/.local/bin the
    whole time (fork 014) — the lie was the environment, not the machine."""
    return (f"`{name}` did not resolve on this process's PATH — searched: "
            + (os.environ.get("PATH") or "(empty PATH)"))


def _loadout_clis(contract: dict[str, Any] | None) -> list[str]:
    if not contract:
        return []
    raw = contract.get("skill_loadout")
    try:
        lo = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(c) for c in (lo.get("cli") or [])] if isinstance(lo, dict) else []


def firm_cli_map(conn: sqlite3.Connection, firm_id: str) -> dict[str, list[str]]:
    """member_id -> the CLI tools their Contract sanctions."""
    contracts = {c["id"]: c for c in repo.find(conn, "contract", firm_id=firm_id)}
    out: dict[str, list[str]] = {}
    for m in repo.find(conn, "member", firm_id=firm_id):
        out[m["id"]] = _loadout_clis(contracts.get(m.get("contract_id")))
    return out


def dead_tools(conn: sqlite3.Connection, firm_id: str) -> dict[str, str]:
    """Probe every loadout-named CLI; return {tool: why} for the dead ones.

    Probes ride the discovery survey (cached, concurrent, read-only). A tool
    with no verify probe defined can only die by uninstall; presence is all
    the preflight can honestly assert about it.
    """
    named: set[str] = set()
    for clis in firm_cli_map(conn, firm_id).values():
        named.update(clis)
    if not named:
        return {}

    from firm.dashboard.discovery import cli_survey
    surveyed = {c["name"]: c for c in cli_survey()}

    dead: dict[str, str] = {}
    for name in sorted(named):
        c = surveyed.get(name)
        if c is None:
            # Not in the probe catalog — an operator wrapper, a custom CLI
            # (fork 014: gws-acct, the governed door of fork 013). Unknown is
            # NOT absent: we cannot verify its account, but we can answer the
            # one question the preflight may honestly ask about it — does it
            # resolve on PATH? Fail closed only on genuine absence.
            if shutil.which(name) is None:
                dead[name] = _absent_reason(name)
            continue
        if not c["present"]:
            dead[name] = _absent_reason(name)
        elif c["live"] is False:
            dead[name] = "installed but not signed in — the identity probe failed"
    return dead


def block_blind_members(
    conn: sqlite3.Connection,
    firm_id: str,
    members: list[dict[str, Any]],
    dead: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split *members* into (sighted, blocked-with-reason) against *dead*."""
    cli_map = firm_cli_map(conn, firm_id)
    sighted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for m in members:
        hit = [t for t in cli_map.get(m["id"], []) if t in dead]
        if hit:
            blocked.append({
                "member": m,
                "reason": ("credential preflight failed: "
                           + "; ".join(f"{t} — {dead[t]}" for t in hit)),
            })
        else:
            sighted.append(m)
    return sighted, blocked


def _fix_for(why: str) -> str:
    """The CORRECT remediation for a dead surface — never a blanket 're-login'.

    A PATH miss and a dead credential need opposite fixes; conflating them
    (ESC-015: a PATH problem told to 'usually a re-login') sent the operator
    down the wrong road for days while the tool sat installed in ~/.local/bin.
    The ``why`` string already encodes the cause; route the fix on it.
    """
    w = why.lower()
    if "not signed in" in w or "identity probe failed" in w:
        return ("Fix — CREDENTIAL: the tool is installed and on PATH but its "
                "account is dead. Re-authenticate it (usually a re-login); no "
                "PATH or install work is needed.")
    if "did not resolve" in w or "path" in w or "not installed" in w:
        return ("Fix — ENVIRONMENT, not a credential (this is NOT a login "
                "problem): the tool did not resolve on the pulse's PATH. Confirm "
                "it is installed (commonly ~/.local/bin) and reachable on the "
                "pulse PATH. The pulse dispatch now carries a full PATH, so a "
                "stale systemd --user env after a host restart is the culprit.")
    return "Fix: resolve the surface named above, then re-pulse."


def raise_escalations(
    conn: sqlite3.Connection, firm_id: str, dead: dict[str, str],
) -> None:
    """One deduped escalation per dead surface, raised by the lead.

    Deduped on the tool name: a dead credential stays one escalation no
    matter how many pulses trip over it. The body names the ACCURATE fix for
    the cause (env/install vs re-login) — see ``_fix_for``.
    """
    from firm.services import escalation as escalation_svc

    members = repo.find(conn, "member", firm_id=firm_id)
    lead = next((m for m in members if not m.get("reports_to_member_id")),
                members[0] if members else None)
    if lead is None:
        return
    for tool, why in dead.items():
        escalation_svc.raise_escalation(conn, firm_id, {
            "raised_by_member_id": lead["id"],
            "severity": "high",
            "title": f"The firm has gone blind: {tool} is {why}",
            "body": (
                f"The pulse preflight probed `{tool}` before spawning and it "
                f"failed: {why}. Every Member whose loadout carries it was held "
                "back from running — they cannot read their inputs, and a run "
                "without inputs produces confident, empty output.\n\n"
                f"{_fix_for(why)}\n\n"
                "No Member was spawned blind; the next pulse clears this on its "
                "own once resolved."
            ),
            "dedupe_key": f"preflight:{tool}",
        })
