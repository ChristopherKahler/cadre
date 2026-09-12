"""The acceptance harness's platform guard must not admit what it cannot name.

THE DEFECT (issue #61). `scripts/acceptance-e2e.py` decides whether the
base-engine section may run on this host. It classifies the resolved `base` by
magic bytes and compares that to the native kind:

    elif base_kind not in (native_kind, "script", "unknown"):

`"unknown"` is on the ALLOW list, so anything the classifier cannot name is
waved through. And the classifier names only TWO Mach-O magic numbers:

    if magic[:4] == b"\\xcf\\xfa\\xed\\xfe" or magic[:4] == b"\\xca\\xfe\\xba\\xbe":

while `src/firm/pulse/spawn.py` -- the same product, already -- lists EIGHT.
So on macOS, six of the eight forms classify "unknown" and the section runs.
The one check standing between this harness and the operator's own base tier
waves through the case it failed to identify. That is a passing value standing
in for an absent answer, inside the guard written to prevent exactly that.

WHY THIS NEEDS NO macOS HOST. The defect is a LIST. A list can be read and
tested from any platform, and `binary_kind` only ever looks at four bytes off
the front of a file, so a synthetic file reproduces every case exactly. The
arm below is therefore a real before-column on any machine, not a proxy for one.

The control is not optional and it is deliberately a REAL binary. If
`binary_kind` regressed into answering "macho" to everything, every synthetic
assertion above would still pass and the guard would be broken in the other
direction.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

from firm.pulse.spawn import _MACHO

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "scripts" / "acceptance-e2e.py"
SPAWN = REPO / "src" / "firm" / "pulse" / "spawn.py"


def _harness():
    """Load the harness by file path -- its name is not importable.

    Same mechanism tests/test_row_conservation.py already uses.
    """
    spec = importlib.util.spec_from_file_location("acceptance_e2e", HARNESS)
    assert spec and spec.loader, f"cannot load {HARNESS}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_harness_is_there_to_be_read():
    """Assert the anchor before asserting anything about what it anchors.

    Every arm below reads `binary_kind` out of this file. If the path were
    wrong, or the function renamed, the arms would error rather than measure --
    and a suite that errors is easy to misread as a suite that has a defect.
    """
    assert HARNESS.is_file(), f"{HARNESS} is not there"
    assert hasattr(_harness(), "binary_kind"), (
        "the harness has no binary_kind; this test file is measuring nothing")


@pytest.mark.parametrize("magic", _MACHO, ids=lambda m: m.hex())
def test_binary_kind_names_every_macho_magic_the_product_knows(magic, tmp_path):
    """Six of these eight are red at f947dab0, and that is the whole finding.

    The magics come from `firm.pulse.spawn._MACHO` rather than being retyped
    here. Retyping them would make this test agree with whatever I typed, which
    is not a check. Importing them means the harness is measured against the
    product's own answer, and the two lists cannot silently drift apart without
    this failing.
    """
    fake = tmp_path / f"fake-macho-{magic.hex()}"
    fake.write_bytes(magic + b"\x00" * 60)

    kind = _harness().binary_kind(str(fake))

    assert kind == "macho", (
        f"a Mach-O binary beginning {magic.hex()} is classified {kind!r}. "
        "The guard's allow list contains 'unknown', so on macOS this binary "
        "would pass the platform check and the base-engine section would run "
        "against the operator's own tier.")


def test_the_classifier_still_names_a_real_binary_correctly():
    """The control, and it uses a REAL binary, not a synthetic one.

    Every arm above feeds four hand-written bytes to the classifier. A
    classifier that had started answering "macho" to everything would satisfy
    all of them while being broken in the other direction, so this asks about a
    file neither this test nor the fix chose: the running interpreter.
    """
    native = {"win32": "pe", "darwin": "macho"}.get(sys.platform, "elf")

    kind = _harness().binary_kind(sys.executable)

    assert kind == native, (
        f"{sys.executable} is this platform's own interpreter and the "
        f"classifier calls it {kind!r}, not {native!r}")


def test_absent_and_unreadable_stay_distinct_from_a_real_answer():
    """A second control, on the other edge.

    "absent" and "unreadable" must not collapse into a kind that the guard's
    allow list accepts. If a missing file ever answered "unknown", the guard
    would admit a host with no base at all.
    """
    h = _harness()
    assert h.binary_kind(None) == "absent"
    assert h.binary_kind("/nonexistent/never-created-by-this-suite") == "unreadable"


# ---------------------------------------------------------------------------
# UNKNOWN MEANS DENY — the actual fix. The longer list is the smaller half.
# ---------------------------------------------------------------------------

def test_the_section_refuses_a_binary_it_could_not_identify():
    """THE fix for #61, and the arm that pins the class rather than the case.

    A longer `_MACHO` shrinks the hole. It does not close it: the next format
    nobody here has seen still lands on "unknown", and "unknown" used to be on
    the allow list beside `native_kind` and `"script"`. So the guard admitted
    every binary it had failed to identify, which is the whole shape of the
    2026-09-11 incident arriving one level up.

    THIS ARM CANNOT BE RED ON MAIN, and saying so is the point rather than an
    excuse: `section_may_run` does not exist at f947dab0, so running it there
    raises AttributeError, and an AttributeError is not a measurement. The
    before-column for #61 is carried by the Mach-O arm above, which runs
    against main unchanged. This one holds the decision once it is reachable.
    """
    h = _harness()
    assert h.section_may_run("unknown", "elf") is False
    assert h.section_may_run("unknown", "pe") is False
    assert h.section_may_run("unknown", "macho") is False


def test_the_section_still_runs_on_a_host_it_can_vouch_for():
    """The control for the arm above.

    "Refuse everything" satisfies every assertion in that test and would skip
    the base-engine section on every machine in the world, turning eighteen
    rows into permanent silence. A check that cannot say yes is not a check.
    """
    h = _harness()
    for native in ("elf", "pe", "macho"):
        assert h.section_may_run(native, native) is True
        assert h.section_may_run("script", native) is True, (
            "a shebang wrapper named base is the normal shape of a dev "
            "install; refusing it skips the section on ordinary machines")


def test_a_cross_platform_binary_is_still_refused():
    """The original defect the guard was written for must not have regressed.

    Widening `_MACHO` and moving "unknown" to deny both touch this decision, so
    the case it already handled is asserted again rather than assumed.
    """
    h = _harness()
    assert h.section_may_run("pe", "elf") is False
    assert h.section_may_run("elf", "pe") is False
    assert h.section_may_run("macho", "elf") is False


# ---------------------------------------------------------------------------
# The two copies of the list must agree
# ---------------------------------------------------------------------------

def _macho_in(source: str, where: str) -> tuple[bytes, ...]:
    """Read the `_MACHO` literal out of Python source, by AST.

    `ast.literal_eval` of the SPECIFIC assignment node, never a substring of
    `ast.dump`. An `ast.dump` of a module carries its docstrings, so a
    substring test over it can match text that is only a comment about the
    constant and pass for a reason unrelated to the constant's value.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        if target == "_MACHO":
            return tuple(ast.literal_eval(node.value))
    raise AssertionError(f"no _MACHO assignment found in {where}")


