"""No fixture may stub `which_base` at a bare string literal.

THE DEFECT. A test that replaces `which_base` with `lambda: "/fake/base"` is
proving the code works against something no host could execute. `/fake/base` is
not a file. Nothing opens it, nothing identifies it, nothing runs it.

WHY THAT IS NOT TEST HYGIENE. It makes a refusal test unable to tell REFUSED
CORRECTLY from NEVER REACHED THE CHECK. Both come back the same colour. That is
what hid issue #87: `base_domain.scaffold_tier` had no refusal at all, and no
test could see the gap, because every fixture on that path was handing it
something a check would have rejected anyway. A real operator running
`cadre init` on WSL got their tier written. The stubs are why the gap was
invisible, not a detail beside it.

It has now bitten twice in one day -- issue #75 on `base_extension.install`,
issue #87 on `base_domain.scaffold_tier` -- and both times the fix broke
neighbour tests that had been passing on string stubs. Loosening a guard to
admit an unreadable binary would be fail-open, so the stubs become real files
instead. `_make_stub_base()` in tests/services/test_writeback.py is the
reference shape: it writes THIS host's magic bytes (PE, Mach-O, or ELF) to a
real file and chmods it, so it asserts the outcome rather than one platform's
mechanism.

HOW THE CHECK DECIDES, and this part took three attempts. Deciding whether a
pattern is PRESENT means telling code apart from writing ABOUT code, and the two
obvious tests are both wrong in opposite directions:

  - "the line starts with a quote or a hash" misses every literal inside a
    docstring, so it reports prose as work. It flagged a sentence that had just
    been written to explain the shape.
  - "any STRING token touches the line" catches docstrings, and it also catches
    every real stub, because a stub's path IS a string token. Measured on a
    two-line file: `monkeypatch.setattr(m, "which_base", lambda: "/fake/base")`
    came back prose.

So this asks where the OCCURRENCE SITS. Inside a COMMENT token, or inside a
STRING token that forms a whole logical line by itself (a docstring or a
free-floating string), it is prose. A literal being passed as a value is code.
`tokenize` gives exact line and column ranges for both, and the columns matter:
one line can carry a real stub and a comment about stubs, and those are two
different answers on the same row.

NOTHING IS FINE BY DEFAULT, INSIDE THE CALL SHAPES classify_stubs LISTS.
A stub whose VALUE this guard cannot pin down fails as UNKNOWN rather than
passing as acceptable, because a guard that shrugs at what it cannot parse
is a guard with a silent hole. A name is resolved the way Python resolves
it, innermost scope first, so a function-local constant is caught as
WEAK_CONST instead of waved through as an expression, and a name bound both
to a literal and to something else is UNKNOWN, because which value arrives
is not decidable from the text.

THAT SENTENCE IS SCOPED ON PURPOSE. It used to be broader, and broader made
it false: it claimed every unrecognised value shape failed as UNKNOWN, while
a function-local string constant -- a value shape, inside a recognised call
-- came back OK_EXPR. Measured by avocet as arm E: 143 passed, unchanged,
not caught, because the constant collector walked module level only.

ONE shape this guard does not SEE at all, as opposed to classifying
wrongly: a plain attribute assignment (`m.which_base = lambda: ...`),
which is not an ast.Call and so yields no verdict rather than UNKNOWN.
An imported name was listed here too and that was WRONG -- it is seen,
and it now lands as UNKNOWN because nothing in the file binds it.

THE BIGGEST REMAINING HOLE IS AN INDIRECTION, named here rather than
left to look covered. A helper that takes the path as a PARAMETER and
passes it on --

    def _point_which_base_at(monkeypatch, path):
        monkeypatch.setattr("...which_base", lambda: path)

-- is correctly OK_EXPR, because `path` arrives at call time. But a
caller one hop away writing _point_which_base_at(monkeypatch,
"/usr/bin/base") passes a bare string this guard never sees, and every
foreign-base test in this tree routes through two such helpers. That is
the highest-leverage blind spot in the file.

EVERY OTHER SHAPE THAT STAYS ACCEPTABLE, NAMED. An unnamed blessing is
what made block 3, so these are listed rather than left to be discovered:

  - An iterable this guard cannot see into -- a call, a name, a
    generator -- binds its loop variable at run time and is OK_EXPR.
    That is a MISS, not a blessing: the text genuinely does not show the
    value. `for p in ["/usr/bin/base"]` IS caught, because it does.
  - A dict METHOD call is one of those. `for label, path in
    {"foreign": "/usr/bin/base"}.items()` reads as a call, so the bare
    path is not seen. Iterating the dict DIRECTLY is caught, because
    that hands out keys this guard can read.
  - A name imported from another module is UNKNOWN, not acceptable, and
    that is deliberate: its value cannot be checked here.

All of these and the plain attribute assignment are issue #93's door,
and #93's own note holds: widen the recognised SURFACE, never widen what
counts as an acceptable value.

KNOWN LIMIT, stated here rather than discovered later. This keys on the NAME
`which_base`. A fixture that patches `shutil.which` to hand back a stand-in base
path is the identical fail-open shape and is structurally invisible to this
guard. Filed as issue #93, which names the two live sites:
tests/test_equip.py:96 and tests/test_secrets.py:110.
Widening the name list is the fix when those land, not widening what counts as
an acceptable value.
"""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"
TARGET = "which_base"

