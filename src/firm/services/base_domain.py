"""The firm's BASE domain — the wire that makes the firm's own graph arrive.

``base scaffold`` writes ``.base/domains.toml`` as an all-commented template.
With no domain block the UserPromptSubmit hook has nothing to match, so a
Member receives only the operator's global-tier rules and the firm's own graph
reaches it ONLY if the Member thinks to run ``base recall`` -- the tool call the
whole mechanism exists to remove.

Measured on chief-of-staff, 2026-09-09, same prompt and workspace: 2513 bytes
injected with the shipped template, 5988 with a real block, the difference
being a ``[<firm> CONTEXT]`` section carrying the firm's own decisions.

The block is DERIVED, never hand-maintained: its triggers are the firm id, the
firm name, and every active Member's name and role. Rebuild it whenever the
roster changes and it grows with the firm. ``sync`` is idempotent and rewrites
in place between its markers, so it is safe to call on every roster change, from
``firm doctor --fix``, and from the Boardroom while the Board is standing in the
firm.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

BEGIN = "# >>> cadre:base-domain (generated — edit prompt_keywords freely, the"
BEGIN2 = "# rest is rebuilt from the roster on every sync) >>>"
END = "# <<< cadre:base-domain <<<"


def _roster_words(conn: Any, firm_id: str) -> list[str]:
    """Firm name plus every active Member's name and role, in roster order."""
    words: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in words:
            words.append(text)

    add(firm_id)
    try:
        row = conn.execute("select name from firm where id = ?",
                           (firm_id,)).fetchone()
        if row:
            add(row[0])
    except Exception:
        pass
    try:
        for name, role, status in conn.execute(
                "select name, role, status from member order by id"):
            if str(status or "active").lower() not in ("retired", "archived"):
                add(name)
                add(role)
    except Exception:
        pass
    return words


def _proposal_words(firm_id: str, proposal: dict[str, Any]) -> list[str]:
    """Same shape, for founding — the DB has no roster yet at that point."""
    words: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in words:
            words.append(text)

    add(firm_id)
    add(proposal.get("name"))
    for member in (proposal.get("members") or []):
        add(member.get("name"))
        add(member.get("role"))
    return words


def render(workspace: Path, firm_id: str, words: list[str]) -> str:
    """The generated block, markers included."""
    lines = [BEGIN, BEGIN2, "[[domain]]", 'name = "' + firm_id + '"',
             'mode = "triggered"', "prompt_keywords = ["]
    lines += ['    "' + w.replace('"', "'") + '",' for w in words]
    lines += ["]", "file_keywords = []",
              'paths = ["' + workspace.as_posix() + '"]',
              "exclude = []", "rules = []", "commands = []",
              'command_activation = "both"', END]
    return chr(10).join(lines)


def _strip(body: str, firm_id: str = "") -> str:
    """Remove every block this function owns, leaving hand-written content.

    That means the marker-delimited block AND any bare ``[[domain]]`` whose
    name is the firm id. Two domains sharing a name make base match NEITHER --
    measured on chief-of-staff 2026-09-09, injection fell from 5988 bytes back
    to 2513 the moment a generated block joined a hand-written one. Owning the
    name outright is the only way sync can be safely re-run.
    """
    while BEGIN in body:
        head, _, rest = body.partition(BEGIN)
        _, _, tail = rest.partition(END)
        body = head.rstrip() + tail
    if not firm_id:
        return body
    target = 'name = "' + firm_id + '"'
    out: list[str] = []
    block: list[str] = []
    in_block = False
    for line in body.splitlines():
        if line.strip() == "[[domain]]":
            if in_block and target not in chr(10).join(block):
                out.extend(block)
            block, in_block = [line], True
            continue
        if in_block:
            block.append(line)
            continue
        out.append(line)
    if in_block and target not in chr(10).join(block):
        out.extend(block)
    return chr(10).join(out)


