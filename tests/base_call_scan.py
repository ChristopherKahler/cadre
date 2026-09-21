"""Every base subprocess call in a tree, and which ones do not name their directory.

Issue #136. base finds its WORKSPACE tier by walking up from the directory the
process runs in (base 0.15.2 `config.rs:49-59`), and `BASE_HOME` moves only the
GLOBAL tier (`home.rs:25-46`). So a base call that does not name its working
directory reads whatever `.base` happens to sit above wherever the caller
stood. Measured on the operator's Windows machine 2026-09-14: the hub ran from
`C:\\Users\\Chris\\.base-gbl\\scripts`, one level under his own real global
graph, and the rule listing inside founding's install read that graph as if it
were the firm's workspace tier.

This module is the enumerator behind `test_base_calls_name_their_cwd.py`. It is
a module rather than a function inside the test so the guard and the fixture
that proves the guard can share one implementation -- two copies of a scanner
is two chances for the guard and its own proof to disagree.

WHAT IT KNOWS, AND WHAT IT DOES NOT
-----------------------------------
The shapes below were enumerated FROM this codebase, not from this scanner's
own imagination (law 31: a guard proven only against its own test cases is
blind to whatever the codebase does that the author did not think of). gadwall's
G0 for #136 read all 23 direct base calls at main `9848c444` and found six
distinct shapes; `base_call_shapes_fixture.py` carries one of each, and
`test_the_guard_names_all_six_shapes` proves this scanner fires on every one.

Two boundaries, stated rather than left to be discovered:

1. **The binary-name test is partly a name heuristic.** A call is base's when
   argv[0] is the literal `"base"`, or a name this module bound from
   `which_base()` / `shutil.which("base")`, or a PARAMETER whose name is in
   `BASE_PARAM_NAMES`. The last one is a heuristic: `graph_rules(base, domain,
   env)` receives the resolved binary as a parameter and nothing inside the
   function proves it is base. Widening it costs cross-module flow analysis and
   buys nothing this codebase needs, so the heuristic stays and says so here.
2. **The runner fixpoint is within one module.** A function becomes a runner
   when it hands one of its own parameters to a known runner as argv; that
   repeats until nothing changes. Every wrapper in this codebase
   (`provider._run`, `firm_relay._run`, `discovery._run`) is module-local, so
   within-module is enough. A wrapper imported from another module would be
   missed, and no such wrapper exists at `9848c444`.

Neither boundary can turn a dirty call clean: both can only make the scanner
MISS a call, never invent one. That asymmetry is deliberate -- an enumerator
whose failure mode is a false CLEAN would be the wrong shape entirely (law 41),
so `scan_tree` reports the size of every set it visited and a caller that reads
zero is required to treat it as blindness rather than as a clean tree.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

#: Functions whose first positional argument is a child process's argv.
SEED_RUNNERS = frozenset({
    "run_utf8", "popen_utf8", "run", "Popen",
    "check_output", "check_call", "call",
})

#: Calls that resolve base's path. `which` covers `shutil.which("base")`, which
#: is checked for its argument as well as its name.
RESOLVER_NAMES = frozenset({"which_base", "which"})

#: Parameter names this codebase passes a resolved base binary in. A heuristic,
#: and the module docstring says so.
BASE_PARAM_NAMES = frozenset({"base", "binary", "base_bin", "base_binary"})

#: Expressions that take the directory from the PROCESS rather than from the
#: caller. A `cwd=` built from one of these is not a controlled directory: it is
#: the uncontrolled one, spelled out. That is verdict amendment G-a, and it is
#: `base_extension.install`'s `cwd=str(Path.cwd())` exactly.
PROCESS_CWD_MARKERS = ("Path.cwd()", "os.getcwd()", "getcwd()", "Path().absolute()")


@dataclass(frozen=True)
class BaseCall:
    """One base subprocess call, and what it does about its working directory."""

    path: str          # repo-relative file
    line: int
    func: str          # enclosing function, for the allow-list and the report
    verb: str          # the base verb, when argv spells it literally
    shape: str         # which of the six shapes matched
    cwd_source: str    # the `cwd=` expression as written, or "" when absent
    verdict: str       # "controlled" | "no-cwd" | "process-cwd"

    @property
    def site(self) -> str:
        return f"{self.path}:{self.line}"

    def __str__(self) -> str:
        return f"{self.site} ({self.func}, base {self.verb or '?'}) -- {self.verdict}"


@dataclass
class ScanReport:
    """What a scan found AND how much it looked at.

    The counts are not decoration. A guard that reports "0 uncontrolled calls"
    over a tree it failed to parse reads identically to a clean tree (law 23,
    law 48), so every consumer of this report asserts `files_visited` and
    `calls_visited` are non-zero before it believes `uncontrolled`.
    """

    files_visited: int = 0
    calls_visited: int = 0
    base_calls: list[BaseCall] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    @property
    def uncontrolled(self) -> list[BaseCall]:
        return [c for c in self.base_calls if c.verdict != "controlled"]

    @property
    def sites(self) -> list[str]:
        return [c.site for c in self.uncontrolled]


def _attr_name(node: ast.AST) -> str:
    """The callee's name: `foo` for `foo()`, `bar` for `a.bar()`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _resolver_call(node: ast.AST,
                   local_resolvers: dict[str, int | None] | None = None
                   ) -> tuple[bool, int | None]:
    """(is it a call that resolves base, WHICH PART of its value is the path).

    The second item is `None` when the call evaluates to the path itself, and a
    tuple INDEX when it evaluates to a tuple carrying the path at that position.
    That distinction is the whole of G2 finding 3: `firm_relay._base` returns
    `(path, reason)`, so treating its value as a path would be wrong and
    treating it as "not a resolver" made a real base call invisible.

    *local_resolvers* carries the module's own resolver wrappers, found by
    `_resolver_functions`. Without it this test missed four real base calls --
    see that function.
    """
    local = local_resolvers or {}
    if not isinstance(node, ast.Call):
        return False, None
    name = _attr_name(node.func)
    if name == "which_base":
        return True, None
    if name in local:
        return True, local[name]
    if name == "which":
        # shutil.which("base") only -- shutil.which("git") is not base.
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and first.value == "base":
            return True, None
    return False, None


