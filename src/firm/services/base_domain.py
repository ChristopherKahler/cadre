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
from enum import Enum
from pathlib import Path
from typing import Any

from firm.core.proc import NoOutput, run_utf8

BEGIN = "# >>> cadre:base-domain (generated — edit prompt_keywords freely, the"
BEGIN2 = "# rest is rebuilt from the roster on every sync) >>>"
END = "# <<< cadre:base-domain <<<"


class Verdict(Enum):
    """What ``assess`` was able to establish about the firm's domain block.

    Three outcomes, because there are three situations and a bool holds two.
    ``is_current`` returned ``tuple[bool, str]``, so "the rule count could not
    be read at all" had nowhere to go and was answered True -- unreadable
    presented as healthy, and ``firm doctor`` printed a tick over it (#62).

    The distinction that matters is not stale-vs-current, it is
    ESTABLISHED-vs-NOT. CURRENT and STALE are both findings of fact.
    UNDETERMINABLE is the absence of one, and it is a first-class value here so
    that a caller can act on it without substring-matching a message.
    """

    CURRENT = "current"
    STALE = "stale"
    UNDETERMINABLE = "undeterminable"


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
        seeded, why = _seed_rule_detail(Path(workspace), firm_id)
        return {"ok": True, "changed": changed, "keywords": words,
                "rule_seeded": seeded, "rule_reason": why, "path": str(path)}
    except OSError as exc:
        return {"ok": False, "reason": str(exc), "changed": False}


def assess(workspace: Path, firm_id: str, conn: Any) -> tuple[Verdict, str]:
    """(verdict, detail) -- what could be established about the domain block.

    Replaces ``is_current``, and the rename is load-bearing rather than
    cosmetic. Widening the return type while keeping the name would have left
    every call site that did not move both COMPILING and PASSING, because every
    ``Enum`` member is truthy: ``doctor`` would have put the verdict straight in
    as a check's pass/fail and printed a tick, which is the defect surviving its
    own fix. With the old name gone, a caller that did not move is an
    AttributeError, and an AttributeError cannot be mistaken for health.

    ``firm doctor`` routes on the verdict. STALE is mechanical, so ``--fix``
    repairs it. UNDETERMINABLE is NOT: rebuilding the block from the roster
    cannot make an unreadable base readable, and a tool that claims that repair
    is committing the same fault this function was fixed for.
    """
    path = Path(workspace) / ".base" / "domains.toml"
    if not path.exists():
        # Determinable, and the answer is that there is nothing to be stale
        # against: with no BASE tier there is no block that can drift from the
        # roster. Stays CURRENT, which is exactly the behaviour before #62 --
        # that issue widened the outcome TYPE and deliberately did not
        # re-decide this branch.
        return Verdict.CURRENT, "no BASE tier on this firm — nothing to wire"
    try:
        body = path.read_text(encoding="utf-8")
    except OSError as exc:
        # The mirror image of #62, in the same function. This answered False,
        # i.e. "the block is stale", which nobody established -- a file you
        # could not read cannot be called out of date. It errs loud instead of
        # quiet so it was the less dangerous of the two, but it is the same
        # mistake, and routing it mechanical had ``--fix`` rebuilding a file it
        # had just failed to read.
        return Verdict.UNDETERMINABLE, str(exc)
    if BEGIN not in body:
        return Verdict.STALE, (
            "the firm's graph reaches no Member — domains.toml has no domain "
            "block, so base injects nothing firm-specific")
    # Two [[domain]] entries sharing a name make base match NEITHER, so the
    # marker block being perfect is not sufficient: count every block carrying
    # this firm's name. Measured on chief-of-staff 2026-09-09.
    named = body.count('name = "' + firm_id + '"')
    if named > 1:
        return Verdict.STALE, (
            f"{named} domain blocks are named {firm_id!r} — base matches none "
            "of them while the name is duplicated")
    want = render(Path(workspace), firm_id, _roster_words(conn, firm_id))
    head, _, rest = body.partition(BEGIN)
    have = BEGIN + rest.partition(END)[0] + END
    if have.strip() != want.strip():
        return Verdict.STALE, (
            "the domain block is stale against the roster — Members are "
            "missing from its triggers")
    # A perfect block is not a live wire. base drops a MATCHED domain that
    # carries zero rules, silently and totally, so a firm can pass every
    # structural check above and still reach no Member. Measured on
    # chief-of-staff 2026-09-09 and again by godwit 2026-09-10 against base
    # 0.15.0. This check is the reason the finding stops being invisible.
    count = rule_count(Path(workspace), firm_id)
    if count is None:
        # THE #62 BRANCH. ``rule_count`` answers None for three separate
        # causes -- base unresolvable, ``base rule list`` exiting non-zero, and
        # output its count regex does not recognise -- and all three mean the
        # same thing here: nothing was established. This returned True.
        #
        # The reason string below still splits suppressed from missing from
        # unparseable, because an operator who set CADRE_NO_BASE must not be
        # sent debugging an install that is fine. The reason was never the
        # defect; the outcome type was.
        from firm.sysconfig.service import base_absence_reason, which_base
        why = (base_absence_reason() if which_base() is None
               else "base is installed but its rule listing was not recognised")
        return Verdict.UNDETERMINABLE, (
            f"domain block matches the roster; rule count unread — {why}")
    if count == 0:
        return Verdict.STALE, (
            "the firm's domain carries no rules, so base drops the whole "
            "block — the block is perfect and injects nothing")
    return (Verdict.CURRENT,
            f"domain block matches the roster, {count} rule(s) live")