# THIS FILE IS NOT SWEPT, and a test below asserts that rather than trusting it.
# Its red arms contain bare-string stubs on purpose, because those strings are
# the thing they exist to prove the guard catches. Sweep this file and the guard
# fails on its own arms, and a guard that fails on correct code gets edited
# until it stops guarding -- exactly the warning tests/test_no_hardcoded_roots.py
# records about its own thirteen lines.
SELF = Path(__file__).resolve()

# The ONE deliberate exception, named with its reason and re-checked below.
# tests/cli/test_doctor_unreadable_base.py binds STUB_BASE to a bare string and
# stubs which_base with it. That is safe ONLY because the same module replaces
# `subprocess.run` for that argv, so the path is never opened and never
# executed; the string only has to be recognisable. An unexplained exemption is
# worse than an over-matching guard, because the exemption is invisible until it
# costs something -- so test_the_one_exemption_is_still_true() asserts the
# property that makes it safe, and fails if that stops being the case.
KNOWN_DELIBERATE = {"tests/cli/test_doctor_unreadable_base.py"}

# The sweep's recorded size, measured 2026-09-12: 125 files under tests/,
# 124 swept once this guard excludes itself.
#
# This assert read `>= 20` against an actual 124, which made it a
# formality: 104 files, 83.9% of the sweep, could be dropped with it still
# green, and only 9 files mention which_base at all so the companion
# assert needed just one of them to survive. A floor that far under the
# real number cannot fail in the direction it exists for. Pinned to the
# measured count instead -- a deliberate deletion updates this line in the
# same commit, and anything else fails loudly.
SWEPT_BASELINE = 124

_STRING_TOKENS = {tokenize.STRING}
for _name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
    _t = getattr(tokenize, _name, None)
    if _t is not None:
        _STRING_TOKENS.add(_t)


def _tokens(text: str):
    try:
        return list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []


def prose_ranges(text: str) -> list[tuple[int, int, int, int]]:
    """Exact (line, col) ranges that are writing ABOUT code, not code.

    Two kinds and only two: a COMMENT token, and a STRING token that makes up a
    whole logical line by itself. A string being passed, assigned or returned is
    never in here, which is the distinction both earlier attempts got wrong.
    """
    ranges: list[tuple[int, int, int, int]] = []
    logical: list[tokenize.TokenInfo] = []

    def flush() -> None:
        if logical and all(t.type in _STRING_TOKENS for t in logical):
            for t in logical:
                ranges.append((t.start[0], t.start[1], t.end[0], t.end[1]))

    for tok in _tokens(text):
        if tok.type == tokenize.COMMENT:
            ranges.append((tok.start[0], tok.start[1], tok.end[0], tok.end[1]))
        elif tok.type in (tokenize.NL, tokenize.INDENT, tokenize.DEDENT,
                          tokenize.ENCODING, tokenize.ENDMARKER):
            continue
        elif tok.type == tokenize.NEWLINE:
            flush()
            logical = []
        else:
            logical.append(tok)
    flush()
    return ranges


def in_prose(line: int, col: int, ranges) -> bool:
    for sl, sc, el, ec in ranges:
        if sl <= line <= el and not (line == sl and col < sc) \
                and not (line == el and col >= ec):
            return True
    return False


def yields_literals(node: ast.AST) -> list[str]:
    """String literals this expression can evaluate to DIRECTLY.

    A flat `isinstance(node, ast.Constant)` test is fail-open. In this tree
    `lambda: "/fake/base" if base_present else None` came back acceptable,
    because the value node is an IfExp -- while handing a bare string back on
    one branch. Conditionals and boolean fallbacks pass a literal through, so
    they are followed.

    A Call is opaque and STAYS opaque, which is why
    `lambda: str(tmp_path / "base")` is correctly fine: the "base" inside it is
    a path component being built into a real file, never the returned value.
    Following into calls would fail the reference fixture this guard exists to
    encourage.
    """
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else []
    if isinstance(node, ast.IfExp):
        return yields_literals(node.body) + yields_literals(node.orelse)
    if isinstance(node, ast.BoolOp):
        out: list[str] = []
        for v in node.values:
            out.extend(yields_literals(v))
        return out
    return []


_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                  ast.ClassDef)


# A name BOUND AT RUN TIME, meaning the text CANNOT show what value it
# takes. Marked distinctly from "bound to a non-literal expression"
# because it is a different FACT even though the two share a verdict.
# Block 3 was two different facts sharing one token, so this file does
# not do that again.
#
# THE WORD "CANNOT" IS DOING REAL WORK AND IT WAS WRONG ONCE. A first
# draft called every parameter and every loop variable runtime, which
# would have BLESSED both of these by design:
#
#     def _helper(monkeypatch, path="/usr/bin/base")
#     for path in ["/usr/bin/base"]:
#
# Both are bare string literals written right here. avocet measured it
# before it shipped. A blessing is far harder to take back than an
# omission, so runtime is asserted ONLY where the text genuinely cannot
# show the value: look at a parameter's DEFAULT and a loop's ITERABLE
# first, and fall back to runtime only when neither yields a literal.
_RUNTIME = object()

_PARAM_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _iter_elements(node: ast.AST):
    """The element expressions iterating `node` would hand out, or None.

    None means this guard cannot SEE what it yields, which is a different
    answer from "it yields nothing" -- the distinction block 3 was about.

    A DICT HANDS OUT ITS KEYS, NOT ITS VALUES. Reading .values would be
    wrong in both directions at once: it would miss
    {"/usr/bin/base": True}, whose key is the bare path, and it would
    reach past the key in {"k": "/usr/bin/base"} to a value no loop
    variable ever receives.

    yields_literals is deliberately NOT changed. It answers what an
    EXPRESSION evaluates to, which is the right question for the direct
    value and the wrong one for an iterable; widening it would
    reclassify every arm in this file.
    """
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return list(node.elts)
    if isinstance(node, ast.Dict):
        return [k for k in node.keys if k is not None]
    return None


