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

ROUTES = ("mechanical", "train", "board")


def _check(key: str, label: str, ok: bool, route: str, detail: str = "",
           fix: str | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "ok": ok, "route": route,
            "detail": detail, "fix": fix}


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
        mark = "✓" if c["ok"] else "✗"
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
    return 0