_SEED_RULE = ("Members of this firm read the firm's own graph before acting and "
              "write what they learn back to it, so the next run starts where "
              "this one finished.")


def _base_env(workspace: Path | str | None = None) -> dict[str, str]:
    """Explicit env for every `base` call — a systemd-spawned hub has a bare PATH.

    WITH A WORKSPACE THIS IS THE ISOLATION SEAM (#117). base takes its entire
    global tier from BASE_HOME — extensions, domains, rules, the relay store —
    so pointing it at the firm's own tier is the whole of "nothing outside the
    firm ever writes into the firm's graph". Measured on the operator's machine
    2026-09-14 with base 0.15.2: against his real tier one session-start wrote
    13,564 lines of an unrelated extension's data and 116 lines of his own
    domain definitions into a firm's graph; against a tier of the firm's own,
    the same wire produced one line.

    THE FIRM'S TIER BEATS AN AMBIENT BASE_HOME, and that is a deliberate
    reversal of what this function used to do. It carried the caller's
    BASE_HOME through so a harness could aim base at a scratch tier. A caller
    aiming base somewhere else WHILE CADRE IS WORKING A FIRM is exactly the
    hole this seam closes, so the firm wins, and the reason lives here rather
    than in a commit message nobody opens. With no workspace there is no firm
    to isolate and the passthrough stands unchanged, which is what keeps
    `cadre extension install` and the suite's own fence working.

    The directory is created here rather than by a caller because this is the
    one place every `base` subprocess in this package passes through, and a
    BASE_HOME naming a path that does not exist is not isolation, it is a
    guess about what base will do with it.
    """
    env = {"HOME": str(Path.home()),
           "PATH": os.environ.get("PATH") or "/usr/bin:/bin"}
    for passthrough in ("BASE_HOME", "XDG_CONFIG_HOME"):
        value = os.environ.get(passthrough)
        if value:
            env[passthrough] = value
    if workspace is not None:
        from firm.services.graph_isolation import ensure_tier

        env["BASE_HOME"] = str(ensure_tier(workspace))
    if os.name == "nt":
        # HOME is not how Windows finds a home directory. `Path.home()` reads
        # USERPROFILE, or HOMEDRIVE+HOMEPATH, and raises RuntimeError when it
        # can find none of them.
        #
        # That is not a hypothetical. `firm/__main__.py` builds its argparse
        # tree with `default=Path.home() / "firms"`, which runs for EVERY
        # command before a single argument is parsed. So an env carrying only
        # HOME is an env in which Cadre cannot start at all:
        #
        #     RuntimeError: Could not determine home directory.
        #
        # Cadre spawning Cadre through this helper therefore died on Windows
        # while the same command worked perfectly from a shell. Measured on
        # Windows 10 / Python 3.12.6 against the installed wheel.
        #
        # SYSTEMROOT and friends are here for the same reason one level down:
        # a Windows process with no SystemRoot cannot load the socket and ssl
        # machinery the interpreter initialises on import.
        for win_var in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "SYSTEMROOT",
                        "windir", "TEMP", "TMP", "PATHEXT", "COMSPEC",
                        "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
                        "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"):
            value = os.environ.get(win_var)
            if value:
                env[win_var] = value
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

    The verdict only. :func:`_rule_count` carries the reason with it; this
    signature is what ``assess`` and the tests around #62 are written against
    and it does not change.
    """
    return _rule_count(workspace, firm_id)[0]


def _rule_count(workspace: Path, firm_id: str) -> tuple[int | None, str]:
    """``rule_count``, plus WHY when the answer is None.

    Five different situations answer None here, and until #114 the operator
    was told the same thing about all five: "carries no rules, run firm doctor
    --fix" -- an instruction that is wrong for four of them and silent about
    the fifth. The fifth was the new one: on Windows an undecodable byte in
    base's UTF-8 banner killed subprocess's reader thread, ``run`` returned rc
    0 with ``stdout`` None, and a firm whose domain could not be read at all
    was reported as a firm whose domain was empty.

    ``require_output=True`` is what makes that particular silence impossible
    to mistake for data: ``base rule list`` exits 0 for a populated domain, an
    empty one AND a domain that never existed, so its output is the only
    signal there is, and a call that returns none of it has failed rather than
    answered. The decode is fixed one layer down in ``firm.core.proc``; this
    is the belt to that braces, and it is what keeps a FUTURE silence -- some
    other cause, on some other host -- arriving named instead of dressed up as
    an empty rule set.
    """
    import subprocess
    from firm.sysconfig.service import which_base
    base = which_base()
    if not base:
        return None, "base is not installed, so the domain cannot be read"
    try:
        listed = run_utf8(
            [base, "rule", "list", "--domain", firm_id],
            capture_output=True, timeout=60, require_output=True,
            cwd=str(workspace), env=_base_env(workspace),
            stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"base rule list did not run: {exc}"
    except NoOutput as exc:
        return None, str(exc)
    if listed.returncode != 0:
        said = (listed.stderr or listed.stdout or "").strip().splitlines()
        return None, (f"base rule list exited {listed.returncode}: "
                      + (said[-1][:200] if said else "and said nothing"))
    match = _COUNT_RE.search(listed.stdout or "")
    if match and match.group("domain") == firm_id:
        return int(match.group("n")), ""
    if _EMPTY_RE.search(listed.stdout or ""):
        return 0, ""
    return None, ("base rule list printed something this version of cadre "
                  "does not recognise, so the rule count is unknown -- not "
                  "zero")


def _seed_rule(workspace: Path, firm_id: str) -> bool:
    """Ensure the firm's domain carries at least one rule.

    A matched domain with an empty rule set is dropped from injection whatever
    its graph holds, so a firm can have a perfect domain block, a full graph,
    and still reach no Member. Seeding one rule is what turns the block into a
    live wire. Idempotent: `base rule add` is only called when the domain has
    no rules yet. Never raises -- BASE may not be installed at all.
    """
    return _seed_rule_detail(workspace, firm_id)[0]


def _seed_rule_detail(workspace: Path, firm_id: str) -> tuple[bool, str]:
    """``_seed_rule``, plus why it could not seed -- empty string on success.

    The reason travels out through ``sync`` to ``wire_workspace``'s ``detail``,
    which is the line an operator reads after founding a firm. An empty string
    means the honest zero: the domain really does carry no rules, which is the
    one case the old fixed sentence was right about.
    """
    import subprocess
    from firm.sysconfig.service import which_base
    base = which_base()
    if not base:
        return False, "base is not installed, so no rule could be seeded"
    count, why = _rule_count(workspace, firm_id)
    if count is None:
        return False, why         # cannot read the domain; do not claim a seed
    if count > 0:
        return True, ""           # already has rules; leave them alone
    try:
        added = run_utf8(
            [base, "rule", "add", "--domain", firm_id, "--text", _SEED_RULE],
            capture_output=True, timeout=60,
            cwd=str(workspace), env=_base_env(workspace),
            stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"base rule add did not run: {exc}"
    if added.returncode != 0:
        said = (added.stderr or added.stdout or "").strip().splitlines()
        return False, (f"base rule add exited {added.returncode}: "
                       + (said[-1][:200] if said else "and said nothing"))
    # Read back. `base rule add` returning 0 is the writer's own opinion; the
    # only thing that proves the rule landed is asking for it again.
    back, why = _rule_count(workspace, firm_id)
    if (back or 0) > 0:
        return True, ""
    return False, (why or "base rule add exited 0 but the read-back still "
                          "shows no rules, so nothing landed")


def scaffold_tier(workspace: Path) -> dict[str, Any]:
    """Give a workspace its own BASE tier -- ``.base/`` -- and say what happened.

    This half was private inside ``firm/dashboard/founding.py``, reachable only
    from the dashboard founding flow, which is the whole of F4 and F5: a firm
    made with ``cadre init`` had no ``.base/`` at all, so ``cadre learn``
    refused to write outside a workspace, ``sync`` returned early, and
    ``_seed_rule`` -- reachable only from inside ``sync`` -- never ran. One
    missing directory, both symptoms.

    base absent is not an error. A licensee may not carry base, and a firm
    without it is degraded, never broken; the same contract ``sync`` and
    ``base_extension.install`` keep.

    Idempotent, and safe to call on a workspace that already has a tier:
    measured 2026-09-10 against base 0.15.1, a second ``base scaffold`` left
    ``domains.toml`` byte-identical and preserved a domain block written
    between the two runs. That is why founding can init a workspace and then
    wire it without the two steps fighting, and why a workspace copied without
    its registry entry still heals.
    """
    import subprocess
    from firm.sysconfig.service import which_base

    from firm.sysconfig.service import base_absence_reason

    result: dict[str, Any] = {"scaffolded": False, "detail": ""}

    # Point every Claude session opened in this firm at the firm's own tier
    # (#117, coverage row 3). Cadre's own calls and the Member runs it spawns
    # are covered in code; this covers the sessions Cadre is not present for --
    # the Boardroom, or a person working in the firm's directory -- because the
    # operator's global settings register `base hook session-start` and that
    # hook ingests his whole global tier into whatever workspace it stands in.
    #
    # Before the base-absent check on purpose: a firm founded on a machine
    # without base is then already isolated on the day base arrives.
    from firm.services.graph_isolation import write_session_env

    result["session_env"] = write_session_env(workspace)

    base = which_base()
    if not base:
        result["detail"] = (
            f"{base_absence_reason()} — the firm is degraded, not broken")
        return result

    # REFUSE BEFORE ANYTHING RUNS. Issue #87.
    #
    # This is the same assertion `base_extension.install` makes, at its
    # neighbour. Both resolve a binary with `which_base()`, which takes no
    # parameter, so NEITHER can be directed at one by its caller. #75 taught
    # install() to assert its own resolution and the fix did not reach here.
    #
    # The exposure is not the test suite: conftest's autouse `_no_ambient_base`
    # makes `which_base` None under pytest, so the suite never spawns base at
    # all. It is an operator running `cadre init` on WSL, where `which_base`
    # resolves the WINDOWS base across /mnt/c. That binary executes happily
    # under interop, ignores this POSIX workspace path, and scaffolds the
    # operator's own global tier instead -- which is how a throwaway /tmp
    # workspace reached the operator's real base.toml on 2026-09-12.
    #
    # NOT `BASE_HOME`. Measured 2026-09-11 and recorded at binaries.py:92-93
    # and service.py:54-56: a POSIX BASE_HOME handed to a Windows base is
    # IGNORED and does not redirect the write. Refusal is the only thing that
    # reliably stops it, which is why #79 chose refusal over environment.
    from firm.sysconfig.binaries import base_can_honour_tier

    expected = workspace / ".base"
    may_run, refusal = base_can_honour_tier(base, expected)
    if not may_run:
        result["detail"] = refusal
        return result

    try:
        # Explicit env, never ambient — a systemd-spawned hub's PATH is bare.
        proc = run_utf8(
            [base, "scaffold", str(workspace)],
            capture_output=True, timeout=120,
            env=_base_env(workspace),
        )
        if proc.returncode != 0:
            result["detail"] = (
                f"base scaffold exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout or '').strip()[:200]}")
            return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["detail"] = f"base scaffold did not run: {exc}"
        return result
    result["scaffolded"] = True
    return result


def wire_workspace(workspace: Path,
                   firm_id: str = "",
                   proposal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Give the newborn firm its BASE workspace — the firm's own memory.

    Every founded firm gets `.base/` (graph, domains.toml, global registry
    entry) so Members have an institutional memory from day one; the charter's
    §6 charges them with using and maintaining it. BASE absent is not an
    error — licensees may not carry it — and a scaffold failure degrades the
    firm, it never aborts a founding.

    Returns the three states SEPARATELY, because they fail separately and a
    single flag cannot tell them apart. A firm can have its tier scaffolded, a
    correct domain block written, and STILL reach no Member, because base drops
    a matched domain that carries zero rules. This function used to return True
    on `base scaffold` exiting 0 and throw away everything ``sync`` told it, so
    that firm was reported as wired. chief-of-staff-cto shipped that way.
    """
    result: dict[str, Any] = {"scaffolded": False, "domain_ok": False,
                              "rule_seeded": False, "live": False, "detail": ""}
    tier = scaffold_tier(workspace)
    result["scaffolded"] = tier["scaffolded"]
    result["detail"] = tier["detail"]
    if not result["scaffolded"]:
        return result

    synced = sync(workspace, firm_id, proposal=proposal or {})
    result["domain_ok"] = bool(synced.get("ok"))
    result["rule_seeded"] = bool(synced.get("rule_seeded"))
    if not result["domain_ok"]:
        result["detail"] = (
            "the firm has a BASE tier but no domain block, so its graph reaches "
            f"no Member: {synced.get('reason') or 'unknown'}")
        return result
    if not result["rule_seeded"]:
        # An unreadable rule set and an empty one are not the same finding, and
        # the fixed sentence below used to be printed over both (#114). The
        # route stays in either branch: `doctor --fix` is still where an
        # operator goes next, whichever of the two it turns out to be.
        why = str(synced.get("rule_reason") or "").strip()
        result["detail"] = (
            "the firm's domain block was written but carries no rules, so base "
            "drops it whole and injects nothing — run firm doctor --fix"
            if not why else
            "the firm's domain block was written but its rule set could not be "
            f"established, so base may be dropping it whole: {why} — run firm "
            "doctor --fix")
        return result
    result["live"] = True
    result["detail"] = "the firm's graph reaches its Members"
    return result
