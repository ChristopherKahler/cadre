"""Every base-tier child in section 11 goes through `brun`, and `brun` supplies
the environment itself.

THE DEFECT (issue #61, the harness half). `scripts/acceptance-e2e.py` section 11
runs `base` and the product against a SANDBOX tier, and it does so by having
every call site remember two things at once: `cwd=ws` and `env=benv`. The issue's
own constraint 5 names the problem exactly -- nothing should depend on sixteen
call sites each remembering a convention.

BOTH HALVES OF THE CONVENTION ARE LOAD-BEARING, and the harness says why at the
point it builds them: BASE_HOME governs the GLOBAL tier, and the WORKSPACE tier
follows the CURRENT DIRECTORY. Set one and forget the other and `base` reads and
writes the operator's own graph while every assertion in the section still
passes. That is not hypothetical: it is the 2026-09-11 incident, six junk
workspaces in the operator's real registry written by a run that looked clean.

THE INVENTORY WAS NEVER SIXTEEN. Sixteen is what a grep for `env=benv` and
`env=bmember` finds, and it was the number in the issue body and in the design
doc. An `ast` walk of section 11 at 89bcf3208abc finds NINETEEN child spawns. The
three the grep could not see:

  * `base --version`, the probe that FEEDS the guard -- no `cwd`, no `env`.
  * the bridge-module load -- `cwd=sandbox`, no env, touches no base tier.
  * `fire_gate`'s direct `subprocess.run`, which spells the convention by hand as
    `cwd=str(ws)` and `env={**os.environ, **bmember(mid)}` and is therefore
    invisible to a selector written for the convention's SPELLING.

That last one is the reusable half: a convention written out by hand cannot be
found by a pattern written for the convention's usual form. The same class as
`git grep` being blind to untracked files. Only the AST walk saw it, which is why
this guard is an AST walk and not a regex.

THE PREDICATE, stated once so a reader knows what this sweep claims to cover: a
child spawn inside section 11 is BASE-TIER if it runs the `base` binary, or is
handed the sandbox BASE_HOME environment, or runs with the firm workspace as its
current directory. Any one of the three is enough to reach a base tier, because
BASE_HOME governs the global tier and cwd governs the workspace tier.

The predicate is deliberately narrow rather than "every child spawn", and that
choice removes an exemption instead of adding one. The bridge-module load is not
a member of the set at all -- it runs the sandbox venv's python in the sandbox
with no base environment -- so it needs no waiver. An exemption list is a
standing invitation to grow, and every entry on it is a place the guard does not
look.

THE ONE OPT-OUT, and it is a parameter rather than a list entry: `base --version`
is the probe whose result the platform guard classifies. Requiring the guard's
verdict before running the call that produces the verdict is circular, so that
call passes `feeds_the_guard=True`. It turns off the VERDICT check and nothing
else -- the sandbox environment is still supplied, because the guard classifies
the BINARY while `benv` decides WHERE THAT BINARY WRITES, and those are
independent claims. Bundling them would leave the earliest and least-classified
`base` call in the harness holding the operator's ambient environment.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "scripts" / "acceptance-e2e.py"

# Section 11 is located by its own marker comment and its own single exit, never
# by a line number. Every line number in this section moves when the call sites
# are rewritten, and a guard pinned to one either fails at once -- merely
# annoying -- or keeps passing while pointing at different code, which is the
# dangerous one.
SECTION_MARKER = "# ---- 11. base as the engine"
SECTION_EXIT = "return end_base_section()"

# The two names that spawn a child in this file. `subprocess.run` is here
# because `fire_gate` used it directly; a sweep that knew only `run` would have
# reported the section clean while one call site kept its hand-rolled
# environment merge.
SPAWNERS = ("run", "subprocess.run")

WRAPPER = "brun"


def _purge_bytecode() -> None:
    """Drop the harness's cached bytecode. Issue #89.

    CPython validates a `.pyc` on the source's mtime SECOND and its SIZE, never
    its content, and `cache_from_source` keys the cache on the SOURCE PATH
    rather than the module name -- so every by-path importer of this harness,
    under whatever name it passes, shares ONE cache file. A plant that keeps the
    byte count and lands inside the same whole second is therefore invisible:
    the source is never read, the old bytecode runs, and the guard agrees with
    its own unmodified file.

    The restore needs this every bit as much as the plant. An unpurged restore
    can leave the MUTATED bytecode in place and turn the after-column green into
    a lie in the other direction.

    This arm's plant is +56 bytes today, so size alone would invalidate the
    cache. That is luck and not a safeguard: it becomes a zero delta the moment
    the violation is reworded to the length of the line it displaces, and
    nothing about that edit would look dangerous.
    """
    cache = Path(importlib.util.cache_from_source(str(HARNESS)))
    for stale in cache.parent.glob(f"{HARNESS.stem}.*.pyc"):
        stale.unlink()


def _source() -> str:
    return HARNESS.read_text(encoding="utf-8")


def _callee(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_callee(node.value)}.{node.attr}"
    return ""


def _section_bounds(src: str) -> tuple[int, int]:
    """The line range of section 11, derived from the file, not remembered.

    Both ends are asserted. A marker that has been reworded collapses the range
    to nothing, and a sweep over an empty range reports no violations -- the
    same shape as a clean sweep. `absent-is-a-passing-value`, inside the guard
    written to stop it.
    """
    lines = src.splitlines()
    starts = [i for i, ln in enumerate(lines, 1)
              if ln.strip().startswith(SECTION_MARKER)]
    assert len(starts) == 1, (
        f"expected exactly one {SECTION_MARKER!r} marker, found {len(starts)} "
        f"at {starts}. The sweep cannot locate section 11 and would report a "
        "clean file while checking nothing.")
    exits = [i for i, ln in enumerate(lines, 1) if ln.strip() == SECTION_EXIT]
    assert exits, (
        f"no {SECTION_EXIT!r} found; section 11's single exit is how its end is "
        "located and the sweep has no range to walk.")
    return starts[0], max(exits)


def _brun_body_lines(tree: ast.AST) -> set[int]:
    """Every line inside `brun`'s own definition.

    `brun` calls `run()` -- that is what it is for -- and that one call must not
    be reported as a violation of the rule it implements. Anchored on the
    ENCLOSING FUNCTION, which survives every edit to the section, rather than on
    the line the call happens to sit at today.
    """
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == WRAPPER:
            return {d.lineno for d in ast.walk(n) if hasattr(d, "lineno")}
    return set()


def _kwsrc(call: ast.Call, name: str, src: str) -> str | None:
    kw = next((k for k in call.keywords if k.arg == name), None)
    if kw is None:
        return None
    return ast.get_source_segment(src, kw.value) or ""


def _base_tier_reason(call: ast.Call, src: str) -> str | None:
    """Why this spawn reaches a base tier, or None if it does not.

    Three independent fingerprints, and ANY of them puts the call in the set.
    """
    if call.args:
        first = call.args[0]
        if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
            head = first.elts[0]
            if isinstance(head, ast.Constant) and head.value == "base":
                return "runs the base binary"
    env = _kwsrc(call, "env", src)
    if env is not None and ("benv" in env or "bmember" in env):
        return f"is handed the sandbox base environment ({env})"
    cwd = _kwsrc(call, "cwd", src)
    if cwd is not None and cwd in ("ws", "str(ws)"):
        return f"runs in the firm's workspace tier ({cwd})"
    return None


def _section_spawns() -> tuple[list[dict], str]:
    """Every base-tier spawn in section 11, compliant or not."""
    src = _source()
    tree = ast.parse(src)
    lo, hi = _section_bounds(src)
    inner = _brun_body_lines(tree)
    found = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        if not (lo <= n.lineno <= hi):
            continue
        name = _callee(n.func)
        if name == WRAPPER:
            found.append({"line": n.lineno, "callee": name, "why": "wrapper",
                          "compliant": True, "node": n})
            continue
        if name not in SPAWNERS or n.lineno in inner:
            continue
        why = _base_tier_reason(n, src)
        if why is None:
            continue
        found.append({"line": n.lineno, "callee": name, "why": why,
                      "compliant": False, "node": n})
    return found, src


def test_the_harness_and_its_section_are_there_to_be_read():
    """Assert the anchor before asserting anything about what it anchors."""
    assert HARNESS.is_file(), f"{HARNESS} is missing; this guard checks nothing"
    lo, hi = _section_bounds(_source())
    assert hi > lo, f"section 11 spans {lo}..{hi}, which is not a range"


def test_the_sweep_actually_finds_the_sections_spawns():
    """A floor, so an empty sweep cannot read as a clean one.

    Measured at 89bcf3208abc: nineteen child spawns in section 11, of which
    EIGHTEEN are base-tier and one -- the bridge-module load -- is not. If this
    drops toward zero the sweep has stopped looking, and nothing about that
    looks different from a section with no violations in it.
    """
    spawns, _ = _section_spawns()
    assert len(spawns) >= 15, (
        f"only {len(spawns)} base-tier spawn(s) found in section 11. The sweep "
        "has stopped reaching the code, and an empty sweep reports exactly what "
        "a clean one does.")


def test_every_base_tier_spawn_in_section_11_goes_through_brun():
    """THE RULE. One place holds the convention, and it is not sixteen places."""
    spawns, _ = _section_spawns()
    bad = [s for s in spawns if not s["compliant"]]
    assert not bad, (
        f"{len(bad)} base-tier spawn(s) in section 11 bypass `{WRAPPER}` and "
        "carry the cwd/env convention by hand, so nothing stops the next one "
        "from carrying half of it:\n  "
        + "\n  ".join(f"line {s['line']}: {s['callee']}(...) -- {s['why']}"
                      for s in bad))


def test_no_caller_hands_brun_a_cwd_or_an_env():
    """The wrapper SUPPLIES the environment; it does not ask the caller to.

    Isolate by making the wrong thing unreachable, never by passing a root and
    trusting the callee to use it. A `brun` that accepted `cwd=` would be the
    sixteen call sites again, wearing one name.
    """
    spawns, src = _section_spawns()
    wrappers = [s for s in spawns if s["compliant"]]
    offenders = []
    for s in wrappers:
        for arg in ("cwd", "env"):
            got = _kwsrc(s["node"], arg, src)
            if got is not None:
                offenders.append(f"line {s['line']}: {arg}={got}")
    assert not offenders, (
        "a caller supplied the environment to the wrapper whose whole job is to "
        "supply it:\n  " + "\n  ".join(offenders))


def test_the_only_opt_out_is_the_probe_that_feeds_the_guard():
    """Exactly one call may skip the verdict check, and it is named, not listed.

    Anchored on the ARGV SHAPE, never on a line number: this test exists across
    an edit that moves every line in the section.
    """
    spawns, src = _section_spawns()
    opted = [s for s in spawns
             if _kwsrc(s["node"], "feeds_the_guard", src) is not None]
    assert len(opted) == 1, (
        f"{len(opted)} call(s) opt out of the guard's verdict; exactly one may, "
        "and a second is a second place the guard does not look: "
        + ", ".join(f"line {s['line']}" for s in opted))
    argv = ast.get_source_segment(src, opted[0]["node"].args[0])
    assert argv is not None and '"--version"' in argv and '"base"' in argv, (
        f"the verdict opt-out is on {argv!r}, which is not the `base --version` "
        "probe. Only the call that PRODUCES the verdict may skip waiting for it; "
        "any other call skipping it is running against an unclassified base.")
    assert _kwsrc(opted[0]["node"], "env", src) is None, (
        "the probe opted out of the environment as well as the verdict. Those "
        "are separate claims: the guard classifies the BINARY, `benv` decides "
        "WHERE IT WRITES. The earliest, least-classified `base` call in the "
        "harness is the last one that should hold the ambient environment.")


def test_a_base_tier_spawn_planted_in_the_real_section_is_caught():
    """THE RED ARM, planted in the real file and shown to change it.

    A `tmp_path` fixture cannot make this claim. It proves the PATTERN matches;
    it says nothing about whether the sweep's RANGE reaches section 11. So the
    seventeenth call site is planted in the real harness, the real sweep is
    asked about it, and the file is restored in a `finally`.

    The assertion that the plant ACTUALLY CHANGED THE SOURCE is not decoration.
    An earlier planted-drift control in this lane planted nothing -- its pattern
    assumed each magic number started its own line and they sit two per line --
    and it passed, agreeing with itself. A plant that did not land is a guard
    grading its own unmodified file.
    """
    original = _source()
    anchor = '            reads_firm = str(ws) in out.replace("/", os.sep)\n'
    assert original.count(anchor) == 1, (
        "the plant's anchor is not unique in the harness, so the violation "
        "would land somewhere this test did not choose")
    violation = '            rc, out = run(["base", "doctor"], env=benv)\n'
    planted_src = original.replace(anchor, violation + anchor, 1)
    assert planted_src != original, "the plant did not change the source"
    assert violation in planted_src, "the plant did not land in the source"
    try:
        HARNESS.write_text(planted_src, encoding="utf-8")
        _purge_bytecode()
        assert _source() == planted_src, "the planted file did not reach disk"
        spawns, _ = _section_spawns()
        bad = [s for s in spawns if not s["compliant"]]
        assert bad, (
            "a `base` call carrying `env=benv` and NO `cwd=ws` was planted in "
            "section 11 and the sweep reported the section clean. Every green "
            "this guard prints is meaningless.")
        assert any("runs the base binary" in s["why"] for s in bad), (
            "the sweep caught something, but not through the predicate branch "
            f"this arm names: {[s['why'] for s in bad]}")
    finally:
        HARNESS.write_text(original, encoding="utf-8")
        _purge_bytecode()
    assert _source() == original, "the arm did not restore the harness"


# ---------------------------------------------------------------------------
# The refusal, exercised as a function.
#
# CANNOT BE RED ON THE OLD TREE, AND THAT IS STATED RATHER THAN COUNTED. `brun`
# is a closure over section 11's locals and no test can call it, which is why
# the refusal is split into a module-level `brun_refusal` -- the same split, for
# the same reason, that `skip_reason` got from `section_may_run`. On a tree
# without that function these raise AttributeError. An AttributeError is not a
# measurement: a test for an API that does not exist yet raises instead of
# measuring, and counting it as a before-column reports a red that carries no
# before-and-after information at all. The real before-column for this lane is
# the sweep above.
# ---------------------------------------------------------------------------

def _harness_module():
    spec = importlib.util.spec_from_file_location("acceptance_e2e", HARNESS)
    assert spec and spec.loader, f"cannot load {HARNESS}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_brun_refuses_before_the_guard_has_recorded_a_verdict():
    """A row added ABOVE the guard is refused, not run against an unknown base."""
    m = _harness_module()
    why = m.brun_refusal(m.VERDICT_UNSET, {"BASE_HOME": "/tmp/x"})
    assert why, (
        "with no verdict recorded, `brun` allowed a child to spawn. A row added "
        "above the guard would run against a base nobody has classified, which "
        "is the hole the guard was written to close.")
    assert "verdict" in why.lower()


def test_brun_refuses_when_the_guard_ruled_against_this_host():
    m = _harness_module()
    why = m.brun_refusal(m.VERDICT_REFUSED, {"BASE_HOME": "/tmp/x"})
    assert why, (
        "the guard ruled this host may NOT run the section and `brun` spawned "
        "anyway; the refusal is decorative")


def test_brun_allows_the_spawn_once_the_guard_has_cleared_the_host():
    """The other direction. A refusal that refuses everything is not a guard."""
    m = _harness_module()
    assert m.brun_refusal(m.VERDICT_MAY_RUN, {"BASE_HOME": "/tmp/x"}) is None, (
        "`brun` refused on a cleared host, so section 11 can never run and the "
        "guard passes by never measuring anything")


def test_feeds_the_guard_turns_off_the_verdict_check_and_nothing_else():
    """The split osprey ruled: the probe skips the VERDICT, never the ENV.

    Both halves are asserted. The first, that the circular case is allowed
    through. The second, that the opt-out did not quietly become a general one --
    which is the whole reason it is a named parameter and not a `force` flag.
    """
    m = _harness_module()
    env = {"BASE_HOME": "/tmp/x"}
    assert m.brun_refusal(m.VERDICT_UNSET, env, feeds_the_guard=True) is None, (
        "the probe that PRODUCES the verdict was made to wait for it, which is "
        "circular; section 11 can never start")
    why = m.brun_refusal(m.VERDICT_UNSET, None, feeds_the_guard=True)
    assert why, (
        "`feeds_the_guard` waived the ENVIRONMENT as well as the verdict. The "
        "guard classifies the binary; `benv` decides where that binary writes. "
        "Waiving both leaves the earliest `base` call in the harness pointed at "
        "the operator's own tier.")
    assert "environment" in why.lower() or "base_home" in why.lower()


@pytest.mark.parametrize("verdict", ["may-run", "refused", None])
def test_no_verdict_value_waives_the_environment(verdict):
    """There is no state in which a missing environment is acceptable."""
    m = _harness_module()
    assert m.brun_refusal(verdict, None) is not None, (
        f"verdict {verdict!r} allowed a spawn with no sandbox environment at "
        "all, so the child would inherit the operator's own BASE_HOME")
