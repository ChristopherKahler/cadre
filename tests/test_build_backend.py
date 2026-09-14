"""The build backend asks git only about its own repository (#120, G2 finding F3).

``_build/backend.py`` stamps the commit a build stands on. It ran git in the
source root and trusted the answer, but git walks UP the directory tree: a Cadre
source tree unpacked inside some other project's repository was stamped with
that project's commit, and the stamp outranks the correct commit the archive
carries. avocet measured it as G2 leg L9.

Each test copies the backend into a scratch source layout and loads it from
there, so the backend's ``_ROOT`` is the scratch tree. setuptools is replaced by
an empty stand-in: the backend imports it at load time, these tests never call
into it, and the suite's environment is not guaranteed to have it.
"""

from __future__ import annotations

import importlib.util
import itertools
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "_build" / "backend.py"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_names = itertools.count()

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git on PATH")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=test@cadre.invalid", "-c", "user.name=test", *args],
        cwd=cwd, check=True, capture_output=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    ).stdout.strip()


def _toplevel(path: Path) -> str | None:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=path, capture_output=True,
        encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    )
    return out.stdout.strip() if out.returncode == 0 else None


def _new_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "commit", "-q", "--allow-empty", "-m", "scratch")
    return _git(path, "rev-parse", "HEAD")


def _source_tree(root: Path) -> Path:
    (root / "_build").mkdir(parents=True, exist_ok=True)
    shutil.copy(BACKEND, root / "_build" / "backend.py")
    (root / "src" / "firm").mkdir(parents=True, exist_ok=True)
    return root


