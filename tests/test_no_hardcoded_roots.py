"""No script may hard-code one machine's checkout, home directory, or user.

THE DEFECT (issue #64). Two verify scripts carried
`/home/chriskahler/dev/cadre-wt-extension`. `verify_ext_install.sh` **cd-ed**
into it, so run from any other checkout it silently measured THAT tree and
printed provenance hashes for files the caller never chose. A real number from
the wrong file is worse than no number, because nothing in the output says it is
wrong. `accept_relay_title.py` did the same through `sys.path.insert`, importing
one worktree's `firm` package and printing `spawn.py md5=...` about it.

The same file also fell back to `/home/chriskahler/.local/bin/base` when `base`
was not on PATH, so on a host without base it hashed an absent operator's binary
and called that provenance.

These went unnoticed because they never fail. They produce confident output
about the wrong thing, which is the one failure mode no amount of reading the
result will catch. So the guard is mechanical and runs in CI.

THE SECOND DEFECT (issue #69). The sweep read `scripts/` and stopped, so the
117 files of `src/` were never checked at all. Nothing about a directory that
is not swept looks different from a directory that is clean, so the gap was
invisible in exactly the way the original defect was. `src/` is now swept too.
`tests/` is not, on purpose, and a test below enforces that -- the fixtures
there are deliberate and thirteen of them are this module's own red arms.

The rule: resolve roots from the script's own location, and REFUSE with a
distinct exit code when what you find is not the thing you meant to measure.
`scripts/check-action-pins.py` is the reference shape.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

# ISSUE #69. The sweep covered `scripts/` and nothing else, so the 117 files of
# `src/` -- the product itself -- were never looked at. A sweep that does not
# reach a directory reads exactly like a clean one, which is the failure this
# guard exists to catch, one level up from where it was catching it.
#
# `tests/` is DELIBERATELY NOT SWEPT, and the exclusion is enforced by a test
# below rather than left to memory. Measured at 5d19e1b45f75 with this module's
# own `_offending_lines()`: `tests/` is 116 files and 29 offending lines, and
# THIRTEEN of those 29 are in THIS FILE -- the eight parametrized red-arm lines,
# three fixture writes, and two docstring continuation lines that the KNOWN
# LIMIT below does not exempt. Sweep `tests/` and the guard fails on its own
# arms, and a guard that fails on correct code gets edited until it stops
# guarding.
#
# The issue body says 23 hits in `tests/`. That figure is a NON-RECURSIVE sweep
# of the 68 top-level files; the recursive sweep this module actually performs
# finds 116 files and 29 lines. The conclusion is unchanged, and the larger
# number makes it stronger.
SWEPT_ROOTS = (SCRIPTS, REPO / "src")
NOT_SWEPT = REPO / "tests"

# A POSIX or Windows home directory with a NAMED user in it. `~` is fine,
# `$HOME` is fine; a literal person's home is not, because it resolves on
# exactly one machine and silently resolves to nothing everywhere else.
#
# NO LOOKBEHIND. This began as `(?<!["'])(...)`, which refused to match a path
# immediately preceded by a quote -- and a quoted path is the NORMAL way to
# write one in Python. So the guard saw the shell half of issue #64
# (`cd /home/...`) and was blind to the Python half
# (`sys.path.insert(0, "/home/...")`), which is the half it was written for.
# NO PURPOSE FOR THAT EXCLUSION COULD BE ESTABLISHED; it is deleted rather than
# rationalised. A guard carrying an unexplained exclusion is worse than one that
# over-matches, because the exclusion is invisible until it costs something.
#
# Measured by sandpiper over this tree: 0 executable-line hits with the
# lookbehind, 3 without it. It was the only reason the tree passed its own
# guard. Exemptions are named and reasoned in ALLOWED below, never a mechanism
# that silently covers every quoted string.
#
# Named groups so a test can assert WHICH branch matched. An arm that fires
# through a different alternation than the one it names proves the wrong thing
# while reading green -- the Windows arm did exactly that, matching the POSIX
# `/Users/` branch re-anchored after `C:`. Windows first: longest alternative
# wins, so `C:\Users\x` cannot be claimed by the bare `/Users/` branch.
HOME_PATH = re.compile(
    r"""(?P<windows>[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9._-]+)"""
    r"""|(?P<posix_home>/home/[A-Za-z0-9._-]+)"""
    r"""|(?P<posix_users>/Users/[A-Za-z0-9._-]+)""")


def _match_of(line: str):
    """(branch name, matched text) for a line, or None. Used by the red arms."""
    m = HOME_PATH.search(line)
    if not m:
        return None
    return m.lastgroup, m.group(0)

# Lines that MENTION a path while explaining why it was removed are the point of
# the fix, not a violation of it. Only comments get this pass; code never does.
#
# KNOWN LIMIT, stated rather than discovered later: this recognises prose by the
# line's PREFIX, so it exempts a comment line and the opening line of a
# docstring, but NOT a continuation line inside one. A docstring that quotes a
# real path on its second line is reported as a violation.
#
# That is the conservative direction -- it over-reports prose rather than
# under-reporting code -- and it was left this way deliberately: the alternative
# is parsing each file to find docstring ranges, and a guard that is harder to
# read than the rule it enforces is the next thing to go quietly wrong. Reword
# the docstring; a literal machine path is not needed to explain the shape.
COMMENT_PREFIXES = ("#", "//", "*", '"""', "'''")