def sync(workspace: Path, firm_id: str, *, conn: Any = None,
         proposal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write or refresh the firm's domain block. Never raises.

    Pass ``conn`` to derive triggers from the live roster, or ``proposal`` at
    founding when there is no roster yet. Returns a result dict; ``ok`` False
    means the firm has no BASE tier, which is not an error -- licensees may not
    carry BASE, and a firm without it is degraded, never broken.
    """
    path = Path(workspace) / ".base" / "domains.toml"
    if not path.exists():
        return {"ok": False, "reason": "no .base/domains.toml — BASE not scaffolded",
                "changed": False}
    words = (_proposal_words(firm_id, proposal) if proposal is not None
             else _roster_words(conn, firm_id) if conn is not None else [firm_id])
    try:
        body = path.read_text(encoding="utf-8")
        kept = _strip(body, firm_id)
        block = render(Path(workspace), firm_id, words)
        updated = kept.rstrip() + chr(10) * 2 + block + chr(10)
        changed = updated != body
        if changed:
            path.write_text(updated, encoding="utf-8")
        seeded = _seed_rule(Path(workspace), firm_id)
        return {"ok": True, "changed": changed, "keywords": words,
                "rule_seeded": seeded, "path": str(path)}
    except OSError as exc:
        return {"ok": False, "reason": str(exc), "changed": False}


def is_current(workspace: Path, firm_id: str, conn: Any) -> tuple[bool, str]:
    """(current, detail) — does the on-disk block match the live roster.

    Used by ``firm doctor``; the finding is mechanical, so ``--fix`` repairs it.
    """
    path = Path(workspace) / ".base" / "domains.toml"
    if not path.exists():
        return True, "no BASE tier on this firm — nothing to wire"
    try:
        body = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, str(exc)
    if BEGIN not in body:
        return False, ("the firm's graph reaches no Member — domains.toml has "
                       "no domain block, so base injects nothing firm-specific")
    # Two [[domain]] entries sharing a name make base match NEITHER, so the
    # marker block being perfect is not sufficient: count every block carrying
    # this firm's name. Measured on chief-of-staff 2026-09-09.
    named = body.count('name = "' + firm_id + '"')
    if named > 1:
        return False, (f"{named} domain blocks are named {firm_id!r} — base "
                       "matches none of them while the name is duplicated")
    want = render(Path(workspace), firm_id, _roster_words(conn, firm_id))
    head, _, rest = body.partition(BEGIN)
    have = BEGIN + rest.partition(END)[0] + END
    if have.strip() != want.strip():
        return False, ("the domain block is stale against the roster — "
                       "Members are missing from its triggers")
    # A perfect block is not a live wire. base drops a MATCHED domain that
    # carries zero rules, silently and totally, so a firm can pass every
    # structural check above and still reach no Member. Measured on
    # chief-of-staff 2026-09-09 and again by godwit 2026-09-10 against base
    # 0.15.0. This check is the reason the finding stops being invisible.
    count = rule_count(Path(workspace), firm_id)
    if count is None:
        return True, ("domain block matches the roster; rule count unread "
                      "(base absent or its output unrecognised)")
    if count == 0:
        return False, ("the firm's domain carries no rules, so base drops the "
                       "whole block — the block is perfect and injects nothing")
    return True, f"domain block matches the roster, {count} rule(s) live"


_SEED_RULE = ("Members of this firm read the firm's own graph before acting and "
              "write what they learn back to it, so the next run starts where "
              "this one finished.")


def _base_env() -> dict[str, str]:
    """Explicit env for every `base` call — a systemd-spawned hub has a bare PATH.

    BASE_HOME is deliberately carried through when the caller set it: a test
    harness pointing base at a scratch tier must not have Cadre silently
    re-resolve the operator's real one.
    """
    env = {"HOME": str(Path.home()),
           "PATH": os.environ.get("PATH") or "/usr/bin:/bin"}
    for passthrough in ("BASE_HOME", "XDG_CONFIG_HOME"):
        value = os.environ.get(passthrough)
        if value:
            env[passthrough] = value
    return env


# `base rule list` exits 0 for a populated domain, an empty one, AND a domain
# that never existed (measured, base 0.15.0), so the exit code discriminates
# nothing and the output is the only signal. Anchor on the POSITIVE shape --
# "[<domain>] N rules across both tiers:" -- rather than on the absence of the
# empty-case sentence: an absence test silently reads "has rules" the day base
# rewords that sentence, and it fails toward the clean answer.
_COUNT_RE = re.compile(r"^\[(?P<domain>[^\]]+)\]\s+(?P<n>\d+)\s+rules\b",
                       re.MULTILINE)
_EMPTY_RE = re.compile(r"^No rules for domain\b", re.MULTILINE)


def rule_count(workspace: Path, firm_id: str) -> int | None:
    """How many rules the firm's base domain carries.

    ``None`` is NOT zero and must never be rendered as one: it means base is
    absent, timed out, or printed something this function does not recognise.
    A machine without base has no ruleless domain to report, and reporting one
    would fail a firm for the operator's install. Absent, empty and zero are
    three different answers (honesty envelope).
    """
    import subprocess
    from firm.sysconfig.service import which_base
    base = which_base()
    if not base:
        return None
    try:
        listed = subprocess.run(
            [base, "rule", "list", "--domain", firm_id],
            capture_output=True, text=True, timeout=60,
            cwd=str(workspace), env=_base_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode != 0:
        return None
    match = _COUNT_RE.search(listed.stdout or "")
    if match and match.group("domain") == firm_id:
        return int(match.group("n"))
    if _EMPTY_RE.search(listed.stdout or ""):
        return 0
    return None


def _seed_rule(workspace: Path, firm_id: str) -> bool:
    """Ensure the firm's domain carries at least one rule.

    A matched domain with an empty rule set is dropped from injection whatever
    its graph holds, so a firm can have a perfect domain block, a full graph,
    and still reach no Member. Seeding one rule is what turns the block into a
    live wire. Idempotent: `base rule add` is only called when the domain has
    no rules yet. Never raises -- BASE may not be installed at all.
    """
    import subprocess
    from firm.sysconfig.service import which_base
    base = which_base()
    if not base:
        return False
    count = rule_count(workspace, firm_id)
    if count is None:
        return False              # cannot read the domain; do not claim a seed
    if count > 0:
        return True               # already has rules; leave them alone
    try:
        added = subprocess.run(
            [base, "rule", "add", "--domain", firm_id, "--text", _SEED_RULE],
            capture_output=True, text=True, timeout=60,
            cwd=str(workspace), env=_base_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if added.returncode != 0:
        return False
    # Read back. `base rule add` returning 0 is the writer's own opinion; the
    # only thing that proves the rule landed is asking for it again.
    return (rule_count(workspace, firm_id) or 0) > 0
