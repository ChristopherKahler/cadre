"""#135 · `cadre init` founds a complete firm from a proposal. The red set.

Destination: `tests/test_cadre_init_founds.py` on the branch cut from the main
PR 149 lands on. Written as a file first because the branch does not exist
yet (G0 verdict, order 5).

WHAT EVERY LEG DRIVES. The door legs go through `firm.__main__.main(argv)`,
the real command surface a Claude Code session types, so the argparse wiring
is inside the test rather than assumed around it. The schema legs go through
`firm.services.founding.commit`, because that is where the shape is decided
and a leg about the shape should not be able to pass because the CLI happened
to be wired.

WHAT NO LEG DOES. None of them builds its own expected directory, row set or
chart and compares production against it. Each one reads both sides from
production: the chart leg reads `reports_to_member_id` back out of the
database and walks it, the tier legs fingerprint directories rather than
naming files, and the refusal legs assert the `_exit_with` contract measured
at `cli/pulse.py:39-52` rather than "some non-zero code". A leg that builds
its own answer passes when the test and the code are wrong the same way,
which is the failure #136's R2-a shape exists to stop and which #143 caught
three more of last night.

THE PRECONDITION IS ASSERTED IN EVERY LEG THAT HAS ONE. #143 cost this lane
three legs that a mutation walked straight through and one that was red for a
reason unrelated to the defect it named. Where a leg depends on a fact about
the host -- base present or absent, the house docs shipped or not -- it reads
that fact from the product's own probe and says which branch it took.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from firm.core.db import connect, get_db_path
from firm.core import repo

# The seam the CLI legs name. `--proposal` routes here; `run_init` keeps its
# own signature for the bare form.
#   firm/__main__.py   init parser gains --proposal, --proposal-template, --brief
#   firm/cli/init.py   run_found(root: Path, proposal: Path) -> int
#   firm/services/founding.py   commit, _validate, proposal_template
FIRM_ID = "testfirm"


# ---------------------------------------------------------------------------
# Helpers. Each one exists because the alternative is a leg that builds its
# own answer.
# ---------------------------------------------------------------------------

def _chart_proposal(**over: Any) -> dict[str, Any]:
    """A THREE-LEVEL proposal: lead, a manager under the lead, two specialists
    under the manager, and a checker that reports to the lead from outside the
    operation it checks.

    The shape is the operator's methodology in miniature (principles 3 and 4
    of `00-Firm-Design-Methodology.md`), because that is the firm #135 exists
    to let a terminal session found.
    """
    proposal: dict[str, Any] = {
        "firm_id": FIRM_ID,
        "name": "Test Firm",
        "premise": "Turns a proposal into a firm.",
        "north_star": {"target": "ten firms founded", "metric_value": 10,
                       "metric_unit": "firms", "why": "the only number"},
        "gates": ["onboarding a client", "any spend step-up"],
        "operations": [
            {"name": "Build", "purpose": "the work",
             "goal": {"target": "builds pass QA first pass",
                      "metric_value": 90, "metric_unit": "percent",
                      "why": "rework is the cost"}},
            {"name": "Quality", "purpose": "the check"},
        ],
        "members": [
            {"name": "Ada", "role": "CEO", "owns": "the firm",
             "operation": "Build", "leads": True, "reports_to": None,
             "model": "opus", "domains": ["firm-strategy"],
             "skills": [], "gates": ["approve a release"]},
            {"name": "Brac", "role": "Engineering Manager", "owns": "the team",
             "operation": "Build", "leads": False, "reports_to": "Ada",
             "model": "sonnet", "domains": ["engineering"],
             "skills": [], "gates": []},
            {"name": "Cass", "role": "Backend Engineer", "owns": "the server",
             "operation": "Build", "leads": False, "reports_to": "Brac",
             "model": "sonnet", "domains": ["backend", "database"],
             "skills": [], "gates": []},
            {"name": "Dov", "role": "QA Lead", "owns": "acceptance",
             "operation": "Quality", "leads": False, "reports_to": "Ada",
             "model": "sonnet", "domains": ["quality"],
             "skills": [], "gates": []},
        ],
        "first_units": [{"name": "First unit", "member": "Cass",
                         "why": "it is first"}],
        "loadout": {"mcp": [], "skills": [], "commands": []},
    }
    proposal.update(over)
    return proposal


def _old_shape() -> dict[str, Any]:
    """The same firm written the way every proposal before today was written:
    no `reports_to`, no `domains`, no operation goal, no firm gates."""
    p = _chart_proposal()
    p.pop("gates")
    for op in p["operations"]:
        op.pop("goal", None)
    for m in p["members"]:
        m.pop("reports_to", None)
        m.pop("domains", None)
    return p


def _write(tmp_path: Path, proposal: dict[str, Any]) -> Path:
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(proposal), encoding="utf-8")
    return path


def _run(argv: list[str], capsys) -> tuple[int, dict[str, Any] | None, str]:
    """Drive the real command and return ``(rc, last_json_line, stdout)``.

    The result object is taken from the LAST stdout line rather than by
    parsing the whole output, because that is the contract `_exit_with`
    states (`cli/pulse.py:39-52`) and the one a scheduler and a session both
    rely on. `run_init` prints prose on the way past; the result is last.
    """
    from firm.__main__ import main

    rc = main(argv)
    out = capsys.readouterr().out
    last = out.strip().splitlines()[-1] if out.strip() else ""
    try:
        parsed = json.loads(last)
    except json.JSONDecodeError:
        parsed = None
    return rc, parsed, out


def _fingerprint(root: Path) -> str:
    """Every file under *root*, by relative path and content. Named rather
    than listed, so a leg cannot pass by knowing which files to look at."""
    h = hashlib.sha256()
    if not root.is_dir():
        return "ABSENT"
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).replace("\\", "/").encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _rows(workspace: Path, table: str) -> list[dict[str, Any]]:
    conn = connect(get_db_path(workspace))
    try:
        return [dict(r) for r in repo.find(conn, table)]
    finally:
        conn.close()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    firms = tmp_path / "firms"
    firms.mkdir()
    return firms


# ---------------------------------------------------------------------------
# R1, R2, R3 -- Door A founds a real firm
# ---------------------------------------------------------------------------

def test_r1_door_a_founds_the_whole_proposal(root, tmp_path, capsys):
    """Every row the proposal names is in the database afterwards."""
    proposal = _chart_proposal()
    rc, result, _ = _run(["init", str(root), "--proposal",
                          str(_write(tmp_path, proposal))], capsys)

    assert rc == 0 and result and result.get("ok") is True, result
    ws = root / FIRM_ID

    firms = _rows(ws, "firm")
    assert [f["id"] for f in firms] == [FIRM_ID]
    assert firms[0]["north_star"] == proposal["north_star"]["target"]

    members = _rows(ws, "member")
    assert sorted(m["name"] for m in members) == sorted(
        m["name"] for m in proposal["members"])
    assert all(m["contract_id"] for m in members), (
        "a member with no contract has no runtime and no loadout")

    assert sorted(o["name"] for o in _rows(ws, "operation")) == sorted(
        o["name"] for o in proposal["operations"])
    assert [u["name"] for u in _rows(ws, "unit")] == [
        u["name"] for u in proposal["first_units"]]


def test_r2_the_firms_own_tier_is_the_one_that_is_wired(root, tmp_path,
                                                        capsys):
    """Cadre's manifest lands in the FIRM's tier, and the leg says which host
    it measured. With base absent the command still founds (R9) and this leg
    asserts the degraded shape instead of asserting nothing."""
    from firm.services import base_ready

    present = bool(base_ready.check().get("base_present"))
    rc, result, _ = _run(["init", str(root), "--proposal",
                          str(_write(tmp_path, _chart_proposal()))], capsys)
    assert rc == 0 and result["ok"] is True

    ws = root / FIRM_ID
    tier = ws / ".firm" / "base-home" / ".base-gbl"
    if not present:
        assert result["base_present"] is False
        assert result["base_cadre_runs"] is False
        pytest.skip("base is not on this host; R9 owns the degraded case and "
                    "this leg has no tier to read")
    manifests = sorted(p.name for p in (tier / "extensions").glob("*"))
    assert manifests == ["cadre"] or "cadre" in manifests, (
        f"the firm's tier holds {manifests}, which is not exactly Cadre's "
        f"manifest")
    assert result["base_cadre_runs"] is True, (
        "`base cadre` does not run in the firm's own tier, so a Member told "
        "to run it cannot")


def test_r3_the_operators_own_tier_is_untouched(root, tmp_path, capsys,
                                                monkeypatch):
    """Fingerprinted before and after, not intended."""
    operator_home = tmp_path / "operator-home"
    (operator_home / ".base-gbl").mkdir(parents=True)
    (operator_home / ".base-gbl" / "graph.nq").write_text("", encoding="utf-8")
    monkeypatch.setenv("BASE_HOME", str(operator_home))

    before = _fingerprint(operator_home)
    rc, _, _ = _run(["init", str(root), "--proposal",
                     str(_write(tmp_path, _chart_proposal()))], capsys)
    assert rc == 0
    assert _fingerprint(operator_home) == before, (
        "founding a firm changed the operator's own tier")


# ---------------------------------------------------------------------------
# R4, R5 -- the refusals, and the shape of a refusal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mutate,names", [
    (lambda p: p.pop("north_star"), "north star"),
    (lambda p: p["members"][2].__setitem__("operation", "Nowhere"),
     "unknown operation"),
    (lambda p: p["members"][2].__setitem__("reports_to", "Nobody"),
     "unknown reports_to"),
    (lambda p: p["members"][2].__setitem__("reports_to", "Cass"),
     "self-reporting"),
    (lambda p: (p["members"][1].__setitem__("reports_to", "Cass"),
                p["members"][2].__setitem__("reports_to", "Brac")),
     "a cycle"),
    (lambda p: p["members"][0].__setitem__("reports_to", "Brac"),
     "the lead reporting to someone"),
])
def test_r4_r5_a_refusal_is_one_json_object_and_writes_nothing(
        root, tmp_path, capsys, mutate, names):
    """The `_exit_with` contract, not merely a non-zero code.

    #128's defect was an exit code that disagreed with its own line: a
    scheduler recorded a failed run as clean. A session driving `cadre init`
    reads the same two things, so both are asserted here: exit 1, and the
    LAST stdout line is one JSON object with `ok: false` naming the problem.
    """
    proposal = _chart_proposal()
    mutate(proposal)

    rc, result, out = _run(["init", str(root), "--proposal",
                            str(_write(tmp_path, proposal))], capsys)

    assert rc == 1, f"a refusal for {names} exited {rc}"
    assert result is not None, (
        f"the last stdout line for {names} is not JSON: {out.strip()[-200:]}")
    assert result.get("ok") is False
    assert result.get("error"), "a refusal with no reason is not a refusal"
    assert not (root / FIRM_ID).exists(), (
        f"a refusal for {names} left {root / FIRM_ID} behind; a database "
        f"that exists after a refusal is the defect")


# ---------------------------------------------------------------------------
# R6 -- the one-path proof
# ---------------------------------------------------------------------------

def test_r6_both_doors_found_the_same_firm(root, tmp_path, capsys):
    """The hub's `commit` and Door A, one proposal, the same rows.

    Ids and timestamps differ by construction; nothing else may. The
    comparison is over the rows production wrote, not over a row set this
    test built.
    """
    from firm.services.founding import commit

    hub_root = tmp_path / "hub"
    hub_root.mkdir()
    assert commit(hub_root, _chart_proposal())["ok"] is True

    rc, _, _ = _run(["init", str(root), "--proposal",
                     str(_write(tmp_path, _chart_proposal()))], capsys)
    assert rc == 0

    volatile = {"id", "created_at", "updated_at", "contract_id",
                "member_id", "owner_member_id", "reports_to_member_id",
                "project_id", "assignee_member_id", "operation_id",
                "parent_entity_id", "due_date"}

    def scrub(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(({k: v for k, v in r.items() if k not in volatile}
                       for r in rows), key=lambda r: json.dumps(r, default=str,
                                                                sort_keys=True))

    for table in ("firm", "member", "operation", "goal", "contract", "unit"):
        assert scrub(_rows(hub_root / FIRM_ID, table)) == \
               scrub(_rows(root / FIRM_ID, table)), (
            f"the two doors wrote different {table} rows")

    # And the CHART, which ids would have hidden above.
    assert _chart(hub_root / FIRM_ID) == _chart(root / FIRM_ID)


def _chart(workspace: Path) -> dict[str, str | None]:
    """Each member's parent, by NAME, read out of the database."""
    members = _rows(workspace, "member")
    by_id = {m["id"]: m["name"] for m in members}
    return {m["name"]: by_id.get(m["reports_to_member_id"]) for m in members}


