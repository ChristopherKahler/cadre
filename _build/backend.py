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

When there is no git to ask -- a ``git archive`` tree, or a plain source copy
-- the stamp is simply not written. That is not a failure path to be patched
over: ``firm._build_info`` falls through to the commit git substituted into
the archive, and failing that reports ``unknown``. It never invents a commit.
"""

from __future__ import annotations

import datetime
import pathlib
import subprocess

from setuptools.build_meta import *  # noqa: F401,F403  (PEP 517 surface)
from setuptools import build_meta as _orig

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_STAMP = _ROOT / "src" / "firm" / "_build_stamp.py"


def _git(*args: str) -> str | None:
    """Run git in the source root. None on any failure, including no git."""
    try:
        out = subprocess.run(
            ("git", *args), cwd=_ROOT, capture_output=True, text=True, timeout=30,
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


def _stamp() -> None:
    commit = _git("rev-parse", "HEAD")
    if commit is None:
        # No git. Leave no stamp; _build_info falls through to the archive
        # substitution, and then to an honest "unknown".
        return
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


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    _stamp()
    return _orig.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_sdist(sdist_directory, config_settings=None):
    _stamp()
    return _orig.build_sdist(sdist_directory, config_settings)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    _stamp()
    return _orig.prepare_metadata_for_build_wheel(
        metadata_directory, config_settings)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    _stamp()
    return _orig.build_editable(
        wheel_directory, config_settings, metadata_directory)


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    _stamp()
    return _orig.prepare_metadata_for_build_editable(
        metadata_directory, config_settings)