def _is_resolver_call(node: ast.AST,
                      local_resolvers: dict[str, int | None] | None = None) -> bool:
    """Is this expression a call that resolves base's binary path, any shape?"""
    return _resolver_call(node, local_resolvers)[0]


def _is_the_path_itself(node: ast.AST, local_resolvers: dict[str, int | None],
                        bound_here: set[str]) -> bool:
    """Does this expression evaluate to base's path, rather than to a tuple?"""
    resolves, index = _resolver_call(node, local_resolvers)
    if resolves:
        return index is None
    return isinstance(node, ast.Name) and node.id in bound_here


def _names_bound_by(fn: ast.AST, local_resolvers: dict[str, int | None]) -> set[str]:
    """Names inside *fn* that hold base's path, from either resolver shape."""
    bound: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        resolves, index = _resolver_call(node.value, local_resolvers)
        if not resolves:
            continue
        for target in node.targets:
            if index is None and isinstance(target, ast.Name):
                bound.add(target.id)
            elif (index is not None and isinstance(target, ast.Tuple)
                  and index < len(target.elts)
                  and isinstance(target.elts[index], ast.Name)):
                # ONLY the element that carries the path. `binary, absent =
                # _base()` must not bind `absent`: it holds the REASON base is
                # missing, and treating it as base would let the scanner name a
                # call that is not one.
                bound.add(target.elts[index].id)
    return bound