def _swept_files() -> list[Path]:
    """Every checkable file under every swept root.

    RECURSIVE -- `rglob`, not `glob` -- and the distinction is not cosmetic. A
    non-recursive sweep of `tests/` sees 68 files where the recursive one sees
    116, and that is exactly how the issue body arrived at 23 hits where this
    module finds 29.
    """
    return sorted(
        p for root in SWEPT_ROOTS
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix in {".sh", ".py"}
        and "__pycache__" not in p.parts)


def _offending_lines(path: Path) -> list[str]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not HOME_PATH.search(line):
            continue
        if line.strip().startswith(COMMENT_PREFIXES):
            continue
        try:
            where = path.relative_to(REPO)
        except ValueError:          # a tmp_path fixture, in the red arms below
            where = path
        out.append(f"{where}:{n}  {line.strip()[:120]}")
    return out


@pytest.mark.parametrize("root", SWEPT_ROOTS, ids=lambda p: p.name)
def test_every_swept_root_contributes_files(root):
    """The guard must be looking at something, and at each root SEPARATELY.

    A floor on the combined total is a weaker claim than it looks: `scripts/`
    alone clears any reasonable floor, so a `src/` that silently stopped being
    swept -- renamed, moved, excluded by a filter typo -- would hide behind the
    scripts count while every file in the product went unchecked. Assert the
    anchor was found before asserting anything about what it anchors.
    """
    assert root.is_dir(), f"{root} is not a directory, so it contributes nothing"
    mine = [p for p in _swept_files() if root in p.parents]
    assert len(mine) >= 5, f"only {len(mine)} file(s) swept under {root}"


def test_the_sweep_does_not_reach_the_tests_directory():
    """`tests/` is excluded ON PURPOSE, so the exclusion is asserted, not assumed.

    Thirteen of that directory's offending lines are inside THIS FILE: the red
    arms below hard-code `/home/someone/...` because that string is the thing
    they exist to prove the guard catches. Sweep `tests/` and the guard fails on
    its own arms, and the next person makes it green by weakening it.
    """
    reached = [p for p in _swept_files() if NOT_SWEPT in p.parents]
    assert not reached, (
        f"{len(reached)} file(s) under {NOT_SWEPT} entered the sweep, starting "
        f"with {reached[0]}. Those fixtures are deliberate and the guard would "
        "fail on its own red arms.")


def test_a_violation_planted_in_src_is_actually_caught():
    """THE ARM FOR ISSUE #69, and it was seen RED before the sweep was widened.

    A `tmp_path` fixture cannot make this claim. It proves the PATTERN matches;
    it says nothing about whether the sweep's ROOTS reach the product. So the
    violation is planted in the real `src/` tree, the real sweep is asked about
    it, and the file is removed in a `finally`.

    Measured against the sweep before this change, as its assertion message:
    `src/ is not in the sweep (15 files swept, none of them the planted one)`.
    """
    planted = REPO / "src" / "firm" / "_hardcoded_root_red_arm.py"
    assert not planted.exists(), (
        f"{planted} already exists; refusing to overwrite a real file")
    try:
        planted.write_text('ROOT = "/home/someone/dev/checkout"\n',
                           encoding="utf-8")
        swept = _swept_files()
        assert planted in swept, (
            f"src/ is not in the sweep ({len(swept)} files swept, none of them "
            "the planted one), so every file in the product is unchecked while "
            "this guard reads green")
        assert _offending_lines(planted), (
            "the planted violation is inside the sweep but was not reported")
    finally:
        planted.unlink(missing_ok=True)
    assert not planted.exists(), f"the arm left {planted} behind"


