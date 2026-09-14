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
from firm.core.migrate import (
    _default_migrations_dir,
    applied_migration_names,
    discover_migrations,
)

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


def diagnose(workspace: Path, firm_id: str, *,
             unit_dir: Path | None = None) -> list[dict[str, Any]]:
    """The report card. Read-only — every finding carries its route."""
    from firm.cli.heartbeat import _UNIT_PREFIX, _sched
    from firm.cli.install_hooks import POLICY_HOOK_COMMAND, POLICY_HOOK_SCRIPT_NAME
    from firm.pulse import preflight
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
        stem = f"{_UNIT_PREFIX}{firm_id}"
        st = sched.status(stem)
        schedule = firm.get("schedule")
        in_sync = bool(schedule) == bool(st.get("installed"))
        checks.append(_check(
            "schedule", "firm.schedule matches the timer", in_sync,
            "mechanical",
            f"schedule={schedule!r}, timer installed: {bool(st.get('installed'))} "
            f"({sched.name})",
            fix="reconcile the row to timer truth"))
        ghost = bool(st.get("failed")) and not st.get("installed")
        checks.append(_check(
            "ghost-units", "No failed scheduler ghosts", not ghost, "mechanical",
            f"{stem} sits failed with its files gone" if ghost else "clean",
            fix="clear the scheduler's failure residue"))

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
                _foreign = _base_extension.foreign_rules(
                    _rendered, _base_bin, _base_extension._base_env(workspace))
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
            repo.update(conn, "firm", firm_id, {"schedule": interval})
            did.append(f"schedule: reconciled to {interval!r}")
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
        if any(not c["ok"] and c["route"] == "operator" for c in checks):
            print("  → undeterminable: this machine could not read the "
                  "answer, so the firm is not what needs repairing — "
                  "install base, or unset CADRE_NO_BASE, then re-run")
    return 0