# ---------------------------------------------------------------------------
# R7 -- the template IS the schema, proved by round trip
# ---------------------------------------------------------------------------

def test_r7_the_template_round_trips_into_a_firm(root, tmp_path, capsys):
    """Print the template, fill the placeholders, found a firm from it.

    The template's own roster is three levels deep, so this also proves the
    schema can express a chart -- which is the thing the old shape could not
    do and the reason #135 widened it.
    """
    rc, _, out = _run(["init", "--proposal-template"], capsys)
    assert rc == 0
    template = json.loads(out)          # stdout is JSON and nothing else

    template["firm_id"] = FIRM_ID
    template["north_star"]["metric_value"] = 1
    for member in template["members"]:
        member["skills"] = []
    template["loadout"] = {"mcp": [], "skills": [], "commands": []}

    rc, result, _ = _run(["init", str(root), "--proposal",
                          str(_write(tmp_path, template))], capsys)
    assert rc == 0 and result["ok"] is True, result

    chart = _chart(root / FIRM_ID)
    depths = {name: _depth(chart, name) for name in chart}
    assert max(depths.values()) >= 2, (
        f"the template founded a flat firm: {chart}. A template that cannot "
        f"express a chart teaches every session to write a flat one")


def _depth(chart: dict[str, str | None], name: str) -> int:
    depth, seen = 0, set()
    while chart.get(name) and name not in seen:
        seen.add(name)
        name = chart[name]
        depth += 1
    return depth