@pytest.mark.parametrize("swept", _swept_files(),
                         ids=lambda p: str(p.relative_to(REPO)))
def test_no_swept_file_hard_codes_a_named_home_directory(swept):
    bad = _offending_lines(swept)
    assert not bad, (
        "this file carries a named home directory in EXECUTABLE code, so it "
        "resolves on one machine and silently measures the wrong thing (or "
        "nothing) everywhere else:\n  " + "\n  ".join(bad) +
        "\nResolve the root from the script's own location the way "
        "scripts/check-action-pins.py does, and REFUSE with a distinct exit "
        "code when it is not what you meant to measure.")


# Every arm names the branch it must fire through, and asserts it. The Windows
# arm used to be written with forward slashes and passed by matching the POSIX
# `/Users/` branch re-anchored after `C:` -- green, while proving nothing about
# the alternative it tests. "Something matched" is not the claim; "THIS matched"
# is.
@pytest.mark.parametrize("branch,line", [
    ("posix_home", 'cd /home/someone/dev/checkout || exit 1'),
    ("posix_home", 'sys.path.insert(0, "/home/someone/dev/checkout/src")'),
    ("posix_home", "ROOT = '/home/someone/dev/x'"),
    ("posix_home", 'cd "/home/someone/dev/x" || exit 1'),
    ("posix_home", 'BASE = "/home/someone/.local/bin/base"'),
    ("posix_users", 'ROOT = "/Users/someone/dev/checkout"'),
    ("windows", r'ROOT = "C:\Users\someone\dev\checkout"'),
    ("windows", 'ROOT = "C:/Users/someone/dev/checkout"'),
])
def test_each_branch_of_the_guard_fires_through_ITSELF(branch, line):
    """RED ARM. A sweep that matches nothing reads exactly like a clean sweep.

    Four of these eight lines were invisible before the lookbehind came out, and
    they are not edge cases: two of them are the literal lines from issue #64.
    """
    got = _match_of(line)
    assert got is not None, f"{line!r} is invisible to the guard"
    matched_branch, text = got
    assert matched_branch == branch, (
        f"{line!r} matched the {matched_branch!r} branch, not {branch!r}. The "
        f"arm would pass while proving nothing about the branch it names "
        f"(matched {text!r}).")


def test_the_guard_can_actually_fail(tmp_path):
    """The same claim through the real file sweep, not just the regex."""
    bad = tmp_path / "bad.sh"
    bad.write_text('cd /home/someone/dev/checkout || exit 1\n', encoding="utf-8")
    assert _offending_lines(bad), (
        "the pattern does not match a plain hard-coded home path, so every "
        "script above passed this guard without being checked")

    quoted = tmp_path / "bad_quoted.py"
    quoted.write_text(
        'sys.path.insert(0, "/home/someone/dev/checkout/src")\n', encoding="utf-8")
    assert _offending_lines(quoted), (
        "a QUOTED path is invisible to the sweep. That is the normal way to "
        "write one in Python and it is the exact half of issue #64 this exists "
        "to catch")

    win = tmp_path / "bad2.py"
    win.write_text('ROOT = "C:' + chr(92) + 'Users' + chr(92) + 'someone' + chr(92)
                   + 'dev"\n', encoding="utf-8")
    assert _offending_lines(win), "Windows paths with backslashes are missed"


def test_the_guard_does_not_fire_on_a_comment_explaining_the_fix(tmp_path):
    """The other direction: it must not force people to stop documenting it."""
    ok = tmp_path / "ok.sh"
    ok.write_text(
        '# This used to read /home/someone/dev/checkout, which resolved on one box.\n'
        'REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)\n',
        encoding="utf-8")
    assert not _offending_lines(ok)


def test_the_two_scripts_from_issue_64_resolve_their_root_from_their_location():
    """Named directly, so a future edit that reintroduces the path is obvious."""
    sh = (SCRIPTS / "verify" / "verify_ext_install.sh").read_text(encoding="utf-8")
    assert 'BASH_SOURCE[0]' in sh, "verify_ext_install.sh no longer derives its root"
    assert "rev-parse --is-inside-work-tree" in sh, (
        "verify_ext_install.sh does not refuse a root that is not a git work tree")

    py = (SCRIPTS / "verify" / "accept_relay_title.py").read_text(encoding="utf-8")
    assert "Path(__file__).resolve()" in py, (
        "accept_relay_title.py no longer derives its root")
    assert 'sys.exit(91)' in py, (
        "accept_relay_title.py does not refuse a non-git root with a distinct code")