def _resolver_functions(tree: ast.AST) -> dict[str, int | None]:
    """A module's OWN functions that return base's path, grown until stable.

    The value is WHICH PART of the return carries the path: `None` when the
    function hands the path back whole, or the tuple index that holds it.

    THIS WAS A BLIND SPOT AND IT COST FOUR REAL CALLS. The first version of this
    scanner knew two ways to resolve base: `which_base()` and
    `shutil.which("base")`. `firm/rail/turns.py` has a third -- a module-local
    ladder `find_base()` (`:75-82`) that wraps `shutil.which("base")` and falls
    back to `~/.local/bin/base` -- and all four relay functions bind through it
    (`base = find_base()` at `:342`, `:379`, `:418`). The scanner saw none of
    them, so `test_the_allow_list_has_no_stale_entries` reported their allow-list
    entries as stale. The allow-list was right and the scanner was blind: exactly
    law 31's shape, where a guard fires perfectly on every case its author
    thought of and cannot see most of what it claims to cover.

    IT COST A FIFTH, ONE STEP FURTHER OUT (G2 finding 3). `firm_relay._base`
    (`:76-83`) is the same ladder with the path returned INSIDE A TUPLE,
    `(path, reason)`. Returning a tuple is not returning a resolver call and it
    is not returning a bound name, so the function did not count; `binary,
    absent = _base()` therefore bound nothing, and `firm_relay.py:91` -- a real
    base call -- was invisible. It passes `base_cwd` already, so nothing leaked;
    what failed was the guard's claim to see every call, and a dropped `cwd`
    there would have stayed green. avocet swept all 131 files: this is the only
    function of that shape in the tree.

    A function counts when one of its `return` statements yields the path
    directly -- a resolver call or a name bound from one -- or yields a tuple
    with the path at some index. That is tight on purpose: a function that
    merely PROBES for base without returning its path is not a resolver, and
    treating it as one would make the guard name calls that are not base's.

    WHEN THE RETURNS DISAGREE, THE FUNCTION IS NOT COUNTED. Two returns putting
    the path at different indices is a shape nobody in this tree writes, and
    guessing one would let the scanner bind a name that is not base. Missing a
    call is the failure direction this module already accepts and states; an
    invented one is not (law 41).
    """
    found: dict[str, int | None] = {}
    for _ in range(10):                       # bounded; converges in 1-2
        grew = False
        for fn in _functions(tree):
            if fn.name in found:
                continue
            bound_here = _names_bound_by(fn, found)
            indices: set[int | None] = set()
            for node in ast.walk(fn):
                if not isinstance(node, ast.Return) or node.value is None:
                    continue
                value = node.value
                if _is_the_path_itself(value, found, bound_here):
                    indices.add(None)
                elif isinstance(value, ast.Tuple):
                    for position, element in enumerate(value.elts):
                        if _is_the_path_itself(element, found, bound_here):
                            indices.add(position)
            if len(indices) == 1:
                found[fn.name] = indices.pop()
                grew = True
        if not grew:
            break
    return found


