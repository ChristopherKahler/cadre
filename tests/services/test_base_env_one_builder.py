"""There is exactly ONE builder of the environment every ``base`` call gets.

This is the guard, not a style rule. On 2026-09-10 there were two copies of
``_base_env``: one in ``firm/services/base_domain.py``, one in
``firm/services/base_extension.py``. They drifted the moment one was fixed.

The fix was load-bearing. ``Path.home()`` on Windows reads USERPROFILE, not
HOME, and raises ``RuntimeError: Could not determine home directory.`` when it
finds neither. ``firm/__main__.py`` builds its argparse tree with
``default=Path.home() / "firms"``, which runs for EVERY command before a single
argument is parsed -- so an environment carrying only HOME is one in which
Cadre cannot start at all. base_domain's copy learned to pass USERPROFILE and
its neighbours through on ``nt``. base_extension's did not, so
``cadre extension install`` went on dying on Windows behind a fix that read as
complete (49e87d1).

Nothing in the suite noticed, because nothing in the suite named ``_base_env``.
That is the shape that let the session-pulse hook drift 123 diff lines with the
build green throughout; ``tests/hooks/test_session_pulse_one_source.py`` is the
guard this one is modelled on.

WHY THIS DOES NOT COUNT DEFINITIONS. Two ``def _base_env`` in the source tree
is the CORRECT state: one body in base_domain, one delegator in base_extension
that calls it. Counting definitions would fail on the fixed tree. What has to
stay unique is the BODY -- the code that builds the dictionary -- so that is
what these tests pin, from two directions. A copy under the same name is caught
by parsing; a copy under a different name is caught by its fingerprint.

The enumeration is ``git ls-files`` and never a filesystem walk. Any tree where
a wheel has been built carries ``build/lib/firm/services/base_domain.py``, a
copy of the whole package frozen at whatever it said when the wheel was made.
It is untracked, it ships nothing, and a walk would read it as a second builder
and fail the build over untracked output. The control below proves the
difference by putting an untracked copy in the tree and requiring it to be
ignored.
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest

from firm.services import base_domain, base_extension

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The one module allowed to hold the body. Every other definition delegates.
CANONICAL = "src/firm/services/base_domain.py"

#: A line unique to the body of the builder. A file carrying it is building the
#: environment itself, whatever it decided to call the function.
FINGERPRINT = '"PATH": os.environ.get("PATH")'

needs_checkout = pytest.mark.skipif(
    not (REPO_ROOT / CANONICAL).exists(),
    reason=f"needs the repo checkout: {REPO_ROOT / CANONICAL}",
)


def _collect(root: Path) -> dict[str, str]:
    """Tracked ``src`` Python files under ``root``, as path -> source text.

    Paths are repo-relative with forward slashes, which is what ``git
    ls-files`` prints on every platform.
    """
    done = subprocess.run(["git", "ls-files", "--", "src"],
                          capture_output=True, text=True, cwd=str(root))
    if done.returncode != 0:
        pytest.fail(f"git ls-files failed in {root}: {done.stderr.strip()}")
    files: dict[str, str] = {}
    for line in done.stdout.splitlines():
        rel = line.strip()
        if rel.endswith(".py"):
            files[rel] = (root / rel).read_text(encoding="utf-8")
    return files


def _builders(files: dict[str, str]) -> list[str]:
    """Every file whose text builds the environment itself.

    Catches a copy that was renamed on the way in, which the parse below
    cannot see because it no longer answers to ``_base_env``.
    """
    return sorted(path for path, text in files.items() if FINGERPRINT in text)


def _non_delegating_definitions(files: dict[str, str]) -> list[str]:
    """Files defining ``_base_env`` whose body is a second implementation.

    A delegation refers to ``base_domain`` in CODE and builds no environment of
    its own. Anything that builds one is a second implementation however it is
    spelled, and anything that neither delegates nor builds is a stub that will
    hand ``base`` an environment nobody wrote.

    Both halves are read off the parse tree and never off the text. Matching
    ``"base_domain" in ast.dump(node)`` looks equivalent and is not: the dump
    carries the docstring, and this function's docstring explains the
    two-copy history by name. A body replaced with ``return dict(os.environ)``
    passed that check on its docstring alone -- measured, as mutant 2b, while
    writing this file.
    """
    offenders: list[str] = []
    for path, text in files.items():
        if path == CANONICAL:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != "_base_env":
                continue
            if _builds_an_environment(node) or not _refers_to_the_builder(node):
                offenders.append(path)
    return sorted(offenders)


def _refers_to_the_builder(node: ast.FunctionDef) -> bool:
    """``node`` names base_domain in code, not merely in its prose."""
    for kid in ast.walk(node):
        if isinstance(kid, ast.ImportFrom) and "base_domain" in (kid.module or ""):
            return True
        if isinstance(kid, ast.Name) and kid.id == "base_domain":
            return True
        if isinstance(kid, ast.Attribute) and kid.attr == "base_domain":
            return True
    return False


def _builds_an_environment(node: ast.FunctionDef) -> bool:
    """``node`` assembles a mapping instead of asking for the shared one.

    Three spellings, because all three are ways the copy came back: a literal
    ``{...}``, a ``dict(...)`` call, and a ``.copy()`` of something -- the last
    two being how a rewrite reaches for ``os.environ`` wholesale.
    """
    for kid in ast.walk(node):
        if isinstance(kid, ast.Dict):
            return True
        if isinstance(kid, ast.Call):
            func = kid.func
            if isinstance(func, ast.Name) and func.id == "dict":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "copy":
                return True
    return False


@needs_checkout
def test_only_one_tracked_file_builds_the_base_environment():
    builders = _builders(_collect(REPO_ROOT))
    assert builders == [CANONICAL], (
        "the environment every `base` call gets is built in more than one "
        f"place: {builders}. Two builders is two chances to configure a "
        "subprocess wrong, and the second one is the copy that does not get "
        "the fix. Delegate to base_domain._base_env instead.")


@needs_checkout
def test_every_other_definition_delegates_rather_than_reimplements():
    offenders = _non_delegating_definitions(_collect(REPO_ROOT))
    assert offenders == [], (
        f"these define their own `_base_env` instead of delegating: {offenders}")


@needs_checkout
def test_the_guard_catches_a_reintroduced_third_copy(tmp_path):
    """The control: build the failure, do not reason about it.

    A real third copy -- the body verbatim, in a new module, in a real git
    tree -- run through the same two predicates. Both must name it.

    The same tree also carries two copies git does not track: one under
    ``build/`` and, more pointedly, one inside ``src/`` itself. A filesystem
    walk would find both and fail the build over output nobody ships. Reading
    git's index is what makes the guard see the shipped source and only the
    shipped source.
    """
    real = _collect(REPO_ROOT)
    body = real[CANONICAL]
    root = tmp_path / "tree"

    tracked = {
        CANONICAL: body,
        "src/firm/services/base_extension.py":
            real["src/firm/services/base_extension.py"],
        "src/firm/services/base_rogue.py": body,   # the reintroduced copy
    }
    for rel, text in tracked.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=str(root),
                   check=True, capture_output=True)
    subprocess.run(["git", "add", "src"], cwd=str(root),
                   check=True, capture_output=True)

    for rel in ("build/lib/firm/services/base_domain.py",
                "src/firm/services/base_stale.py"):
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")  # written, never `git add`ed

    seen = _collect(root)
    assert "src/firm/services/base_stale.py" not in seen, (
        "the enumeration walked the filesystem instead of reading git's "
        "index, so untracked output counts as source")
    assert not any(path.startswith("build/") for path in seen)

    assert _builders(seen) == [CANONICAL, "src/firm/services/base_rogue.py"], (
        "the fingerprint scan did not name the third copy, so it would not "
        "have failed the build when one came back")
    assert _non_delegating_definitions(seen) == [
        "src/firm/services/base_rogue.py"], (
        "the parse did not name the third definition")


def test_the_extension_delegates_to_the_one_builder(monkeypatch):
    """Delegation is a property of the running code, not of its shape.

    A delegator that quietly stops delegating passes both scans above: it
    defines nothing new and carries no fingerprint. This is the arm that
    notices. BASE_HOME is set so the comparison runs over a passthrough rather
    than over the two constant keys.
    """
    monkeypatch.setenv("BASE_HOME", str(Path("/tmp/scratch-tier")))
    assert base_extension._base_env() == base_domain._base_env()


def _fixed_home(value: str) -> type:
    """A stand-in for ``pathlib.Path`` that answers ``home()`` with ``value``.

    ``pathlib`` picks its flavour from ``os.name`` when a ``Path`` is built, so
    a test that patches ``os.name`` alone makes ``Path.home()`` try to
    construct the other platform's class and die with ``NotImplementedError:
    cannot instantiate 'WindowsPath' on your system``. Testing the ``nt``
    branch from Linux therefore has to patch the home lookup as well as the
    branch. Both directions are patched, so these two run identically on every
    leg of CI instead of quietly covering nothing off their native platform.
    """

    class _Home:
        @staticmethod
        def home() -> str:
            return value

    return _Home


def test_the_windows_arm_carries_a_home_windows_can_find(monkeypatch):
    """The env must let Cadre START on Windows, not merely find `base`.

    HOME is not how Windows finds a home directory, and `firm/__main__.py`
    resolves one while building the parser for every command. Delete this
    passthrough and both scans above stay green over an environment in which
    Cadre cannot run at all.
    """
    monkeypatch.setattr(base_domain, "Path", _fixed_home(r"C:\Users\example"))
    monkeypatch.setenv("USERPROFILE", r"C:\Users\example")
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setattr(os, "name", "nt")

    env = base_domain._base_env()

    assert env.get("USERPROFILE") == r"C:\Users\example"
    assert env.get("SYSTEMROOT") == r"C:\Windows"


def test_that_windows_check_can_fail(monkeypatch):
    """The control on the arm above: off `nt` the passthrough is absent.

    Without this, an unconditional `env.update(os.environ)` would satisfy the
    assertion while carrying the operator's whole environment into every
    subprocess, which is the thing `_base_env` exists to avoid.
    """
    monkeypatch.setattr(base_domain, "Path", _fixed_home("/home/example"))
    monkeypatch.setenv("USERPROFILE", r"C:\Users\example")
    monkeypatch.setattr(os, "name", "posix")

    assert "USERPROFILE" not in base_domain._base_env()
