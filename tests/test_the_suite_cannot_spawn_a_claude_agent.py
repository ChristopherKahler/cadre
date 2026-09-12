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
#
# MATCHED AS CALLS, NOT AS TEXT (issue #94). Version one was
#
#     LOCATE_CALLS = ("shutil.which", "os.access", "os.path.isfile",
#                     "exists", "glob")
#     def _locates(seg): return any(call in seg for call in LOCATE_CALLS)
#
# -- a substring search over source text, sitting ten lines below
# `_mentions_claude`, which parses the code and inspects it. One file, two
# predicates answering the same shape of question, and only one of them written
# carefully. That asymmetry was the defect; the missing call shapes were its
# symptoms.
#
# It was wrong in BOTH directions, measured over the 833 functions under
# src/firm at 15eae05d:
#
#   TOO LOOSE   "exists" appears in 142 functions, 66 of which make no
#               existence call at all -- already_exists, workspace_exists.
#               "glob" appears in 42, 29 of which make none -- globals(),
#               glob_pattern. "shutil.which" in 16, 2 of which make none.
#   TOO NARROW  43 functions make a real locating call the text search cannot
#               see, because the receiver's name is different every time:
#               subprocess.run (15), `.is_file()` (10), subprocess.Popen (9),
#               `.is_dir()` (7), `.iterdir()` (5), `.stat()` (4),
#               os.listdir (3), os.walk (1).
#
# The looseness harmed nothing only because it was ANDed with the exact
# claude-name check, which almost nothing passed. That is a condition nobody
# wrote down, and widening ENTRY below removes it -- so the looseness had to go
# first, in the same change.

# Calls matched on the whole dotted chain.
LOCATE_DOTTED = frozenset({
    # is it there, and what kind of thing is it
    "os.path.isfile", "os.path.exists", "os.path.isdir", "os.path.islink",
    "os.stat", "os.lstat",
    # can it be run
    "os.access", "shutil.which", "distutils.spawn.find_executable",
    # list a folder
    "os.listdir", "os.scandir", "os.walk",
    # search a folder by name
    "glob.glob", "glob.iglob",
    # run the thing and see whether it answers
    "subprocess.run", "subprocess.Popen", "subprocess.check_output",
    "subprocess.call", "subprocess.check_call",
})

# Calls matched on the ATTRIBUTE ALONE, whatever the receiver is. This is the
# half a text search cannot do: `cand.exists()`, `Path(p).is_file()` and
# `home.rglob(...)` are the same act as `os.path.exists(cand)`, and the name of
# the object is different at every call site. The same set is accepted as a bare
# call, for `from shutil import which`.
#
# ENUMERATED, and three obvious members are deliberately absent. "walk" would
# match `ast.walk`, which this very file calls. "run" and "call" would match
# `self.run()` and `handler.call()`. Those three are reachable only through
# their dotted forms above. A receiver-blind match is safe only for a name that
# means one thing, which is why this is a list and not a pattern -- the same
# reasoning `_mentions_claude` records for EXEC_NAMES.
LOCATE_ATTRS = frozenset({
    "which", "find_executable", "access",
    "isfile", "isdir", "islink", "exists",
    "is_file", "is_dir", "is_symlink",
    "listdir", "scandir", "iterdir",
    "glob", "iglob", "rglob",
    "stat", "lstat",
    "Popen", "check_output", "check_call",
})

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


def _parse_func(seg: str):
    """Parse one function's source, which arrives indented at its own level.

    Wrapped in `if 1:` so a method body parses as readily as a module-level
    function. Shared by every predicate below so they cannot drift apart -- the
    string is character-identical to the one `_mentions_claude` carried before
    issue #94, and that function's two red arms pin its behaviour either way.
    """
    try:
        return ast.parse("if 1:\n" + "\n".join("    " + l for l in seg.splitlines()))
    except SyntaxError:                          # pragma: no cover
        return None


def _dotted(node: ast.AST) -> str | None:
    """"os.path.isfile" for an attribute chain rooted in a bare name, else None.

    None for `Path(p).is_file` and `self.x.exists`, whose roots are not names.
    Those are exactly the shapes `LOCATE_ATTRS` catches on the attribute alone.
    """
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


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
    return _names_claude(_parse_func(seg))


