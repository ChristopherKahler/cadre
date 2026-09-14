"""What this install is, and what it can reach.

One producer, several consumers. ``cadre identity`` prints this, ``firm doctor
--install`` checks it, ``cadre heartbeat status`` embeds it and the hub serves
it. None of them computes any of it themselves, because "three surfaces agree
about what is installed" is only worth something if the agreement is structural
-- three independent readers that happen to match today will disagree the first
time one of them is edited.

Three sources are deliberately kept SEPARATE rather than merged into one
answer, because they are written by three different processes and their
disagreement is the finding:

``build``    baked into the package at build time (``firm._build_info``)
``metadata`` what the installer recorded (``importlib.metadata``)
``wheel``    the artifact pip installed, and its hash (``direct_url.json``)

Editing installed bytes moves the first. Re-pointing the install moves the
third. A mismatch means the install is not what someone thinks it is, which is
the entire failure this module exists to make visible.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from urllib.parse import unquote
from typing import Any

from firm import _build_info

_DIST = "cadre"


def _metadata_version() -> str | None:
    """The version the installer recorded, which is not the same question."""
    try:
        from importlib import metadata
        return metadata.version(_DIST)
    except Exception:
        return None


def _direct_url() -> dict[str, Any] | None:
    """pip's own record of the artifact it installed.

    pip writes ``direct_url.json`` beside the metadata whenever a distribution
    is installed from a file or a URL rather than resolved from an index. It
    carries the source and its hash, so an install can prove WHICH FILE it came
    from without anyone having to add a mechanism for it. Absent for an index
    install or an editable one, and absent is reported as absent.
    """
    try:
        from importlib import metadata
        dist = metadata.distribution(_DIST)
        raw = dist.read_text("direct_url.json")
    except Exception:
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    url = parsed.get("url") or ""
    info = parsed.get("archive_info") or {}
    hashes = info.get("hashes") or {}
    digest = hashes.get("sha256") or ""
    if not digest and isinstance(info.get("hash"), str):
        # Older pip wrote "hash": "sha256=<hex>" instead of a hashes map.
        _, _, digest = info["hash"].partition("=")
    # Percent-DECODE before taking the basename. direct_url.json holds a URL,
    # and the "+" that every stamped version carries in its local segment is
    # encoded as %2B there -- so a live install reported its own wheel as
    # "cadre-0.1.0.dev421%2Bg3392d00-py3-none-any.whl". Measured, not guessed.
    name = os.path.basename(unquote(url).rstrip("/"))
    return {
        "url": url,
        "filename": name or None,
        "sha256": digest or None,
    }


def _package_path() -> str | None:
    try:
        return str(Path(_build_info.__file__).resolve().parent)
    except Exception:
        return None


def installed_identity() -> dict[str, Any]:
    """The whole identity of this install. Never raises."""
    build = _build_info.build_info()
    version = _build_info.version_string()
    meta = _metadata_version()
    wheel = _direct_url()

    sources = {
        "build": {
            "commit": build["commit"],
            "commit_short": build["commit"][:7] if build["commit"] else None,
            "source": build["source"],
            "dirty": build["dirty"],
            "built_at": build["built_at"],
            "commit_count": build["commit_count"],
            "tag": build["tag"],
            "remote_distance": build["remote_distance"],
        },
        "metadata": {"version": meta},
        "wheel": wheel,
    }

    return {
        "name": _DIST,
        "version": version,
        "sources": sources,
        "agreement": _agreement(version, meta, build),
        "package_path": _package_path(),
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
            "platform": platform.platform(),
        },
    }


def _agreement(version: str, meta: str | None, build: dict[str, Any]) -> dict[str, Any]:
    """Do the imported code and the installer's record say the same thing?

    Three honest states before any fault is considered, and getting these wrong
    is how a check becomes noise that people learn to scroll past:

    ``not-installed``
        No metadata at all. A source tree on ``sys.path`` and nothing
        installed. A state, not a fault.

    ``source-tree``
        The imported package carries NO build stamp -- ``source`` is
        ``unknown`` -- so its version is the bare base. If an egg-info happens
        to sit beside it (``python -m build`` leaves one) the two will read
        differently, and that difference says only that the working tree was
        never stamped. Comparing them is comparing answers to two different
        questions.

    ``agree`` / ``disagree``
        Both sides have a real identity. Now the comparison means something: a
        package that knows its commit, disagreeing with the version the
        installer recorded, is installed bytes that are not what the installer
        put there. That is issue #120 wearing a different hat and it is a real
        finding.
    """
    if meta is None:
        return {"ok": True, "state": "not-installed",
                "detail": "running from a source tree; nothing is installed"}
    if build["source"] == "unknown":
        return {
            "ok": True,
            "state": "source-tree",
            "detail": (f"imported from an unstamped source tree, so it reports "
                       f"the base version {version}; the installed "
                       f"distribution records {meta}"),
        }
    if meta == version:
        return {"ok": True, "state": "agree",
                "detail": f"imported and installed both report {version}"}
    return {
        "ok": False,
        "state": "disagree",
        "detail": (f"the imported package reports {version} but the installer "
                   f"recorded {meta}; the code being imported is not the code "
                   f"that was installed"),
    }


def _fmt(label: str, value: Any) -> str:
    return f"  {label:<14}{value}"


def render_text(identity: dict[str, Any]) -> str:
    """Human-readable. Every absent value says so rather than printing blank."""
    b = identity["sources"]["build"]
    w = identity["sources"]["wheel"]
    m = identity["sources"]["metadata"]
    lines = [f"{identity['name']} {identity['version']}"]

    if b["commit"]:
        commit = b["commit"]
        if b["dirty"]:
            commit += "  (tree was dirty at build)"
        lines.append(_fmt("commit", f"{commit}   [{b['source']}]"))
    else:
        lines.append(_fmt("commit", "unknown — built from a source copy with "
                                    "no git metadata and no archive stamp"))
    if b["built_at"]:
        lines.append(_fmt("built", b["built_at"]))
    if b["remote_distance"]:
        lines.append(_fmt("upstream", b["remote_distance"]))
    lines.append(_fmt("installed", m["version"] or "not installed "
                                                  "(running from a source tree)"))
    if w:
        lines.append(_fmt("wheel", w["filename"] or w["url"] or "unknown"))
        lines.append(_fmt("sha256", w["sha256"] or "not recorded"))
    else:
        lines.append(_fmt("wheel", "not recorded (index or editable install)"))
    lines.append(_fmt("package", identity["package_path"] or "unknown"))
    lines.append(_fmt("python", f"{identity['python']['version']}  "
                                f"{identity['python']['executable']}"))

    ag = identity["agreement"]
    if not ag["ok"]:
        lines.append("")
        lines.append(f"  MISMATCH: {ag['detail']}")
    elif ag["state"] in ("source-tree", "not-installed"):
        lines.append("")
        lines.append(f"  note: {ag['detail']}")
    return "\n".join(lines)


def run_identity(*, as_json: bool = False) -> int:
    """`cadre identity`. Needs no workspace and no firm database.

    That independence is the point: the question "what commit are you?" has to
    be answerable on a machine that has Cadre installed and no firm yet, and
    `firm doctor` cannot answer it because it exits early without a
    .firm/firm.db.
    """
    identity = installed_identity()
    if as_json:
        print(json.dumps(identity, indent=2, default=str))
    else:
        print(render_text(identity))
    return 0 if identity["agreement"]["ok"] else 1

def hub_identity(firms_root: Any, firms: Any) -> dict[str, Any]:
    """What the hub serves at ``/api/identity``.

    A function rather than a dict built inline in the request handler, so that
    DoD 5 -- the three surfaces agree about what is installed -- can be tested
    by driving it, instead of by reading the server source and believing it.
    A test that asserts about code it has read is not a test.

    It ADDS to the shared identity and never recomputes any of it: the reach
    half is the only thing the hub knows that the others do not.
    """
    identity = installed_identity()
    identity["reaches"] = {
        "firms_root": str(firms_root),
        "firms": sorted(firms),
    }
    return identity
