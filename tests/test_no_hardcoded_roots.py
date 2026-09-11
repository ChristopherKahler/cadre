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

# A POSIX or Windows home directory with a NAMED user in it. `~` is fine, `$HOME`
# is fine; a literal person's home is not, because it resolves on exactly one
# machine and silently resolves to nothing everywhere else.
HOME_PATH = re.compile(
    r"""(?<!["'])(/home/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+"""
    r"""|[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9._-]+)""")

# Lines that MENTION a path while explaining why it was removed are the point of
# the fix, not a violation of it. Only comments get this pass; code never does.
COMMENT_PREFIXES = ("#", "//", "*", '"""', "'''")


def _script_files() -> list[Path]:
    return sorted(
        p for p in SCRIPTS.rglob("*")
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


def test_there_are_scripts_to_check():
    """The guard must be looking at something. An empty sweep passes forever."""
    files = _script_files()
    assert len(files) >= 5, f"only found {len(files)} scripts under {SCRIPTS}"


@pytest.mark.parametrize("script", _script_files(), ids=lambda p: p.name)
def test_no_script_hard_codes_a_named_home_directory(script):
    bad = _offending_lines(script)
    assert not bad, (
        "this script carries a named home directory in EXECUTABLE code, so it "
        "resolves on one machine and silently measures the wrong thing (or "
        "nothing) everywhere else:\n  " + "\n  ".join(bad) +
        "\nResolve the root from the script's own location the way "
        "scripts/check-action-pins.py does, and REFUSE with a distinct exit "
        "code when it is not what you meant to measure.")


def test_the_guard_can_actually_fail(tmp_path):
    """RED ARM. A sweep that matches nothing reads exactly like a clean sweep."""
    bad = tmp_path / "bad.sh"
    bad.write_text('cd /home/someone/dev/checkout || exit 1\n', encoding="utf-8")
    assert _offending_lines(bad), (
        "the pattern does not match a plain hard-coded home path, so every "
        "script above passed this guard without being checked")

    win = tmp_path / "bad2.py"
    win.write_text('ROOT = "C:/Users/someone/dev/checkout"\n', encoding="utf-8")
    assert _offending_lines(win), "the pattern misses Windows home paths"


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