# ---------------------------------------------------------------------------
# R8, R9, R10 -- the bare form, base absent, and no window
# ---------------------------------------------------------------------------

def test_r8_the_bare_form_says_the_firm_is_not_founded(tmp_path, capsys):
    """Silence here is how #135 happened.

    The absence is asserted alongside the sentence, because the sentence is
    a claim ABOUT the absence and a leg that reads only the prose passes on
    the day the prose stops being true.
    """
    ws = tmp_path / "bare"
    ws.mkdir()
    rc, _, out = _run(["init", str(ws)], capsys)

    assert rc == 0
    assert "--proposal" in out and "--brief" in out, (
        "the bare form does not name the two flags that found a firm")
    assert "not founded" in out.lower()
    assert _rows(ws, "firm") == [], (
        "the bare form wrote a firm row, so the sentence it prints is false")


def test_r9_base_absent_is_degraded_not_broken(root, tmp_path, capsys):
    """base absent must not refuse, and the output must say what is missing.

    NOTHING IS PATCHED HERE, and that is the point. This suite already runs
    with base absent for every test: conftest's autouse `_no_ambient_base`
    replaces `firm.sysconfig.service.which_base` with one that answers None.
    So the leg drives the product's own probe in the state the suite puts it
    in. The first draft of this leg invented a return value for
    `base_ready.check` instead and died inside `base_ready.ensure` on
    `KeyError: 'base_runs'` — a key the real probe returns and the invented
    one had not thought of. A stub of a thing you did not read is a second,
    wrong copy of it.

    The base-PRESENT control cannot live in this suite for the same reason,
    and it is not missing: it is arm S1 at G2, against a real base.
    """
    from firm.services import base_ready

    assert base_ready.check().get("base_present") is not True, (
        "base reads as present inside the suite, so this leg is measuring a "
        "host it was not written for and its name is a lie")

    rc, result, out = _run(["init", str(root), "--proposal",
                            str(_write(tmp_path, _chart_proposal()))], capsys)

    assert rc == 0 and result["ok"] is True, "base absent must not refuse"
    assert result["base_present"] is False
    assert _rows(root / FIRM_ID, "member"), "the firm was not founded"
    assert "base" in out.lower(), (
        "the output does not name what is missing, so an operator learns "
        "about it from a Member's first failed run")


