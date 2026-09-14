"""``cadre doctor --install`` -- the install half of doctor.

It is separate from ``run_doctor`` for a reason that is structural, not
stylistic: ``run_doctor`` needs ``.firm/firm.db`` and returns ``db-not-found``
before a single check runs without one. Every question it asks is about a firm.
"Is this install what it says it is" is a question about the machine, and it
has to be answerable on a box that has Cadre installed and no firm yet -- which
is exactly the box where a bad install does the most damage.

Three sources, three different writers, and the checks are about whether they
agree (law 39): the commit baked into the package at build time, the version
the installer recorded, and the wheel pip says it installed. Two readings of
the same source would corroborate each other exactly as falsely as one.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from firm.identity import installed_identity


def _card(key: str, label: str, ok: bool, detail: str,
          state: str | None = None) -> dict[str, Any]:
    card = {"key": key, "label": label, "ok": ok, "detail": detail,
            "route": "mechanical"}
    if state:
        card["state"] = state
    return card


def diagnose_install(identity: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    ident = identity if identity is not None else installed_identity()
    build = ident["sources"]["build"]
    meta = ident["sources"]["metadata"]
    wheel = ident["sources"]["wheel"]
    checks: list[dict[str, Any]] = []

    # 1. Can this install name its commit at all?
    if build["commit"]:
        checks.append(_card(
            "install-commit", "The install knows its commit", True,
            f"{build['commit']} (from {build['source']})"))
    else:
        checks.append(_card(
            "install-commit", "The install knows its commit", False,
            "no commit: built from a source copy with neither git metadata "
            "nor a git-archive stamp, so this install cannot say what code it "
            "is running",
            state="undeterminable"))

    # 2. Was the tree clean when it was built?
    if build["commit"]:
        checks.append(_card(
            "install-clean", "Built from a committed tree", not build["dirty"],
            "clean" if not build["dirty"]
            else "the working tree had uncommitted changes at build time, so "
                 "the commit above does not fully describe these bytes"))

    # 3. Do the package and the installer agree?
    ag = ident["agreement"]
    checks.append(_card("install-agreement", "Imported code matches what was installed",
                        ag["ok"], ag["detail"],
                        state=None if ag["ok"] else "mismatch"))

    # 4. Is there a record of WHICH artifact was installed?
    if wheel and wheel.get("sha256"):
        checks.append(_card(
            "install-artifact", "The installed artifact is recorded", True,
            f"{wheel.get('filename') or wheel.get('url')}  sha256 {wheel['sha256']}"))
    elif meta["version"] is None:
        checks.append(_card(
            "install-artifact", "The installed artifact is recorded", True,
            "nothing is installed; this is a source tree", state="undeterminable"))
    elif wheel and wheel.get("kind") in ("editable", "directory"):
        # pip DID write direct_url.json for an editable install; it records the
        # directory under dir_info. Saying none was written was false (#120 G2 F4).
        checks.append(_card(
            "install-artifact", "The installed artifact is recorded", True,
            f"{wheel['kind']} install of {wheel.get('dir') or wheel.get('url')}: pip "
            "recorded the directory, so there are no wheel bytes to hash.",
            state="undeterminable"))
    else:
        checks.append(_card(
            "install-artifact", "The installed artifact is recorded", True,
            "not recorded — installed from an index, so pip wrote no "
            "direct_url.json. Not a fault, but this install cannot prove which "
            "file it came from.",
            state="undeterminable"))

    return checks


def run_install_doctor(*, as_json: bool = False) -> int:
    ident = installed_identity()
    checks = diagnose_install(ident)
    if as_json:
        print(json.dumps({"ok": all(c["ok"] for c in checks),
                          "identity": ident, "checks": checks},
                         indent=2, default=str))
        return 0 if all(c["ok"] for c in checks) else 1

    print(f"cadre doctor --install — {ident['name']} {ident['version']}")
    for c in checks:
        mark = "?" if c.get("state") == "undeterminable" else ("✓" if c["ok"] else "✗")
        print(f"  {mark} {c['label']} — {c['detail']}")
    bad = [c for c in checks if not c["ok"]]
    if bad:
        print(f"  → {len(bad)} finding(s). This is about the install, not the firm.")
        return 1
    return 0