def test_the_two_copies_of_the_macho_list_agree():
    """The harness copies this list from the product. Pin them equal.

    The harness imports only the standard library -- it runs against an
    installed wheel and cannot import the package it is deciding whether to
    test -- so the copy is deliberate. This test is what makes the duplication
    honest instead of a drift waiting to happen; three copies quietly
    disagreeing is how #61 happened in the first place.

    WHAT THIS TEST DOES NOT PROVE, stated here rather than left to be
    discovered: it proves the two copies AGREE. It says nothing about whether
    the list is COMPLETE. If the world has a ninth Mach-O magic number, both
    copies are wrong together and this stays green. Closing that is what
    `section_may_run` refusing "unknown" is for -- the list makes the guard
    accurate, the deny makes it safe.
    """
    harness = _macho_in(HARNESS.read_text(encoding="utf-8"), str(HARNESS))
    product = _macho_in(SPAWN.read_text(encoding="utf-8"), str(SPAWN))

    assert harness == product, (
        "the harness's Mach-O list has drifted from the product's:\n"
        f"  only in harness: {sorted(set(harness) - set(product))}\n"
        f"  only in product: {sorted(set(product) - set(harness))}")


def test_the_equality_check_actually_fails_when_the_copies_drift():
    """The control for the test above, and it is the one most often skipped.

    A copy-equality test that has never been seen failing does not catch drift;
    it just agrees. So drift is PLANTED -- one magic removed from the harness's
    source, in memory, never on disk -- and the comparison is required to
    notice. Same shape as
    tests/test_no_hardcoded_roots.py::test_a_violation_planted_in_src_is_actually_caught.
    """
    source = HARNESS.read_text(encoding="utf-8")
    full = _macho_in(source, str(HARNESS))
    assert len(full) > 1, "cannot plant drift in a list of one"

    # No indentation in the pattern. The magics sit TWO PER LINE, so the last
    # one does not start its line and an indent-anchored pattern matches
    # nothing at all. The next assertion caught exactly that when this control
    # was first run, which is the control controlling for itself.
    dropped = full[-1]
    literal = 'b"' + "".join(f"\\x{b:02x}" for b in dropped) + '",'
    planted = source.replace(literal, "", 1)
    assert planted != source, (
        "the plant did not change the source, so this control proved nothing")

    shortened = _macho_in(planted, "planted")
    assert len(shortened) == len(full) - 1, (
        f"planting removed {len(full) - len(shortened)} entries, not 1")

    product = _macho_in(SPAWN.read_text(encoding="utf-8"), str(SPAWN))
    assert shortened != product, (
        "one magic number was removed from the harness copy and the equality "
        "check still saw the two lists as equal, so it cannot catch drift")
