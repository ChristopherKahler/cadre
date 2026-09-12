"""The manifest declares files to ingest. Something has to write them.

`src/firm/base_ext/cadre.toml.template` declares three `[[hooks.session_start.ingest]]`
blocks and three `[[hooks.post_tool.handlers]]` re-ingest handlers, all of them on
paths under `.firm/base-export/`. Measured at main `89bcf3208abc` with a whole-worktree
`grep -rn` — not `git grep`, so untracked files were in scope — **nothing in `src/`
ever wrote any of them.** The only producer on the machine was the fixture inside
`scripts/verify/verify_ext_install.sh`, which writes them so that its own probe has
something to ingest.

So base opened three paths that did not exist, the graph got nothing, and "firm state
in the graph" — one of the four things issue #4's *what done looks like* names — never
happened in a real firm. A probe proved the mechanism worked and no firm ever used it.

**Why nobody saw it, which is the reusable half.** A test asserted that the manifest
DECLARES the paths. The manifest QUOTES the paths. Nobody ever asked who WRITES them.
A selector finds sites that quote a string; it cannot find the absence of a writer.
That absence is what this file exists to make loud.

Nothing here imports `firm.services.base_export` at module scope. On a tree that has
no exporter this file must still COLLECT and still REPORT A NUMBER — an import error
at the top would turn the red arm into a collection error, and an arm that did not run
has not been seen to fail.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from firm.services import base_extension


# ---------------------------------------------------------------------------
# Reading the contract off the manifest, never restating it
# ---------------------------------------------------------------------------

def _manifest() -> dict[str, Any]:
    """The shipped manifest, parsed, with its machine placeholders neutralised."""
    raw = base_extension.manifest_source().read_text(encoding="utf-8")
    raw = raw.replace(base_extension.PLACEHOLDER, "/tmp/framework")
    raw = raw.replace(base_extension.HANDLER_PLACEHOLDER, "/tmp/framework/bin/cadre")
    return tomllib.loads(raw)


def _declared_ingest_files() -> list[str]:
    """Every path the manifest declares as an ingest source, as it declares it.

    Derived, never typed. If somebody renames `members.json` in the manifest,
    this test follows the rename instead of quietly checking a path nobody uses
    any more — which is issue #35's family, refused here rather than repeated.
    """
    session_start = _manifest().get("hooks", {}).get("session_start", {})
    return [str(block["file"]) for block in session_start.get("ingest", [])
            if block.get("file")]


def _state_dir() -> str:
    """The directory base resolves an ingest `file` against."""
    return str(_manifest()["extension"]["state_dir"]).strip("/")


def _absent(workspace: Path, names: list[str]) -> list[str]:
    """Which of *names* the product did not leave on disk. The instrument."""
    root = workspace / _state_dir()
    return [n for n in names if not (root / n).is_file()]


def _produce(workspace: Path) -> tuple[int, str]:
    """Drive the product's own route for producing a firm's base exports.

    Returns (rc, note) and NEVER raises, because the whole point of the red arm
    is to measure a tree where that route does not exist yet. argparse exits the
    process on an unknown verb, so a bare call would end the test run rather than
    report a number; catching it turns "there is no such route" into data.
    """
    from firm import __main__ as cli

    try:
        rc = cli.main(["export", "base", "--workspace", str(workspace)])
        return int(rc), ""
    except SystemExit as exc:
        return int(exc.code or 0), "the firm CLI has no `export base` verb"


# ---------------------------------------------------------------------------
# A firm with state in it, built through the product's own services
# ---------------------------------------------------------------------------

@pytest.fixture()
def firm_workspace(tmp_path: Path) -> Path:
    """A real initialised workspace holding one member, one unit and one gate."""
    from firm.cli.init import run_init
    from firm.core import repo
    from firm.core.db import db_connection

    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert run_init(workspace, force=False, demo=False,
                    install_hooks_flag=False) == 0

    with db_connection(workspace) as conn:
        repo.create(conn, "firm", {"id": "zqfirm", "name": "Zed Quarter",
                                   "description": "a firm for measuring with"})
        repo.create(conn, "member", {
            "id": "MEM-001", "firm_id": "zqfirm", "name": "Vantage",
            "role": "Chief of Staff", "status": "active"})
        repo.create(conn, "operation", {
            "id": "OPS-001", "firm_id": "zqfirm", "name": "Running it",
            "owner_member_id": "MEM-001"})
        # status and due_date are both NOT NULL on project with no default —
        # read off the CREATE TABLE, not from memory, after the first version
        # of this fixture died on `NOT NULL constraint failed: project.status`
        # and turned the red arm into an error instead of a measurement.
        repo.create(conn, "project", {
            "id": "PRJ-001", "firm_id": "zqfirm", "name": "First shift",
            "operation_id": "OPS-001", "status": "in_progress",
            "due_date": "2099-01-01"})
        repo.create(conn, "unit", {
            "id": "UNIT-001", "firm_id": "zqfirm", "project_id": "PRJ-001",
            "name": "Ship it", "status": "pending",
            "assignee_member_id": "MEM-001"})
        repo.create(conn, "gate", {
            "id": "GATE-001", "firm_id": "zqfirm",
            "requesting_member_id": "MEM-001", "action": "publish",
            "target_entity_type": "unit", "target_entity_id": "UNIT-001",
            "status": "pending"})
    return workspace


# ---------------------------------------------------------------------------
# THE RED ARM
# ---------------------------------------------------------------------------

def test_every_ingest_file_the_manifest_declares_is_produced(firm_workspace):
    """The defect, stated as a check.

    Red before the fix: 3 declared, 0 on disk, and `_produce` reports that the
    CLI has no verb to make them with. Green after: 3 declared, 3 on disk.

    This touches no API that does not already exist, so it MEASURES on both
    trees rather than raising.
    """
    declared = _declared_ingest_files()
    rc, note = _produce(firm_workspace)
    absent = _absent(firm_workspace, declared)
    assert absent == [], (
        f"{len(declared)} ingest files are declared in "
        f"{base_extension.manifest_source().name} and {len(absent)} of them are "
        f"not on disk after the product ran: {absent}. base opens each of these "
        f"at session start; a path that is never written ingests nothing, so the "
        f"firm's state never reaches the graph. "
        f"(export rc {rc}{': ' + note if note else ''})")


# ---------------------------------------------------------------------------
# CONTROLS — an arm only ever seen agreeing has not been shown to be an arm
# ---------------------------------------------------------------------------

def test_control_the_instrument_sees_a_file_that_is_there(tmp_path):
    """`_absent` must be able to report NOTHING missing.

    Without this, a broken `_absent` that always returns [] would pass the red
    arm by being broken rather than by the defect being fixed.
    """
    root = tmp_path / _state_dir()
    root.mkdir(parents=True)
    (root / "present.json").write_text("[]", encoding="utf-8")
    assert _absent(tmp_path, ["present.json"]) == []


def test_control_the_instrument_reports_a_file_that_is_not(tmp_path):
    """`_absent` must be able to report something missing.

    The other half of the same control. A check that can only ever answer one
    way is not a check, and these two together are what make the red arm's
    number mean anything.
    """
    (tmp_path / _state_dir()).mkdir(parents=True)
    assert _absent(tmp_path, ["nobody-writes-this.json"]) == ["nobody-writes-this.json"]


def test_control_the_manifest_actually_declares_something_to_ingest():
    """If the manifest declared nothing, the red arm would pass vacuously.

    Then deleting the ingest blocks entirely — which is the defect made total —
    would read as a fix. This is the assertion that stops an empty list being
    mistaken for a clean one.
    """
    declared = _declared_ingest_files()
    assert len(declared) >= 3, (
        f"the manifest declares {len(declared)} ingest files; the red arm above "
        "is only meaningful while there is something to declare")
    assert all(d.endswith(".json") for d in declared), declared


# ---------------------------------------------------------------------------
# The map that cannot be derived, guarded from both directions
# ---------------------------------------------------------------------------

def test_every_declared_entity_has_a_producer_and_every_producer_is_declared():
    """`PRODUCERS` maps the manifest's vocabulary onto the database's.

    Nothing can derive `CadreMember` from `member`, so that map is a literal,
    and a literal against a moving thing is issue #35's whole family. Asserting
    it in BOTH directions is what keeps it safe: add an ingest block with no
    producer and this fails; delete an ingest block and leave the producer
    behind and this fails too.

    One direction alone would not do it. Checking only that every producer is
    declared lets a new ingest block arrive with nothing writing it — which is
    the defect this lane exists to fix, arriving again by a different door.
    """
    from firm.services import base_export

    declared = {str(b["entity"]) for b in base_export.declared_exports()}
    produced = set(base_export.PRODUCERS)

    assert declared - produced == set(), (
        f"the manifest declares {sorted(declared - produced)} and this module "
        "has no producer for them, so base would open a path nothing writes")
    assert produced - declared == set(), (
        f"{sorted(produced - declared)} has a producer but the manifest no "
        "longer declares it, so the export writes a file nothing reads")


def test_every_producer_names_a_real_table(firm_workspace):
    """A producer pointing at a table that does not exist is the same defect.

    `repo.find` raises `ValueError: Unknown table` on a name the schema does not
    have, so without this the map could be wrong in the other direction and only
    fail at runtime in a real firm.
    """
    from firm.core import repo
    from firm.core.db import db_connection
    from firm.services import base_export

    with db_connection(firm_workspace) as conn:
        for entity, table in base_export.PRODUCERS.items():
            rows = repo.find(conn, table, firm_id="zqfirm")
            assert isinstance(rows, list), f"{entity} -> {table}"


# ---------------------------------------------------------------------------
# Does the export write the truth? NOT a before-column — see the docstring.
# ---------------------------------------------------------------------------

def test_export_writes_every_declared_file_with_rows_matching_the_db(firm_workspace):
    """The content half of the fix.

    **This test cannot be red on the tree that has the defect.**
    `firm.services.base_export` does not exist there, so it raises instead of
    measuring, and an `AttributeError` is not a measurement. It is recorded here
    as a correctness test only. The before-column for this lane is
    `test_every_ingest_file_the_manifest_declares_is_produced`, which touches no
    new API and returned a real number on the unfixed tree.
    """
    from firm.services import base_export

    result = base_export.export(firm_workspace, "zqfirm")
    assert result["ok"], result

    root = firm_workspace / base_export.state_dir()
    by_entity = {str(b["entity"]): str(b["file"])
                 for b in base_export.declared_exports()}

    members = json.loads((root / by_entity["CadreMember"]).read_text(encoding="utf-8"))
    units = json.loads((root / by_entity["CadreUnit"]).read_text(encoding="utf-8"))
    gates = json.loads((root / by_entity["CadreGate"]).read_text(encoding="utf-8"))

    # The shape base accepts, read off a working producer rather than assumed:
    # a JSON array of flat objects each carrying an id.
    for rows in (members, units, gates):
        assert isinstance(rows, list), rows
        for row in rows:
            assert isinstance(row, dict) and row.get("id"), row

    assert [m["id"] for m in members] == ["MEM-001"]
    assert [u["id"] for u in units] == ["UNIT-001"]
    assert [g["id"] for g in gates] == ["GATE-001"]
    # Not just ids: the fields a Member would actually ask about have to survive.
    assert members[0]["name"] == "Vantage" and members[0]["role"] == "Chief of Staff"
    assert units[0]["status"] == "pending"
    assert gates[0]["action"] == "publish"


def test_the_export_follows_the_database_rather_than_reporting_a_fixed_answer(
        firm_workspace):
    """Control for the test above: it must not pass by writing a constant.

    A writer that always emitted the same array would satisfy every structural
    assertion in this file. Changing the database and re-exporting is what tells
    a real read from a canned one.
    """
    from firm.core import repo
    from firm.core.db import db_connection
    from firm.services import base_export

    base_export.export(firm_workspace, "zqfirm")
    root = firm_workspace / base_export.state_dir()
    members_file = root / next(str(b["file"]) for b in base_export.declared_exports()
                              if b["entity"] == "CadreMember")
    before = json.loads(members_file.read_text(encoding="utf-8"))

    with db_connection(firm_workspace) as conn:
        repo.create(conn, "member", {
            "id": "MEM-002", "firm_id": "zqfirm", "name": "Late",
            "role": "Hire", "status": "active"})

    base_export.export(firm_workspace, "zqfirm")
    after = json.loads(members_file.read_text(encoding="utf-8"))

    assert [m["id"] for m in before] == ["MEM-001"]
    assert [m["id"] for m in after] == ["MEM-001", "MEM-002"]


# ---------------------------------------------------------------------------
# CONTAINMENT — the export must not be able to reach the operator's own tier
# ---------------------------------------------------------------------------

def _tree(root: Path) -> dict[str, int]:
    """Every file under *root*, by relative path and size. The probe."""
    return {str(p.relative_to(root)): p.stat().st_size
            for p in root.rglob("*") if p.is_file()}


def test_the_export_writes_nothing_outside_the_firms_own_workspace(tmp_path,
                                                                   firm_workspace):
    """A write into `.firm/base-export/` is what makes base ingest a file.

    Into WHICH store is the question, and on this repo it has a history: a write
    reached the operator's own tier through `base_extension.install()` on
    2026-09-11 and the file is quarantined. `tests/conftest.py` points BASE_HOME
    at a throwaway directory, but **that protects the test run; it does not
    prove the product.** Those are two different claims and this is the second
    one: nothing anywhere outside the firm's workspace changes when an export
    runs.
    """
    from firm.services import base_export

    decoy = tmp_path / "operator-tier"
    (decoy / ".base-gbl" / "extensions").mkdir(parents=True)
    (decoy / ".base-gbl" / "extensions" / "cadre.toml").write_text(
        "# the operator's own manifest\n", encoding="utf-8")
    (decoy / ".base-gbl" / "base.toml").write_text("# the operator's own tier\n",
                                                   encoding="utf-8")

    before = _tree(tmp_path)
    result = base_export.export(firm_workspace, "zqfirm")
    assert result["ok"], result
    after = _tree(tmp_path)

    changed = {p for p in set(before) | set(after)
               if before.get(p) != after.get(p)}
    inside = firm_workspace.relative_to(tmp_path).as_posix() + "/"
    escaped = sorted(p for p in changed if not p.replace("\\", "/").startswith(inside))
    assert escaped == [], (
        f"the export changed {escaped} outside the firm's workspace "
        f"{firm_workspace}; a Cadre export must never reach the operator's own "
        "store, and that is the route the 2026-09-11 tier-write incident took")
    assert changed, "control: the export must have changed something"


def test_control_the_containment_probe_detects_a_write_that_escapes(tmp_path,
                                                                    firm_workspace):
    """The probe above must be able to SEE an escape, or its silence means nothing.

    Same probe, same comparison, with one file deliberately written outside the
    workspace. If this does not report it, the test above is passing because the
    instrument is blind rather than because the product is contained.
    """
    decoy = tmp_path / "operator-tier"
    decoy.mkdir(parents=True, exist_ok=True)

    before = _tree(tmp_path)
    (decoy / "escaped.json").write_text("[]", encoding="utf-8")
    after = _tree(tmp_path)

    changed = {p for p in set(before) | set(after)
               if before.get(p) != after.get(p)}
    inside = firm_workspace.relative_to(tmp_path).as_posix() + "/"
    escaped = sorted(p for p in changed if not p.replace("\\", "/").startswith(inside))
    assert escaped == ["operator-tier/escaped.json"], escaped


def test_the_export_refuses_a_manifest_path_outside_the_workspace(tmp_path,
                                                                  firm_workspace,
                                                                  monkeypatch):
    """The refusal osprey ruled in, and the measurement that made it necessary.

    The target is `workspace / state_dir / file`, and both halves come from the
    manifest. Before the refusal existed this test FAILED, and not on a
    technicality: a manifest declaring `../../escaped-by-manifest.json` wrote a
    real 509-byte file one level above the firm's workspace, confirmed twice —
    once by this assertion and once by `find` on disk, at
    `/tmp/pytest-of-chriskahler/pytest-22/test_where_the_export_can_writ0/escaped-by-manifest.json`.

    The export now REFUSES and names the offending path. It does not clamp the
    path back inside, because that would hand the manifest a file other than the
    one it asked for and base would ingest a path nobody declared. And it
    refuses BEFORE the first write: a guard that runs after the write is a
    detector, which is exactly what issue #75 is about.

    **The depth here is load-bearing.** The first version of this test used
    `../../../`, which from `<tmp_path>/ws/.firm/` lands ABOVE `tmp_path` — so
    `_tree(tmp_path)` could not see the file and the test PASSED while the
    export was escaping. It was caught by looking on disk, not by reading the
    green tick. Two levels put the landing site inside the probe's root and
    outside the workspace, which is the only arrangement where a pass means
    anything. Do not restore the third dot.
    """
    from firm.services import base_export

    hostile = tmp_path / "hostile.toml.template"
    hostile.write_text(
        '[extension]\n'
        'name = "cadre"\n'
        'version = "0.0.0"\n'
        'framework_dir = "/tmp/framework"\n'
        'state_dir = ".firm/"\n\n'
        '[hooks.session_start]\n'
        'inject = "x"\n\n'
        '[[hooks.session_start.ingest]]\n'
        'file = "../../escaped-by-manifest.json"\n'
        'entity = "CadreMember"\n'
        'strategy = "replace"\n',
        encoding="utf-8")
    monkeypatch.setattr("firm.services.base_extension.manifest_source",
                        lambda: hostile)

    before = _tree(tmp_path)
    result = base_export.export(firm_workspace, "zqfirm")
    after = _tree(tmp_path)
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    inside = firm_workspace.relative_to(tmp_path).as_posix() + "/"
    escaped = sorted(p for p in changed if not p.replace("\\", "/").startswith(inside))

    assert escaped == [], (
        "a manifest declaring a parent-relative ingest path made the export "
        f"write outside the firm's workspace: {escaped}. That is the escape "
        "route measured on 2026-09-12 and the refusal is supposed to close it.")
    # Refused, and said which path — not quietly skipped, and not clamped.
    assert result["ok"] is False, result
    assert "escaped-by-manifest.json" in result["reason"], result["reason"]
    assert "Nothing was written" in result["reason"], result["reason"]
    assert result["written"] == [], result
    # Nothing AT ALL was written, inside the workspace either. A refusal that
    # still wrote the files it could reach would be a partial export reported as
    # a refusal, which is the same dishonesty one layer down.
    assert changed == set(), sorted(changed)


# ---------------------------------------------------------------------------
# The verb tells the truth; the library call never raises
# ---------------------------------------------------------------------------

def test_the_verb_exits_non_zero_when_the_export_did_not_happen(tmp_path, capsys):
    """A person running this by hand and seeing 0 over a failed export has been
    told the opposite of the truth. That is issue #5's defect and #62's, and it
    is why the CLI reads `ok` instead of returning 0 unconditionally."""
    from firm.services import base_export

    empty = tmp_path / "not-a-firm"
    empty.mkdir()
    rc = base_export.run_export(empty)
    assert rc == 1
    assert "Error:" in capsys.readouterr().err


def test_the_verb_exits_zero_on_a_real_export(firm_workspace, capsys):
    """Control for the test above: a verb that always exits 1 is not a check."""
    from firm.services import base_export

    assert base_export.run_export(firm_workspace, firm_id="zqfirm") == 0
    assert "Error:" not in capsys.readouterr().err


def test_the_library_call_never_raises_even_with_no_database(tmp_path):
    """The two internal call sites — a founding and the end of a pulse — must
    survive an export failure. An exception there takes down work that has
    nothing to do with base."""
    from firm.services import base_export

    empty = tmp_path / "not-a-firm"
    empty.mkdir()
    result = base_export.export(empty)
    assert result["ok"] is False
    assert result["reason"]


def test_the_library_call_never_raises_on_an_unreadable_manifest(firm_workspace,
                                                                 monkeypatch):
    """Same contract, a different failure: the shipped manifest gone missing."""
    from firm.services import base_export

    monkeypatch.setattr("firm.services.base_extension.manifest_source",
                        lambda: Path("/nonexistent/cadre.toml.template"))
    result = base_export.export(firm_workspace, "zqfirm")
    assert result["ok"] is False
    assert "manifest" in result["reason"]


def test_a_declared_entity_with_no_producer_is_named_rather_than_written_empty(
        firm_workspace, monkeypatch, tmp_path):
    """An empty export file and a missing producer look identical in the graph.

    They need different fixes, so the export refuses rather than writing `[]`
    and calling itself complete. This is the honesty envelope the rest of this
    repo keeps: absent, empty and zero are three different answers.
    """
    from firm.services import base_export

    odd = tmp_path / "odd.toml.template"
    odd.write_text(
        '[extension]\nname = "cadre"\nversion = "0.0.0"\n'
        'framework_dir = "/tmp/framework"\nstate_dir = ".firm/"\n\n'
        '[hooks.session_start]\ninject = "x"\n\n'
        '[[hooks.session_start.ingest]]\n'
        'file = "base-export/nobody.json"\nentity = "CadreNobody"\n'
        'strategy = "replace"\n',
        encoding="utf-8")
    monkeypatch.setattr("firm.services.base_extension.manifest_source",
                        lambda: odd)

    result = base_export.export(firm_workspace, "zqfirm")
    assert result["ok"] is False
    assert "CadreNobody" in result["reason"]
    assert not (firm_workspace / ".firm" / "base-export" / "nobody.json").exists()


def test_the_write_is_atomic_and_leaves_no_temp_files_behind(firm_workspace):
    """`post_tool` fires on a write to exactly these paths, so a reader can
    arrive mid-write. `os.replace` over a same-directory temp file is what makes
    the swap indivisible — and the temp file must not survive it."""
    from firm.services import base_export

    assert base_export.export(firm_workspace, "zqfirm")["ok"]
    root = firm_workspace / base_export.state_dir() / "base-export"
    leftovers = sorted(p.name for p in root.iterdir()
                       if p.name.startswith(".cadre-export-"))
    assert leftovers == [], leftovers


# ---------------------------------------------------------------------------
# The call sites — the export has to actually be reached, not merely exist
#
# A PULSE SPAWNS REAL MEMBERS, AND THE FIRST VERSION OF THESE TESTS DID.
# `_pulse_once` builds the firm's real runner and `pulse()`'s activation loop
# calls it for every active Member that passes the gates. The fixture above
# creates one — MEM-001, active — so `_pulse_once(dry_run=False)` did not
# simulate a pulse, it RAN one, and the spawned Member took a relay title in the
# operator's live fleet. It was caught from outside: the orchestrator saw a
# session answering as `zqfirm-MEM-001` that was registered to nobody, and that
# string exists nowhere on this machine except this file's fixture.
#
# So every test below fences the spawn off twice, and the second fence is the
# one that matters:
#   1. `make_runner` is replaced with a stub that returns a result dict.
#   2. `spawn_member_run` is replaced with something that RAISES. If any route
#      reaches a real spawn — now or after somebody refactors the runner — the
#      test fails loudly instead of quietly launching an agent.
# A test that needs a live agent to prove a file was written is the wrong test.
# ---------------------------------------------------------------------------

@pytest.fixture()
def no_spawn(monkeypatch):
    """Nothing in this file may start a real Member. Returns the call log."""
    ran: list[str] = []

    def _stub_runner(firm_id, cwd):
        def _run(conn, member):
            ran.append(str(member["id"]))
            return {"status": "done", "stub": True}
        return _run

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "a test reached spawn_member_run — this would launch a real Claude "
            "session into the operator's fleet, which is exactly what happened "
            "on 2026-09-12 and produced a stray zqfirm-MEM-001")

    monkeypatch.setattr("firm.cli.pulse.make_runner", _stub_runner)
    monkeypatch.setattr("firm.pulse.spawn.spawn_member_run", _forbidden)
    return ran


def test_a_pulse_leaves_the_declared_exports_on_disk(firm_workspace, no_spawn):
    """A pulse is what moves the roster and the unit board, so it is where the
    exports have to be refreshed. Without this the exporter could ship perfect
    and never be called by anything a firm actually runs."""
    from firm.cli.pulse import _pulse_once
    from firm.core.db import db_connection

    declared = _declared_ingest_files()
    with db_connection(firm_workspace) as conn:
        out = _pulse_once(conn, firm_workspace, "zqfirm", dry_run=False)

    assert out["base_export"] is True, out
    assert _absent(firm_workspace, declared) == []
    # The pulse really did activate, through the stub and not through a spawn.
    assert no_spawn == ["MEM-001"], no_spawn


def test_a_dry_run_pulse_writes_no_exports(firm_workspace, no_spawn):
    """Dry run is read-only by contract and must leave no trace.

    This is the control for the test above: a pulse that exported
    unconditionally would pass that one while breaking the dry-run promise, and
    the two together are what pin the behaviour to the right branch.
    """
    from firm.cli.pulse import _pulse_once
    from firm.core.db import db_connection

    declared = _declared_ingest_files()
    with db_connection(firm_workspace) as conn:
        out = _pulse_once(conn, firm_workspace, "zqfirm", dry_run=True)

    assert "base_export" not in out, out
    assert _absent(firm_workspace, declared) == declared
    assert no_spawn == [], "a dry run activated a Member"


def test_a_pulse_says_so_when_the_export_did_not_happen(firm_workspace, no_spawn,
                                                        monkeypatch):
    """A firm whose exports stopped updating looks exactly like a firm whose
    graph is simply quiet. Those need different fixes, so the pulse reports the
    failure instead of swallowing it."""
    from firm.cli.pulse import _pulse_once
    from firm.core.db import db_connection

    monkeypatch.setattr("firm.services.base_extension.manifest_source",
                        lambda: Path("/nonexistent/cadre.toml.template"))
    with db_connection(firm_workspace) as conn:
        out = _pulse_once(conn, firm_workspace, "zqfirm", dry_run=False)

    assert out["base_export"] is False, out
    assert out["base_export_reason"], out


def test_a_pulse_still_reports_its_own_result_when_the_export_fails(firm_workspace,
                                                                    no_spawn,
                                                                    monkeypatch):
    """An export failure must not turn a pulse that ran into a pulse that errored.

    This is the whole reason `export` never raises. The pulse's own `ok`, `ran`,
    `skipped` and `errors` have to survive an unreadable manifest untouched.
    """
    from firm.cli.pulse import _pulse_once
    from firm.core.db import db_connection

    with db_connection(firm_workspace) as conn:
        healthy = _pulse_once(conn, firm_workspace, "zqfirm", dry_run=False)

    monkeypatch.setattr("firm.services.base_extension.manifest_source",
                        lambda: Path("/nonexistent/cadre.toml.template"))
    with db_connection(firm_workspace) as conn:
        broken = _pulse_once(conn, firm_workspace, "zqfirm", dry_run=False)

    for key in ("ok", "ran", "skipped", "errors"):
        assert healthy[key] == broken[key], (key, healthy, broken)


def test_control_the_spawn_fence_actually_fires(firm_workspace, no_spawn):
    """The fence above must be able to FAIL, or its silence proves nothing.

    Calling the patched `spawn_member_run` directly has to raise. Without this,
    a monkeypatch that silently failed to apply would leave every test in this
    section free to launch real agents again while still reading green — which
    is precisely how the first version got through.
    """
    from firm.pulse import spawn

    with pytest.raises(AssertionError, match="real Claude"):
        spawn.spawn_member_run()


def test_founding_a_firm_reaches_the_export(monkeypatch, tmp_path):
    """The third call site, and without this it could be dead and still green.

    `founding.commit` is where a firm first exists, so it is where the firm's
    exports first have to. The content of the export is proved elsewhere; what
    this pins is the WIRING — that `commit` calls it at all, with the new firm's
    own workspace, and reports the answer rather than swallowing it.

    A call site nobody tested is the same defect this whole lane is about: the
    manifest declared three files and nothing wrote them, and every test was
    green because no test asked whether anything ran.
    """
    from firm.dashboard import founding
    from firm.services import base_export

    seen: list[tuple[str, str]] = []

    def _spy(workspace, firm_id=None, *, conn=None):
        seen.append((str(workspace), str(firm_id)))
        return {"ok": True, "written": ["spy"], "skipped": [], "reason": "spy"}

    monkeypatch.setattr(base_export, "export", _spy)

    result = founding.commit(tmp_path, {
        "firm_id": "zqfound",
        "name": "Zed Founding",
        "premise": "a firm for measuring the wiring with",
        "north_star": {"target": "prove the export is reached"},
        "operations": [{"name": "Running it", "purpose": "keep it running"}],
        "members": [{"name": "Vantage", "role": "Chief of Staff",
                     "owns": "everything", "operation": "Running it",
                     "leads": True, "model": "sonnet",
                     "skills": [], "gates": []}],
    })

    assert result.get("ok") is True, result
    assert seen == [(str(tmp_path / "zqfound"), "zqfound")], seen
    assert result["base_export"]["ok"] is True, result["base_export"]


def test_founding_survives_an_export_that_fails(monkeypatch, tmp_path):
    """Control for the test above, and the contract `export` exists to keep.

    A firm that cannot write its exports is degraded, not un-founded. If this
    ever goes red, `export` has started raising and a founding can now be lost
    to a base problem that has nothing to do with the org being created.
    """
    from firm.dashboard import founding
    from firm.services import base_export

    def _broken(workspace, firm_id=None, *, conn=None):
        return {"ok": False, "written": [], "skipped": [],
                "reason": "the shipped manifest could not be read"}

    monkeypatch.setattr(base_export, "export", _broken)

    result = founding.commit(tmp_path, {
        "firm_id": "zqbroke",
        "name": "Zed Broken",
        "premise": "a firm whose export fails",
        "north_star": {"target": "still be founded"},
        "operations": [{"name": "Running it", "purpose": "keep it running"}],
        "members": [{"name": "Vantage", "role": "Chief of Staff",
                     "owns": "everything", "operation": "Running it",
                     "leads": True, "model": "sonnet",
                     "skills": [], "gates": []}],
    })

    assert result.get("ok") is True, result
    assert result["base_export"]["ok"] is False
    assert result["base_export"]["reason"]