def _names_claude(tree: ast.AST | None) -> bool:
    """`_mentions_claude` over an already-parsed body. Same test, one parse."""
    if tree is None:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if n.value.lower() in EXEC_NAMES:
                return True
    return False


def _names_claude_path(tree: ast.AST | None) -> bool:
    """A string constant whose LAST path segment is an exact exec name.

    ENTRY, widened a second way (issue #94). `_mentions_claude` matches the
    constant "claude" exactly, which is right for what it guards and is left
    untouched here -- its two red arms record the two near-misses that
    exactness already survived. But a resolver does not have to spell the bare
    name:

        def find_claude():
            p = "/usr/local/bin/claude"
            return p if os.path.isfile(p) else None

    That locates a claude binary, ignores the escape hatch, and the sweep could
    not see it, because "/usr/local/bin/claude" is not the string "claude". Five
    of the red arms below were written that way before this existed and all five
    walked straight through the guard.

    Matched on the FINAL segment only, after normalising Windows separators, so
    the document paths the product really writes stay quiet: "CLAUDE.md",
    "~/.claude/settings.json" and
    "claude/cadre-framework/frameworks/org-design.md" all end in something else.

    A TRAILING SLASH MEANS A DIRECTORY and must not match. "docs/claude/" is a
    folder of documents, not a binary; stripping the slash first turned it into
    a hit, which is how this line was found. Splitting without stripping leaves
    an empty final segment, and empty is not an exec name.

    Measured on the tree at 15eae05d: exactly ZERO functions under src/firm name
    the binary only this way, so this widening is free here. It exists for the
    resolver somebody writes next month, which is the whole point of a sweep.
    """
    if tree is None:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            v = n.value.lower().replace("\\", "/")
            if "/" in v and v.rsplit("/", 1)[-1] in EXEC_NAMES:
                return True
    return False


def _locate_calls(tree: ast.AST | None) -> set[str]:
    """Every locating call this function actually MAKES, named.

    A set rather than a bool so a red arm can assert WHICH shape it proved. An
    arm that checks only `_locates(...) is True` passes for the wrong reason as
    easily as the right one, and an arm set that proves one shape and assumes
    the rest reads exactly as green as one that covers them.
    """
    if tree is None:
        return set()
    found: set[str] = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        dotted = _dotted(n.func)
        if dotted is not None and dotted in LOCATE_DOTTED:
            found.add(dotted)
        elif isinstance(n.func, ast.Attribute) and n.func.attr in LOCATE_ATTRS:
            found.add("." + n.func.attr + "()")
        elif isinstance(n.func, ast.Name) and n.func.id in LOCATE_ATTRS:
            found.add(n.func.id + "()")
    return found


def _locates(seg: str) -> bool:
    """This function asks the filesystem about a path, or runs one."""
    return bool(_locate_calls(_parse_func(seg)))


def _calls_made(tree: ast.AST | None) -> set[str]:
    """Bare names of everything this function calls: `f()` and `x.f()` alike."""
    if tree is None:
        return set()
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name):
                out.add(n.func.id)
            elif isinstance(n.func, ast.Attribute):
                out.add(n.func.attr)
    return out


