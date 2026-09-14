"""In-tree PEP 517 backend: stamp the commit, then hand off to setuptools.

This exists for one reason. The version had never moved off the literal
``0.1.0``, so ``pip install`` of a freshly built wheel into an existing
environment printed "Requirement already satisfied" and installed nothing,
while ``--version`` agreed with the old bytes and the new ones alike. A fix
could be declared landed while the old code was still running. Issue #120.

The fix is to stop handing pip a version that cannot change. Before setuptools
collects anything, this backend writes ``src/firm/_build_stamp.py`` with the
commit the build is actually standing on, and ``firm.__version__`` is derived
from it. Two commits therefore produce two versions, and pip's short-circuit
stops firing.

The stamp lives only for the length of a build hook. It is removed again when
setuptools returns, and an editable install gets none at all: its commit is read
live from the checkout, because a stamp names the commit it was written at and
a checkout moves on (#120 G2 F1).

When there is no git to ask -- a ``git archive`` tree, or a plain source copy
-- the stamp is simply not written. That is not a failure path to be patched
over: ``firm._build_info`` falls through to the commit git substituted into
the archive, and failing that reports ``unknown``. It never invents a commit.
"""

from __future__ import annotations

import contextlib
import datetime
import os
import pathlib
import subprocess

from setuptools.build_meta import *  # noqa: F401,F403  (PEP 517 surface)
from setuptools import build_meta as _orig

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_STAMP = _ROOT / "src" / "firm" / "_build_stamp.py"


def _git(*args: str) -> str | None:
    """Ask git about THIS source tree. None on any failure, including no git.

    Also None when the source root is not the top of its own repository. git
    walks up from its working directory, so a Cadre tree unpacked inside some
    other project's repository used to be stamped with that project's commit,
    and the stamp outranks the commit the archive really carries (#120 G2 F3,
    measured by avocet as leg L9).
    """
    if not _own_repository():
        return None
    return _run_git(*args)


_OWN_REPOSITORY: bool | None = None


def _own_repository() -> bool:
    """True only when git's top level is the source root itself. Asked once."""
    global _OWN_REPOSITORY
    if _OWN_REPOSITORY is None:
        top = _run_git("rev-parse", "--show-toplevel")
        _OWN_REPOSITORY = top is not None and _same_path(top, _ROOT)
    return _OWN_REPOSITORY


def _same_path(a: object, b: object) -> bool:
    """Compare paths the way the filesystem does: resolved, and case-folded on
    Windows. git prints forward slashes there, and macOS temp directories sit
    behind a symlink."""
    return (os.path.normcase(os.path.realpath(str(a)))
            == os.path.normcase(os.path.realpath(str(b))))


def _run_git(*args: str) -> str | None:
    """Run git in the source root. None on any failure, including no git."""
    try:
        # encoding and errors spelled out as literals, not text=True. This runs
        # in the isolated build, where firm.core.proc is not importable, so it
        # cannot use run_utf8 -- and the #114 sweep scans src/firm only, so it
        # would never catch this call. The policy is the same: UTF-8, replace.
        out = subprocess.run(
            ("git", *args), cwd=_ROOT, capture_output=True,
            encoding="utf-8", errors="replace", timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _remote_distance() -> str:
    """How this checkout stands against its upstream, recorded not enforced.

    A build from a branch is legitimate, so being behind is not refused -- but
    it is stamped, because the failure this lane exists to fix is a build from
    a stale tree that looked clean. The canonical clone was seven commits
    behind origin/main with nothing modified when this was written, so a build
    there would have shipped pre-fix code with nothing to notice.
    """
    # Try the branch's own upstream first, then the default remote branch. A
    # branch made by `git worktree add -b` has NO upstream, which is every
    # builder branch in this repository -- measured: the first build of this
    # lane stamped an empty string. Without the fallback the field would be
    # empty exactly where it is most wanted.
    for ref in ("@{upstream}", "origin/HEAD", "origin/main"):
        counts = _git("rev-list", "--left-right", "--count", f"{ref}...HEAD")
        if not counts:
            continue
        parts = counts.split()
        if len(parts) != 2:
            continue
        behind, ahead = parts
        if behind == "0" and ahead == "0":
            return f"level with {ref}"
        # Name the ref. A distance is meaningless without the baseline it was
        # measured against, and a reader who assumes the wrong one gets a
        # confident wrong answer rather than no answer.
        return f"behind {ref} by {behind}, ahead by {ahead}"
    return ""


def _stamp() -> bool:
    """Write the stamp. True when one was written, False when there is no git."""
    commit = _git("rev-parse", "HEAD")
    if commit is None:
        # No git. Leave no stamp; _build_info falls through to the archive
        # substitution, and then to an honest "unknown".
        return False
    status = _git("status", "--porcelain")
    count = _git("rev-list", "--count", "HEAD")
    describe = _git("describe", "--tags", "--always") or ""
    # The direct question, asked directly. `describe --always` falls back to a
    # bare short sha when the repo has no tags, and a bare sha is indis-
    # tinguishable from a tag by shape alone -- which is how the raw sha nearly
    # became the version string. --exact-match answers None unless HEAD really
    # is a tag.
    tag = _git("describe", "--tags", "--exact-match") or ""
    built_at = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    _STAMP.write_text(
        '"""Generated at build time. Do not edit and do not commit."""\n'
        f'COMMIT = {commit!r}\n'
        f'DESCRIBE = {describe!r}\n'
        f'TAG = {tag!r}\n'
        f'DIRTY = {bool(status)!r}\n'
        f'BUILT_AT = {built_at!r}\n'
        f'COMMIT_COUNT = {int(count) if count and count.isdigit() else None!r}\n'
        f'REMOTE_DISTANCE = {_remote_distance()!r}\n',
        encoding="utf-8",
    )
    return True


@contextlib.contextmanager
def _stamped():
    """Stamp for the length of one build hook, then put src/ back as it was.

    A stamp that outlived its build kept naming the commit it was written at:
    an editable install went on reporting that commit after the checkout moved,
    and said everything agreed (#120 G2 F1). So the stamp exists only while
    setuptools collects. Whatever was at that path before the hook, an older
    build's leftover or nothing, is exactly what is there after it, even when
    the build fails. An unpacked sdist has no git, so nothing is written there
    and the stamp the sdist carries is left alone.
    """
    before = _STAMP.read_bytes() if _STAMP.exists() else None
    wrote = _stamp()
    try:
        yield
    finally:
        if wrote:
            if before is None:
                _STAMP.unlink(missing_ok=True)
            else:
                _STAMP.write_bytes(before)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    with _stamped():
        return _orig.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_sdist(sdist_directory, config_settings=None):
    with _stamped():
        return _orig.build_sdist(sdist_directory, config_settings)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    with _stamped():
        return _orig.prepare_metadata_for_build_wheel(
            metadata_directory, config_settings)


# No stamp for an editable install. It runs the checkout's own files, so the true
# commit is whatever the checkout is on when the question is asked, and
# firm._build_info reads that live. A stamp written here named the commit the
# install was made at for as long as the file survived (#120 G2 F1).
def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return _orig.build_editable(
        wheel_directory, config_settings, metadata_directory)


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    return _orig.prepare_metadata_for_build_editable(
        metadata_directory, config_settings)
