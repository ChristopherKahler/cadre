"""``firm doctor`` — framework-drift diagnosis, and repair of the mechanical.

Every framework advance leaves standing firms behind: founded under old law,
missing the new sections, gates, and columns. Before this existed the only
remediation was a boardroom doing hand surgery (2026-07-14, nine forks in one
day). The doctor is the drift detector and the mechanical half of the repair.

The boundary is the whole design:

    Doctor fixes what has ONE RIGHT ANSWER.  (migrations, the policy gate,
        materialized policy, systemd ghosts, schedule truth, denial backlog)
    Train re-decides what needs JUDGMENT.    (loadouts, deny rules, charter)
    The Board authors what needs AUTHORITY.  (models, goals, credentials)

``--fix`` therefore never touches a loadout, a deny list, a model, or a goal.
It reports them, routed: ``mechanical`` (fixable here), ``train`` (re-run
Train), ``board`` (the Board decides). Diagnose is read-only; exit 0 means the
doctor ran, not that the firm is healthy — read the card.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.core.db import connect, get_db_path, resolve_firm_id
from firm.pulse.stranded import stranded_units
from firm.services import pulse_ledger
from firm.core.migrate import (
    _default_migrations_dir,
    applied_migration_names,
    discover_migrations,
)
from firm.sched.base import interval_to_seconds

# "operator" is the route for a finding the firm cannot fix, because
# the firm is not what is broken: the machine could not establish an
# answer. `--fix` selects on "mechanical", so this route is what keeps
# it from claiming a repair it cannot make.
ROUTES = ("mechanical", "train", "board", "operator")


def _check(key: str, label: str, ok: bool, route: str, detail: str = "",
           fix: str | None = None, state: str = "") -> dict[str, Any]:
    """One card. ``state`` is the pass/fail with its third value restored.

    ``ok`` is a bool and a bool has two values, so a check that could not
    ESTABLISH its answer still had to pick one of them -- and picking True
    is how `base-domain` printed a tick over a rule count it had failed to
    read (#62). ``state`` carries "undeterminable" as a first-class value,
    so a caller can tell the three apart without substring-matching
    ``detail``. It defaults to the ok/finding pair so every card carries a
    meaningful one, and it is additive -- `firm doctor --json` gains a key
    in its checks array and loses none.
    """
    return {"key": key, "label": label, "ok": ok, "route": route,
            "detail": detail, "fix": fix,
            "state": state or ("ok" if ok else "finding")}


def _parse_json_col(row: dict[str, Any] | None, col: str) -> dict[str, Any]:
    raw = (row or {}).get(col)
    try:
        val = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        return {}
    return val if isinstance(val, dict) else {}


def _interval_in(value: Any) -> str | None:
    """The pulse interval *value* holds, stripped, or None when it holds none.

    The grammar is the one `heartbeat enable` validates with, so the card
    below and the writer that caused what it reports agree on what an
    interval is.
    """
    if not isinstance(value, str):
        return None
    try:
        interval_to_seconds(value)
    except ValueError:
        return None
    return value.strip()


def diagnose(workspace: Path, firm_id: str, *,
             unit_dir: Path | None = None) -> list[dict[str, Any]]:
    """The report card. Read-only — every finding carries its route."""
    from firm.cli.heartbeat import _UNIT_PREFIX, _sched
    from firm.cli.install_hooks import POLICY_HOOK_COMMAND, POLICY_HOOK_SCRIPT_NAME
    from firm.pulse import preflight
    from firm.services import pulse_ledger
    from firm.services import policy as policy_svc

    sched = _sched(unit_dir)
    checks: list[dict[str, Any]] = []
    conn = connect(get_db_path(workspace))
    try:
        firm = repo.get(conn, "firm", firm_id) or {}
        contracts = repo.find(conn, "contract", firm_id=firm_id)
        members = {m["contract_id"]: m for m in
                   repo.find(conn, "member", firm_id=firm_id)
                   if m.get("contract_id")}

        # 1. migrations — mechanical
        pending = [name for _n, name, _p in
                   discover_migrations(_default_migrations_dir())
                   if name not in applied_migration_names(conn)]
        checks.append(_check(
            "migrations", "Schema is current", not pending, "mechanical",
            f"pending: {', '.join(pending)}" if pending else "all applied",
            fix="apply pending migrations"))

        # 2. charter — train (regeneration is a judgment act)
        charter_path = workspace / "CLAUDE.md"
        missing_sections: list[str] = []
        if charter_path.is_file():
            text = charter_path.read_text(encoding="utf-8", errors="replace")
            for marker, name in (("### Host CLI tools", "host armory"),
                                 ("**The goal.**", "goal line"),
                                 ("Know your number", "goal duty")):
                if marker not in text:
                    missing_sections.append(name)
            checks.append(_check(
                "charter", "Charter carries current law", not missing_sections,
                "train",
                ("missing: " + ", ".join(missing_sections)) if missing_sections
                else "current"))
        else:
            checks.append(_check(
                "charter", "Charter exists", False, "train", "no CLAUDE.md"))

        # 3. policy gate installed + registered + CURRENT — mechanical
        #
        # The hook is a copy, taken at install time. The framework upgrading
        # itself does nothing for a firm already standing: `pip install -e`
        # makes every firm's *library* live, but the gate on disk stays
        # whatever it was the day it was written. Checking only that the file
        # exists reports a firm running a gate with a known hole as armed —
        # which is how a fixed gate quietly fails to ship (fork 015).
        # Compare against the RENDERED gate, not the raw template: the gate
        # ships with `shell_intent` spliced into it, so a firm whose hook
        # predates a resolver change is exactly as stale as one whose hook
        # predates a template change, and must report the same.
        from firm.cli.install_hooks import render_policy_hook
        hook_file = workspace / ".claude" / "hooks" / POLICY_HOOK_SCRIPT_NAME
        try:
            current = hook_file.read_text(encoding="utf-8") == render_policy_hook()
        except OSError:
            current = False
        registered = False
        try:
            settings = json.loads(
                (workspace / ".claude" / "settings.json").read_text(encoding="utf-8"))
            registered = any(
                h.get("command") == POLICY_HOOK_COMMAND
                for e in (settings.get("hooks") or {}).get("PreToolUse") or []
                for h in (e.get("hooks") or []) if isinstance(h, dict))
        except (OSError, json.JSONDecodeError):
            pass
        armed = hook_file.is_file() and registered and current
        checks.append(_check(
            "policy-gate", "NEVER-enforcement gate armed and current", armed,
            "mechanical",
            "installed, registered, matches the shipped gate" if armed
            else (f"hook file: {hook_file.is_file()}, registered: {registered}, "
                  f"matches shipped gate: {current}"),
            fix="install + register the current PreToolUse policy gate"))

        # 4. policy materialized and fresh — mechanical
        want = policy_svc.member_denies(conn, firm_id)
        pol_path = workspace / ".firm" / policy_svc.POLICY_FILE
        try:
            have = json.loads(pol_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            have = None
        fresh = (have == want) or (not want and have in (None, {}))
        checks.append(_check(
            "policy-fresh", "Materialized policy matches Contracts", fresh,
            "mechanical",
            "in sync" if fresh else "policy.json is stale or missing",
            fix="re-materialize .firm/policy.json"))

        # 4b. the NEVERs can actually fire — train (re-patterning is judgment)
        # A fresh, correctly-installed, correctly-registered gate enforcing
        # rules aimed at nothing passes every check above it. chief-of-staff
        # ran that way for its whole life (ESC-021). This is the check that
        # would have said so on day one.
        blind = policy_svc.unfireable_members(conn, firm_id)
        checks.append(_check(
            "policy-aim", "NEVERs match tools Members can call", not blind, "train",
            "every rule can fire" if not blind else "; ".join(
                f"{f['name']}: all {len(f['rules'])} rules are API-method names, "
                f"unreachable from {'/'.join(f['servers'])}" for f in blind),
            fix="re-run Train to re-pattern the deny rules against real tool names"))

        # 5. models — board (a model is a budget decision)
        modelless = [
            (members.get(c["id"], {}).get("name") or c.get("name") or c["id"])
            for c in contracts
            if not _parse_json_col(c, "pulse_config").get("model")
        ]
        checks.append(_check(
            "models", "Every Contract has a model budget", not modelless,
            "board",
            ("inheriting the session default (Opus, at Opus prices): "
             + ", ".join(modelless)) if modelless else
            f"{len(contracts)} contract(s) set"))

        # 5b. the firm's BASE domain — mechanical.
        # A firm whose domains.toml carries no domain block (or a stale one)
        # injects nothing firm-specific into its Members: base falls back to
        # the operator's global tier and the firm's own graph is reachable
        # only by an explicit `base recall`, which is the tool call the
        # mechanism exists to remove. Derived from the roster, so --fix it.
        from firm.services import base_domain as _base_domain
        _bd = _base_domain.assess(workspace, firm_id, conn)
        _bd_verdict, _bd_detail = _bd
        # UNDETERMINABLE is deliberately NOT mechanical. `fix()` selects on
        # route == "mechanical", and rebuilding the block from the roster
        # cannot make an unreadable base readable, so routing it away is
        # what stops `--fix` reporting a repair it did not make.
        checks.append(_check(
            "base-domain", "The firm's graph reaches its Members",
            _bd_verdict is _base_domain.Verdict.CURRENT,
            "mechanical" if _bd_verdict is _base_domain.Verdict.STALE
            else "operator",
            _bd_detail, state=_bd_verdict.value,
            fix="rebuild .base/domains.toml from the roster"))

        # 5b. where this firm's Members are reachable (#117, #122)
        #
        # A firm keeps its own message store, in its own base tier, so "ping
        # the Member" needs an answer to "which store". Naming the path is the
        # whole point of the card: a steer that goes to the wrong store looks
        # exactly like a Member ignoring you.
        from firm.services import firm_relay as _firm_relay
        from firm.services import graph_isolation as _isolation
        from firm.services import relay_title as _relay_title

        _store = _firm_relay.store_path(workspace)
        _store_here = _store.exists()
        checks.append(_check(
            "relay-store", "Where this firm's Members are reachable",
            _store_here, "operator",
            f"Members register in {_store}; steer with "
            f"cadre relay ping --to <member name>" if _store_here else
            f"{_store} does not exist yet — it appears the first time this "
            "firm runs base, so where Members will register is known but "
            "unconfirmed",
            state="ok" if _store_here else "undeterminable"))

        # 5c. every Member answers to its own name
        _clashes = _relay_title.collisions(workspace, firm_id)
        _unreadable = [c for c in _clashes if not c["title"]]
        if _unreadable:
            _titles_detail = _unreadable[0]["reason"]
            _titles_state = "undeterminable"
        elif _clashes:
            _titles_detail = (
                "; ".join(f"{c['title']} is claimed by " + ", ".join(c["members"])
                          for c in _clashes)
                + " — both carry their row id until one is renamed, because "
                  "picking one of them silently would send a steer to the "
                  "wrong Member")
            _titles_state = "finding"
        else:
            _titles_detail = "each active Member's name resolves to one title"
            _titles_state = "ok"
        checks.append(_check(
            "relay-titles", "Every Member answers to its own name",
            _titles_state == "ok", "board" if _titles_state == "finding"
            else "operator", _titles_detail, state=_titles_state))

        # 5d. nothing outside the firm has written into the firm's graph
        #
        # Cleanup is NOT mechanical and must never be a --fix: it rewrites the
        # operator's own machine state, and a founding flow does not get to do
        # that unasked.
        _census = _isolation.census(workspace)
        if _census["reason"]:
            _iso_state = "undeterminable"
        elif _census["foreign_lines"]:
            _iso_state = "finding"
        else:
            _iso_state = "ok"
        checks.append(_check(
            "graph-isolation", "The firm's graph is the firm's alone",
            _iso_state == "ok", "operator", _isolation.summary(_census),
            state=_iso_state))

        # 6. goal — board
        goals = repo.find(conn, "goal", firm_id=firm_id)
        firm_goal = any((g.get("parent_entity_type"), g.get("parent_entity_id"))
                        == ("firm", firm_id) for g in goals)
        has_ns = bool(str(firm.get("north_star") or "").strip())
        checks.append(_check(
            "north-star", "Firm has its number", has_ns and firm_goal, "board",
            "north_star + firm goal set" if has_ns and firm_goal else
            f"north_star: {has_ns}, firm-level goal: {firm_goal} — "
            "a firm with no number cannot fail, only be busy"))

        # 7 + 8. schedule truth and scheduler ghosts — mechanical
        #
        # The cadence is firm.pulse_interval (#134). firm.schedule is business
        # hours: reading it here called a firm with hours and no timer out of
        # sync, and --fix then wrote NULL over the hours. The card keeps its
        # key, because fix() and `firm doctor --json` readers select on it.
        stem = f"{_UNIT_PREFIX}{firm_id}"
        st = sched.status(stem)
        pulse_interval = firm.get("pulse_interval")
        in_sync = bool(pulse_interval) == bool(st.get("installed"))
        checks.append(_check(
            "schedule", "firm.pulse_interval matches the timer", in_sync,
            "mechanical",
            f"pulse_interval={pulse_interval!r}, timer installed: "
            f"{bool(st.get('installed'))} ({sched.name})",
            fix="reconcile the row to timer truth"))
        ghost = bool(st.get("failed")) and not st.get("installed")
        checks.append(_check(
            "ghost-units", "No failed scheduler ghosts", not ghost, "mechanical",
            f"{stem} sits failed with its files gone" if ghost else "clean",
            fix="clear the scheduler's failure residue"))

        # 8a. a timer whose stored command carries no --source — OPERATOR
        #
        # ROUTED TO THE OPERATOR, AND THAT IS A RULING RATHER THAN A DEFAULT.
        # `fix()` selects on route == "mechanical" and dispatches per key, so
        # "mechanical" is a promise that `--fix` performs the repair. The repair
        # here is re-running `heartbeat enable`, which rewrites a LIVE timer
        # (`systemctl enable --now`, `schtasks /Create`) -- something the doctor
        # has never done -- and on Windows that is #147: enable over a running
        # heartbeat re-creates the task and leaves the old pulse running. A
        # `--fix` that can leave two pulses running is worse than a card that
        # prints one command. The #28 rule is still satisfied: the command
        # exists, and this card prints it with the firm's own values.
        #
        # THREE STATES, THREE ANSWERS (law 48). `source` absent means the stored
        # command could not be read, which is not the same finding as a command
        # read and carrying no flag -- so absent is "undeterminable" and says
        # WHAT it could not read, rather than sending an operator to re-enable a
        # timer on the strength of a reading that never happened.
        if not st.get("installed"):
            # The `schedule` card above owns "there is no timer". Two cards for
            # one condition is two findings an operator has to reconcile.
            checks.append(_check(
                "timer-source", "The timer says which command installed it",
                True, "operator", "no timer installed, nothing to label"))
        elif "source" not in st:
            checks.append(_check(
                "timer-source", "The timer says which command installed it",
                False, "operator",
                f"could not read the stored command for {stem} from "
                f"{sched.name}, so whether it carries --source is unknown",
                state="undeterminable"))
        elif st["source"] is None:
            # ONE PRODUCER. The printed report shows `detail` and never `fix`
            # (the loop below prints label, route and detail), so the command
            # has to be in the detail to reach an operator at all -- and it has
            # to be in `fix` for `--json` readers. Two spellings of one command
            # are two commands, and only one of them gets tested.
            relabel = ("cadre heartbeat enable --workspace "
                       f"{workspace} --firm-id {firm_id}"
                       + (f" --interval {pulse_interval}" if pulse_interval
                          else ""))
            checks.append(_check(
                "timer-source", "The timer says which command installed it",
                False, "operator",
                # The fact AND the remedy, which is 8b's pattern. The #28 rule
                # is satisfied twice over: the command exists, and now it is
                # somewhere the operator running `cadre doctor` will read it.
                f"{stem} was installed before --source existed, so its pulses "
                f"record {pulse_ledger.UNSET!r} and nothing says why. "
                f"Run: {relabel}",
                fix=relabel))
        else:
            checks.append(_check(
                "timer-source", "The timer says which command installed it",
                True, "operator",
                f"{stem} labels its pulses {st['source']!r}"))

        # 8b. business hours an interval overwrote — board (#134)
        #
        # Before migration 015 the interval was written into firm.schedule,
        # over whatever hours the firm had. 015 copied it to pulse_interval and
        # left schedule as it was, because the hours cannot be read back:
        # nothing keeps a field's history. Only the Board knows its hours, so
        # the card routes to the Board and --fix has nothing to do.
        lost_to = _interval_in(firm.get("schedule"))
        checks.append(_check(
            "business-hours", "firm.schedule holds business hours, not an interval",
            lost_to is None, "board",
            # The fact and its effect only. A card never names a command that does
            # not exist (the #28 rule), and nothing in Cadre sets business hours
            # yet, so it names the issue that asks for one.
            f"the schedule column holds the pulse interval {lost_to!r} where business "
            "hours belong, so the business-hours gate reads always open. Cadre has no "
            "command to set business hours yet; see issue #139" if lost_to is not None
            else "no pulse interval in firm.schedule"))

        # 8c. pulse gaps — operator (#128 D3)
        #
        # A GAP, NEVER A DROP. Under Chris's rule a logged-out Windows box does
        # not pulse, and that leaves precisely the evidence a dropped tick
        # leaves: no row. This card reports the window it measured and says
        # nothing about the cause, because it read none.
        #
        # Between recorded pulses only. The window from the newest row to now
        # belongs to the schedule card above (firm.pulse_interval against the
        # installed timer): a firm whose timer is off has no newest row moving,
        # and this card would then fire forever about a finding that already
        # has an owner.
        #
        # Three answers and three states, because an absent table, a firm with
        # nothing to measure, and a firm with a real gap are three different
        # findings and one of them is not the operator's to act on (law 48).
        starts, ledger_note = pulse_ledger.best_effort(
            lambda: pulse_ledger.starts(conn, firm_id))
        if ledger_note is not None:
            checks.append(_check(
                "pulse-gap", "The pulse ledger shows no gaps", False, "mechanical",
                f"pulse ledger not available: {ledger_note}",
                fix="apply pending migrations", state="undeterminable"))
        elif not pulse_interval:
            checks.append(_check(
                "pulse-gap", "The pulse ledger shows no gaps", True, "operator",
                "no pulse_interval recorded, so there is no cadence to measure "
                f"a window against ({len(starts)} pulse(s) in the ledger)",
                state="undeterminable"))
        elif len(starts) < 2:
            checks.append(_check(
                "pulse-gap", "The pulse ledger shows no gaps", True, "operator",
                f"{len(starts)} pulse(s) recorded; a window needs two",
                state="undeterminable"))
        else:
            try:
                # RAISES on anything that is not a simple span. Migration 015
                # clears a malformed value and `heartbeat enable` validates its
                # argument, so nothing in the product writes one -- but the
                # verb an operator runs when something is already wrong must
                # not be the thing that breaks on a bad row.
                every = interval_to_seconds(pulse_interval)
            except ValueError as exc:
                checks.append(_check(
                    "pulse-gap", "The pulse ledger shows no gaps", False,
                    "mechanical",
                    f"firm.pulse_interval is {pulse_interval!r}, which is not "
                    f"an interval: {exc}",
                    fix="reconcile the row to timer truth",
                    state="undeterminable"))
            else:
                windows = pulse_ledger.gaps(starts, every)
                named = "; ".join(f"no pulse started between {a} and {b}"
                                  for a, b in windows[:3])
                if len(windows) > 3:
                    named += f"; and {len(windows) - 3} more"
                checks.append(_check(
                    "pulse-gap", "The pulse ledger shows no gaps", not windows,
                    "operator",
                    named if windows else
                    f"{len(starts)} pulses recorded, none more than two "
                    f"intervals ({pulse_interval}) apart"))

        # 8d. stranded units — board (#128 C2)
        #
        # A firm whose every Member skips at load=0 while Units like these sit
        # on its board prints the same "ok: true, ran: 0" as a firm with
        # nothing to do, and those need opposite responses. The pulse says so
        # in its JSON; this is where an operator reads it without running one.
        #
        # Board, not mechanical: assigning a Unit or making a Member active is
        # a judgment act, and there is no command the doctor could run.
        #
        # The query is `pulse/stranded.py`'s, which the pulse calls too. Two
        # surfaces computing "unreachable" for themselves agree until the day
        # one of them is edited.
        try:
            stranded = stranded_units(conn, firm_id)
        except Exception as exc:
            # Undeterminable, never "clean": a query that raised has not
            # established that there are none (law 48).
            checks.append(_check(
                "stranded-units", "Every open Unit is reachable", False,
                "board",
                f"the Unit board could not be read: {type(exc).__name__}: {exc}",
                state="undeterminable"))
        else:
            checks.append(_check(
                "stranded-units", "Every open Unit is reachable", not stranded,
                "board",
                "; ".join(f"{u['id']} ({u['status']}) {u['reason']}"
                          for u in stranded) if stranded
                else "every pending or in-progress Unit is claimed by or "
                     "assigned to an active Member"))

        # 9. credential liveness — board (a re-login is a human act)
        dead = preflight.dead_tools(conn, firm_id)
        checks.append(_check(
            "credentials", "Loadout credentials are live", not dead, "board",
            "; ".join(f"{t}: {why}" for t, why in dead.items()) if dead
            else "all probed surfaces answered"))

        # 10. denial backlog — mechanical (ingestion is what the pulse does)
        log_path = workspace / ".firm" / policy_svc.DENIAL_LOG
        backlog = 0
        if log_path.exists():
            try:
                done = int((workspace / ".firm" / policy_svc.DENIAL_CURSOR)
                           .read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                done = 0
            backlog = max(0, len(log_path.read_text(
                encoding="utf-8", errors="replace").splitlines()) - done)
        checks.append(_check(
            "denials", "No unread policy denials", backlog == 0, "mechanical",
            f"{backlog} denial(s) not yet on Records" if backlog else "none",
            fix="ingest into Records + escalations"))

        # 10b. deliverables reach Records — train (registering needs the file
        #      mapping, which is judgment; the doctor names, it does not guess).
        #
        # The rule says the artifact must exist and be REGISTERED before the
        # Unit closes. Nothing checked, and nothing a Member could call did it:
        # chief-of-staff closed 26 Units and registered 3 Documents, with
        # unit.outputs NULL firm-wide (ESC-026). The Board reviews Documents, so
        # 23 finished deliverables were invisible while every other check
        # reported green.
        #
        # Deliberately NOT anchored on the contract's `file_exists
        # require_written` opt-in, tempting as that is: not one chief-of-staff
        # contract declares it, so that check would have passed on the firm that
        # defined the defect — ESC-021's "aimed at nothing" rebuilt. A done Unit
        # with no Document AND no outputs means the firm has no record of what it
        # produced, which is the harm itself, stated in a way no opt-in can hide.
        unregistered = [
            u["id"] for u in repo.find(conn, "unit", firm_id=firm_id, status="done")
            if not repo.find(conn, "document", firm_id=firm_id,
                             parent_entity_type="unit", parent_entity_id=u["id"])
            and not (u.get("outputs") or [])
        ]
        shown = ", ".join(unregistered[:8])
        more = f" (+{len(unregistered) - 8} more)" if len(unregistered) > 8 else ""
        checks.append(_check(
            "deliverables", "Done Units have a registered deliverable",
            not unregistered, "train",
            f"{len(unregistered)} done Unit(s) with no Document and no outputs — "
            f"the Board cannot review what Records never saw: {shown}{more}"
            if unregistered else "every done Unit's product is on Records",
            fix="register each Unit's file with `firm doc register --unit <id> "
                "--path <file>`; a Unit that legitimately produces no file "
                "should say so in its outputs"))

        # 11. the proving run — train (its absence means wiring predates it).
        #    A pending unit is the DESIGNED state right after wiring, not
        #    drift; only a wired firm with no proving unit at all is behind.
        proving = [u for u in repo.find(conn, "unit", firm_id=firm_id)
                   if u.get("name") == "Prove the armory"]
        if proving:
            status = proving[0].get("status")
            checks.append(_check(
                "proving-run", "Armory verification is in the system", True,
                "train", "proven" if status == "done"
                else f"queued ({status}) — the next pulse proves it"))
        else:
            checks.append(_check(
                "proving-run", "Armory verification is in the system",
                not charter_path.is_file(), "train",
                "no proving unit — the wiring predates verified equipping"
                if charter_path.is_file() else "firm not yet wired"))

        # 12. the graph copy of this extension's prompt domain — OPERATOR, and
        #     deliberately NOT mechanical. Issue #115: base matches a domain's
        #     keywords from the installed manifest but SERVES its rule text
        #     from the graph, and the graph copy wins. A copy written by an
        #     older `base domain sync` therefore stands in front of the
        #     shipped manifest and Members are told the old thing.
        #
        #     The route is "operator" because `fix()` acts on route ==
        #     "mechanical" and THERE IS NO FIX HERE TO MAKE. base 0.15.2
        #     exposes no verb that removes such a rule — `rule remove --index`
        #     matches an `index` triple these rules do not carry, `domain sync`
        #     appends rather than replaces, `graph supersede` does not index
        #     rules, `graph apply-ops` retires only facts with a sync id. The
        #     owner of the repair is base. Routing it mechanical would have
        #     `--fix` report a repair nobody can perform.
        from firm.services import base_extension as _base_extension
        from firm.sysconfig.service import which_base as _which_base

        _base_bin = _which_base()
        if not _base_bin:
            checks.append(_check(
                "base-extension-rules",
                "Members are told what the manifest says", True, "operator",
                "base is not on this machine, so no domain is injected at all"))
        else:
            try:
                _rendered = _base_extension.render()
                # THIS FIRM's tier (#117). The manifest and the rules a Member
                # is served live in `<firm>/.firm/base-home` once the extension
                # is installed there, and a Member spawned in this firm reads
                # that tier and no other. Read with the env of no workspace,
                # this card reports on the operator's tier, which no Member of
                # this firm uses: clean over a firm that collides, and a
                # collision over a firm that is clean.
                from firm.services.base_domain import base_cwd as _base_cwd

                _foreign = _base_extension.foreign_rules(
                    _rendered, _base_bin,
                    _base_extension._base_env(workspace), _base_cwd(workspace))
            except _base_extension.GraphReadFailed as _exc:
                # A zero here would mean "I could not see", which reads
                # identically to "nothing is wrong". Say undeterminable.
                checks.append(_check(
                    "base-extension-rules",
                    "Members are told what the manifest says", False,
                    "operator", f"could not read base's graph copy: {_exc}",
                    state="undeterminable"))
            except (OSError, ValueError, subprocess.SubprocessError) as _exc:
                checks.append(_check(
                    "base-extension-rules",
                    "Members are told what the manifest says", False,
                    "operator", f"the check could not run: {_exc}",
                    state="undeterminable"))
            else:
                _n = sum(len(f["foreign"]) for f in _foreign)
                _names = ", ".join(f["domain"] for f in _foreign)
                # PRINT THE RULES, not just how many. A card that says a
                # collision exists without showing what is being served leaves
                # the operator exactly where they started: unable to tell which
                # of two rule sets is reaching their Members.
                _texts = "; ".join(
                    _r for _f in _foreign for _r in _f["foreign"])
                checks.append(_check(
                    "base-extension-rules",
                    "Members are told what the manifest says", not _foreign,
                    "operator",
                    (f"{_n} rule(s) under {_names} come from base's graph, not "
                     "from Cadre's manifest, and the graph copy is what a "
                     f"Member receives: {_texts} — OWNER: base, which offers "
                     "no verb that removes them (issue #115). Cadre installed "
                     f"correctly. See them with: base rule list --domain {_names}")
                    if _foreign else
                    "every rule served under Cadre's domain is Cadre's own"))
    finally:
        conn.close()
    return checks


def fix(workspace: Path, firm_id: str, checks: list[dict[str, Any]], *,
        unit_dir: Path | None = None) -> list[str]:
    """Apply the mechanical fixes for failed checks. Judgment stays routed."""
    from firm.cli.heartbeat import _UNIT_PREFIX, _sched
    from firm.cli.install_hooks import install_policy_hook
    from firm.core.migrate import apply_migrations
    from firm.services import policy as policy_svc

    sched = _sched(unit_dir)
    failed = {c["key"] for c in checks if not c["ok"] and c["route"] == "mechanical"}
    did: list[str] = []
    if not failed:
        return did

    conn = connect(get_db_path(workspace))
    try:
        if "migrations" in failed:
            applied = apply_migrations(conn)
            did.append(f"migrations: applied {', '.join(applied)}")
        if "policy-gate" in failed:
            install_policy_hook(workspace)
            did.append("policy-gate: installed + registered")
        if "policy-fresh" in failed:
            policy_svc.materialize(conn, workspace, firm_id)
            did.append("policy: re-materialized")
        if "schedule" in failed:
            st = sched.status(f"{_UNIT_PREFIX}{firm_id}")
            interval = st.get("interval") if st.get("installed") else None
            # The interval's own column, never the business hours (#134).
            repo.update(conn, "firm", firm_id, {"pulse_interval": interval})
            did.append(f"pulse_interval: reconciled to {interval!r}")
        if "ghost-units" in failed:
            sched.clear_failed(f"{_UNIT_PREFIX}{firm_id}")
            did.append("ghost-units: failure residue cleared")
        if "base-domain" in failed:
            from firm.services import base_domain as _base_domain
            res = _base_domain.sync(workspace, firm_id, conn=conn)
            if not res.get("ok"):
                did.append(f"base-domain: {res.get('reason')}")
            elif not res.get("rule_seeded"):
                # The block is only half the wire. Saying "rebuilt" here while
                # the domain carries no rules reports a repair that did not
                # happen: base drops a ruleless domain whole.
                did.append(
                    "base-domain: block rebuilt from the roster "
                    f"({len(res.get('keywords') or [])} triggers) BUT THE RULE "
                    "SEED FAILED — the domain still injects nothing. Check that "
                    f"`base rule add --domain {firm_id}` works from {workspace}")
            else:
                did.append("base-domain: rebuilt from the roster "
                           f"({len(res.get('keywords') or [])} triggers), "
                           "rule seeded")
        if "denials" in failed:
            n = policy_svc.ingest_denials(conn, workspace, firm_id)
            did.append(f"denials: {n} ingested")
        conn.commit()
    finally:
        conn.close()
    return did


def run_doctor(workspace: Path, *, firm_id: str | None = None,
               apply_fixes: bool = False, as_json: bool = False,
               unit_dir: Path | None = None) -> int:
    workspace = workspace.expanduser().resolve()
    db_path = get_db_path(workspace)
    if not db_path.exists():
        print(json.dumps({"ok": False, "reason": "db-not-found",
                          "workspace": str(workspace)}), file=sys.stderr)
        return 1
    conn = connect(db_path)
    try:
        firm_id = resolve_firm_id(conn, firm_id)
    except ValueError as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}), file=sys.stderr)
        return 1
    finally:
        conn.close()

    checks = diagnose(workspace, firm_id, unit_dir=unit_dir)
    did = fix(workspace, firm_id, checks, unit_dir=unit_dir) if apply_fixes else []
    if did:
        checks = diagnose(workspace, firm_id, unit_dir=unit_dir)

    if as_json:
        print(json.dumps({"ok": True, "firm_id": firm_id,
                          "checks": checks, "fixed": did}, default=str))
        return 0

    healthy = all(c["ok"] for c in checks)
    print(f"firm doctor — {firm_id}")
    for c in checks:
        # .get, never [], because cards are built by hand elsewhere --
        # tests/cli/test_base_wire_reporting.py passes a five-key dict
        # straight into fix() -- and a new key must not KeyError on one.
        undeterminable = c.get("state") == "undeterminable"
        mark = "?" if undeterminable else ("✓" if c["ok"] else "✗")
        route = "" if c["ok"] else f"  [{c['route']}]"
        print(f"  {mark} {c['label']}{route} — {c['detail']}")
    for d in did:
        print(f"  ⚒ fixed: {d}")
    if not healthy and not apply_fixes:
        n_mech = sum(1 for c in checks if not c["ok"] and c["route"] == "mechanical")
        if n_mech:
            print(f"  → {n_mech} finding(s) are mechanical: `firm doctor --fix`")
        if any(not c["ok"] and c["route"] == "train" for c in checks):
            print("  → judgment findings: re-run Train from the dashboard")
        if any(not c["ok"] and c["route"] == "board" for c in checks):
            print("  → authority findings: the Board decides these")
        # KEYED TO THE CARD IT BELONGS TO (#158). This sentence is
        # base-domain's own advice, and it printed under EVERY failed operator
        # card. It also called them all "undeterminable", which `operator` does
        # not mean: a timer installed before `--source` existed was read
        # perfectly well, and what it needs is one command, not a base install.
        # Wrong advice under a real finding is worse than no footer, because it
        # sends an operator after a failure that is not there.
        operator_findings = [c for c in checks
                             if not c["ok"] and c["route"] == "operator"]
        if any(c["key"] == "base-domain" for c in operator_findings):
            print("  → undeterminable: this machine could not read the "
                  "answer, so the firm is not what needs repairing — "
                  "install base, or unset CADRE_NO_BASE, then re-run")
        if any(c["key"] != "base-domain" for c in operator_findings):
            print("  → operator findings: each card's detail says what to do")
    return 0