def _bind_value(target: ast.AST, value: ast.AST, binds: dict) -> None:
    """Bind `target` to `value`, matching tuple shape POSITIONALLY.

        for label, path in [("foreign", "/usr/bin/base")]

    must give `path` the path and `label` the label. An earlier draft
    opened the list, got a Tuple back, asked the EXPRESSION question of
    it, got nothing, and blessed the bare path. Handing every literal to
    every name is not a fix either -- it pairs "foreign" with `path`.

    When the shapes do not line up -- a starred target, a length
    mismatch -- every literal the value could hand out goes to every name
    in the target. Conservative on purpose: reporting a name that MIGHT
    receive a bare string is the safe error here, and blessing one that
    might is not.
    """
    if (isinstance(target, (ast.Tuple, ast.List))
            and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
            and not any(isinstance(t, ast.Starred) for t in target.elts)):
        for t, v in zip(target.elts, value.elts):
            _bind_value(t, v, binds)
        return

    lits = list(yields_literals(value))
    inner = _iter_elements(value)
    if inner is not None:
        for e in inner:
            lits.extend(yields_literals(e))
    for name in _target_names(target):
        if lits:
            binds.setdefault(name, set()).update(lits)
        else:
            binds.setdefault(name, set()).add(_RUNTIME)


def _bind_loop_target(target: ast.AST, iterable: ast.AST,
                      binds: dict) -> None:
    """One binding per element the iterable would hand out."""
    elements = _iter_elements(iterable)
    if elements is None:
        for name in _target_names(target):
            binds.setdefault(name, set()).add(_RUNTIME)
        return
    for el in elements:
        _bind_value(target, el, binds)


def _param_binds(scope: ast.AST) -> dict[str, set]:
    """Each parameter name -> the literal it defaults to, else _RUNTIME.

    A bare-string DEFAULT is a literal written in this file, so it is
    recorded as one and classifies through the ordinary machinery. Only a
    parameter with no default, or a non-literal default, is runtime.
    """
    if not isinstance(scope, _PARAM_SCOPES):
        return {}
    a = scope.args
    positional = list(a.posonlyargs) + list(a.args)
    # Defaults align to the LAST len(defaults) positional parameters.
    pad = len(positional) - len(a.defaults)
    pairs = [(p.arg, a.defaults[i - pad] if i >= pad else None)
             for i, p in enumerate(positional)]
    pairs += list(zip([p.arg for p in a.kwonlyargs], a.kw_defaults))
    pairs += [(x.arg, None) for x in (a.vararg, a.kwarg) if x is not None]

    out: dict[str, set] = {}
    for name, default in pairs:
        lits = yields_literals(default) if default is not None else []
        if lits:
            out.setdefault(name, set()).update(lits)
        else:
            out.setdefault(name, set()).add(_RUNTIME)
    return out


def _target_names(target: ast.AST) -> list:
    """Every Name a binding target introduces, tuple unpacking included."""
    return [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]


def _own_scope_binds(scope: ast.AST) -> dict[str, set]:
    """Names bound in THIS scope's own body, to the string literals they take.

    A binding to anything that is not a string literal records None
    alongside, so a caller can tell "always this one string" from
    "sometimes this string, sometimes a real path". Nested function,
    lambda and class scopes are not descended into: they bind their own
    names, not this scope's.

    This walked tree.body only until avocet's arm E. A function-local
    constant was therefore never collected, so the WEAK_CONST branch --
    which exists for exactly that shape -- could only ever fire on a
    module-level name, and a local fell through to OK_EXPR.

    PARAMETERS ARE BOUND HERE, and that is block 3. They were not, so
    the live helper

        def _point_which_base_at(monkeypatch, path):
            monkeypatch.setattr("...which_base", lambda: path)

    resolved to "nothing binds this name". Once unbound started failing
    as UNKNOWN that would have turned the guard RED on the two files
    every foreign-base test in this tree routes through. A parameter is
    not an unresolvable name; it is a value that arrives at call time.
    For targets, with-as names, comprehension variables and except-as
    names are the same fact and are bound the same way.

    IMPORTS ARE DELIBERATELY NOT BOUND. An imported name has no value
    this file can read, so it stays unresolvable and fails as UNKNOWN.
    """
    binds: dict[str, set] = {k: set(v) for k, v in
                             _param_binds(scope).items()}

    stack = list(ast.iter_child_nodes(scope))
    while stack:
        n = stack.pop()
        if isinstance(n, _NESTED_SCOPES):
            continue
        targets: list = []
        value = None
        if isinstance(n, ast.Assign):
            targets, value = list(n.targets), n.value
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets, value = [n.target], n.value
        elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
            # The ITERABLE decides, element by element, matched to the
            # target positionally. Iterating a literal container binds a
            # bare string, and calling that runtime would bless it.
            _bind_loop_target(n.target, n.iter, binds)
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if item.optional_vars is not None:
                    for name in _target_names(item.optional_vars):
                        binds.setdefault(name, set()).add(_RUNTIME)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            binds.setdefault(n.name, set()).add(_RUNTIME)
        if value is not None:
            lit = (value.value if isinstance(value, ast.Constant)
                   and isinstance(value.value, str) else None)
            for t in targets:
                if isinstance(t, ast.Name):
                    binds.setdefault(t.id, set()).add(lit)
        stack.extend(ast.iter_child_nodes(n))
    return binds