def _name_sources(facts) -> set[str]:
    """Functions that supply a claude BINARY NAME and never go looking for one.

    THE CIRCULARITY THIS BREAKS (issue #94). Entry used to be
    `(_mentions_claude(seg) or ENV_VAR in seg) and _locates(seg)`. Measured on
    the tree at 15eae05d, exactly THREE functions under src/firm contain an
    exact claude binary name: `launch._which_claude`,
    `spawn._candidate_names` and `turns.find_claude`.

    `resolve_claude_bin` contains NONE. It is the primary resolver -- the one
    the Member runtime actually spawns through -- and it builds its candidates
    by calling `_candidate_names()`, so no literal "claude" appears in its own
    body. It entered the sweep ONLY through the `ENV_VAR in seg` arm: it was
    recognised as a resolver BECAUSE IT ALREADY CONSULTED THE ESCAPE HATCH.

    That made the env-var arm contribute nothing to the test it feeds.
    `test_every_claude_resolver_consults_the_escape_hatch` could only ever fail
    for a function the claude-NAME arm had caught, so a resolver that neither
    spelled `claude` literally nor mentioned the variable was invisible to the
    sweep -- precisely the resolver this fence exists to catch. The number:
    strip the env arm off the old entry condition and the sweep falls from four
    functions to two, without `resolve_claude_bin` among them.

    WHY THE CHAIN STOPS AT A LOCATOR, and this is the whole subtlety. The
    obvious fix is a transitive closure -- sweep anything that reaches a
    claude-namer through any call at all. Measured, that sweeps 13 functions and
    leaves NINE of them failing the invariant: `__main__.main`, `run_dashboard`,
    `run_pulse`, `_run_wiring`, `set_manifest` and friends, none of which touch
    a filesystem. Bolting CADRE_CLAUDE_BIN onto a CLI entry point is meaningless
    and the next person would loosen the sweep until it guarded nothing, which
    is the exact failure `_mentions_claude` already records surviving twice.

    So naming propagates only through functions that DO NOT locate. A function
    that takes a name AND asks the filesystem about it has turned a NAME into a
    PATH: it IS the resolver, and everything above it consumes one. On the real
    tree this set is exactly {"_candidate_names"}.

    Keyed by bare function name rather than by (file, name): two modules that
    both define a name source would merge, which sweeps MORE functions, and
    widening entry is the safe direction. A miss here hides a resolver; a
    false hit only asks one more function to consult the hatch.
    """
    sources = {name for _, name, _, tree in facts
               if _names_claude(tree) and not _locate_calls(tree)}
    while True:
        grew = {name for _, name, _, tree in facts
                if name not in sources
                and not _locate_calls(tree)
                and _calls_made(tree) & sources}
        if not grew:
            return sources
        sources |= grew


def _facts():
    """(relpath, name, source, parsed) for every function under src/firm.

    One parse per function, reused by every predicate, because `_resolvers` is
    called from a dozen arms and each of those used to re-parse the tree three
    times over.
    """
    out = []
    for path, node, seg in _funcs():
        if not seg:
            continue
        out.append((path.relative_to(REPO), node.name, seg, _parse_func(seg)))
    return out


def _resolvers(*, env_arm: bool = True):
    """Every function under src/firm that LOCATES a claude binary.

    ENTRY has three arms, and `env_arm=False` turns off the one that used to
    carry the primary resolver on its own. Production reads never pass it; the
    circularity arm below does, so the claim this sweep makes about itself is
    tested rather than asserted in a comment.
    """
    facts = _facts()
    sources = _name_sources(facts)
    out = []
    for rel, name, seg, tree in facts:
        seeks = (_names_claude(tree)
                 or _names_claude_path(tree)
                 or (env_arm and ENV_VAR in seg)
                 or bool(_calls_made(tree) & sources))
        if seeks and _locate_calls(tree):
            out.append((rel, name, seg))
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
# ------------------------------------------- issue #94: the widened predicate
#
# A RED ARM PER SHAPE, NOT ONE ARM FOR THE CLASS. Every entry below is planted
# in the real src/firm tree as its own file, swept, and removed. An arm set that
# omits the shape under test reads exactly as green as one that covers it, so
# the shapes are enumerated here and the parametrisation makes each one its own
# test id -- deleting a shape deletes a visible test rather than shrinking a
# loop nobody counts.
#
# Each body names the claude binary and never mentions CADRE_CLAUDE_BIN, so a
# sweep that sees it must report it DEAF. `call` is the name `_locate_calls`
# should return, asserted so an arm cannot pass because some OTHER call in the
# body happened to match.