def _bound_from_resolver(tree: ast.AST) -> set[str]:
    """Names assigned from a resolver anywhere in this module.

    Walks the whole module rather than one scope: `base = which_base()` sits in
    the function that uses it, and a module-level binding is just as valid. The
    cost of ignoring scope is that a name bound in one function is treated as
    base-ish in another, which can only ADD calls to the report.

    `local` carries the module's own resolver wrappers so that
    `base = find_base()` binds too -- see `_resolver_functions`.

    WHICH NAME BINDS DEPENDS ON THE RESOLVER'S SHAPE. A resolver that hands the
    path back whole binds a plain target; one that hands back `(path, reason)`
    binds ONLY the element at the recorded index. Binding the whole unpack would
    make `absent` -- the sentence explaining that base is missing -- read as
    base, which is the opposite of a measurement.
    """
    local = _resolver_functions(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = node.value
            # `base = which_base()`, `base = find_base()`, and
            # `binary = shutil.which("base") or (...)`
            candidates = [value]
            if isinstance(value, ast.BoolOp):
                candidates = list(value.values)
            shaped = [_resolver_call(c, local) for c in candidates]
            hits = [index for resolves, index in shaped if resolves]
            if not hits:
                continue
            index = hits[0]
            for target in node.targets:
                if index is None:
                    if isinstance(target, ast.Name):
                        found.add(target.id)
                elif (isinstance(target, ast.Tuple) and index < len(target.elts)
                      and isinstance(target.elts[index], ast.Name)):
                    found.add(target.elts[index].id)
        elif isinstance(node, ast.AnnAssign):
            resolves, index = _resolver_call(node.value, local) if node.value is not None \
                else (False, None)
            if resolves and index is None and isinstance(node.target, ast.Name):
                found.add(node.target.id)
    return found


def _functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    a = fn.args
    names = [p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    if a.vararg:
        names.append(a.vararg.arg)
    return names


def _argv_of(call: ast.Call, runners: dict[str, int]) -> ast.AST | None:
    """The expression a runner call passes as argv, or None when it is not one."""
    name = _attr_name(call.func)
    if name not in runners:
        return None
    index = runners[name]
    if len(call.args) > index:
        return call.args[index]
    for kw in call.keywords:
        if kw.arg in ("argv", "cmd", "args"):
            return kw.value
    return None


def _runner_fixpoint(tree: ast.AST) -> dict[str, int]:
    """Runner name -> the positional index of its argv, grown until stable.

    A function joins the set when it passes one of its OWN parameters to a known
    runner as argv. That is how `_run(argv)` two hops from `run_utf8` is reached
    -- the shape gadwall's first #136 scan missed and its cross-check caught.
    """
    runners: dict[str, int] = {n: 0 for n in SEED_RUNNERS}
    for _ in range(10):                      # bounded; this converges in 2-3
        grew = False
        for fn in _functions(tree):
            if fn.name in runners:
                continue
            names = _params(fn)
            for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
                argv = _argv_of(call, runners)
                if isinstance(argv, ast.Name) and argv.id in names:
                    runners[fn.name] = names.index(argv.id)
                    grew = True
                    break
        if not grew:
            break
    return runners


def _base_shape(argv: ast.AST, base_names: set[str],
                param_names: set[str]) -> tuple[str, str] | None:
    """(shape, verb) when argv[0] is base, else None."""
    if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
        return None
    head = argv.elts[0]
    verb = ""
    for element in argv.elts[1:]:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            verb = element.value
            break
    if isinstance(head, ast.Constant) and head.value == "base":
        return "bare-string", verb
    if isinstance(head, ast.Name):
        if head.id in base_names:
            return "resolver-bound-name", verb
        if head.id in param_names and head.id in BASE_PARAM_NAMES:
            return f"parameter-named-{head.id}", verb
    return None


def _cwd_verdict(call: ast.Call) -> tuple[str, str]:
    """(cwd_source, verdict) for one runner call."""
    for kw in call.keywords:
        if kw.arg == "cwd":
            source = ast.unparse(kw.value)
            if any(marker in source for marker in PROCESS_CWD_MARKERS):
                return source, "process-cwd"
            return source, "controlled"
    return "", "no-cwd"


def scan_source(source: str, rel_path: str) -> tuple[list[BaseCall], int]:
    """Every base call in one module's source, plus the runner calls visited."""
    tree = ast.parse(source)
    runners = _runner_fixpoint(tree)
    base_names = _bound_from_resolver(tree)
    calls: list[BaseCall] = []
    visited = 0

    owner: dict[int, str] = {}
    for fn in _functions(tree):
        for node in ast.walk(fn):
            owner.setdefault(id(node), fn.name)
    param_names_of: dict[str, set[str]] = {
        fn.name: set(_params(fn)) for fn in _functions(tree)}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        argv = _argv_of(node, runners)
        if argv is None:
            continue
        visited += 1
        func = owner.get(id(node), "<module>")
        shaped = _base_shape(argv, base_names, param_names_of.get(func, set()))
        if shaped is None:
            continue
        shape, verb = shaped
        source_text, verdict = _cwd_verdict(node)
        calls.append(BaseCall(path=rel_path, line=node.lineno, func=func,
                              verb=verb, shape=shape, cwd_source=source_text,
                              verdict=verdict))
    return calls, visited


def scan_tree(root: Path) -> ScanReport:
    """Scan every `.py` under *root*. Reports what it visited, always."""
    report = ScanReport()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root.parent).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:        # pragma: no cover
            report.unparsed.append(f"{rel}: {exc}")
            continue
        try:
            calls, visited = scan_source(source, rel)
        except SyntaxError as exc:                          # pragma: no cover
            report.unparsed.append(f"{rel}: {exc}")
            continue
        report.files_visited += 1
        report.calls_visited += visited
        report.base_calls.extend(calls)
    return report
