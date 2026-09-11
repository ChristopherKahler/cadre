#!/usr/bin/env python3
"""Red-arm scripts/check-pins-doc.py and the new floor in check-action-pins.py.

Every arm copies the REAL script out of the worktree into a throwaway tree and
runs it there. Nothing is reimplemented here, so an arm cannot pass against a
restatement of logic the repo does not contain.

The arms that matter most are the BLINDNESS arms at the end: empty the glob, or
change the shape so the regex stops matching, and require a RED. An empty set
satisfies every assertion made about its members, and a check that cannot fail on
nothing is not a check. The control proves the point: the ARCHIVED pre-fix
check-action-pins.py, given the same emptied tree, exits 0.

Usage:  python3 prove_pins_doc.py <worktree>
"""

from __future__ import annotations

import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

W = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                 else "/home/chriskahler/dev/curlew-wt-pins")
DOC_REL = "docs/ACTION-PINS.md"
WF = "'.github/workflows'"

# PATH must survive or python cannot start and EVERY arm returns non-zero, which
# reads exactly like every guard firing on a broken tree.
ENV = {k: v for k, v in os.environ.items()
       if k in ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP")}
ENV["PYTHONIOENCODING"] = ""
ENV.pop("PYTHONIOENCODING")


def build(tmp: pathlib.Path, scripts: list[str]) -> pathlib.Path:
    root = tmp / "tree"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    for f in ("tests.yml", "release.yml"):
        shutil.copy(W / ".github" / "workflows" / f,
                    root / ".github" / "workflows" / f)
    shutil.copy(W / DOC_REL, root / DOC_REL)
    for s in scripts:
        shutil.copy(W / "scripts" / s, root / "scripts" / s)
    return root


def run(root: pathlib.Path, script: str) -> tuple[int, str]:
    p = subprocess.run([sys.executable, f"scripts/{script}"], cwd=str(root),
                       capture_output=True, text=True, env=ENV, timeout=120,
                       stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout + p.stderr


def sub(root: pathlib.Path, rel: str, old: str, new: str, *, count: int = 1):
    p = root / rel
    s = io.open(p, encoding="utf-8", newline="").read()
    n = s.count(old)
    if n != count:
        sys.exit(f"REFUSING: fixture anchor occurs {n} times in {rel}, "
                 f"expected {count}. The arm would have tested an unmodified "
                 f"tree and passed on nothing.\n  {old[:100]}")
    io.open(p, "w", encoding="utf-8", newline="\n").write(s.replace(old, new))


RESULTS: list[tuple[str, bool]] = []


def arm(name: str, script: str, want_rc: int, mutate=None, expect_text=None,
        scripts=("check-pins-doc.py", "check-action-pins.py")):
    with tempfile.TemporaryDirectory() as t:
        root = build(pathlib.Path(t), list(scripts))
        if mutate:
            mutate(root)
        rc, out = run(root, script)
    good = rc == want_rc
    saw = "-"
    if expect_text is not None:
        hit = expect_text.lower() in out.lower()
        saw = "yes" if hit else "NO"
        good = good and hit
    RESULTS.append((name, good))
    print(f"  {name:<52} rc={rc:<3} want={want_rc:<3} {saw:>3}  "
          f"{'PASS' if good else 'FAIL'}")


def main() -> int:
    for s in ("check-pins-doc.py", "check-action-pins.py"):
        if not (W / "scripts" / s).is_file():
            sys.exit(f"REFUSING: {W}/scripts/{s} does not exist. This prover "
                     f"would be grading a tree without the fix in it.")

    print(f"worktree {W}")
    print()
    print("scripts/check-pins-doc.py")

    def agree(r):
        """Bring the ledger up to the pins the workflows actually carry.

        The live worktree is REALLY drifted right now - PR #56 moved
        action-gh-release and the ledger update is deliberately a later commit -
        so a baseline arm reading the tree as-is grades the repository instead
        of the checker. This arm constructs agreement from the live files, and
        the drift is reported separately at the end rather than hidden.
        """
        sub(r, DOC_REL,
            "`3bb12739c298aeb8a4eeaf626c5b8d85266b0e65` | v2.6.2 |",
            "`efb35369e0ad2afab669f228072c1b0d510eae64` | v3.0.3 |")
        sub(r, DOC_REL,
            "| `softprops/action-gh-release` | none | — | — | — | v2.6.2 | no |",
            "| `softprops/action-gh-release` | none | — | — | — | v3.0.3 | no |")

    arm("the doc and the workflows agree", "check-pins-doc.py", 0, agree,
        "matches the workflows")

    arm("a workflow SHA differs from the ledger", "check-pins-doc.py", 1,
        lambda r: sub(r, DOC_REL,
                      "| `actions/checkout` | `v4` | `11d5960a326750d5838078e36cf38b85af677262` |",
                      "| `actions/checkout` | `v4` | `0000000000000000000000000000000000000000` |"),
        "records 0000000000000000000000000000000000000000")

    arm("a workflow TAG differs from the ledger", "check-pins-doc.py", 1,
        lambda r: sub(r, DOC_REL,
                      "`11d5960a326750d5838078e36cf38b85af677262` | v4.4.0 |",
                      "`11d5960a326750d5838078e36cf38b85af677262` | v4.9.9 |"),
        "records v4.9.9")

    arm("an action is used but has NO ledger row", "check-pins-doc.py", 1,
        lambda r: sub(r, DOC_REL,
                      "| `actions/upload-artifact` | `v4` | `ea165f8d65b6e75b540449e92b4886f43607fa02` | v4.6.2 |\n",
                      ""),
        "has no row")

    arm("a ledger row for an action nothing uses", "check-pins-doc.py", 1,
        lambda r: sub(r, DOC_REL,
                      "| `actions/checkout` | `v4` |",
                      "| acme/ghost-action | `v4` | `1111111111111111111111111111111111111111` | v1.0.0 |\n| `actions/checkout` | `v4` |"),
        "which no workflow uses")

    arm("one action pinned to TWO different commits", "check-pins-doc.py", 1,
        lambda r: sub(r, ".github/workflows/release.yml",
                      "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
                      "actions/checkout@2222222222222222222222222222222222222222"),
        "more than one commit")

    arm("a pinned action has no advisory row", "check-pins-doc.py", 1,
        lambda r: sub(r, DOC_REL,
                      "| `actions/setup-python` | none | — | — | — | v5.6.0 | no |\n",
                      ""),
        "no advisory row")

    arm("the advisory row names a different version", "check-pins-doc.py", 1,
        lambda r: sub(r, DOC_REL,
                      "| `actions/setup-python` | none | — | — | — | v5.6.0 | no |",
                      "| `actions/setup-python` | none | — | — | — | v5.0.0 | no |"),
        "not the one pinned")

    arm("the advisory row leaves the answer blank", "check-pins-doc.py", 1,
        lambda r: sub(r, DOC_REL,
                      "| `actions/setup-python` | none | — | — | — | v5.6.0 | no |",
                      "| `actions/setup-python` |  | — | — | — | v5.6.0 |  |"),
        "records nothing")

    arm("the document records no query DATE", "check-pins-doc.py", 1,
        lambda r: sub(r, DOC_REL, "**Date:** 2026-09-10", "**Date:** sometime"),
        "no '**date:** yyyy-mm-dd' line")

    arm("the document records no advisory QUERY", "check-pins-doc.py", 1,
        lambda r: sub(r, DOC_REL, "**Advisory query:**", "Advisory query:"),
        "does not record the advisory query")

    print()
    print("scripts/check-pins-doc.py  -  BLINDNESS (an empty scan must REFUSE)")
    arm("the workflows directory is empty", "check-pins-doc.py", 3,
        lambda r: [p.unlink() for p in (r / ".github/workflows").glob("*.yml")],
        "found zero pinned actions")

    arm("the uses: shape changed, the regex matches nothing",
        "check-pins-doc.py", 3,
        lambda r: [sub(r, f".github/workflows/{f}", "uses:", "USES:",
                       count=io.open(r / ".github/workflows" / f,
                                     encoding="utf-8").read().count("uses:"))
                   for f in ("tests.yml", "release.yml")],
        "found zero pinned actions")

    arm("the pin table header was renamed", "check-pins-doc.py", 3,
        lambda r: sub(r, DOC_REL, "| Action | Was | Now | Tag at that SHA |",
                      "| Action | Was | Commit | Tag at that SHA |"),
        "found zero rows under the pin table header")

    print()
    print("scripts/check-action-pins.py")
    arm("every reference is a SHA with its tag", "check-action-pins.py", 0,
        None, "references scanned")

    arm("a reference lost its tag comment", "check-action-pins.py", 1,
        lambda r: sub(r, ".github/workflows/release.yml",
                      "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093  # v4.3.0",
                      "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"),
        "no trailing comment")

    arm("a reference went back to a moving tag", "check-action-pins.py", 1,
        lambda r: sub(r, ".github/workflows/release.yml",
                      "softprops/action-gh-release@efb35369e0ad2afab669f228072c1b0d510eae64  # v3.0.3",
                      "softprops/action-gh-release@v3  # v3.0.3"),
        "not a 40-character commit sha")

    print()
    print("scripts/check-action-pins.py  -  BLINDNESS, and the control")
    arm("the workflows directory is empty", "check-action-pins.py", 3,
        lambda r: [p.unlink() for p in (r / ".github/workflows").glob("*.yml")],
        "found zero action references")

    # The control. The pre-fix script is taken from origin/main, not retyped.
    with tempfile.TemporaryDirectory() as t:
        root = build(pathlib.Path(t), ["check-action-pins.py"])
        old = subprocess.run(
            ["git", "-C", str(W), "show", "origin/main:scripts/check-action-pins.py"],
            capture_output=True, text=True, timeout=60)
        if old.returncode != 0 or "def offenders" not in old.stdout:
            sys.exit("REFUSING: could not read the pre-fix script from "
                     "origin/main; the control would prove nothing.")
        io.open(root / "scripts" / "check-action-pins.py", "w",
                encoding="utf-8", newline="\n").write(old.stdout)
        for p in (root / ".github/workflows").glob("*.yml"):
            p.unlink()
        rc, out = run(root, "check-action-pins.py")
    blind = rc == 0
    RESULTS.append(("CONTROL: pre-fix script is blind to an empty tree", blind))
    print(f"  {'CONTROL: the pre-fix script on an empty tree':<52} rc={rc:<3} "
          f"want=0    {'-':>3}  {'PASS' if blind else 'FAIL'}")
    if blind:
        print(f"       it printed: {out.strip()[:70]!r}")
        print("       That is the defect: success, over a scan of nothing.")

    print()
    print("THE LIVE WORKTREE, AS IT STANDS")
    rc, out = run(build(pathlib.Path(tempfile.mkdtemp()), ["check-pins-doc.py"]),
                  "check-pins-doc.py")
    if rc == 0:
        print("  the ledger matches the workflows on this tree")
    else:
        print(f"  RED, rc={rc}. This is a real disagreement in the repository,")
        print("  not a prover failure. The lines it named:")
        for ln in out.splitlines():
            if ln.strip() and not ln.startswith("Fix:"):
                print(f"    {ln.rstrip()}")

    print()
    failed = [n for n, good in RESULTS if not good]
    if failed:
        print(f"SOMETHING DID NOT DISCRIMINATE - {len(failed)} arm(s):")
        for n in failed:
            print(f"  - {n}")
        return 1
    print(f"RESULT: all {len(RESULTS)} arms fire on exactly their own cause")
    return 0


if __name__ == "__main__":
    sys.exit(main())