LOCATE_SHAPES = {
    "shutil_which": (
        "shutil.which",
        "import shutil\n\n\n"
        "def red_arm():\n"
        "    return shutil.which('claude')\n"),
    "bare_which": (
        "which()",
        "from shutil import which\n\n\n"
        "def red_arm():\n"
        "    return which('claude')\n"),
    "os_path_isfile": (
        "os.path.isfile",
        "import os\n\n\n"
        "def red_arm():\n"
        "    p = os.path.join('/usr/bin', 'claude')\n"
        "    return p if os.path.isfile(p) else None\n"),
    "os_path_exists": (
        "os.path.exists",
        "import os\n\n\n"
        "def red_arm():\n"
        "    p = os.path.join('/usr/bin', 'claude')\n"
        "    return p if os.path.exists(p) else None\n"),
    "os_access": (
        "os.access",
        "import os\n\n\n"
        "def red_arm():\n"
        "    p = os.path.join('/usr/bin', 'claude')\n"
        "    return p if os.access(p, os.X_OK) else None\n"),
    "os_stat": (
        "os.stat",
        "import os\n\n\n"
        "def red_arm():\n"
        "    p = os.path.join('/usr/bin', 'claude')\n"
        "    return os.stat(p).st_mode\n"),
    "os_listdir": (
        "os.listdir",
        "import os\n\n\n"
        "def red_arm():\n"
        "    return ['claude' for f in os.listdir('/usr/bin') if f == 'claude']\n"),
    "os_scandir": (
        "os.scandir",
        "import os\n\n\n"
        "def red_arm():\n"
        "    return [e for e in os.scandir('/usr/bin') if e.name == 'claude']\n"),
    "os_walk": (
        "os.walk",
        "import os\n\n\n"
        "def red_arm():\n"
        "    for _, _, files in os.walk('/usr/bin'):\n"
        "        if 'claude' in files:\n"
        "            return True\n"
        "    return False\n"),
    "glob_glob": (
        "glob.glob",
        "import glob\n"
        "import os\n\n\n"
        "def red_arm():\n"
        "    return glob.glob(os.path.join('/usr/bin', 'claude'))\n"),
    "find_executable": (
        "distutils.spawn.find_executable",
        "import distutils.spawn\n\n\n"
        "def red_arm():\n"
        "    return distutils.spawn.find_executable('claude')\n"),
    "method_is_file": (
        ".is_file()",
        "from pathlib import Path\n\n\n"
        "def red_arm():\n"
        "    p = Path('/usr/bin') / 'claude'\n"
        "    return p if Path(str(p)).is_file() else None\n"),
    "method_exists": (
        ".exists()",
        "from pathlib import Path\n\n\n"
        "def red_arm():\n"
        "    cand = Path('/usr/bin') / 'claude'\n"
        "    return cand if cand.exists() else None\n"),
    "method_is_dir": (
        ".is_dir()",
        "from pathlib import Path\n\n\n"
        "def red_arm():\n"
        "    home = Path('/opt') / 'claude'\n"
        "    return home if home.is_dir() else None\n"),
    "method_iterdir": (
        ".iterdir()",
        "from pathlib import Path\n\n\n"
        "def red_arm():\n"
        "    return [f for f in Path('/usr/bin').iterdir() if f.name == 'claude']\n"),
    "method_glob": (
        ".glob()",
        "from pathlib import Path\n\n\n"
        "def red_arm():\n"
        "    return list(Path('/usr/bin').glob('claude'))\n"),
    "method_rglob": (
        ".rglob()",
        "from pathlib import Path\n\n\n"
        "def red_arm():\n"
        "    return list(Path('/opt').rglob('claude'))\n"),
    "method_stat": (
        ".stat()",
        "from pathlib import Path\n\n\n"
        "def red_arm():\n"
        "    cand = Path('/usr/bin') / 'claude'\n"
        "    return cand.stat().st_mode\n"),
    "subprocess_run": (
        "subprocess.run",
        "import subprocess\n\n\n"
        "def red_arm():\n"
        "    out = subprocess.run(['claude', '--version'], capture_output=True)\n"
        "    return out.returncode == 0\n"),
    "subprocess_popen": (
        "subprocess.Popen",
        "import subprocess\n\n\n"
        "def red_arm():\n"
        "    return subprocess.Popen(['claude', '--version'])\n"),
    "subprocess_check_output": (
        "subprocess.check_output",
        "import subprocess\n\n\n"
        "def red_arm():\n"
        "    return subprocess.check_output(['claude', '--version'])\n"),
}


