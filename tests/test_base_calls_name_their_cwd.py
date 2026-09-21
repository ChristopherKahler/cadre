"""No base call in `src` runs from a directory nobody chose. Issue #136.

base resolves its WORKSPACE tier by walking up from the process's working
directory (base 0.15.2 `config.rs:49-59`) and `BASE_HOME` moves only the GLOBAL
tier (`home.rs:25-46`). A base call that does not name its directory therefore
reads whichever `.base` sits above wherever the caller happened to stand.

Measured on the operator's machine 2026-09-14: the Windows hub's interpreter ran
in `C:\\Users\\Chris\\.base-gbl\\scripts`, so the walk resolved
`C:\\Users\\Chris\\.base-gbl\\.base` -- his own real global graph, 37.9 MB of it
-- as the firm's workspace tier. Started one directory differently it would have
resolved `C:\\Users\\Chris\\.base`, which holds 18 domain and 15 rule lines for
this very extension, and founding would have reported the operator's own rules
as a collision inside the firm.

This guard is the law-31 half of the fix. The seam and the per-call legs live in
`test_base_calls_use_the_cwd_seam.py`; this file asserts that no NEW call escapes
the seam later, and it is proven against the seven shapes the codebase actually
writes rather than against its own cases -- see `base_call_shapes_fixture.py`.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.base_call_scan import scan_source, scan_tree

_SRC = Path(__file__).resolve().parents[1] / "src" / "firm"
_FIXTURE = Path(__file__).resolve().parent / "base_call_shapes_fixture.py"

#: Calls that may run from any directory, each with the reason.
#:
#: Keyed by `<file>::<function>` rather than by line, so that moving code does
#: not silently widen the list -- a renamed or relocated FUNCTION drops out of
#: the list and the guard names it again, which is the behaviour we want.
#:
#: The reasons are osprey's #136 G0 verdict, ruling 1:
#:   * operator-level -- no firm exists, so there is no firm tier to point at,
#:     and every one of them is a capability probe (`--help`, `--version`,
#:     `ext list`) whose answer does not depend on a graph at all.
#:   * no caller -- dead code kept because this round deletes nothing.
ALLOWED: dict[str, str] = {
    "firm/secrets/provider.py::capable":
        "operator-level: `base env --help`, a capability probe with no firm",
    "firm/sysconfig/service.py::_base_ext_capable":
        "operator-level: `base ext --help`, a capability probe with no firm",
    "firm/sysconfig/service.py::_base_ext_list":
        "operator-level: `base ext list`, the host inventory, no firm",
    "firm/dashboard/discovery.py::base_survey":
        "operator-level: the host survey (`--version`, `ext --help`, `ext list`)",
    "firm/rail/turns.py::relay_register":
        "no caller in the repo at 9848c444; kept, not deleted",
    "firm/rail/turns.py::relay_steer":
        "no caller in the repo at 9848c444; kept, not deleted",
    "firm/rail/turns.py::relay_task_state":
        "no caller in the repo at 9848c444; kept, not deleted",
}

#: What the guard must name on a tree where the seam has not landed. Written
#: before the first run (osprey's verdict, G-b). A red naming any other site, or
#: fewer than five, is a finding about this guard and not about the tree.
PRE_REGISTERED_RED = {
    "firm/services/base_extension.py::graph_rules",      # C1, rule list
    "firm/services/base_extension.py::install",          # C2, C3, C4
    "firm/services/base_domain.py::scaffold_tier",       # C5, scaffold
}


def _key(call) -> str:
    return f"{call.path}::{call.func}"


def _report():
    """Scan `src/firm`, and refuse to report a count from a blind reader.

    A guard that says "0 uncontrolled calls" over a tree it could not parse
    reads exactly like a clean tree (law 23 and law 48). So the visited counts
    are asserted first, and an empty scan fails loudly instead of passing.
    """
    report = scan_tree(_SRC)
    assert report.files_visited > 50, (
        "the scanner visited %d files under %s -- it is blind, and a count from "
        "a blind reader is not a measurement"
        % (report.files_visited, _SRC))
    assert report.calls_visited > 20, (
        "the scanner visited %d subprocess calls, which cannot be right for "
        "this tree" % report.calls_visited)
    assert not report.unparsed, (
        "files the scanner could not read, so its zero means nothing: %s"
        % report.unparsed)
    assert report.base_calls, (
        "the scanner found no base calls at all in src/firm, which would mean "
        "its binary-name test has stopped matching this codebase")
    return report


def test_no_base_call_in_src_runs_without_a_named_cwd():
    """Every base call for a firm names its working directory, or is allow-listed."""
    report = _report()
    offenders = [c for c in report.uncontrolled if _key(c) not in ALLOWED]
    detail = "\n".join(
        f"  {c.site}  {c.func}()  base {c.verb or '?'}  shape={c.shape}  "
        f"verdict={c.verdict}"
        + (f"  cwd={c.cwd_source}" if c.cwd_source else "")
        for c in offenders)
    assert not offenders, (
        "base calls that run from a directory nobody chose (%d of %d base calls "
        "found, %d files and %d subprocess calls visited):\n%s\n\n"
        "Each of these reads whatever .base sits above the caller's directory. "
        "Route it through `base_domain.base_cwd(workspace)`, or add it to "
        "ALLOWED with a reason."
        % (len(offenders), len(report.base_calls), report.files_visited,
           report.calls_visited, detail))


def test_the_allow_list_has_no_stale_entries():
    """An allow-list entry for a call that no longer exists hides the next one.

    A stale key is not harmless: it is a pre-excused blind spot, and the next
    call that lands in that function inherits the excuse (law 31's corollary).
    """
    report = _report()
    live = {_key(c) for c in report.base_calls}
    stale = sorted(set(ALLOWED) - live)
    assert not stale, (
        "ALLOWED names calls the scanner no longer finds, so the reason has "
        "outlived the call and would excuse whatever lands there next: %s"
        % stale)


def test_the_guard_names_all_eight_shapes():
    """The guard reaches every shape this codebase writes, not just its own cases.

    This leg tests the GUARD, not the tree, so it is green on both sides of the
    fix and is NOT evidence that the fix works (law 45). Its evidence is the
    deletion table below it.

    Eight since G2: shape 8 is a resolver that returns `(path, reason)`, which
    the scanner counted as no resolver at all -- so one real base call
    (`firm_relay.py:91`) was invisible to a guard whose whole claim is that none
    are.
    """
    calls, visited = scan_source(_FIXTURE.read_text(encoding="utf-8"),
                                 "tests/base_call_shapes_fixture.py")
    assert visited > 0, "the scanner visited no runner calls in the fixture"
    named = {c.func for c in calls if c.verdict != "controlled"}
    expected = {
        "shape_1_resolver_bound_name",
        "shape_2_shutil_which_name",
        "shape_3_bare_string",
        "shape_4_wrapper_two_hops",
        "shape_5_popen",
        "shape_6_cwd_from_the_process",
        "shape_7_module_local_resolver",
        "shape_8_tuple_returning_resolver",
    }
    assert named == expected, (
        "the guard must name all eight shapes and only those.\n"
        "  missed  : %s\n  spurious: %s"
        % (sorted(expected - named), sorted(named - expected)))


def test_the_guard_leaves_the_two_controls_alone():
    """The benign inputs beside the hostile ones (law 49).

    Without these, a guard that named every base call it found would pass the
    shape leg and still be useless.
    """
    calls, _ = scan_source(_FIXTURE.read_text(encoding="utf-8"),
                           "tests/base_call_shapes_fixture.py")
    named = {c.func for c in calls if c.verdict != "controlled"}
    assert "control_a_controlled_call_is_not_named" not in named, (
        "the guard named a call that passes cwd=workspace, so it cannot tell a "
        "controlled call from an uncontrolled one")
    assert "control_a_non_base_call_is_not_named" not in named, (
        "the guard named `git rev-parse`, so its binary test matches "
        "subprocesses that are not base")


@pytest.mark.parametrize("shape", [
    "shape_1_resolver_bound_name",
    "shape_2_shutil_which_name",
    "shape_3_bare_string",
    "shape_4_wrapper_two_hops",
    "shape_5_popen",
    "shape_6_cwd_from_the_process",
    "shape_7_module_local_resolver",
    "shape_8_tuple_returning_resolver",
])
def test_deleting_a_fixture_shape_drops_the_count_by_exactly_one(shape):
    """Prove each shape by mutation, not by presence (law 38).

    Removing one shape's call must cost the guard exactly one finding. A shape
    the guard cannot see costs it none, and this row is the only thing that
    separates those two states.
    """
    source = _FIXTURE.read_text(encoding="utf-8")
    before, _ = scan_source(source, "fixture")
    baseline = len([c for c in before if c.verdict != "controlled"])

    lines = source.splitlines(keepends=True)
    out, inside, removed = [], False, 0
    for line in lines:
        if line.startswith(f"def {shape}("):
            inside = True
            out.append(line)
            continue
        if inside:
            stripped = line.strip()
            if stripped.startswith(("return ", "run_utf8", "popen_utf8", "_run")):
                removed += 1
                out.append(textwrap.indent("return None\n", "    "))
                inside = False
                continue
            if line and not line[0].isspace():
                inside = False
        out.append(line)

    assert removed == 1, (
        "the mutation did not land: %d call lines replaced in %s, so this row "
        "tests nothing" % (removed, shape))

    after, _ = scan_source("".join(out), "fixture")
    mutated = len([c for c in after if c.verdict != "controlled"])
    assert mutated == baseline - 1, (
        "deleting %s's call changed the guard's count from %d to %d. Exactly "
        "one was expected: a shape the guard cannot see costs it nothing, and "
        "that is indistinguishable from a guard that works until this row runs."
        % (shape, baseline, mutated))
