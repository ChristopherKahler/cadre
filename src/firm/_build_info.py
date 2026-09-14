"""Where this copy of Cadre came from.

An install has to be able to answer "what commit are you?" and the answer has
to be true, including when it is not knowable. Three sources, tried in order,
and the third one is the honest failure rather than a guess:

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
import re

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
    }


def build_info() -> dict:
    """Resolve the build identity. Never raises, never invents a commit."""
    for source in (_from_stamp, _from_archive):
        found = source()
        if found is not None:
            return found
    return {
        "commit": None,
        "tag": None,
        "describe": None,
        "dirty": False,
        "built_at": None,
        "commit_count": None,
        "remote_distance": None,
        "source": "unknown",
    }


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

