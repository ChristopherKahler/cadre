"""Where this copy of Cadre came from.

An install has to be able to answer "what commit are you?" and the answer has
to be true, including when it is not knowable. Four sources, tried in order,
and the last one is the honest failure rather than a guess:

``checkout``
    This package is running from a Cadre git checkout: an editable install, or
    ``src`` on ``sys.path``. The commit is read live from git every time, and no
    stamp file is consulted, because a stamp names the commit it was written at
    and a checkout moves on (#120 G2 F1). When git cannot answer there -- not on
    PATH, refusing the repository, timing out -- the source is ``unknown`` and
    ``checkout_error`` says why; a stamp beside a checkout is still never read
    (#120 G2 re-grade R1).

``git``
    The build ran inside a git checkout and the build backend wrote
    ``_build_stamp.py`` beside this file with the real commit.

``archive``
    The tree came from ``git archive``, so there is no ``.git`` to ask -- but
    git substituted the commit into ``_ARCHIVE_COMMIT`` below on its way out,
    through the ``export-subst`` attribute in ``.gitattributes``.

``unknown``
    Neither of the above. Someone copied the source without git metadata and
    without archiving it. The commit is reported as ``None`` and the source as
    ``"unknown"``.

The last case is the one worth being careful about, because the tempting bug is
to paper over it. ``_ARCHIVE_COMMIT`` keeps its unsubstituted placeholder in
that case, and the placeholder is deliberately **not** sha-shaped: it cannot
pass ``^[0-9a-f]{40}$``, so no looser matcher downstream can mistake it for a
commit and no caller can print it as one. Absent stays absent.
"""

from __future__ import annotations

import importlib
import os
import re
import subprocess
from pathlib import Path

#: Rewritten by git at ``git archive`` time. In a plain checkout this keeps the
#: literal placeholder below, which is exactly what tells us we are not in an
#: archive. Do not "tidy" this into something sha-shaped.
_ARCHIVE_COMMIT = "$Format:%H$"

#: The semantic base. The version a human reads and reasons about; the build
#: appends the commit distance and the short sha to it for untagged builds.
BASE_VERSION = "0.1.0"

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _is_sha(value: object) -> bool:
    """True only for a full 40-character lowercase hex commit.

    The unsubstituted ``$Format:%H$`` placeholder fails this, which is the
    whole reason the check is a shape test rather than a truthiness test.
    """
    return isinstance(value, str) and bool(_SHA_RE.match(value))


#: The directory this module lives in. Tests point it at a scratch checkout.
_PACKAGE_DIR = Path(__file__).resolve().parent

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


_GIT_TIMEOUT = 30


