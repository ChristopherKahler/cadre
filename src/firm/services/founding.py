"""Founding a firm — the one path, and the shape a proposal has to hold.

Moved here from `dashboard/founding.py` for #135, unchanged. It sat in the
dashboard package because the hub was the only door; `cadre init --proposal`
is a second door onto the same path, and a CLI importing from the dashboard
is the wrong direction, and this is the end of that dependency rather than
another link in it.

ONE EXCEPTION, and it is stated because a reader will otherwise trust the
rule: `_inventory` reads the operator's Armory through
`firm.dashboard.discovery`, `exclusions` and `inventory`, so it carries a
function-local dashboard import with it. Leaving it behind would have meant
`cli/init.py` importing the dashboard instead, which is worse and which R18
forbids; moving those three modules is a larger change than #135. They are
services wearing a dashboard package name, and that is an issue of its own.

`dashboard/founding.py` keeps the hub's job machinery — the prompts, the
narrator, the arsenal, the four job functions — and binds these names back
into its own globals.

Not to be confused with `firm.services._validate`, which is a different
module about entity references. This `_validate` is the proposal's shape.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.core.db import connect, get_db_path
from firm.core.proc import popen_utf8

_FIRM_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_MODEL_TIERS = ("fable", "opus", "sonnet", "haiku")
_DEFAULT_MODEL = "sonnet"


# The shape, with placeholder values. Every key the command reads is here and
# nothing else is: a key in this document that `_validate` discards would
# teach a session to write something that vanishes.
PROPOSAL_TEMPLATE: dict[str, Any] = {
    "firm_id": "your-firm-id",
    "name": "Your Firm",
    "premise": "One sentence: what this firm turns input into, and for whom.",
    "north_star": {
        "target": "The one measurable outcome the whole firm is judged on",
        "metric_value": 0,
        "metric_unit": "units",
        "why": "Why this number and not another",
    },
    "gates": [
        "A move that needs the Board's approval before it happens",
    ],
    "operations": [
        {
            "name": "Operation One",
            "purpose": "What this department is for",
            "goal": {
                "target": "This operation's own measurable outcome",
                "metric_value": 0,
                "metric_unit": "units",
                "why": "Why this number",
            },
        },
    ],
    "members": [
        {
            "name": "Lead Name",
            "role": "CEO / General Manager",
            "owns": "Runs the firm; translates Board direction into Operations",
            "operation": "Operation One",
            "leads": True,
            "reports_to": None,
            "model": "sonnet",
            "domains": ["firm-strategy"],
            "skills": [],
            "gates": [],
        },
        {
            "name": "Manager Name",
            "role": "Engineering Manager",
            "owns": "Owns the build team and the technical standard",
            "operation": "Operation One",
            "leads": False,
            "reports_to": "Lead Name",
            "model": "sonnet",
            "domains": ["engineering"],
            "skills": [],
            "gates": [],
        },
        {
            "name": "Specialist Name",
            "role": "Backend Engineer",
            "owns": "Server code, data models, business logic",
            "operation": "Operation One",
            "leads": False,
            "reports_to": "Manager Name",
            "model": "sonnet",
            "domains": ["backend"],
            "skills": [],
            "gates": [],
        },
    ],
    "first_units": [
        {
            "name": "The first piece of work this firm was born holding",
            "member": "Specialist Name",
            "why": "Why it is first",
        },
    ],
    "loadout": {
        "mcp": [{"name": "an-mcp-server", "why": "what the firm needs it for"}],
        "skills": [{"name": "a-skill", "why": "what the firm needs it for"}],
        "commands": [{"name": "a-command", "why": "what the firm needs it for"}],
    },
}

# One line per key, and the line says which of three fates the key has:
# REFUSED (the command stops and writes nothing), COERCED (the command
# substitutes the named default), DROPPED (a malformed entry disappears and
# the rest is kept). Written from the code, not from memory: `_validate` at
# `founding.py:256-365` and `commit` at `:1039-1249`, main 20bdc5a0.
LEGEND: list[tuple[str, str]] = [
    ("firm_id",
     "REFUSED unless it matches ^[a-z][a-z0-9-]{1,31}$ (lowercased first)."),
    ("name",
     "COERCED to firm_id when empty."),
    ("premise",
     "COERCED to an empty string when absent."),
    ("north_star.target",
     "REFUSED when empty: a firm with no number cannot fail, only be busy."),
    ("north_star.metric_value",
     "COERCED to null unless it is a number."),
    ("north_star.metric_unit / .why",
     "COERCED: trimmed to 60 and 300 characters."),
    ("gates[]",
     "FIRM-LEVEL approvals. Added to every member's own gates on every "
     "contract. COERCED to none when absent, which founds today's firm."),
    ("operations[].name",
     "REFUSED when the list is empty. An entry with no name is DROPPED."),
    ("operations[].purpose",
     "COERCED to an empty string."),
    ("operations[].goal",
     "A goal for this operation, in the north star's shape. COERCED to none "
     "when absent, and then the operation simply has no goal of its own."),
    ("members[]",
     "REFUSED when the list is empty. A member with no name is DROPPED "
     "before any other check, so a typo'd key loses the whole person."),
    ("members[].operation",
     "REFUSED unless it names an operation in this same proposal."),
    ("members[].role / .owns",
     "COERCED to empty strings. `owns` becomes the member's description."),
    ("members[].leads",
     "COERCED: if there is not exactly one lead, every flag is cleared and "
     "the FIRST member in the list becomes the lead."),
    ("members[].reports_to",
     "A member NAME in this proposal. REFUSED when it names nobody here, "
     "when it names the member itself, when it makes a cycle, or when the "
     "lead names anyone (the lead reports to the Board). COERCED to the "
     "lead when absent, which is what every firm founded before today has."),
    ("members[].model",
     "COERCED to sonnet unless it is one of fable, opus, sonnet, haiku."),
    ("members[].domains",
     "The base domains this role owns. COERCED to an empty list when absent. "
     "The firm's own domain block is derived from the roster either way."),
    ("members[].skills",
     "COERCED to strings. Nothing is filtered on this path."),
    ("members[].gates",
     "This member's own approvals, unioned with the firm-level gates above."),
    ("first_units[].name",
     "DROPPED when absent. The whole list is optional."),
    ("first_units[].member",
     "COERCED: a name that is not on the roster falls to the lead rather "
     "than out of the firm."),
    ("first_units[].why",
     "COERCED to an empty string; becomes the unit's description."),
    ("loadout.mcp / .skills / .commands",
     "Each item is {name, why}. Non-objects, missing names and duplicates "
     "are DROPPED; `why` is trimmed to 200 characters."),
]

# Two sentences that stop a session writing something that has no destination.
NOTES: list[str] = [
    "CLI tools are not attached here. A member's CLI access is assigned at "
    "wiring (the Train screen), not at founding, so this proposal has no "
    "loadout.cli and a `cli` key would be discarded.",
    "A NEVER is not a field. A NEVER is enforced by leaving the tool out of "
    "the loadout and by the policy gate, never by prose in a description.",
    "reports_to is the authority chart, not the collaboration chart. Work "
    "passes sideways between peers through Unit dependencies; the chart only "
    "says who sets direction and who escalates to whom.",
]


def proposal_template() -> tuple[str, str]:
    """Return ``(json_for_stdout, legend_for_stderr)``.

    Two values rather than one printed blob, so the caller decides the
    streams and a test can assert on each half without parsing prose.
    """
    body = json.dumps(PROPOSAL_TEMPLATE, indent=2) + "\n"
    width = max(len(key) for key, _ in LEGEND)
    lines = ["The proposal schema. Fill the placeholders and pass the JSON "
             "back with --proposal.", ""]
    for key, meaning in LEGEND:
        lines.append(f"  {key.ljust(width)}  {meaning}")
    lines.append("")
    lines.extend(f"  {note}" for note in NOTES)
    return body, "\n".join(lines) + "\n"


def _resolve_chart(members: list[dict[str, Any]],
                   lead_name: str) -> list[dict[str, Any]]:
    """#135. Settle every member's `reports_to` and return the HIRE ORDER.

    Depth order, not a second pass. `create_member` validates the foreign key
    it is given (`services/member.py`, `validate_fk(conn, "member",
    data.get("reports_to_member_id"))`), so hiring a member only after their
    parent exists keeps that check meaningful. The alternative — hire flat and
    then `repo.update` the parents in — writes the tree through a path that
    validates nothing.

    Cycle detection is the same walk: a member the walk never reaches has no
    path to the lead, which is what a cycle IS here. One algorithm, not two.

    The rules, in the order they are applied:

      * the lead reports to the Board, so a lead naming anyone is refused
        rather than quietly ignored;
      * an absent or null `reports_to` means the lead, which is exactly what
        every firm founded before #135 has and is what makes an old-shape
        proposal found today's firm;
      * a name nobody on the roster carries, a member naming itself, and a
        cycle are each refused, naming the member and the value.
    """
    by_name = {m["name"]: m for m in members}
    for m in members:
        target = str(m.get("reports_to") or "").strip()
        if m["name"] == lead_name:
            if target:
                raise ValueError(
                    f"the lead {m['name']!r} reports to the Board, not to "
                    f"{target!r} — remove reports_to from the lead")
            m["reports_to"] = None
            continue
        if not target:
            m["reports_to"] = lead_name
            continue
        if target == m["name"]:
            raise ValueError(f"member {m['name']!r} reports to itself")
        if target not in by_name:
            raise ValueError(
                f"member {m['name']!r} reports to {target!r}, who is not on "
                f"this roster")
        m["reports_to"] = target

    order: list[dict[str, Any]] = []
    placed: set[str] = set()
    frontier = [m for m in members if m["reports_to"] is None]
    while frontier:
        order.extend(frontier)
        placed.update(m["name"] for m in frontier)
        frontier = [m for m in members
                    if m["name"] not in placed and m["reports_to"] in placed]
    if len(order) != len(members):
        stuck = sorted(m["name"] for m in members if m["name"] not in placed)
        raise ValueError(
            f"reports_to makes a cycle: {', '.join(stuck)} report to each "
            f"other and never reach the lead")
    return order


def _goal_shape(raw: Any) -> dict[str, Any] | None:
    """#135. The north star's shape, reused for an operation's own goal.

    One function for both so the two can never disagree about what a goal
    looks like — the same reason `_one_spelling` exists one module over.
    """
    if not isinstance(raw, dict) or not str(raw.get("target") or "").strip():
        return None
    value = raw.get("metric_value")
    return {
        "target": str(raw["target"]).strip()[:300],
        "metric_value": value if isinstance(value, (int, float)) else None,
        "metric_unit": str(raw.get("metric_unit") or "").strip()[:60],
        "why": str(raw.get("why") or "").strip()[:300],
    }


def _validate(proposal: dict[str, Any],
              inv: dict[str, set[str]] | None = None) -> dict[str, Any]:
    """Shape the agent's output into something the Board can act on.

    Fails loudly on the two things that would corrupt a firm — a bad id, or a
    Member assigned to an Operation that doesn't exist. Everything else is
    coerced, because a missing "skills" list is not worth losing the org over.

    *inv* is the arsenal index the prompt was built from; loadout picks that
    don't resolve against it are dropped — the Board never reviews a ghost.
    With inv=None (commit-time revalidation) the loadout passes through as-is.

    #135 ADDED four keys and two refusals. The keys — `members[].reports_to`,
    `members[].domains`, `operations[].goal` and a firm-level `gates` — are
    each absent-means-today's-firm, so a proposal written before they existed
    founds exactly the firm it founded before. The refusals are the price of
    naming people by name: duplicate member names become ambiguous the moment
    one member can point at another, and a chart can be cyclic.
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
        {"name": str(o["name"]).strip(),
         "purpose": str(o.get("purpose") or "").strip(),
         # #135: an operation may carry its own measurable outcome. Absent
         # means the operation simply has no goal, which is today's firm.
         "goal": _goal_shape(o.get("goal"))}
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
            # #135: kept raw here and settled by `_resolve_chart` below, once
            # the lead is known — the default is "reports to the lead" and
            # the lead is not decided until the coercion a few lines down.
            "reports_to": m.get("reports_to"),
            # #135: the base domains this role owns, straight to
            # `create_member`'s `suggested_domains`. Absent is an empty list.
            "domains": [str(d).strip() for d in (m.get("domains") or [])
                        if str(d).strip()],
        })
    if not members:
        raise ValueError("proposal has no members")

    # #135. NAMES MUST BE UNIQUE, because `reports_to` names people. Before
    # this key a duplicate name cost one row in `commit`'s `by_name` map and
    # nothing else; now it makes "reports to Sam" ambiguous, and an ambiguous
    # chart resolved by dict order is worse than a refusal.
    names = [m["name"] for m in members]
    duplicated = sorted({n for n in names if names.count(n) > 1})
    if duplicated:
        raise ValueError(
            f"two members share the name {duplicated[0]!r}; reports_to names "
            f"people, so every member needs their own name")

    leads = [m for m in members if m["leads"]]
    if len(leads) != 1:  # the agent gets this wrong occasionally; the org can't be headless
        for m in members:
            m["leads"] = False
        members[0]["leads"] = True

    # #135. Settle the chart and hire in depth order.
    members = _resolve_chart(members, next(m["name"] for m in members
                                           if m["leads"]))

    # The firm's ONE goal. Coerced to shape here, REQUIRED at commit — a firm
    # with no number cannot fail, only be busy, and the Board must see and
    # own the number before the hire. None (agent omitted it) is survivable
    # on the roster screen, where the Board writes one; not past it.
    north_star = _goal_shape(proposal.get("north_star"))

    return {
        "firm_id": fid,
        "name": str(proposal.get("name") or fid).strip(),
        "premise": str(proposal.get("premise") or "").strip(),
        "north_star": north_star,
        # #135: the Board's own approvals, applied to every contract at
        # commit. Absent is none, which is today's firm.
        "gates": [str(g).strip() for g in (proposal.get("gates") or [])
                  if str(g).strip()],
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
    it was born in a browser — or, since #135, at a terminal.
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

        from firm.services import goal as goal_svc

        def _write_goal(target_shape: dict[str, Any], *, level: str,
                        parent_type: str, parent_id: str) -> None:
            """#135. One writer for the firm's goal and an operation's.

            Two call sites building the same row two ways is how a metric
            ends up on one level and not the other; `_goal_shape` already
            made the two shapes one, and this keeps the write one too.
            """
            metric: dict[str, Any] = {}
            if target_shape.get("metric_value") is not None:
                metric["value"] = target_shape["metric_value"]
            if target_shape.get("metric_unit"):
                metric["unit"] = target_shape["metric_unit"]
            goal_svc.create_goal(conn, fid, {
                "target": target_shape["target"],
                "parent_entity_type": parent_type,
                "parent_entity_id": parent_id,
                "level": level,
                **({"metric": metric} if metric else {}),
            })

        # The number, as a Goal row — the denominator every drift verdict,
        # brief, and goal-health banner divides by. Board-authored: the Board
        # read and could edit it on the roster screen, so committing IS the
        # approval. Members propose theirs later via `firm goal propose`.
        _write_goal(ns, level="firm", parent_type="firm", parent_id=fid)

        # #135: the Board's own approvals ride on every contract, unioned
        # with the member's own rather than replacing them. `sorted` so two
        # firms founded from one proposal hold the list in one order.
        firm_gates = set(proposal.get("gates") or [])

        # Members before Operations: an Operation names its owner, not the reverse.
        # #135: the roster arrives in DEPTH order from `_resolve_chart`, so a
        # member's parent is always already hired and `create_member`'s own
        # foreign-key check is doing real work. The lead is first by
        # construction, since the lead is the only member with no parent.
        by_name: dict[str, str] = {}
        lead_id: str | None = None
        hired: list[dict[str, Any]] = []
        for m in proposal["members"]:
            con = contract_svc.create_contract(conn, fid, {
                "name": f"{m['name']} — {m['role']}",
                "runtime_type": "claude_code",
                "skill_loadout": {"skills": m["skills"]},
                # Trust is earned. A new hire's gates are declared up front and
                # relaxed later — this list is what the Board must sign off on.
                "validation_config": {
                    "gates_required": sorted(firm_gates | set(m["gates"]))},
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
                # #135: the role's own base domains. The firm's domain block
                # is still derived from the whole roster by
                # `base_domain.sync`; this is the per-role list the operator's
                # spec documents carry and the column has always had.
                #
                # OMITTED WHEN EMPTY, and that is not tidiness. Measured
                # before the branch existed: passing `[]` writes an empty
                # list where today's code leaves the column NULL, so an
                # old-shape proposal would found a firm that differs from
                # today's by one column on every member. R17 says "exactly
                # the firm it founds today, row for row", and a leg that
                # needs a tolerant helper to read past that difference is a
                # leg with a hole in it. `create_member` only writes the
                # fields it is given.
                **({"suggested_domains": m["domains"]} if m["domains"] else {}),
                "reports_to_member_id": (None if m["reports_to"] is None
                                         else by_name[m["reports_to"]]),
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
            # #135: an operation's own measurable outcome, at its own level.
            # `00-Firm-Design-Methodology.md` §3.9 asks for a goal per level;
            # before this a firm could only hold one.
            if op.get("goal"):
                _write_goal(op["goal"], level="operation",
                            parent_type="operation", parent_id=op_row["id"])
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


_TOP_TIER = "opus"


_FOUNDING_FLAGS = [
    "--print",
    # Board ruling 2026-07-13: the firm only gets created once — architect it
    # on the top tier at max effort, never the operator's session default. The
    # stage choreography absorbs the latency; quality is the point. Shared by
    # the wiring and Co-Board briefing agents (they import these flags).
    # Alias, never a pinned id: a pin goes stale silently the next time
    # Anthropic ships a tier (it sat on claude-opus-4-8 through Opus 5).
    "--model", _TOP_TIER,
    "--effort", "max",
    "--output-format", "stream-json",
    "--verbose",
    # Token-level deltas. Without this a `--print` run is one silent assistant turn:
    # the agent reads two docs, thinks for ninety seconds, and dumps a JSON blob. The
    # Board stares at a bar. With it — plus a prompt that tells the agent to narrate —
    # they watch it reason about their business in real time, which is the difference
    # between a spinner and a deliberation.
    "--include-partial-messages",
    "--dangerously-skip-permissions",
    "--strict-mcp-config",
]


NARRATION_CONTRACT = """\

## Narrate as you work

Think out loud where the Board can see it. Write lines beginning with `· ` (middot,
space). The Board reads these while they wait — they are the only window into what
you're doing, so make them worth reading.

**Write your first line before you do anything else** — before you read a file, before
you plan. The Board is staring at an empty screen until you speak.

**Four to six lines. No more.** Each one is a *conclusion you reached*, stated whole —
not a step in a checklist. Consolidate: if you made five small decisions that all serve
one judgment, that is ONE line about the judgment. A line that only makes sense as item
three of a list is the wrong line.

Good: `· They record the talking head themselves — so nobody films, nobody directs.
That kills two roles I'd otherwise have staffed, and it means the whole org sits
downstream of the camera.`

Bad (too granular, too many, reads as a checklist):
`· Reviewing the brief.` `· Three platforms noted.` `· Deciding on operations.`
`· Assigning gates.` `· Naming members.`

Write them as you genuinely arrive at each judgment, not all at once at the end. Then
output what you were asked for. Nothing between or after the `· ` lines except the
output itself.
"""


_FOUNDING_PROMPT = """\
You are the founding agent for a new Cadre firm. The Board has described a
business in their own words. Design the organization that runs it.

## The Board's brief

{brief}

## What Cadre is

A Firm is a company of AI Members. Each Member has a Contract (what they may
run and which skills they carry) and claims Units (atomic work) inside
Operations (departments) toward Goals. A Gate is Board approval, required for
anything significant. The Board is the human. They govern; they do not do the
work.

## House rules on org design

The full house docs are inlined below — they are already in front of you.
Do NOT read any files; everything you need is here.

__HOUSE_RULES__

## The operator's arsenal

Everything below actually exists on the operator's machine. When you design
the org, ALSO design its starting loadout — which of these this firm needs,
and why. Recommend ONLY names that appear here, spelled exactly as written;
anything invented is silently discarded. Be lean: recommend what the work
needs, usually three to ten items across all three lists. The Board reviews
your picks with your rationale next to each — the rationale is what they read.

__INVENTORY__

## How to design this org

- Start from the work, not from a template. What must happen every week for
  this business to move? Those are your Operations.
- Staff the full shape. Roles are free: a Member with no work assigned is
  skipped by the pulse and costs nothing, while a missing role hides the real
  shape and forces another Member to double up. Prefer specialists to
  generalists. When a spec is given, every role in it becomes a Member: none
  merged, none dropped.
- Every Member owns an outcome, not a tool. "Grows the audience" is a role.
  "Uses Instagram" is not.
- Name them like people, because the Board will talk to them like people.
  One word. Distinct. No cute AI puns, no "Bot", no "AI" in the name.
- Give exactly one Member the lead; the lead reports to the Board. Every other
  Member names who they report to (reports_to). Build a multi-level chart with
  a span of control near four, five at most, never a flat one where everyone
  reports to the lead. A checker never reports to the people or the work it
  checks; it reports to the lead, outside the operation it checks. When a spec
  names the chart, use it as written.
- Be explicit about what needs a Gate. Anything published, anything spent,
  anything sent to another human. Default to gating; trust is earned later.
- Staff the model like you staff the org. Every run bills the Board, and the
  Member's model is the cost lever. Default to "sonnet". Reserve "opus" for a
  role whose whole job is judgment — usually the lead, sometimes nobody. Use
  "haiku" for mechanical, high-frequency work. A four-Member firm running
  all-Opus bills like a law firm.
- The firm gets ONE goal, not a list. Pick the single measurable outcome
  that, if true at the end of a quarter, means this firm worked. A firm with
  no number cannot fail — it can only be busy, which is worse. Give the Board
  a number to argue with, not prose to admire.

- An Operation may carry its own goal as well, one level down from the firm's.
  The firm still has exactly ONE north star; an Operation's goal is how that
  department knows it is holding up its end. Give one only where there is a
  real number to give.
- Name the Board's own approvals once, at the firm level, in "gates". Those
  ride on every Member's contract on top of whatever that Member needs
  approval for. A Member's own "gates" are the ones specific to their work.
- Give every Member the base domains their role owns, in "domains" — the
  narrow subjects that Member reads and writes. A specialist with its own
  domain retrieves a tighter context on every run than one sharing the firm's.

## Output

Return ONLY a JSON object, no prose before or after, no code fence:

{{
  "firm_id": "kebab-case-slug, max 32 chars, letters/digits/hyphens, starts with a letter",
  "name": "The firm's display name, title case",
  "premise": "One sentence: what this company exists to do. The Board's words, sharpened.",
  "north_star": {{
    "target": "The firm's ONE goal — a sentence with a number in it. If it is true at the end of the quarter, the firm worked.",
    "metric_value": 5,
    "metric_unit": "what the number counts, e.g. pages/week — '' if the target has no clean unit",
    "why": "One line: why THIS number proves the premise."
  }},
  "gates": ["What the Board must approve before it happens — applies to every Member"],
  "operations": [
    {{
      "name": "Department name",
      "purpose": "One line — what this department is accountable for.",
      "goal": {{"target": "This department's own measurable outcome — omit the whole key if there is no real number", "metric_value": 3, "metric_unit": "what the number counts", "why": "One line."}}
    }}
  ],
  "members": [
    {{
      "name": "Onename",
      "role": "Their title",
      "owns": "One sentence: the outcome they are accountable for.",
      "operation": "The name of the Operation they work in — must match one above exactly",
      "leads": true or false,
      "reports_to": "The NAME of the Member they report to — must match one above exactly. null for the lead, who reports to the Board.",
      "domains": ["the base domains this role owns, e.g. backend, design — [] if none obvious"],
      "model": "opus, sonnet, or haiku — the Claude tier this Member runs on (fable exists above opus; do not use it unless the Board asks)",
      "skills": ["skill or command names they'd carry — [] if none obvious"],
      "gates": ["what this Member must get Board approval for, in plain words"]
    }}
  ],
  "first_units": [
    {{"name": "The first real piece of work", "member": "Onename", "why": "One line."}}
  ],
  "reroll_tips": [
    "Advice to the Board on how to brief me better, if they don't like this org."
  ],
  "loadout": {{
    "mcp": [{{"name": "exact server name from the arsenal", "why": "who uses it, for what — one line"}}],
    "skills": [{{"name": "exact skill name from the arsenal", "why": "one line"}}],
    "commands": [{{"name": "exact command name from the arsenal", "why": "one line"}}]
  }}
}}

Exactly one Member has "leads": true, and only the lead has "reports_to": null.
Every other Member's "reports_to" names a Member above them in this same list,
with no cycles. Every Member's "operation" matches an Operation name exactly.
Every Member's "model" is one of opus, sonnet, haiku.
Give two to four first_units — real work this firm could start on tonight,
not setup chores.

`reroll_tips`: two or three specific things the Board could have told you that
would have produced a sharper org. Name what you had to *guess* at — the thing
you inferred because they didn't say. "You didn't say whether you publish or
just draft, so I gated everything" is a useful tip. "Be more specific" is not.
Write them as instructions to the Board, not observations about yourself.
"""


def _framework_root() -> Path:
    """Repo root — where the house docs and the framework tree are READ from.

    It is not where the founding agent runs, and the sentence that said so was
    wrong even before #143 moved that working directory: `_house_rules()` reads
    both documents through `root / rel`, an absolute path, and inlines their
    TEXT into the prompt, so the agent is never handed a path to open. The old
    sentence is what made the tier fix look as though it would break doc
    resolution, and it put a wrong fact into a design document before anyone
    read the code it described.
    """
    return Path(__file__).resolve().parents[3]


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the proposal object out of the agent's final message."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("founding agent returned no JSON object")
    return json.loads(text[start:end + 1])


def _inventory() -> tuple[str, dict[str, set[str]]]:
    """The operator's real arsenal, compacted for the founding prompt.

    The founding agent recommends the firm's starting loadout — so it must
    see what actually exists, and _validate drops anything it invents.
    Skills carry a line of description; commands ride as names only (there
    are ~150 of them — the whole point is that the Board stops scrolling
    through that list). Returns (prompt_text, validation_index).
    """
    from firm.dashboard import discovery, exclusions, inventory

    # The Armory is the survey of record (machine tier, shared with Train and
    # the Floor's equip picker) — founding demands fresh CLI identity probes
    # because its prompt promises "probed just now". The operator's global
    # exclusion list is a hard boundary: an excluded item never enters the
    # agent's head, and the validation index drops it even if the agent
    # hallucinates the name.
    ex = exclusions.load()
    inv = inventory.ensure(max_cli_age_sec=3600)
    mcp = [s for s in inv.get("mcp") or []
           if s.get("available") and s["name"] not in set(ex["mcp"])]
    skills = [sk for sk in inv.get("skills") or []
              if sk["name"] not in set(ex["skills"])]
    commands = [c for c in inv.get("commands") or []
                if c["name"] not in set(ex["commands"])]
    clis = [c for c in inv.get("cli") or []
            if c["present"] and c["name"] not in set(ex["clis"])]

    lines = ["### MCP servers (firm-wide armory — every Member shares these)",
             "BASE is NOT in this list and never will be: it is a CLI tool with "
             "its own card, and its graph is read and written through the `base` "
             "CLI. Never describe any MCP server as the surface for BASE or its "
             "graph."]
    for s in mcp:
        keys = f" (needs {', '.join(s['needs_keys'])})" if s.get("needs_keys") else ""
        lines.append(f"- {s['name']}{keys}")
    lines.append("")
    lines.append("### CLI tools (host machine — every Member can shell out to these)")
    lines.append(
        "Probed on this machine moments ago; this list is ground truth. A tool "
        "marked LIVE is installed AND signed in — design the org around it. "
        "Never treat a capability as absent when a LIVE tool below provides it: "
        "a firm was once founded believing it had no email while a signed-in "
        "Google Workspace CLI sat right here. Do not repeat that.")
    for c in clis:
        lines.append(f"- {discovery.cli_prompt_line(c)}")
    lines.append("")
    lines.append("### Skills (attachable per Member)")
    for sk in skills:
        desc = (sk.get("description") or "").strip().replace("\n", " ")[:90]
        lines.append(f"- {sk['name']} — {desc}" if desc else f"- {sk['name']}")
    lines.append("")
    lines.append("### Commands (attachable per Member; names only)")
    lines.append(", ".join(c["name"] for c in commands))

    index = {
        "mcp": {s["name"] for s in mcp},
        "skills": {sk["name"] for sk in skills},
        "commands": {c["name"] for c in commands},
    }
    return "\n".join(lines), index


def _house_rules() -> str:
    """The org-design house docs, inlined verbatim into the prompt.

    Identical input to what the agent used to fetch itself — but each Read
    was a full model round-trip, and the two of them were most of the silent
    first minute. Inlining is lossless: same text, zero tool turns.
    """
    root = _framework_root()
    parts = []
    for rel in ("docs/FIRM-SCAFFOLDING-GUIDE.md",
                "claude/cadre-framework/frameworks/org-design.md"):
        try:
            parts.append(f"### {rel}\n\n{(root / rel).read_text(encoding='utf-8')}")
        except OSError:
            parts.append(f"### {rel}\n\n(unavailable — design from the rules above)")
    return "\n\n".join(parts)


def founding_prompt(brief: str, arsenal: str) -> str:
    """The founding agent's prompt, built once for both doors.

    House rules and the arsenal are token-swapped AFTER `.format`, because
    the inlined documents contain literal braces that `str.format` would
    choke on. That ordering is not decoration and it is why this is a
    function rather than two call sites doing the same three steps.
    """
    return (_FOUNDING_PROMPT.format(brief=brief)
            .replace("__HOUSE_RULES__", _house_rules())
            .replace("__INVENTORY__", arsenal)
            + NARRATION_CONTRACT)


def found_from_brief(root: Path, brief: str, *,
                     timeout_sec: int = 900) -> dict[str, Any]:
    """Door B: run the founding agent on a written spec, then commit it.

    The hub's agent, not a new one — same flags, same prompt, same
    validation, same `commit`. The difference is only that this waits for
    the answer instead of handing a job id back to a browser.

    The session gets a scratch tier of its own (#143): founding runs before
    a firm exists, so there is no firm tier to point at, and without one the
    agent's base hooks walk up out of the install and into whatever `.base`
    sits above it — on the operator's machine, his own workspace graph.

    Returns `commit`'s own result object, or an `{"ok": False, "error": ...}`
    of the same shape, so the caller has one thing to print and one thing to
    exit on.
    """
    import os
    import shutil
    import subprocess
    import tempfile

    from firm.pulse.spawn import resolve_claude_bin
    from firm.services.base_domain import session_spawn

    claude_bin, detail = resolve_claude_bin()
    if not claude_bin:
        return {"ok": False, "error": f"claude runtime not wired: {detail}"}

    arsenal, inv = _inventory()
    argv = [claude_bin, *_FOUNDING_FLAGS, "-p", founding_prompt(brief, arsenal)]
    env = dict(os.environ)
    env.pop("CADRE_DB_URL", None)      # a founding run has no firm yet
    env.pop("CADRE_DB_TOKEN", None)
    scratch = tempfile.mkdtemp(prefix="cadre-founding-")
    try:
        cwd, env["BASE_HOME"] = session_spawn(home=scratch)
        try:
            proc = popen_utf8(argv, cwd=cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env)
        except OSError as exc:
            return {"ok": False,
                    "error": f"could not spawn the founding agent: {exc}"}

        final = ""
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "result":
                    final = event.get("result") or ""
            proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            proc.kill()
            return {"ok": False, "error": "the founding agent took too long"}

        if not final:
            # A mute failure is the worst kind. Say what the runtime said.
            err = (proc.stderr.read() if proc.stderr else "").strip()
            tail = (err.splitlines()[-1][:200] if err
                    else f"exit code {proc.returncode}")
            return {"ok": False,
                    "error": f"the founding agent returned nothing — {tail}"}

        try:
            proposal = _validate(_extract_json(final), inv=inv)
        except (ValueError, json.JSONDecodeError) as exc:
            return {"ok": False,
                    "error": f"the founding agent's org did not hold up: {exc}"}
        return commit(root, proposal)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