def test_r10_no_site_in_the_door_opens_a_window():
    """The M-W2 counting rule, read on the source: every spawn in the path
    goes through `popen_utf8`, which carries CREATE_NO_WINDOW."""
    import inspect

    from firm.cli import init as cli_init
    from firm.services import founding as svc_founding

    for module in (cli_init, svc_founding):
        assert "subprocess.Popen(" not in inspect.getsource(module), (
            f"{module.__name__} spawns without CREATE_NO_WINDOW")


# ---------------------------------------------------------------------------
# R12 -- the move must not unhook the hub's own tests
# ---------------------------------------------------------------------------

def test_r12_the_re_export_binds_the_names_into_the_dashboard_module(
        monkeypatch):
    """The move must not unhook the tests that patch the moved names.

    MEASURED BEFORE THIS LEG WAS WRITTEN. Across `tests/`: nothing patches
    `founding.commit`; three files CALL it through the dashboard module and a
    re-export keeps them working; `cli/test_base_wire_reporting.py:294`
    patches by string path (`firm.services.base_domain.wire_workspace`),
    which survives; and `test_child_output_is_decoded_as_utf8.py` patches
    `founding._validate`, `_house_rules` and `_inventory` ON THE DASHBOARD
    MODULE, all three of which now live in services.

    So the hazard is exact: the re-export must bind the NAMES into
    `dashboard/founding.py`'s globals. Holding the module instead and calling
    `_svc._validate(...)` would leave those patches pointing at nothing and
    the tests green over unpatched runs.

    Both halves are mechanical. Identity says the two modules name one
    object. The globals check says the dashboard function resolves the name
    from the dict `monkeypatch.setattr` writes into — which is the whole of
    what "the patch still works" means. An earlier draft of this leg drove
    `_run_founding` against a fake runtime instead and proved nothing,
    because the spawn fails before `_validate` is ever reached.
    """
    from firm.dashboard import founding as dash
    from firm.services import founding as svc

    moved = ("commit", "_validate", "_house_rules", "_inventory",
             "_FOUNDING_FLAGS", "NARRATION_CONTRACT", "_FOUNDING_PROMPT")
    for name in moved:
        assert hasattr(svc, name), f"{name} did not move to services"
        assert getattr(dash, name) is getattr(svc, name), (
            f"dashboard.{name} is not the same object as services.{name}, so "
            f"the two doors are two paths again")

    sentinel = object()
    monkeypatch.setattr(dash, "_validate", sentinel)
    assert dash._run_founding.__globals__["_validate"] is sentinel, (
        "_run_founding does not resolve `_validate` from the dashboard "
        "module's globals, so every test that patches it there is testing "
        "nothing")
    assert dash._run_reshuffle.__globals__["_validate"] is sentinel

    # AND THE PATCH MUST BE THE ONE THAT RUNS. The assertion above proves
    # only that the patch landed in the right dict; mutation NI showed that
    # a body calling `_svc._validate(...)` leaves it landed and unused, and
    # this leg green over it. So the run is driven far enough to reach
    # `_validate` for real.
    seen: list[dict[str, Any]] = []

    def spy(proposal, inv=None):
        seen.append(proposal)
        return svc._validate(proposal, inv)

    agent = _FakeAgent(_chart_proposal())
    monkeypatch.setattr(dash, "_validate", spy)
    monkeypatch.setattr(dash, "popen_utf8", agent, raising=True)
    monkeypatch.setattr(dash, "resolve_claude_bin",
                        lambda: ("/usr/bin/claude", "fake"))
    monkeypatch.setattr(dash, "_house_rules", lambda: "(elided)")
    monkeypatch.setattr(dash, "_inventory", lambda: ("(no arsenal)", {}))
    dash._jobs["J12"] = {"status": "running", "proc": None, "narration": []}
    try:
        dash._run_founding("J12", "a two-person writing firm")
    finally:
        dash._jobs.pop("J12", None)

    assert seen, (
        "`_run_founding` reached its own `_validate` without going through "
        "the patched name, so every test that patches it on the dashboard "
        "module is testing an unpatched run")


