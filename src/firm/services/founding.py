"""Founding a firm — the one path, and the shape a proposal has to hold.

Moved here from `dashboard/founding.py` for #135, unchanged. It sat in the
dashboard package because the hub was the only door; `cadre init --proposal`
is a second door onto the same path, and a CLI importing from the dashboard
is the wrong direction. Nothing under `services/` imports the dashboard, so
this is the end of that dependency rather than another link in it.

`dashboard/founding.py` keeps the hub's job machinery — the prompts, the
narrator, the arsenal, the four job functions — and binds these names back
into its own globals.

Not to be confused with `firm.services._validate`, which is a different
module about entity references. This `_validate` is the proposal's shape.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.core.db import connect, get_db_path

_FIRM_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")


_MODEL_TIERS = ("fable", "opus", "sonnet", "haiku")


_DEFAULT_MODEL = "sonnet"


def _validate(proposal: dict[str, Any],
              inv: dict[str, set[str]] | None = None) -> dict[str, Any]:
    """Shape the agent's output into something the Board can act on.

    Fails loudly on the two things that would corrupt a firm — a bad id, or a
    Member assigned to an Operation that doesn't exist. Everything else is
    coerced, because a missing "skills" list is not worth losing the org over.

    *inv* is the arsenal index the prompt was built from; loadout picks that
    don't resolve against it are dropped — the Board never reviews a ghost.
    With inv=None (commit-time revalidation) the loadout passes through as-is.
    """
    def _loadout(kind: str) -> list[dict[str, str]]:
        seen: set[str] = set()
        out = []
        for it in (proposal.get("loadout") or {}).get(kind) or []:
            if not isinstance(it, dict) or not it.get("name"):
                continue
            name = str(it["name"]).strip()
            if name in seen or (inv is not None and name not in inv.get(kind, set())):
                continue
            seen.add(name)
            out.append({"name": name, "why": str(it.get("why") or "").strip()[:200]})
        return out

    fid = str(proposal.get("firm_id") or "").strip().lower()
    if not _FIRM_ID_RE.match(fid):
        raise ValueError(f"invalid firm_id {fid!r}")

    ops = [
        {"name": str(o["name"]).strip(), "purpose": str(o.get("purpose") or "").strip()}
        for o in proposal.get("operations") or []
        if isinstance(o, dict) and o.get("name")
    ]
    if not ops:
        raise ValueError("proposal has no operations")
    op_names = {o["name"] for o in ops}

    # The roster pills are promises to the Board — scrub them against the same
    # arsenal the loadout is held to, or the agent decorates candidates with
    # tools that don't exist (or that no Member may ever carry).
    allowed = (inv.get("skills", set()) | inv.get("commands", set())
               ) if inv is not None else None

    members = []
    for m in proposal.get("members") or []:
        if not isinstance(m, dict) or not m.get("name"):
            continue
        op = str(m.get("operation") or "").strip()
        if op not in op_names:
            raise ValueError(
                f"member {m['name']!r} assigned to unknown operation {op!r}")
        model = str(m.get("model") or "").strip().lower()
        members.append({
            "name": str(m["name"]).strip(),
            "role": str(m.get("role") or "").strip(),
            "owns": str(m.get("owns") or "").strip(),
            "operation": op,
            "leads": bool(m.get("leads")),
            # Coerced, never fatal — a missing model is not worth losing the
            # org over, and sonnet is the tier a role must argue its way off.
            "model": model if model in _MODEL_TIERS else _DEFAULT_MODEL,
            "skills": [str(s) for s in (m.get("skills") or [])
                       if s and (allowed is None or str(s) in allowed)],
            "gates": [str(g) for g in (m.get("gates") or []) if g],
        })
    if not members:
        raise ValueError("proposal has no members")

    leads = [m for m in members if m["leads"]]
    if len(leads) != 1:  # the agent gets this wrong occasionally; the org can't be headless
        for m in members:
            m["leads"] = False
        members[0]["leads"] = True

    # The firm's ONE goal. Coerced to shape here, REQUIRED at commit — a firm
    # with no number cannot fail, only be busy, and the Board must see and
    # own the number before the hire. None (agent omitted it) is survivable
    # on the roster screen, where the Board writes one; not past it.
    ns = proposal.get("north_star")
    north_star = None
    if isinstance(ns, dict) and str(ns.get("target") or "").strip():
        mv = ns.get("metric_value")
        north_star = {
            "target": str(ns["target"]).strip()[:300],
            "metric_value": mv if isinstance(mv, (int, float)) else None,
            "metric_unit": str(ns.get("metric_unit") or "").strip()[:60],
            "why": str(ns.get("why") or "").strip()[:300],
        }

    return {
        "firm_id": fid,
        "name": str(proposal.get("name") or fid).strip(),
        "premise": str(proposal.get("premise") or "").strip(),
        "north_star": north_star,
        "operations": ops,
        "members": members,
        "loadout": {"mcp": _loadout("mcp"), "skills": _loadout("skills"),
                    "commands": _loadout("commands")},
        "first_units": [
            {"name": str(u["name"]).strip(),
             "member": str(u.get("member") or "").strip(),
             "why": str(u.get("why") or "").strip()}
            for u in proposal.get("first_units") or []
            if isinstance(u, dict) and u.get("name")
        ],
        # Held back until the Board asks to reroll — advice they haven't earned the
        # right to need yet, and noise on a draft they're about to accept.
        "reroll_tips": [str(t).strip() for t in (proposal.get("reroll_tips") or []) if t][:3],
    }


def commit(root: Path, proposal: dict[str, Any]) -> dict[str, Any]:
    """Scaffold the workspace and write the approved org.

    Everything before this point was a conversation. This is the moment the
    firm exists. Routed through the same ``run_init`` + service layer a
    hand-seeded firm uses, so nothing about this firm's Records betrays that
    it was born in a browser.
    """
    from firm.cli.init import run_init
    from firm.services import contract as contract_svc
    from firm.services import member as member_svc
    from firm.services import operation as operation_svc

    try:
        proposal = _validate(proposal)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    fid = proposal["firm_id"]
    # A firm cannot finish founding without its goal (fork 003: four firms
    # shipped with goal-count zero and north_star null — unfalsifiable by
    # construction). The roster screen guarantees one is present; a commit
    # without one is a bug or a bypass, and both should stop here.
    ns = proposal.get("north_star")
    if not (isinstance(ns, dict) and str(ns.get("target") or "").strip()):
        return {"ok": False, "error": "the firm has no goal — write the north "
                                      "star before hiring; a firm with no "
                                      "number cannot fail, only be busy"}
    root = root.resolve()
    workspace = (root / fid).resolve()
    if workspace.parent != root:   # a firm_id with slashes must not escape the root
        return {"ok": False, "error": "refusing to found a firm outside the firms root"}
    if get_db_path(workspace).exists():
        return {"ok": False, "error": f"a firm already lives at {workspace}"}

    # BEFORE ANY STATE IS WRITTEN: is base even on this machine? (#118, DoD 1)
    #
    # Above the mkdir on purpose. This reading is a fact about the host at the
    # moment the firm was made, and taking it after the workspace exists would
    # be taking it about a machine the founding had already started changing.
    # It never refuses anything: base absent is degraded, never broken, and a
    # firm founded without base is still a firm.
    from firm.services import base_ready
    base_before = base_ready.check()

    workspace.mkdir(parents=True, exist_ok=True)
    if run_init(workspace, force=False, demo=False, install_hooks_flag=False) != 0:
        return {"ok": False, "error": "could not initialize the firm workspace"}
    from firm.services import base_domain
    base_wire = base_domain.wire_workspace(workspace, fid, proposal)

    # The firm has a base tier now, so ask the question a Member will ask:
    # does `base cadre` run with THIS FIRM'S BASE_HOME (#117 spawns every
    # Member into it). If it does not, `ensure` installs Cadre's manifest into
    # the firm's own tier -- never the operator's -- and reads the tier again
    # (#118 DoD 4: usable with no manual follow-up). Never raises, never
    # refuses: a gap that remains is named in the result and on the readiness
    # screen.
    base_state = base_ready.ensure(workspace)

    conn = connect(get_db_path(workspace))
    try:
        repo.create(conn, "firm", {
            "id": fid,
            "name": proposal["name"],
            "description": proposal["premise"],
            "north_star": ns["target"],
        })

        # The number, as a Goal row — the denominator every drift verdict,
        # brief, and goal-health banner divides by. Board-authored: the Board
        # read and could edit it on the roster screen, so committing IS the
        # approval. Members propose theirs later via `firm goal propose`.
        from firm.services import goal as goal_svc
        metric: dict[str, Any] = {}
        if ns.get("metric_value") is not None:
            metric["value"] = ns["metric_value"]
        if ns.get("metric_unit"):
            metric["unit"] = ns["metric_unit"]
        goal_svc.create_goal(conn, fid, {
            "target": ns["target"],
            "parent_entity_type": "firm",
            "parent_entity_id": fid,
            "level": "firm",
            **({"metric": metric} if metric else {}),
        })

        # Members before Operations: an Operation names its owner, not the reverse.
        # Lead first — everyone else reports to them, so they must exist to be pointed at.
        lead_id: str | None = None
        by_name: dict[str, str] = {}
        hired: list[dict[str, Any]] = []
        for m in sorted(proposal["members"], key=lambda m: not m["leads"]):
            con = contract_svc.create_contract(conn, fid, {
                "name": f"{m['name']} — {m['role']}",
                "runtime_type": "claude_code",
                "skill_loadout": {"skills": m["skills"]},
                # Trust is earned. A new hire's gates are declared up front and
                # relaxed later — this list is what the Board must sign off on.
                "validation_config": {"gates_required": m["gates"]},
                # Generous on purpose: the orchestrator's 300s fallback kills a
                # first real run mid-work, and a founded firm's first
                # experience must never be a timeout to troubleshoot. The
                # Board tightens this per contract later if they want to.
                # (pulse_config is the key _contract_timeout_sec reads.)
                # `model` is the slate's tier pick, Board-approved on the
                # roster screen; a contract with no model inherits the
                # operator's session default — Opus, at Opus prices.
                "pulse_config": {"timeout_sec": 1800, "model": m["model"]},
            })
            mem = member_svc.create_member(conn, fid, {
                "name": m["name"],
                "role": m["role"],
                "description": m["owns"],
                "contract_id": con["id"],
                "suggested_skills": m["skills"],
                "reports_to_member_id": None if m["leads"] else lead_id,
            }, cwd=str(workspace))
            repo.update(conn, "contract", con["id"], {"member_id": mem["id"]})
            if m["leads"]:
                lead_id = mem["id"]
            by_name[m["name"]] = mem["id"]
            hired.append({"id": mem["id"], "name": m["name"], "role": m["role"]})

        first_op_id: str | None = None
        for op in proposal["operations"]:
            owner = next(
                (m for m in proposal["members"]
                 if m["operation"] == op["name"] and m["leads"]),
                next((m for m in proposal["members"]
                      if m["operation"] == op["name"]), None),
            )
            op_row = operation_svc.create_operation(conn, fid, {
                "name": op["name"],
                "description": op["purpose"],
                "owner_member_id": by_name.get(owner["name"]) if owner else None,
            })
            if first_op_id is None:
                first_op_id = op_row["id"]

        # First units — the reason the first pulse DOES something. Firms used
        # to be born with an empty queue, so the inaugural pulse answered
        # "load=0 (no queued Units)" and the whole ceremony ended in silence.
        if proposal.get("first_units") and first_op_id:
            from datetime import date, timedelta

            from firm.services import project as project_svc
            from firm.services import unit as unit_svc
            proj = project_svc.create_project(conn, fid, {
                "name": "First shift",
                "description": "The founding slate — the work this firm was born holding.",
                "operation_id": first_op_id,
                "owner_member_id": lead_id,
                "due_date": (date.today() + timedelta(days=7)).isoformat(),
            })
            for u in proposal["first_units"]:
                unit_svc.create_unit(conn, fid, {
                    "name": u["name"],
                    "project_id": proj["id"],
                    "description": u.get("why") or "",
                    # An unassigned pending unit is invisible to compute_load —
                    # a name that no longer matches (rename races) falls to the
                    # lead rather than falling out of the firm's working set.
                    "assignee_member_id": by_name.get(u.get("member")) or lead_id,
                    "status": "pending",
                })

        conn.commit()
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": f"the org did not take: {exc}"}
    finally:
        conn.close()

    # The firm's state, written where base's ingest is already looking. The
    # manifest has always declared these three files; until `base_export`
    # existed nothing wrote them, so a founded firm's roster never reached its
    # own graph (issue #4). Never raises: a firm that cannot export is degraded,
    # not un-founded.
    from firm.services import base_export
    exported = base_export.export(workspace, fid)

    return {
        "ok": True,
        "firm_id": fid,
        "name": proposal["name"],
        "workspace": str(workspace),
        "hired": hired,
        # Reported separately from base_graph, because they fail separately:
        # the domain wire is about rules reaching Members, this is about the
        # roster reaching the graph. One flag covering both would hide either.
        "base_export": exported,
        # base_graph is TRUE only when the wire is live end to end. It used
        # to be true whenever `base scaffold` exited 0, which is how a firm
        # whose domain injects nothing was reported as wired.
        "base_graph": bool(base_wire.get("live")),
        "base_wire": base_wire,
        # Two readings, taken at two different moments on purpose.
        # base_present is from BEFORE the workspace existed, which is the
        # question DoD 1 asks: did founding know before it started making a
        # firm. base_cadre_runs is read in the FIRM'S OWN TIER once it exists,
        # which is the question a Member asks: does the command I am told to
        # run actually run where I run it. It is check's own verdict -- the
        # manifest is in that tier AND the command runs there AND the base may
        # be run at all -- because the process reading alone is True for a base
        # resolving `cadre` from somewhere else (PR 127 G2, F2). That reading
        # is still reported, as base_ready["extension_runs"].
        "base_present": bool(base_before.get("base_present")),
        "base_cadre_runs": bool(base_state.get("ok")),
        "base_ready": base_state,
    }
