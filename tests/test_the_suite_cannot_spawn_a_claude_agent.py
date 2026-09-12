"""The test suite cannot resolve, and therefore cannot spawn, a real Claude agent.

THE DEFECT (issue #81). The suite booted a real ``claude`` carrying
``--dangerously-skip-permissions``, parented by pytest, out of a pytest temp
directory. It took a relay title and answered a ping addressed to another
session. It also spends the operator's tokens on every run.

That is the same class as #61 one level out. #61 was the acceptance harness
reaching the operator's real ``base`` tier; this is the unit suite reaching the
operator's real ``claude``. ``tests/conftest.py`` already fences ``base`` two
ways and there was NO equivalent for ``claude`` at all.

THE FENCE IS AN ENV VAR, AND THE REASON IS MEASURED, NOT PREFERRED. At a489230:

    PATH=/nonexistent python -c "...resolve_claude_bin()"
    -> ('/home/<user>/.local/bin/claude', 'PATH resolution: ...')

``resolve_claude_bin`` appends ``~/.local/bin`` UNCONDITIONALLY, *after* the
PATH walk. A fixture that sanitises ``PATH`` therefore looks exactly like a
fence and is not one, and it fails SILENTLY. Env is also the only kind of fence
that REACHES A CHILD, and the agent that was caught was a child of pytest --
``conftest._no_ambient_base`` already says in so many words that an in-process
monkeypatch does not exist in a child interpreter.

WHAT THIS FILE GUARDS THAT THE FIXTURE CANNOT GUARD ITSELF. A fixture closes the
resolvers that exist today. It says nothing about the fourth one somebody adds
next month. So the sweep below states the invariant instead of the instance:

    EVERY function in src/firm/ that LOCATES a claude binary must consult
    CADRE_CLAUDE_BIN.

A new resolver that honours the variable passes without anyone touching this
file. One that does not, fails -- which is the whole difference between a fix
and a fix that stays fixed. That invariant is exactly what #81 surfaced: there
were two resolvers, and only one of them honoured the documented escape hatch,
so an operator who set it was obeyed by the Member spawn path and silently
ignored by the dashboard launch path.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "firm"
CONFTEST = REPO / "tests" / "conftest.py"

ENV_VAR = "CADRE_CLAUDE_BIN"

# Every file name the claude EXECUTABLE can have. Enumerated, never patterned:
# see `_mentions_claude` for the near-miss this replaced. The Windows suffixes
# are the PATHEXT defaults `firm.pulse.spawn._candidate_names` already uses.
EXEC_NAMES = {"claude"} | {"claude" + e for e in (".com", ".exe", ".bat", ".cmd")}

# The act of LOCATING a binary, as opposed to merely naming one. A function that
# builds candidate file names is not a resolver -- `spawn._candidate_names` does
# exactly that and must not be dragged in as a false positive -- but a function
# that asks the filesystem whether a candidate is there and runnable is.
LOCATE_CALLS = ("shutil.which", "os.access", "os.path.isfile", "exists", "glob")

# Known resolvers, named so a sweep that silently stops finding them fails
# rather than reporting a clean tree. An empty sweep reads exactly like a clean
# one, which is the failure this whole issue is about.
KNOWN_RESOLVERS = {"resolve_claude_bin", "_which_claude", "find_claude"}

# How to CALL each known resolver, so the refusal invariant below can assert the
# OUTCOME rather than a platform's mechanism. Keyed by the same names, and the
# test asserts the two sets are equal -- adding a resolver to KNOWN_RESOLVERS
# without wiring it in here fails rather than silently going unexercised.
RESOLVER_CALLS = {
    "resolve_claude_bin": ("firm.pulse.spawn", lambda f: f()[0]),
    "_which_claude": ("firm.dashboard.launch", lambda f: f()),
    "find_claude": ("firm.rail.turns", lambda f: f()),
}

# The order `_child_resolves` prints its three lines in, named rather than
# assumed. It is NOT alphabetical, so zipping its results against
# `sorted(RESOLVER_CALLS)` would report the wrong resolver as the open one, and
# a guard that names the wrong call site is worse than one that says nothing.
CHILD_ORDER = ("resolve_claude_bin", "_which_claude", "find_claude")


def _funcs():
    """(file, FunctionDef, source) for every function under src/firm."""
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:                      # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seg = ast.get_source_segment(text, node) or ""
                yield path, node, seg


def _mentions_claude(seg: str) -> bool:
    """A string constant naming the claude BINARY appears in this function.

    EXACT, and lower-case, and that is the whole subtlety. A prefix match on
    "claude" was the first version of this and it dragged in eight functions
    that have nothing to do with locating an executable:

        "CLAUDE.md"                          a document the product writes
        "claude runtime not wired: {detail}"  the message a CALLER prints when
                                              a resolver already returned None
        "claude_bin"                         a dict key
        "claude_code", "claude_pro_200"      identifiers

    Every one of those would have been forced to grow a CADRE_CLAUDE_BIN
    reference it has no use for, and the next person would have loosened this
    sweep until it guarded nothing. A guard that fires on correct code gets
    edited until it stops being a guard.

    The binary's name is exactly ``claude`` on POSIX, and ``claude`` plus a
    PATHEXT suffix on Windows, so that is what this matches and nothing else.

    An EXACT SET, not a prefix, and that distinction cost a second pass: the
    obvious tightening was ``startswith("claude.")``, which still matches
    ``"CLAUDE.md"`` once it is lower-cased. A near-miss predicate reads as fixed
    and is not, so the names are enumerated instead of patterned.
    """
    try:
        tree = ast.parse("if 1:\n" + "\n".join("    " + l for l in seg.splitlines()))
    except SyntaxError:                          # pragma: no cover
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if n.value.lower() in EXEC_NAMES:
                return True
    return False


def _locates(seg: str) -> bool:
    return any(call in seg for call in LOCATE_CALLS)


def _resolvers():
    """Every function under src/firm that LOCATES a claude binary."""
    out = []
    for path, node, seg in _funcs():
        if not seg:
            continue
        if (_mentions_claude(seg) or ENV_VAR in seg) and _locates(seg):
            out.append((path.relative_to(REPO), node.name, seg))
    return out


# ---------------------------------------------------------------- the sweep

def test_the_sweep_finds_the_resolvers_it_is_supposed_to_guard():
    """A floor, so an empty sweep cannot read as a clean one."""
    assert SRC.is_dir(), f"{SRC} is missing; this guard checks nothing"
    found = {name for _, name, _ in _resolvers()}
    missing = KNOWN_RESOLVERS - found
    assert not missing, (
        f"the sweep no longer finds {sorted(missing)}, so it has stopped "
        f"looking at the code it exists to guard (it found {sorted(found)}). "
        "A sweep that reaches nothing reports exactly what a clean one does.")


def test_every_claude_resolver_consults_the_escape_hatch():
    """THE INVARIANT. Not 'these two resolvers are fine' -- 'every resolver is'.

    This is what makes #81 a class fix instead of two patches. A resolver that
    ignores CADRE_CLAUDE_BIN cannot be fenced by conftest, so the suite could
    spawn a real agent through it, and an operator who sets the variable is
    silently disobeyed through it.
    """
    deaf = [(f, n) for f, n, seg in _resolvers() if ENV_VAR not in seg]
    assert not deaf, (
        f"{len(deaf)} function(s) locate a claude binary without consulting "
        f"{ENV_VAR}, so the suite cannot fence them and an operator who sets "
        "that variable is silently ignored through them:\n  "
        + "\n  ".join(f"{f}::{n}" for f, n in deaf))


def test_a_resolver_planted_in_the_real_tree_is_caught():
    """THE RED ARM, planted in the real src/ tree and shown to change it.

    A tmp_path fixture would prove the PATTERN matches and say nothing about
    whether the sweep's ROOT reaches the product. The same reasoning
    tests/test_no_hardcoded_roots.py uses for its own plant.
    """
    planted = SRC / "_claude_resolver_red_arm.py"
    assert not planted.exists(), (
        f"{planted} already exists; refusing to overwrite a real file")
    before = {n for _, n, _ in _resolvers()}
    try:
        planted.write_text(
            "import shutil\n\n\n"
            "def find_claude_the_wrong_way():\n"
            "    return shutil.which('claude')\n",
            encoding="utf-8")
        assert planted.exists(), "the plant did not reach disk"
        after = {n for _, n, _ in _resolvers()}
        assert after != before, (
            "planting a new claude resolver in src/ did not change what the "
            "sweep sees, so the sweep is not reading the product tree")
        deaf = [(f, n) for f, n, seg in _resolvers() if ENV_VAR not in seg]
        assert any(n == "find_claude_the_wrong_way" for _, n in deaf), (
            "a resolver that locates claude and never consults "
            f"{ENV_VAR} was planted in the real src/ tree and the guard did "
            f"not report it (it reported {deaf})")
    finally:
        planted.unlink(missing_ok=True)
    assert not planted.exists(), f"the arm left {planted} behind"
    assert {n for _, n, _ in _resolvers()} == before, "the arm did not restore the tree"


def test_a_name_builder_is_not_mistaken_for_a_resolver():
    """The other direction. A guard that over-matches gets weakened until it dies.

    `spawn._candidate_names` mentions "claude" in every branch and resolves
    nothing -- it builds file names for the caller to try. If the predicate
    dragged it in, the honest fix would be to bolt CADRE_CLAUDE_BIN onto a
    function that has no use for it, and the next person would loosen the sweep.
    """
    names = {n for _, n, _ in _resolvers()}
    assert "_candidate_names" not in names, (
        "the sweep counts a pure name-builder as a resolver, so it will force "
        "a meaningless change and then get loosened")


@pytest.mark.parametrize("innocent", [
    "CLAUDE.md",                              # a document the product writes
    "claude runtime not wired: {detail}",     # a CALLER's message after a None
    "claude_bin",                             # a dict key
    "claude_code",                            # an identifier
    "claude_pro_200",                         # a plan name
    "claude/cadre-framework/frameworks/org-design.md",   # a doc path
])
def test_the_predicate_does_not_fire_on_things_that_merely_say_claude(innocent):
    """RED ARM, in the other direction. Every one of these tripped version one.

    A prefix match on "claude" pulled in eight functions across doctor.py,
    heartbeat.py, founding.py, wiring.py, budget.py and launch.py, none of which
    locate a binary. Forcing CADRE_CLAUDE_BIN into any of them would be
    meaningless, and the next person would loosen the sweep until it guarded
    nothing. These are the exact strings that did it, pinned so the predicate
    cannot drift back.
    """
    seg = f"def f():\n    return {innocent!r}\n"
    assert not _mentions_claude(seg), (
        f"{innocent!r} reads as the claude BINARY to this sweep, so the guard "
        "fires on correct code and will be weakened until it stops guarding")


def test_the_predicate_DOES_fire_on_every_real_binary_name():
    """The control for the control: the tightening must not have gone too far."""
    for name in sorted(EXEC_NAMES) + ["CLAUDE.EXE"]:
        seg = f"def f():\n    return shutil.which({name!r})\n"
        assert _mentions_claude(seg), (
            f"{name!r} is a real name for the claude executable and the sweep "
            "is blind to it, so a resolver using it would never be checked")


# ------------------------------------------------- the fence, through a CHILD

def _child_resolves():
    """What a CHILD interpreter resolves, under whatever env this process has.

    A child, not an import, because that is the boundary the breach crossed and
    the boundary an in-process monkeypatch cannot reach.
    """
    code = (
        "from firm.pulse.spawn import resolve_claude_bin\n"
        "from firm.dashboard.launch import _which_claude\n"
        "from firm.rail.turns import find_claude\n"
        "print(resolve_claude_bin()[0])\n"
        "print(_which_claude())\n"
        "print(find_claude())\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120, env=env)
    assert out.returncode == 0, f"child failed: {out.stderr[-400:]}"
    lines = out.stdout.strip().splitlines()
    assert len(lines) == 3, f"child printed {lines!r}"
    return [None if l == "None" else l for l in lines]


def test_the_fence_reaches_a_child_interpreter():
    """THE ARM THAT MATTERS. The agent that was caught was a child of pytest.

    All THREE resolvers, not two. ``rail.turns.find_claude`` was outside the
    guarded set until the Windows failure exposed it, and env is what reaches a
    child, so a resolver left out here is a hole an in-process check cannot see.
    """
    spawn_bin, launch_bin, rail_bin = _child_resolves()
    open_ones = {"spawn": spawn_bin, "launch": launch_bin, "rail": rail_bin}
    still_open = {k: v for k, v in open_ones.items() if v is not None}
    assert not still_open, (
        "a CHILD of the test suite resolved a real claude binary "
        f"({still_open}), so the suite can still boot an agent with the "
        "operator's credentials and spend their tokens")


def test_CONTROL_this_host_would_resolve_a_claude_without_the_fence():
    """Without this, every arm above passes on a machine that has no claude.

    Absent is a passing value, and that is precisely the shape of defect this
    fleet has been closing all week. So: drop the fence for one child and
    require that it DOES find one. If the host genuinely has none, this SKIPS
    and says so -- unmeasured, never passed.
    """
    env = dict(os.environ)
    env.pop(ENV_VAR, None)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    code = ("from firm.pulse.spawn import resolve_claude_bin\n"
            "print(resolve_claude_bin()[0])\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120, env=env)
    assert out.returncode == 0, f"child failed: {out.stderr[-400:]}"
    found = out.stdout.strip()
    if found == "None":
        pytest.skip(
            "this host resolves no claude at all even with the fence removed, "
            "so the arms above cannot tell a working fence from an empty "
            "machine here. Unmeasured, not passed.")
    assert found, "the control child printed nothing"


def test_the_conftest_fence_is_actually_installed():
    """The fixture is the thing under test; assert it exists and is autouse."""
    text = CONFTEST.read_text(encoding="utf-8")
    assert ENV_VAR in text, (
        f"tests/conftest.py no longer sets {ENV_VAR}, so nothing stops a test "
        "from resolving the operator's real claude")
    tree = ast.parse(text)
    fenced = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and ENV_VAR in (
                  ast.get_source_segment(text, n) or "")]
    assert fenced, f"no conftest fixture mentions {ENV_VAR}"
    for fn in fenced:
        decos = [ast.get_source_segment(text, d) or "" for d in fn.decorator_list]
        assert any("autouse=True" in d for d in decos), (
            f"conftest.{fn.name} sets {ENV_VAR} but is not autouse, so it only "
            "fences the tests that remember to ask for it")


def test_the_fence_points_at_a_path_that_does_not_exist():
    """The env var must name a path that is ABSENT, on every platform.

    This assertion used to be ``p.is_file() and not os.access(target, X_OK)``,
    and THAT LINE IS WHAT LET THE WINDOWS HOLE THROUGH. ``os.access(X_OK)`` is
    False for a plain file on Linux and TRUE on Windows, which has no execute
    bit, so the sentinel read as executable there and every resolver returned
    it. A directory is no better: ``X_OK`` is True for a directory on both
    platforms. Absence is the only state ``os.access``, ``is_file`` and
    ``shutil.which`` agree on everywhere.

    It must NOT be an executable stub either: `_is_execable` accepts anything
    with a shebang, so a "refusing" script would be RETURNED as the binary and
    then actually spawned. Louder than silence, still a spawn.
    """
    target = os.environ.get(ENV_VAR)
    assert target, f"{ENV_VAR} is not set inside the suite; the fence is off"
    p = Path(target)
    assert not p.exists(), (
        f"{ENV_VAR}={target} EXISTS. On Windows os.access(X_OK) is True for "
        "any readable file and True for any directory, so anything that exists "
        "here is handed back by every resolver and the suite can spawn it.")
    assert shutil.which(target) is None, (
        f"shutil.which resolved {ENV_VAR}={target}, so a resolver reaching for "
        "it through which() would still get a binary back")


def test_every_known_resolver_refuses_a_set_but_unusable_hatch():
    """THE INVARIANT THE OLD SWEEP MISSED, asserted as an OUTCOME.

    The static sweep asks whether each resolver CONSULTS ``CADRE_CLAUDE_BIN``.
    ``rail.turns.find_claude`` did consult it and then fell through to
    ``shutil.which`` on a miss, so the sweep passed it while it stayed open.
    CONSULTING IS NOT REFUSING.

    Asserting the outcome -- every resolver returns None under the live fence --
    is platform-independent by construction. It cannot be satisfied by a
    mechanism that happens to hold on one operating system, which is exactly how
    the previous version of this file passed on Linux and failed on Windows.
    """
    assert set(RESOLVER_CALLS) == KNOWN_RESOLVERS, (
        "RESOLVER_CALLS and KNOWN_RESOLVERS disagree: "
        f"{sorted(set(KNOWN_RESOLVERS) ^ set(RESOLVER_CALLS))}. A resolver "
        "named as guarded but never called here is not actually exercised.")
    assert set(CHILD_ORDER) == KNOWN_RESOLVERS, (
        f"CHILD_ORDER {CHILD_ORDER} does not name every guarded resolver")
    results = dict(zip(CHILD_ORDER, _child_resolves()))
    open_ones = [f"{RESOLVER_CALLS[n][0]}.{n} -> {v}"
                 for n, v in sorted(results.items()) if v is not None]
    assert not open_ones, (
        f"{len(open_ones)} resolver(s) returned a binary while "
        f"{ENV_VAR} points at an unusable path, so the suite can still spawn a "
        "real agent through them:\n  " + "\n  ".join(open_ones))


def test_shutil_which_alone_would_not_have_been_enough():
    """Why the fence is not `monkeypatch shutil.which`.

    Measured on this box: `shutil.which("claude")` returns None while both
    resolvers still find `~/.local/bin/claude`, because each falls back to the
    home bin directory on its own. Stubbing `shutil.which` would have looked
    like a fence and left both paths open.
    """
    env = dict(os.environ)
    env.pop(ENV_VAR, None)
    env["PATH"] = "/nonexistent"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    code = ("import shutil\n"
            "from firm.pulse.spawn import resolve_claude_bin\n"
            "print(shutil.which('claude'))\n"
            "print(resolve_claude_bin()[0])\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120, env=env)
    assert out.returncode == 0, f"child failed: {out.stderr[-400:]}"
    which_said, resolver_said = out.stdout.strip().splitlines()
    if resolver_said == "None":
        pytest.skip("this host has no claude outside PATH, so the gap this "
                    "test describes is not reachable here. Unmeasured.")
    assert which_said == "None" and resolver_said != "None", (
        "expected shutil.which to come up empty while the resolver still "
        f"found one; got which={which_said!r} resolver={resolver_said!r}")