# ---------------------------------------------------------------------------
# R13 to R17 -- the four keys the schema gained
# ---------------------------------------------------------------------------

def test_r13_the_chart_is_the_chart_that_was_asked_for(root, tmp_path,
                                                       capsys):
    """Four levels in, four levels out, read back by name.

    And the checker: Dov checks the Build operation's work and reports to the
    lead, not into Build. Principle 4 of the methodology is a reporting fact,
    so it is asserted as one.
    """
    rc, _, _ = _run(["init", str(root), "--proposal",
                     str(_write(tmp_path, _chart_proposal()))], capsys)
    assert rc == 0

    chart = _chart(root / FIRM_ID)
    assert chart == {"Ada": None, "Brac": "Ada", "Cass": "Brac", "Dov": "Ada"}, (
        f"the chart founded is {chart}, not the one the proposal named")


def test_r14_each_members_domains_are_recorded(root, tmp_path, capsys):
    rc, _, _ = _run(["init", str(root), "--proposal",
                     str(_write(tmp_path, _chart_proposal()))], capsys)
    assert rc == 0

    by_name = {m["name"]: m for m in _rows(root / FIRM_ID, "member")}
    assert _listy(by_name["Cass"]["suggested_domains"]) == ["backend",
                                                            "database"]
    assert _listy(by_name["Ada"]["suggested_domains"]) == ["firm-strategy"]