def _git_answer(root: Path, *args: str) -> tuple[str | None, str | None]:
    """Run git in *root*: ``(output, None)`` when it answered, ``(None, why)`` when not.

    UTF-8 and no window are spelled out here: ``import firm`` imports this
    module before ``firm.core.proc`` can be imported, so it cannot use run_utf8.
    """
    try:
        out = subprocess.run(
            ("git", *args), cwd=root, capture_output=True, encoding="utf-8",
            errors="replace", timeout=_GIT_TIMEOUT, creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return None, f"git did not answer within {_GIT_TIMEOUT} s"
    except Exception as exc:
        return None, f"git could not be started ({type(exc).__name__}: {exc})"
    if out.returncode != 0:
        said = (out.stderr or "").strip().splitlines()
        return None, f"git exited {out.returncode}" + (f": {said[0]}" if said else "")
    return out.stdout.strip(), None


def _git(root: Path, *args: str) -> str | None:
    """Run git in *root*. None on any failure, including no git."""
    return _git_answer(root, *args)[0]


def _same_path(a: object, b: object) -> bool:
    """Resolved and case-folded the way the filesystem compares paths."""
    return (os.path.normcase(os.path.realpath(str(a)))
            == os.path.normcase(os.path.realpath(str(b))))


def _from_checkout() -> dict | None:
    """The commit of the Cadre checkout this package is running from, read now.

    An editable install, or ``src`` on ``sys.path``, runs the checkout's own
    files, so the only true commit is the one git reports at the moment of
    asking. No stamp file is consulted: a stamp left by an older build named the
    commit it was built at long after the checkout moved on, and the install
    said everything agreed (#120 G2 F1).

    None when this is not a Cadre checkout: no ``_build/backend.py`` beside
    ``src/``, or no repository of its own at that root. An unpacked sdist and a
    git archive have none, even when they sit inside some other project's
    repository (G2 F3), so their own stamp or archive commit is their answer.

    A tree that DOES have its own repository is a checkout whether or not git
    answers. When git cannot, the answer is unknown with the reason, never a
    stamp: with git hidden or refusing the repository, a stamp left in
    ``src/firm/`` made an editable install name the commit it was installed at
    and report agree while the checkout ran other code (#120 G2 re-grade R1).
    """
    root = _PACKAGE_DIR.parent.parent
    if not (root / "_build" / "backend.py").is_file():
        return None
    if not (root / ".git").exists():   # a directory, or the file a worktree has
        return None
    answer, why = _git_answer(root, "rev-parse", "--show-toplevel", "HEAD")
    lines = answer.splitlines() if answer else []
    if why is None and (len(lines) != 2 or not _same_path(lines[0], root)
                        or not _is_sha(lines[1])):
        why = f"git gave no commit of this checkout ({answer!r})"
    if why is not None:
        return _unknown(checkout_error=f"{root} is a git checkout and {why}")
    count = _git(root, "rev-list", "--count", "HEAD")
    return {
        "commit": lines[1],
        "tag": _git(root, "describe", "--tags", "--exact-match") or None,
        "describe": None,
        # --no-optional-locks: a status that finds a stat-stale entry refreshes
        # it and writes the index back when it can take the lock, which can
        # collide with a commit running in the same checkout. Reading must not
        # write (#120 G2 re-grade N3).
        "dirty": bool(_git(root, "--no-optional-locks", "status", "--porcelain")),
        "built_at": None,
        "commit_count": int(count) if count and count.isdigit() else None,
        "remote_distance": None,
        "source": "checkout",
        "checkout_error": None,
    }


def _from_stamp() -> dict | None:
    """The stamp the build backend writes when building from a git checkout."""
    try:
        # importlib, NOT `from firm import _build_stamp`. The `from` form
        # re-imports the real submodule and rebinds it on the package, so a
        # test that injects a fake stamp into sys.modules is ignored and every
        # arm silently reads whatever is on disk. Measured: four synthetic
        # stamps all returned the same string. A stamp that cannot be faked
        # cannot be tested.
        stamp = importlib.import_module("firm._build_stamp")
    except Exception:
        return None
    commit = getattr(stamp, "COMMIT", None)
    if not _is_sha(commit):
        return None
    return {
        "commit": commit,
        "tag": getattr(stamp, "TAG", "") or None,
        "describe": getattr(stamp, "DESCRIBE", "") or None,
        "dirty": bool(getattr(stamp, "DIRTY", False)),
        "built_at": getattr(stamp, "BUILT_AT", "") or None,
        "commit_count": getattr(stamp, "COMMIT_COUNT", None),
        "remote_distance": getattr(stamp, "REMOTE_DISTANCE", "") or None,
        "source": "git",
        "checkout_error": None,
    }


def _from_archive() -> dict | None:
    """The commit git substituted into this file when it made the archive."""
    if not _is_sha(_ARCHIVE_COMMIT):
        return None
    return {
        "commit": _ARCHIVE_COMMIT,
        "tag": None,
        "describe": None,
        "dirty": False,
        "built_at": None,
        "commit_count": None,
        "remote_distance": None,
        "source": "archive",
        "checkout_error": None,
    }


def _unknown(checkout_error: str | None = None) -> dict:
    """No commit. ``checkout_error`` is set only for a checkout git could not read."""
    return {
        "commit": None,
        "tag": None,
        "describe": None,
        "dirty": False,
        "built_at": None,
        "commit_count": None,
        "remote_distance": None,
        "source": "unknown",
        "checkout_error": checkout_error,
    }


def build_info() -> dict:
    """Resolve the build identity. Never raises, never invents a commit."""
    for source in (_from_checkout, _from_stamp, _from_archive):
        found = source()
        if found is not None:
            return found
    return _unknown()


def version_string() -> str:
    """The version this build reports.

    A tagged build is the plain release version. Anything else carries the
    commit distance and the short sha, so that two builds from two commits are
    two different versions -- which is the property that stops ``pip install``
    from deciding a new wheel is already satisfied and doing nothing.
    """
    info = build_info()
    commit = info["commit"]
    count = info["commit_count"]
    # A tagged build is the plain release version. `tag` is only ever set when
    # git answered `describe --tags --exact-match`, so it is a fact rather than
    # an inference from the shape of a describe string -- on a repo with no
    # tags at all, `describe --always` returns a bare short sha, and reading
    # that as a tag would make the raw sha the version.
    if info["tag"] and not info["dirty"]:
        return info["tag"].lstrip("v")
    if commit is None:
        # No commit to hang a version on. Report the base and say nothing more;
        # `cadre identity` is where the absence is spelled out.
        return BASE_VERSION
    suffix = f"+g{commit[:7]}"
    if info["dirty"]:
        suffix += ".dirty"
    if count is None:
        return f"{BASE_VERSION}{suffix}"
    return f"{BASE_VERSION}.dev{count}{suffix}"