def _scope_chains(tree: ast.Module) -> dict[int, list]:
    """For every Call node, the string bindings visible where it sits.

    Innermost scope LAST, which is the order Python's own name lookup
    uses. A flat whole-tree table would be simpler and wrong: an
    unrelated helper binding `path` to a bare string would condemn a
    correct fixture that binds its own `path` to a real file. That is the
    same failure as the `len(args) >= 3` draft, which called five good
    fixtures broken, so there is an arm for it below.
    """
    chains: dict[int, list] = {}

    def visit(node: ast.AST, chain: list) -> None:
        if isinstance(node, ast.Call):
            chains[id(node)] = chain
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Decorators and argument defaults are evaluated OUTSIDE the
            # function, so they keep the outer chain.
            for d in node.decorator_list:
                visit(d, chain)
            for d in list(node.args.defaults) + \
                    [k for k in node.args.kw_defaults if k is not None]:
                visit(d, chain)
            inner = chain + [_own_scope_binds(node)]
            for stmt in node.body:
                visit(stmt, inner)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, chain)

    visit(tree, [_own_scope_binds(tree)])
    return chains


def _resolve_string_name(chain: list, name: str) -> tuple:
    """Look `name` up the way Python does: the innermost binding wins.

      ("literal", s)    every binding in the winning scope is that string
      ("mixed", None)   bound to a string AND to something else, so which
                        value reaches which_base is not decidable here
      ("runtime", None) bound at run time -- a parameter, a for target, a
                        with-as name -- so the value arrives at call time
                        and cannot be a bare literal written here
      ("expr", None)    bound, but never to a string literal
      ("unbound", None) NOTHING in this file binds it, an imported name
                        included, so its value cannot be checked here

    "runtime", "expr" and "unbound" are three different facts. The first
    two are acceptable and the third is not, and block 3 was the last two
    of them sharing one verdict.
    """
    for scope in reversed(chain):
        if name not in scope:
            continue
        vals = scope[name]
        lits = {v for v in vals if isinstance(v, str)}
        if lits:
            if len(lits) == 1 and all(isinstance(v, str) for v in vals):
                return "literal", next(iter(lits))
            return "mixed", None
        if _RUNTIME in vals:
            return "runtime", None
        return "expr", None
    return "unbound", None


# Verdicts. Named so an arm can assert WHICH one fired: "something was reported"
# is not the claim, "THIS was reported" is. An arm that passes through a
# different branch than the one it names proves the wrong thing while reading
# green, which is the trap tests/test_no_hardcoded_roots.py records for its
# Windows arm.
WEAK = "WEAK"                   # value is, or can be, a bare string literal
WEAK_CONST = "WEAK_CONST"       # a name bound to a string literal, in any scope the chain can see
UNKNOWN = "UNKNOWN"             # a stub this guard cannot classify: NOT acceptable
OK_EXPR = "OK_EXPR"             # value is an expression, so it can be a real file
OK_ABSENT = "OK_ABSENT"         # value is None, and absence is a supported state