def _listy(value: Any) -> list[str]:
    """`suggested_domains` is stored as JSON; read it the way the row holds
    it rather than assuming which."""
    if isinstance(value, str):
        return json.loads(value)
    return list(value or [])


def test_r15_an_operation_with_a_goal_gets_one_at_its_own_level(root,
                                                                tmp_path,
                                                                capsys):
    rc, _, _ = _run(["init", str(root), "--proposal",
                     str(_write(tmp_path, _chart_proposal()))], capsys)
    assert rc == 0

    goals = _rows(root / FIRM_ID, "goal")
    levels = sorted((g["level"], g["target"]) for g in goals)
    assert ("firm", "ten firms founded") in levels
    assert ("operation", "builds pass QA first pass") in levels
    assert len([g for g in goals if g["level"] == "operation"]) == 1, (
        "Quality has no goal in the proposal and must not have one here")


def test_r16_firm_gates_reach_every_contract(root, tmp_path, capsys):
    rc, _, _ = _run(["init", str(root), "--proposal",
                     str(_write(tmp_path, _chart_proposal()))], capsys)
    assert rc == 0

    ws = root / FIRM_ID
    by_id = {m["id"]: m["name"] for m in _rows(ws, "member")}
    for contract in _rows(ws, "contract"):
        gates = _gates(contract)
        assert {"onboarding a client", "any spend step-up"} <= set(gates), (
            f"{by_id.get(contract['member_id'])}'s contract is missing the "
            f"firm-level gates: {gates}")
    ada = next(c for c in _rows(ws, "contract")
               if by_id.get(c["member_id"]) == "Ada")
    assert "approve a release" in _gates(ada), (
        "the member's own gate was replaced by the firm's rather than "
        "unioned with it")


def _gates(contract: dict[str, Any]) -> list[str]:
    cfg = contract.get("validation_config")
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    return list((cfg or {}).get("gates_required") or [])