def _plant(stem: str, body: str):
    """Write one arm into the real src/firm tree; caller must unlink it."""
    planted = SRC / f"_issue94_{stem}.py"
    assert not planted.exists(), (
        f"{planted} already exists; refusing to overwrite a real file")
    planted.write_text(body, encoding="utf-8")
    assert planted.exists(), "the plant did not reach disk"
    return planted


@pytest.mark.parametrize("stem", sorted(LOCATE_SHAPES))
def test_a_resolver_of_each_locating_shape_is_caught(stem):
    """RED ARM PER SHAPE, in the real tree. Each one alone was a hole.

    Eight of these shapes were invisible to the substring predicate this
    replaced, measured across 43 functions under src/firm that make a real
    locating call the text search could not see. One arm covering the class
    would prove one shape and assume the rest.
    """
    call, body = LOCATE_SHAPES[stem]
    assert call in _locate_calls(_parse_func(body)), (
        f"the {stem} arm does not make the call it claims ({call}); it would "
        "pass for the wrong reason")
    assert _mentions_claude(body), (
        f"the {stem} arm must name the binary the plain way, so that what it "
        "proves is the LOCATING shape and nothing else")
    planted = _plant(stem, body)
    try:
        deaf = [(f, n) for f, n, seg in _resolvers() if ENV_VAR not in seg]
        assert any(n == "red_arm" for _, n in deaf), (
            f"a resolver that locates claude with {call} and never consults "
            f"{ENV_VAR} was planted in the real src/ tree and the sweep did "
            f"not report it (it reported {deaf})")
    finally:
        planted.unlink(missing_ok=True)
    # ONE sweep per arm, deliberately. Each costs a 1.2s parse of all 833
    # functions under src/firm, and proving the tree was RESTORED is not a
    # shape-specific property -- the plant arm above proves it once, with a real
    # before-and-after comparison. What each arm still owes the next one is an
    # unpolluted tree, and a directory listing answers that without re-reading
    # the product.
    assert not planted.exists(), f"the arm left {planted} behind"
    leaked = sorted(p.name for p in SRC.glob("_issue94_*.py"))
    assert not leaked, (
        f"an arm left {leaked} in src/firm, so every later arm is running "
        "against a polluted tree and a green result there means nothing")


# The resolver that spells nothing. No claude literal, no env var: it gets its
# candidate names from the real `spawn._candidate_names`, exactly the way
# `resolve_claude_bin` does. This is THE hole the entry widening exists to close,
# and it is invisible to the predicate this replaced.
ENTRY_ARM = (
    "import os\n\n"
    "from firm.pulse.spawn import _candidate_names\n\n\n"
    "def red_arm():\n"
    "    for name in _candidate_names():\n"
    "        cand = os.path.join('/usr/bin', name)\n"
    "        if os.path.isfile(cand):\n"
    "            return cand\n"
    "    return None\n")


def test_a_resolver_that_spells_nothing_is_caught():
    """THE ENTRY ARM. It names no binary and mentions no variable.

    The old entry condition was `(_mentions_claude or ENV_VAR in seg) and
    _locates`. This function satisfies NEITHER disjunct, so the old sweep was
    blind to it while it resolved a claude binary and ignored the escape hatch
    -- the precise resolver the fence exists to catch. It enters now because it
    takes its names from a NAME SOURCE.
    """
    old_entry = (_mentions_claude(ENTRY_ARM) or ENV_VAR in ENTRY_ARM)
    assert not old_entry, (
        "this arm is supposed to spell neither the binary nor the variable; if "
        "it does, it proves the claude-name arm and not the entry widening")
    before = {n for _, n, _ in _resolvers()}
    planted = _plant("entry_no_literal", ENTRY_ARM)
    try:
        deaf = [(f, n) for f, n, seg in _resolvers() if ENV_VAR not in seg]
        assert any(n == "red_arm" for _, n in deaf), (
            "a resolver that locates a claude binary through a name source, "
            f"spelling neither 'claude' nor {ENV_VAR}, was planted in the real "
            f"src/ tree and the sweep did not report it (it reported {deaf})")
    finally:
        planted.unlink(missing_ok=True)
    assert not planted.exists(), f"the arm left {planted} behind"
    assert {n for _, n, _ in _resolvers()} == before, "the arm did not restore the tree"