def classify_stubs(text: str) -> list[tuple[str, int, str]]:
    """Every attempt to replace which_base, as (verdict, line, detail).

    Recognised shapes, which are the ones this tree uses:
      monkeypatch.setattr(obj, "which_base", <value>)
      setattr(obj, "which_base", <value>)
      mock.patch("pkg.which_base", return_value=<value>)
      mock.patch.object(obj, "which_base", <value>)
    Anything else is UNKNOWN, never acceptable.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [(UNKNOWN, getattr(e, "lineno", 0) or 0, f"will not parse: {e}")]

    chains = _scope_chains(tree)
    found: list[tuple[str, int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else \
            f.id if isinstance(f, ast.Name) else ""
        if name not in ("setattr", "patch", "object"):
            continue
        named = " ".join(a.value for a in node.args
                         if isinstance(a, ast.Constant) and isinstance(a.value, str))
        if TARGET not in named:
            continue

        # LOCATE THE VALUE BY FINDING THE TARGET FIRST, never by arg count.
        # An earlier draft of this guard said `len(node.args) >= 3`, which is
        # true of `setattr(obj, "which_base", v)` and FALSE of
        # `monkeypatch.setattr("pkg.mod.which_base", v)` -- the two-argument
        # form, where the dotted string IS the first argument. That draft
        # reported five perfectly good fixtures as UNKNOWN, including the
        # reference `_make_stub_base()` ones this guard exists to encourage. The
        # value is whatever follows the argument carrying the name; if nothing
        # follows it, no value was given at all.
        target_at = None
        for i, a in enumerate(node.args):
            if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                    and TARGET in a.value:
                target_at = i
                break

        val, how = None, "none-given"
        for kw in node.keywords:
            if kw.arg in ("return_value", "new", "side_effect"):
                val, how = kw.value, kw.arg
                break
        if val is None and target_at is not None \
                and len(node.args) > target_at + 1:
            val, how = node.args[target_at + 1], "positional"

        if val is None:
            found.append((UNKNOWN, node.lineno,
                          "no value given, so a bare patch injects a Mock and "
                          "which_base returns a Mock rather than a path"))
            continue

        body = val.body if isinstance(val, ast.Lambda) else val
        lits = yields_literals(body)
        if lits:
            shown = ast.unparse(body)[:60]
            found.append((WEAK, node.lineno, f"{how} -> {shown}  yields {lits!r}"))
        elif isinstance(body, ast.Constant) and body.value is None:
            found.append((OK_ABSENT, node.lineno, f"{how} -> None"))
        elif isinstance(body, ast.Name):
            # A lambda's OWN parameters are a real scope, pushed here as
            # one. The previous version FORCED the string "unbound" for a
            # shadowing parameter purely because that reached OK_EXPR --
            # one token carrying two opposite meanings, which is the
            # defect block 3 named. The scope says it instead.
            chain = list(chains.get(id(node), []))
            if isinstance(val, ast.Lambda):
                chain.append(_param_binds(val))
            kind, lit = _resolve_string_name(chain, body.id)
            if kind == "literal":
                found.append((WEAK_CONST, node.lineno,
                              f"{how} -> {body.id} = {lit!r}"))
            elif kind == "mixed":
                found.append((UNKNOWN, node.lineno,
                              f"{how} -> {body.id} is bound both to a "
                              "string literal and to something else, so "
                              "which value reaches which_base is not "
                              "decidable from the text"))
            elif kind == "unbound":
                found.append((UNKNOWN, node.lineno,
                              f"{how} -> nothing in this file binds "
                              f"{body.id}, so its value cannot be "
                              "checked here. An imported constant lands "
                              "here, and NOTHING IS FINE BY DEFAULT"))
            elif kind == "runtime":
                found.append((OK_EXPR, node.lineno,
                              f"{how} -> {body.id} is bound at run time "
                              "(a parameter or loop target), so the "
                              "value arrives at call time"))
            else:
                found.append((OK_EXPR, node.lineno,
                              f"{how} -> {ast.unparse(body)[:60]}"))
        elif isinstance(body, ast.Constant):
            found.append((UNKNOWN, node.lineno,
                          f"{how} -> non-string constant {body.value!r}"))
        else:
            found.append((OK_EXPR, node.lineno, f"{how} -> {ast.unparse(body)[:60]}"))
    return found


BAD_VERDICTS = (WEAK, WEAK_CONST, UNKNOWN)


def _candidate_files() -> list[Path]:
    """Every test file the sweep COULD reach, before any exclusion.

    Split out from _swept_files so the exclusion is a MEASURABLE
    DIFFERENCE between two sets rather than a filter term nobody can see.
    """
    return sorted(p for p in TESTS.rglob("*.py")
                  if "__pycache__" not in p.parts)


def _swept_files() -> list[Path]:
    return [p for p in _candidate_files() if p.resolve() != SELF]


def _offences(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    try:
        where = path.relative_to(REPO).as_posix()
    except ValueError:                      # a tmp_path fixture, in the arms
        where = str(path)
    if where in KNOWN_DELIBERATE:
        return []
    return [f"{where}:{line}  [{verdict}] {detail}"
            for verdict, line, detail in classify_stubs(text)
            if verdict in BAD_VERDICTS]


# --------------------------------------------------------------- the guard

def test_the_sweep_actually_found_files():
    """Assert the anchor before asserting anything about what it anchors.

    A sweep that reaches nothing reads exactly like a clean one, so a rename or
    a filter typo that emptied the sweep would leave every fixture unchecked
    while this module reported green.
    """
    swept = _swept_files()
    assert len(swept) >= SWEPT_BASELINE, (
        f"{len(swept)} test files swept under {TESTS}, down from the\n"
        f"recorded baseline of {SWEPT_BASELINE}. Either test files were\n"
        "deleted -- then update SWEPT_BASELINE in the same commit that\n"
        "deletes them -- or the sweep has quietly narrowed, which is the\n"
        "thing this arm exists to catch.")
    assert any("which_base" in p.read_text(encoding="utf-8") for p in swept), (
        "no swept file mentions which_base at all, so the sweep is looking in "
        "the wrong place")


def test_this_guard_excludes_ITSELF_AND_NOTHING_ELSE():
    """The exclusion is asserted as an EXACT SET, not as "contains".

    The red arms below embed bare-string stubs as source text. If this
    file ever enters the sweep, the guard fails on its own arms and the
    next person makes it green by weakening it. So SELF must be excluded.

    Asserting ONLY that left the other direction open. avocet widened the
    exclusion to also drop tests/services/test_writeback.py and planted a
    weak stub in that very file: the sweep stayed green, this arm stayed
    green, and the entire signal was 143 passed becoming 142 passed. One
    fewer test, zero failures -- because the sweep is parametrized over
    _swept_files() at COLLECTION time, so narrowing it DELETES cases
    rather than failing any. Measured: 124 swept against a floor of 20,
    so 104 files could go, and only 9 mention which_base at all.

    Equality is what makes a second exclusion fail instead of shrink.
    """
    candidates = {p.resolve() for p in _candidate_files()}
    excluded = candidates - {p.resolve() for p in _swept_files()}
    assert SELF in excluded, (
        "this guard is sweeping itself and will fail on its own red arms")
    assert excluded == {SELF}, (
        "the sweep excludes more than this guard file, so these files are\n"
        "silently unchecked while every arm here still reads green:\n  "
        + "\n  ".join(sorted(str(p) for p in excluded - {SELF})))


def test_a_second_exclusion_makes_that_arm_go_RED(monkeypatch):
    """RED ARM for the exclusion check itself.

    An equality assertion that was never shown to fail is the same
    formality as the floor it replaces. This narrows the sweep by one
    real file and requires the arm above to raise.
    """
    victim = (TESTS / "services" / "test_writeback.py").resolve()
    assert victim in {p.resolve() for p in _candidate_files()}, (
        "the victim file is not in the sweep to begin with, so this arm\n"
        "would pass without testing anything")

    real = _swept_files
    monkeypatch.setitem(
        globals(), "_swept_files",
        lambda: [p for p in real() if p.resolve() != victim])

    excluded = ({p.resolve() for p in _candidate_files()}
                - {p.resolve() for p in _swept_files()})
    assert excluded == {SELF, victim}, (
        f"the arm did not actually narrow the sweep; excluded={excluded!r}")
    with pytest.raises(AssertionError):
        test_this_guard_excludes_ITSELF_AND_NOTHING_ELSE()


@pytest.mark.parametrize("swept", _swept_files(),
                         ids=lambda p: p.relative_to(REPO).as_posix())
def test_no_fixture_stubs_which_base_at_a_bare_string(swept):
    bad = _offences(swept)
    assert not bad, (
        "this file stubs which_base at a value that is not a file, so a refusal "
        "test here cannot tell REFUSED CORRECTLY from NEVER REACHED THE "
        "CHECK:\n  " + "\n  ".join(bad) +
        "\nWrite a real file instead. tests/services/test_writeback.py's "
        "_make_stub_base() is the reference shape: this host's magic bytes, a "
        "real path, chmod 0o755.")


def test_the_one_exemption_is_still_true():
    """The exemption is only safe while its reason holds, so check the reason.

    tests/cli/test_doctor_unreadable_base.py may bind STUB_BASE to a bare string
    because it replaces `subprocess.run` for that argv, so the path is never
    opened and never executed. If that stops being true the exemption becomes a
    silent hole, and this arm is what closes it.
    """
    for rel in sorted(KNOWN_DELIBERATE):
        p = REPO / rel
        assert p.is_file(), (
            f"{rel} is exempt but does not exist; a stale exemption silently "
            "covers whatever takes that path next")
        text = p.read_text(encoding="utf-8")
        assert "subprocess.run" in text or "subprocess" in text, (
            f"{rel} is exempt because it never executes the stub path, but it "
            "no longer replaces subprocess. The exemption's reason is gone.")
        assert classify_stubs(text), (
            f"{rel} is exempt but no longer stubs which_base at all, so the "
            "exemption is dead weight and should be deleted")


# ------------------------------------------------------- red arms, both ways

# Each arm names the verdict it must produce. Source is embedded rather than
# written into the swept tree, so the arms cannot become offences themselves.
_ARMS = [
    (WEAK, 'monkeypatch.setattr(m, "which_base", lambda: "/fake/base")'),
    (WEAK, 'monkeypatch.setattr(m, "which_base", lambda: "relative-base")'),
    (WEAK, 'setattr(m, "which_base", lambda: "/fake/base")'),
    (WEAK, 'mock.patch("p.which_base", return_value="/fake/base")'),
    (WEAK, 'mock.patch.object(m, "which_base", lambda: "/fake/base")'),
    (WEAK, 'monkeypatch.setattr(m, "which_base", lambda: "/fake/base" if f else None)'),
    (OK_ABSENT, 'monkeypatch.setattr(m, "which_base", lambda: None)'),
    (OK_EXPR, 'monkeypatch.setattr(m, "which_base", lambda: str(real))'),
    (OK_EXPR, 'monkeypatch.setattr(m, "which_base", lambda: str(tmp_path / "base"))'),
    (UNKNOWN, 'mock.patch("p.which_base")'),
    # THE TWO-ARGUMENT FORM, where the dotted string IS the first argument. This
    # is the shape most of this tree actually uses, and the three arms below are
    # the ones an earlier draft of this guard failed: keying the value on
    # `len(args) >= 3` made every one of them UNKNOWN, so the guard called five
    # correct fixtures broken. An arm set that omits the shape under test reads
    # exactly as green as one that covers it.
    (WEAK, 'monkeypatch.setattr("p.mod.which_base", lambda: "/fake/base")'),
    # FLIPPED FROM OK_EXPR TO UNKNOWN BY BLOCK 3, and called out rather
    # than left looking untouched. Nothing in this snippet binds the
    # name, so the guard cannot check its value -- the case the module
    # docstring says fails as UNKNOWN. It used to share the catch-all
    # else with a genuine expression and came back acceptable.
    (UNKNOWN, 'monkeypatch.setattr("p.mod.which_base", lambda: STUB_BASE_FROM_A_CALL)'),
    (OK_ABSENT, 'monkeypatch.setattr("p.mod.which_base", lambda: None)'),

    # NAMED CONSTANTS. WEAK_CONST existed for exactly this shape and had
    # NO arm at all, so it was never shown to fire; the module-level case
    # is armed here alongside the local one it used to miss.
    (WEAK_CONST,
     'STUB = "/fake/base"\n'
     'monkeypatch.setattr(m, "which_base", lambda: STUB)\n'),
    # avocet's arm E, the one that sent this PR back. It landed OK_EXPR,
    # which is actively ACCEPTABLE, not UNKNOWN.
    (WEAK_CONST,
     'def _probe(monkeypatch, m):\n'
     '    local_stub = "/fake/base"\n'
     '    monkeypatch.setattr(m, "which_base", lambda: local_stub)\n'),
    # THE OTHER DIRECTION. A local bound to a real expression must stay
    # acceptable, or the fix swings into flagging the fixtures this guard
    # exists to encourage.
    (OK_EXPR,
     'def _probe(monkeypatch, m, tmp_path):\n'
     '    real = str(tmp_path / "base")\n'
     '    monkeypatch.setattr(m, "which_base", lambda: real)\n'),
    # SCOPES, not one flat table. An unrelated helper binding the same
    # name to a bare string must not condemn a correct fixture. A
    # collector that walked the whole tree passes every other arm here
    # and fails this one.
    (OK_EXPR,
     'def _elsewhere():\n'
     '    path = "/fake/base"\n'
     '\n'
     'def _probe(monkeypatch, m, tmp_path):\n'
     '    path = str(tmp_path / "base")\n'
     '    monkeypatch.setattr(m, "which_base", lambda: path)\n'),
    # BOUND BOTH WAYS. Which value arrives depends on a branch, so the
    # honest answer is UNKNOWN rather than a guess in either direction.
    (UNKNOWN,
     'def _probe(monkeypatch, m, tmp_path, flag):\n'
     '    p = "/fake/base"\n'
     '    if flag:\n'
     '        p = str(tmp_path / "base")\n'
     '    monkeypatch.setattr(m, "which_base", lambda: p)\n'),

    # BOUND AT RUN TIME. This is the shape the REAL tree uses, at
    # tests/services/test_install_refuses_a_foreign_base.py and
    # tests/services/test_scaffold_tier_refuses_a_foreign_base.py. A
    # naive unbound-to-UNKNOWN turned the guard RED on both, which is
    # every foreign-base test in the tree. avocet measured it before it
    # shipped. A parameter is not an unresolvable name.
    (OK_EXPR,
     'def _point_which_base_at(monkeypatch, path):\n'
     '    monkeypatch.setattr("p.mod.which_base", lambda: path)\n'),
    # A lambda's own parameter, shadowing a module constant. This used to
    # reach OK_EXPR through a special case that returned the string
    # "unbound" -- right answer, wrong reason, and the overload that
    # made block 3 possible.
    (OK_EXPR,
     'STUB = "/fake/base"\n'
     'monkeypatch.setattr(m, "which_base", lambda STUB=1: STUB)\n'),
    # A for target is the same fact as a parameter.
    (OK_EXPR,
     'def _probe(monkeypatch, m, paths):\n'
     '    for p in paths:\n'
     '        monkeypatch.setattr(m, "which_base", lambda: p)\n'),
    # AN IMPORTED NAME. Seen, not invisible, and no longer acceptable:
    # nothing in this file binds it, so its value cannot be checked here.
    # The module docstring used to call this shape unseen, which was a
    # second false sentence in the same paragraph as the first.
    (UNKNOWN,
     'from helpers import STUB_BASE\n'
     'monkeypatch.setattr(m, "which_base", lambda: STUB_BASE)\n'),

    # THE FOUR SHAPES A BLANKET 'BOUND AT RUN TIME' WOULD HAVE BLESSED.
    # avocet measured these against a draft that called every parameter
    # and every loop variable runtime, and they are the reason the rule
    # asks the DEFAULT and the ITERABLE first. A blessing written into
    # the design is much harder to take back than an omission.
    #
    # A bare-string DEFAULT is a literal written right here.
    (WEAK_CONST,
     'def _probe(monkeypatch, m, path="/usr/bin/base"):\n'
     '    monkeypatch.setattr(m, "which_base", lambda: path)\n'),
    # So is iterating a literal container. yields_literals answers the
    # EXPRESSION question and returns nothing for a List, so the
    # container is opened for the iterable case.
    (WEAK_CONST,
     'def _probe(monkeypatch, m):\n'
     '    for path in ["/usr/bin/base"]:\n'
     '        monkeypatch.setattr(m, "which_base", lambda: path)\n'),
    # And a comprehension over one.
    (WEAK_CONST,
     'def _probe(monkeypatch, m):\n'
     '    [monkeypatch.setattr(m, "which_base", lambda: p)\n'
     '     for p in ["/usr/bin/base"]]\n'),
    # A NON-string default stays runtime, so the rule did not swing into
    # flagging every defaulted parameter.
    (OK_EXPR,
     'def _probe(monkeypatch, m, path=None):\n'
     '    monkeypatch.setattr(m, "which_base", lambda: path)\n'),
    # with-as binds an __enter__ result, which the binding itself can
    # never make a bare literal. Unconditional runtime is correct here.
    (OK_EXPR,
     'def _probe(monkeypatch, m, ctx):\n'
     '    with ctx as path:\n'
     '        monkeypatch.setattr(m, "which_base", lambda: path)\n'),

    # DESTRUCTURING. This is the shape of the parametrize tables this
    # repo already uses everywhere, so a future (label, path) table
    # feeding a helper is a realistic edit rather than a contrived one.
    # The element is a Tuple and so is the target, and asking the
    # EXPRESSION question of a Tuple comes back empty -- which is how
    # this was blessed before the positional match.
    (WEAK_CONST,
     'def _probe(monkeypatch, m):\n'
     '    for label, path in [("foreign", "/usr/bin/base")]:\n'
     '        monkeypatch.setattr(m, "which_base", lambda: path)\n'),
    # Nested, to prove the match recurses rather than handling one level.
    (WEAK_CONST,
     'def _probe(monkeypatch, m):\n'
     '    for (label, path), n in [(("foreign", "/usr/bin/base"), 1)]:\n'
     '        monkeypatch.setattr(m, "which_base", lambda: path)\n'),
    # A STARRED target cannot be matched positionally, so the
    # conservative fallback hands every literal to every name. Reporting
    # a name that MIGHT receive a bare string is the safe error.
    (WEAK_CONST,
     'def _probe(monkeypatch, m):\n'
     '    for path, *rest in [("/usr/bin/base", 1, 2)]:\n'
     '        monkeypatch.setattr(m, "which_base", lambda: path)\n'),
    # ITERATING A DICT HANDS OUT ITS KEYS. Keyed by the base path, the
    # loop variable IS the bare path.
    (WEAK_CONST,
     'def _probe(monkeypatch, m):\n'
     '    for path in {"/usr/bin/base": True}:\n'
     '        monkeypatch.setattr(m, "which_base", lambda: path)\n'),
    # Keyed by something else, the guard must NOT reach past the key to
    # the value. It reports the KEY -- still a bare string literal bound
    # to which_base, and "k" is no more a file than "/fake/base" is --
    # but it reports it for the key's own sake, never the value's.
    (WEAK_CONST,
     'def _probe(monkeypatch, m):\n'
     '    for path in {"k": "/usr/bin/base"}:\n'
     '        monkeypatch.setattr(m, "which_base", lambda: path)\n'),
    # THE CASE THAT DISCRIMINATES KEYS FROM VALUES, and the one to check
    # this implementation against. The KEY is an expression and the VALUE
    # is a bare path, so reading keys is OK_EXPR and reading values is a
    # false positive. A dict whose key and value are BOTH bare strings
    # cannot tell the two readings apart -- it comes back bad either way.
    (OK_EXPR,
     'def _probe(monkeypatch, m, tmp_path):\n'
     '    for path in {tmp_path / "base": "/usr/bin/base"}:\n'
     '        monkeypatch.setattr(m, "which_base", lambda: path)\n'),
    # An iterable this guard cannot see into stays runtime.
    (OK_EXPR,
     'def _probe(monkeypatch, m, paths):\n'
     '    for path in sorted(paths):\n'
     '        monkeypatch.setattr(m, "which_base", lambda: path)\n'),
]


@pytest.mark.parametrize("verdict,line", _ARMS)
def test_each_shape_gets_ITS_OWN_verdict(verdict, line):
    """RED ARM. A guard that classifies nothing reads exactly like a clean tree.

    The conditional arm is not an edge case: tests/cli/test_base_wire_reporting.py
    carried that exact shape and a flat Constant test called it acceptable.
    """
    got = classify_stubs(line + "\n")
    assert got, f"{line!r} is invisible to the guard"
    assert [v for v, _, _ in got] == [verdict], (
        f"{line!r} was classified {[v for v, _, _ in got]!r}, not [{verdict!r}]. "
        "An arm that fires through a different branch than the one it names "
        "passes while proving nothing.")


def test_a_stub_planted_in_the_REAL_tree_is_actually_caught(tmp_path):
    """The claim through the real sweep, not just the classifier.

    A tmp_path file proves the pattern matches. It says nothing about whether
    the sweep's ROOT reaches the fixtures, so the stub is planted under the real
    tests/ tree and removed in a finally.
    """
    planted = TESTS / "test_weak_base_stub_red_arm.py"
    assert not planted.exists(), f"{planted} already exists; refusing to overwrite"
    try:
        planted.write_text(
            'def test_x(monkeypatch):\n'
            '    monkeypatch.setattr(m, "which_base", lambda: "/fake/base")\n',
            encoding="utf-8")
        swept = _swept_files()
        assert planted in swept, (
            f"tests/ is not reached by the sweep ({len(swept)} files swept, none "
            "of them the planted one), so every fixture is unchecked while this "
            "guard reads green")
        assert _offences(planted), (
            "the planted stub is inside the sweep but was not reported")
    finally:
        planted.unlink(missing_ok=True)
    assert not planted.exists(), f"the arm left {planted} behind"


def test_the_guard_does_not_fire_on_writing_ABOUT_the_shape(tmp_path):
    """The other direction, and the one both earlier classifiers failed.

    A file that TALKS about the shape without using it must come back clean, or
    the guard punishes the documentation that explains it. The literal appears
    here in a module docstring, a function docstring, and a trailing comment on
    a line of real code.
    """
    ok = tmp_path / "talks_about_it.py"
    ok.write_text(
        '"""Fixtures once stubbed which_base at "/fake/base", which is not a\n'
        'file, so a refusal could not be told from a check never reached.\n'
        '\n'
        'This second paragraph names /fake/base again, on a line that starts\n'
        'with a letter, which is what the first classifier missed.\n'
        '"""\n'
        '\n'
        '\n'
        'def test_ok(monkeypatch, tmp_path):\n'
        '    """Names /fake/base once more, still prose."""\n'
        '    # And a comment naming /fake/base, which is also not work.\n'
        '    monkeypatch.setattr(m, "which_base", lambda: str(tmp_path / "base"))\n',
        encoding="utf-8")
    assert not _offences(ok), (
        "the guard fired on prose. A selector that cannot tell a literal from a "
        "sentence about one blocks its own fix: this is the defect that made "
        "the first two classifiers unusable.")

    verdicts = [v for v, _, _ in classify_stubs(ok.read_text(encoding="utf-8"))]
    assert verdicts == [OK_EXPR], (
        f"the one real stub in that file classified {verdicts!r}; a guard that "
        "loses the code while excluding the prose has swung the other way")


def test_a_literal_and_a_comment_about_it_on_ONE_line_are_told_apart(tmp_path):
    """Columns, not lines. One row can carry a real stub and prose about stubs.

    A line-granular classifier must pick one answer for the row and is therefore
    wrong about the other half whichever way it picks.
    """
    both = tmp_path / "both_on_one_line.py"
    both.write_text(
        'def test_x(monkeypatch):\n'
        '    monkeypatch.setattr(m, "which_base", lambda: "/fake/base")'
        '  # was /fake/base here too\n',
        encoding="utf-8")
    bad = _offences(both)
    assert bad, (
        "the stub was lost because the row also carries a comment mentioning "
        "the path, so prose on a line is hiding code on the same line")
    assert len(bad) == 1, f"expected exactly the one stub, got {bad!r}"