def test_r17_an_old_shape_proposal_founds_todays_firm(root, tmp_path, capsys):
    """None of the four keys, and the firm is exactly the one founded before
    them: every member under the lead, no domains, one goal, the members'
    own gates only. This is what makes the widening additive."""
    rc, _, _ = _run(["init", str(root), "--proposal",
                     str(_write(tmp_path, _old_shape()))], capsys)
    assert rc == 0

    ws = root / FIRM_ID
    assert _chart(ws) == {"Ada": None, "Brac": "Ada", "Cass": "Ada",
                          "Dov": "Ada"}
    # STRICT, not tolerant. The build omits `suggested_domains` entirely
    # when a proposal names none, so the column stays NULL exactly as it is
    # today. Reading this through the tolerant helper would let an empty
    # list pass as "today's firm" when it is one column different on every
    # member, and R17's whole claim is that it is not.
    assert all(m["suggested_domains"] is None for m in _rows(ws, "member")), (
        "an old-shape proposal wrote a domains column that today's code "
        "leaves unset")
    assert [g["level"] for g in _rows(ws, "goal")] == ["firm"]
    by_id = {m["id"]: m["name"] for m in _rows(ws, "member")}
    for contract in _rows(ws, "contract"):
        expected = ["approve a release"] if by_id.get(
            contract["member_id"]) == "Ada" else []
        assert _gates(contract) == expected


def test_r17b_the_hub_prompt_asks_for_the_keys_and_the_shape():
    """The other half of the one-path property.

    Read on the RENDERED prompt, not on the constant: a key named in a string
    that never reaches the agent is a key the agent never sees.

    TWO HALVES, because the keys alone are not enough. Verdict amendment 1:
    a prompt that asks for `reports_to` while still telling the agent
    "everyone else reports to them" produces the flat chart with the key
    empty, which is the same firm with more words. So the sentences that were
    replaced must be GONE and the ones that replaced them must be present.
    Mutation NQ reverts the staffing bullet to its cap and this leg is what
    goes red.
    """
    from firm.dashboard.founding import _FOUNDING_PROMPT

    prompt = _FOUNDING_PROMPT.format(brief="a two-person writing firm")

    # THE OUTPUT BLOCK, not the whole prompt. Mutation NP removes
    # `"reports_to"` from the shape the agent is told to return, and two
    # earlier versions of this check could not see it: the bare word also
    # appears in a guidance bullet, and `"reports_to":` also appears in the
    # rules sentence below the block. A key the agent is told about in prose
    # but never shown in the shape is a key it will not return.
    head = prompt.index("Return ONLY a JSON object")
    tail = prompt.index("Exactly one Member has", head)
    block = prompt[head:tail]
    for key in ("reports_to", "domains", "goal", "gates"):
        assert f'"{key}":' in block, (
            f"the founding prompt's JSON output block has no {key!r} key, so "
            f"the hub door cannot produce a firm the terminal door accepts")

    for says in ("Staff the full shape", "span of control near four"):
        assert says in prompt, (
            f"the prompt no longer says {says!r}, so its guidance has drifted "
            f"back from the methodology the schema was widened for")

    for must_not in ("Three to six Members", "reports to them"):
        assert must_not not in prompt, (
            f"the prompt still says {must_not!r}, which tells the agent to "
            f"produce the firm the old schema could hold")


# ---------------------------------------------------------------------------
# R11 (fake runtime) and R18 -- Door B, and the direction of its imports
# ---------------------------------------------------------------------------