def test_the_sweep_does_not_depend_on_the_env_var_arm():
    """THE CIRCULARITY, ASSERTED AS A NUMBER.

    `resolve_claude_bin` used to enter the sweep only because it consulted
    CADRE_CLAUDE_BIN -- it was called a resolver BECAUSE it already obeyed the
    rule the sweep then checked it against. So the invariant could never fail
    for it, and the env-var arm contributed nothing to the test it fed.

    Turning that arm off must leave the primary resolver in the sweep. If this
    ever fails, entry has gone circular again and the guard's coverage is an
    illusion, whatever its green count says.
    """
    without = {n for _, n, _ in _resolvers(env_arm=False)}
    assert "resolve_claude_bin" in without, (
        "with the env-var arm disabled the sweep no longer finds "
        "resolve_claude_bin, so it is recognised as a resolver only because it "
        f"already consults {ENV_VAR}. The invariant cannot fail for it and the "
        f"sweep found only {sorted(without)}")


def test_the_name_source_that_carries_the_primary_resolver_is_the_one_named():
    """Pin the MECHANISM, not just the outcome above.

    `spawn._candidate_names` is the only function under src/firm that names the
    claude binary and never looks for one. It is what lets `resolve_claude_bin`
    enter the sweep without spelling "claude", and it must itself stay OUT of
    the sweep -- `test_a_name_builder_is_not_mistaken_for_a_resolver` pins that
    from the other side.
    """
    sources = _name_sources(_facts())
    assert "_candidate_names" in sources, (
        "spawn._candidate_names is no longer recognised as a name source, so "
        "resolve_claude_bin can only enter the sweep through the env-var arm "
        f"again (sources: {sorted(sources)})")
    for locator in KNOWN_RESOLVERS:
        assert locator not in sources, (
            f"{locator} both locates and is treated as a name source, so "
            "naming propagates through a resolver and every caller of it gets "
            "swept -- measured, that is 13 functions and 9 failures")


@pytest.mark.parametrize("innocent", [
    "def f():\n    return already_exists(thing)\n",
    "def f():\n    return workspace_exists\n",
    "def f():\n    return globals()['x']\n",
    "def f():\n    glob_pattern = '*.md'\n    return glob_pattern\n",
    "def f():\n    return 'the file exists'\n",
    "def f():\n    self.exists = True\n    return self.exists\n",
])
def test_locating_does_not_fire_on_text_that_merely_looks_like_it(innocent):
    """THE LOOSENESS, pinned. Every one of these matched the old predicate.

    Measured on the tree: "exists" as a substring appeared in 142 functions, 66
    of which made no existence call; "glob" in 42, 29 of which made none. That
    was harmless only while it was ANDed with a claude-name check almost nothing
    passed. Entry is wider now, so the looseness is no longer free.
    """
    assert not _locates(innocent), (
        f"{innocent!r} reads as a filesystem probe to this sweep, so the guard "
        "will fire on correct code and be weakened until it stops guarding")


@pytest.mark.parametrize("excluded", ["walk", "run", "call"])
def test_the_receiver_blind_names_exclude_the_three_that_collide(excluded):
    """Why LOCATE_ATTRS is a list and not every name in LOCATE_DOTTED.

    `ast.walk` is called by this file; `self.run()` and `handler.call()` are
    ordinary. Matching those on the attribute alone would drag in unrelated
    code, which is the same over-match that made the substring predicate
    useless. They stay reachable through their dotted forms.
    """
    assert excluded not in LOCATE_ATTRS, (
        f"{excluded!r} matches on any receiver and collides with ordinary "
        "code; it belongs in LOCATE_DOTTED only")
    assert any(d.endswith("." + excluded) for d in LOCATE_DOTTED), (
        f"{excluded!r} is excluded from LOCATE_ATTRS and has no dotted form "
        "either, so that shape is not covered at all")