def _load_backend(root: Path, monkeypatch) -> types.ModuleType:
    stand_in = types.ModuleType("setuptools")
    stand_in.build_meta = types.ModuleType("setuptools.build_meta")
    monkeypatch.setitem(sys.modules, "setuptools", stand_in)
    monkeypatch.setitem(sys.modules, "setuptools.build_meta", stand_in.build_meta)
    spec = importlib.util.spec_from_file_location(
        f"cadre_backend_under_test_{next(_names)}", root / "_build" / "backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stamp_text(root: Path) -> str | None:
    stamp = root / "src" / "firm" / "_build_stamp.py"
    return stamp.read_text(encoding="utf-8") if stamp.exists() else None


def test_a_tree_inside_another_repository_is_not_stamped_with_that_repository(tmp_path, monkeypatch):
    outer_commit = _new_repo(tmp_path / "project")
    root = _source_tree(tmp_path / "project" / "vendor" / "cadre")
    # The precondition that makes this test mean anything: git, asked from the
    # tree, answers with the OUTER repository. Without it the test would pass
    # for the wrong reason.
    assert _toplevel(root) is not None and Path(_toplevel(root)).resolve() == (tmp_path / "project").resolve()

    backend = _load_backend(root, monkeypatch)

    assert backend._git("rev-parse", "HEAD") is None
    backend._stamp()
    text = _stamp_text(root)
    assert text is None, f"stamped from the enclosing repository: {text!r}"
    assert outer_commit not in (text or "")


def test_a_tree_that_is_its_own_repository_still_reads_its_commit(tmp_path, monkeypatch):
    # Control: the check must not switch git off for the ordinary checkout.
    root = _source_tree(tmp_path / "cadre")
    own_commit = _new_repo(root)

    backend = _load_backend(root, monkeypatch)

    assert backend._git("rev-parse", "HEAD") == own_commit
    backend._stamp()
    assert f"COMMIT = {own_commit!r}" in (_stamp_text(root) or "")


def test_a_tree_outside_any_repository_reads_no_commit(tmp_path, monkeypatch):
    # Control: no repository anywhere above, so there is nothing to read.
    if _toplevel(tmp_path) is not None:
        pytest.skip(f"the temp directory is itself inside a repository ({_toplevel(tmp_path)}); "
                    "this control cannot run here")
    root = _source_tree(tmp_path / "cadre")

    backend = _load_backend(root, monkeypatch)

    assert backend._git("rev-parse", "HEAD") is None
    backend._stamp()
    assert _stamp_text(root) is None


def test_the_repository_check_compares_normalised_paths(tmp_path, monkeypatch):
    # git prints forward slashes on Windows, and macOS temp paths sit behind a
    # symlink. The comparison has to survive both, so it is pinned directly.
    root = _source_tree(tmp_path / "cadre")
    backend = _load_backend(root, monkeypatch)
    assert backend._same_path(str(root), str(root) + os.sep)
    assert backend._same_path(str(root).replace(os.sep, "/"), str(root))
    assert not backend._same_path(str(root), str(root.parent))


# ---------------------------------------------------------------------------
# G2 F1: a build stamps only while setuptools collects, and leaves src/ as it
# found it; an editable build writes no stamp at all
# ---------------------------------------------------------------------------

_STAMPING_HOOKS = ("build_wheel", "build_sdist", "prepare_metadata_for_build_wheel")
_EDITABLE_HOOKS = ("build_editable", "prepare_metadata_for_build_editable")


def _recording_hooks(backend: types.ModuleType, root: Path) -> dict:
    """Stand-in setuptools hooks that record what the stamp held while they ran."""
    seen: dict = {}
    stamp = root / "src" / "firm" / "_build_stamp.py"

    def make(name):
        def hook(*args, **kwargs):
            seen[name] = stamp.read_bytes() if stamp.exists() else None
            return f"{name}-result"
        return hook

    for name in _STAMPING_HOOKS + _EDITABLE_HOOKS:
        setattr(backend._orig, name, make(name))
    return seen


@pytest.mark.parametrize("hook", _STAMPING_HOOKS)
def test_a_build_hook_stamps_while_it_runs_and_leaves_no_stamp_behind(tmp_path, monkeypatch, hook):
    root = _source_tree(tmp_path / "cadre")
    commit = _new_repo(root)
    backend = _load_backend(root, monkeypatch)
    seen = _recording_hooks(backend, root)

    assert getattr(backend, hook)(str(tmp_path / "out")) == f"{hook}-result"

    assert seen[hook] is not None and f"COMMIT = {commit!r}".encode() in seen[hook]
    assert _stamp_text(root) is None, "the build left its stamp in src/"


@pytest.mark.parametrize("hook", _STAMPING_HOOKS)
def test_a_build_hook_puts_back_a_stamp_that_was_already_there(tmp_path, monkeypatch, hook):
    root = _source_tree(tmp_path / "cadre")
    _new_repo(root)
    leftover = b"COMMIT = 'a leftover from an older build'\n"
    (root / "src" / "firm" / "_build_stamp.py").write_bytes(leftover)
    backend = _load_backend(root, monkeypatch)
    _recording_hooks(backend, root)

    getattr(backend, hook)(str(tmp_path / "out"))

    assert (root / "src" / "firm" / "_build_stamp.py").read_bytes() == leftover


@pytest.mark.parametrize("hook", _EDITABLE_HOOKS)
def test_an_editable_build_writes_no_stamp(tmp_path, monkeypatch, hook):
    root = _source_tree(tmp_path / "cadre")
    _new_repo(root)
    backend = _load_backend(root, monkeypatch)
    seen = _recording_hooks(backend, root)

    getattr(backend, hook)(str(tmp_path / "out"))

    assert seen[hook] is None, "an editable build stamped the tree"
    assert _stamp_text(root) is None


def test_a_build_that_fails_still_leaves_no_stamp(tmp_path, monkeypatch):
    root = _source_tree(tmp_path / "cadre")
    _new_repo(root)
    backend = _load_backend(root, monkeypatch)

    def broken(*args, **kwargs):
        raise RuntimeError("setuptools failed")

    backend._orig.build_wheel = broken
    with pytest.raises(RuntimeError):
        backend.build_wheel(str(tmp_path / "out"))
    assert _stamp_text(root) is None