class _FakeAgent:
    """Answers the way `claude --print --output-format stream-json` does.

    Records the call first. A real `claude.exe` is not spawned from this
    seat at all: the acceptance arm at G2 does that, in a slot, and this leg
    is about the wiring around it — the argv, the working directory, the
    tier, and what the command does with the JSON that comes back.
    """

    def __init__(self, proposal: dict[str, Any]) -> None:
        self.proposal = proposal
        self.calls: list[dict[str, Any]] = []
        self.returncode = 0

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": list(argv), "cwd": kwargs.get("cwd"),
                           "env": dict(kwargs.get("env") or {})})
        self.stdout = iter([
            # A real assistant frame carries an OBJECT in `message`, and
            # `Narrator.feed` reads it as one. The first version of this
            # fake put a string there; the narrator raised inside
            # `_run_founding`'s stream loop, which catches everything and
            # finishes the job as failed — so R12 read as "the patch was
            # not used" when the fake was simply the wrong shape.
            json.dumps({"type": "assistant",
                        "message": {"content": [{"type": "text",
                                                 "text": "thinking"}]}})
            + "\n",
            json.dumps({"type": "result",
                        "result": json.dumps(self.proposal)}) + "\n",
        ])
        self.stderr = io.StringIO("")
        return self

    def wait(self, timeout=None):
        return 0

    def kill(self):
        raise AssertionError("the fake agent was killed; it answered already")

    @property
    def one(self) -> dict[str, Any]:
        assert len(self.calls) == 1, f"expected one spawn, got {len(self.calls)}"
        return self.calls[0]


def test_r11_door_b_runs_the_agent_and_commits_what_it_returns(
        root, tmp_path, capsys, monkeypatch):
    """A spec goes in, the agent's proposal comes back, a firm exists.

    Three things are asserted about the run itself, not just the outcome,
    because the outcome would be identical if Door B quietly built its own
    prompt or ran in the wrong place:

      * the prompt carries the spec text and the house rules, so it is the
        hub's prompt and not a new one;
      * the working directory is a `cadre-founding-` scratch tier, not the
        framework root, which is #143's rule for a session that runs before
        a firm exists;
      * that tier is gone afterwards.
    """
    from firm.services import founding as svc

    agent = _FakeAgent(_chart_proposal())
    monkeypatch.setattr(svc, "popen_utf8", agent, raising=True)
    monkeypatch.setattr("firm.pulse.spawn.resolve_claude_bin",
                        lambda: ("/usr/bin/claude", "fake"))
    monkeypatch.setattr(svc, "_inventory", lambda: ("(no arsenal)", {}))

    spec = tmp_path / "spec.md"
    spec.write_text("# A two-person writing firm\n\nIt writes things.\n",
                    encoding="utf-8")

    rc, result, _ = _run(["init", str(root), "--brief", str(spec)], capsys)

    assert rc == 0 and result and result.get("ok") is True, result
    call = agent.one
    prompt = call["argv"][-1]
    assert "A two-person writing firm" in prompt, (
        "Door B did not put the spec in the prompt")
    assert "FIRM-SCAFFOLDING-GUIDE.md" in prompt, (
        "Door B built a prompt of its own instead of the hub's")

    tier = Path(call["cwd"])
    assert "cadre-founding-" in str(tier), (
        f"Door B ran the agent in {tier}, which is not a scratch tier of its "
        f"own — its base hooks will walk up out of the install (#143)")
    assert not tier.exists(), "the scratch tier survived the run"

    assert [m["name"] for m in _rows(root / FIRM_ID, "member")], (
        "the agent's proposal was not committed")
    assert _chart(root / FIRM_ID) == {"Ada": None, "Brac": "Ada",
                                      "Cass": "Brac", "Dov": "Ada"}, (
        "Door B committed a different firm than the one the agent returned")


def test_r18_the_cli_does_not_import_the_dashboard():
    """The direction this build exists to undo, pinned on the source.

    Read rather than exercised: an import inside one branch of one function
    fires only on that branch, so a behavioural leg would sit green until
    the day someone takes it. `cli/board.py` importing `dashboard.auth` is a
    pre-existing wart with its own issue; this leg is about the founding
    door.
    """
    import inspect

    from firm.cli import init as cli_init

    src = inspect.getsource(cli_init)
    assert "firm.dashboard" not in src, (
        "cli/init.py imports the dashboard. The founding path lives in "
        "services precisely so the CLI does not have to, and an import here "
        "puts the cycle back one call at a time")