def test_widening_the_predicate_forced_no_product_change():
    """The sweep on a clean tree is exactly the resolvers, and none are deaf.

    Stated as its own arm because it is the claim that makes this change safe
    to merge: both widenings are free here. If a later commit makes this fail,
    the sweep has found a real resolver that ignores the hatch, and the fix is
    in src/, not in this file.
    """
    found = {n for _, n, _ in _resolvers()}
    assert KNOWN_RESOLVERS <= found, (
        f"the sweep lost {sorted(KNOWN_RESOLVERS - found)}")
    deaf = [(f, n) for f, n, seg in _resolvers() if ENV_VAR not in seg]
    assert not deaf, f"the widened sweep reports {deaf} as deaf"
# The resolver that hardcodes a full path. It never spells the bare name, so
# `_mentions_claude` is blind to it by design, and it never mentions the
# variable. Before `_names_claude_path` the sweep could not see it at all.
PATH_ARM = (
    "import os\n\n\n"
    "def red_arm():\n"
    "    p = '/usr/local/bin/claude'\n"
    "    return p if os.path.isfile(p) else None\n")


def test_a_resolver_that_names_the_binary_only_as_a_path_is_caught():
    """RED ARM for the second entry hole, planted in the real tree.

    Five of the locating arms above were written this way first and every one
    of them walked through the guard, which is how this hole was found -- the
    same way issue 94 itself was found, by running a guard's own stated
    invariant against shapes it had never been shown.
    """
    assert not _mentions_claude(PATH_ARM), (
        "this arm is supposed to name the binary ONLY as a path; if the bare "
        "name predicate already sees it, it proves nothing about the path one")
    assert ENV_VAR not in PATH_ARM, "the arm must not consult the hatch"
    before = {n for _, n, _ in _resolvers()}
    planted = _plant("path_literal", PATH_ARM)
    try:
        deaf = [(f, n) for f, n, seg in _resolvers() if ENV_VAR not in seg]
        assert any(n == "red_arm" for _, n in deaf), (
            "a resolver that locates a claude binary by its full path, "
            f"spelling neither the bare name nor {ENV_VAR}, was planted in the "
            f"real src/ tree and the sweep did not report it (reported {deaf})")
    finally:
        planted.unlink(missing_ok=True)
    assert not planted.exists(), f"the arm left {planted} behind"
    assert {n for _, n, _ in _resolvers()} == before, "the arm did not restore the tree"


@pytest.mark.parametrize("path_name", [
    "/usr/bin/claude",
    "/usr/local/bin/claude",
    "~/.local/bin/claude",
    "C:\\Program Files\\claude.exe",
    "bin/claude.CMD",
    "/opt/node/bin/claude.cmd",
])
def test_the_path_predicate_fires_on_every_real_path_to_the_binary(path_name):
    """The control. Windows separators and PATHEXT suffixes included."""
    seg = f"def f():\n    return {path_name!r}\n"
    assert _names_claude_path(_parse_func(seg)), (
        f"{path_name!r} is a real path to the claude executable and the sweep "
        "is blind to it, so a resolver using it would never be checked")


@pytest.mark.parametrize("innocent", [
    "CLAUDE.md",                                           # a document
    "claude/cadre-framework/frameworks/org-design.md",     # a doc path
    "~/.claude/settings.json",                             # a config file
    "docs/claude/",                                        # a FOLDER, trailing /
    ".claude",                                             # a dotdir, no sep
    "claude_bin",                                          # a dict key
    "runs/claude_code/latest.json",                        # a run directory
])
def test_the_path_predicate_does_not_fire_on_paths_that_merely_say_claude(innocent):
    """The other direction, and "docs/claude/" is the one that caught me.

    The first version stripped a trailing slash before splitting, which turned a
    FOLDER of documents into a hit on the binary. A guard that fires on correct
    code gets edited until it guards nothing -- this file has already recorded
    that happening twice to `_mentions_claude`, and this is the third time the
    same lesson had to be paid for.
    """
    seg = f"def f():\n    return {innocent!r}\n"
    assert not _names_claude_path(_parse_func(seg)), (
        f"{innocent!r} reads as the claude BINARY to this sweep, so the guard "
        "fires on correct code and will be weakened until it stops guarding")
