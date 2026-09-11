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
import re
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


def pin_rows(root: pathlib.Path) -> list[str]:
    """The pin table rows of the fixture's own copy of the document.

    Read, never hardcoded. Every value in that table is something the ledger
    commit changes, so a fixture that names one goes stale on the next pin bump
    and the arm it belongs to stops testing anything.
    """
    out, seen = [], False
    for line in (root / DOC_REL).read_text(encoding="utf-8").splitlines():
        if line.startswith("| Action |") and "Tag at that SHA" in line:
            seen = True
            continue
        if seen:
            if line.startswith("|---"):
                continue
            if not line.startswith("|"):
                break
            out.append(line)
    if len(out) < 2:
        sys.exit("REFUSING: fewer than two pin rows read from the fixture "
                 "document. The arms below would mutate nothing and pass.")
    return out


def adv_rows(root: pathlib.Path) -> list[str]:
    out, seen = [], False
    for line in (root / DOC_REL).read_text(encoding="utf-8").splitlines():
        if line.startswith("| Action |") and "Affected?" in line:
            seen = True
            continue
        if seen:
            if line.startswith("|---"):
                continue
            if not line.startswith("|"):
                break
            out.append(line)
    if len(out) < 2:
        sys.exit("REFUSING: fewer than two advisory rows read from the fixture "
                 "document. The arms below would mutate nothing and pass.")
    return out


def cells_of(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


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

    # The baseline reads the tree as it is. Earlier this arm had to CONSTRUCT
    # agreement, because the ledger was deliberately one commit behind the pins
    # and a baseline reading the tree as-is graded the repository rather than
    # the checker. Once the ledger is correct, constructing anything would hide
    # a real disagreement, so the construction is gone.
    arm("the doc and the workflows agree", "check-pins-doc.py", 0, None,
        "matches the workflows")

    def wrong_sha(r):
        row = pin_rows(r)[0]
        sha = cells_of(row)[2]
        sub(r, DOC_REL, row, row.replace(sha, "`" + "0" * 40 + "`"))

    arm("a workflow SHA differs from the ledger", "check-pins-doc.py", 1,
        wrong_sha, "records " + "0" * 40)

    def wrong_tag(r):
        row = pin_rows(r)[0]
        c = cells_of(row)
        sub(r, DOC_REL, row, row.replace("| " + c[3] + " |", "| v0.0.0-wrong |"))

    arm("a workflow TAG differs from the ledger", "check-pins-doc.py", 1,
        wrong_tag, "records v0.0.0-wrong")

    arm("an action is used but has NO ledger row", "check-pins-doc.py", 1,
        lambda r: sub(r, DOC_REL, pin_rows(r)[1] + "\n", ""),
        "has no row")

    def ghost_row(r):
        row = pin_rows(r)[0]
        ghost = ("| `acme/ghost-action` | `v1` | `" + "1" * 40 + "` | v1.0.0 |")
        sub(r, DOC_REL, row, ghost + "\n" + row)

    arm("a ledger row for an action nothing uses", "check-pins-doc.py", 1,
        ghost_row, "which no workflow uses")

    def two_commits(r):
        """Move ONE site of an action that has more than one site."""
        import collections
        rel = ".github/workflows/release.yml"
        text = (r / rel).read_text(encoding="utf-8")
        m = re.search(r"uses:\s*(\S+)@([0-9a-f]{40})", text)
        if not m:
            sys.exit("REFUSING: no pinned action found in " + rel)
        sub(r, rel, m.group(1) + "@" + m.group(2),
            m.group(1) + "@" + "2" * 40)

    arm("one action pinned to TWO different commits", "check-pins-doc.py", 1,
        two_commits, "more than one commit")

    arm("a pinned action has no advisory row", "check-pins-doc.py", 1,
        lambda r: sub(r, DOC_REL, adv_rows(r)[1] + "\n", ""),
        "no advisory row")

    def wrong_adv_version(r):
        row = adv_rows(r)[1]
        c = cells_of(row)
        sub(r, DOC_REL, row, row.replace("| " + c[5] + " |", "| v0.0.0-wrong |"))

    arm("the advisory row names a different version", "check-pins-doc.py", 1,
        wrong_adv_version, "not the one pinned")

    def blank_answer(r):
        row = adv_rows(r)[1]
        c = cells_of(row)
        sub(r, DOC_REL, row,
            "| " + c[0] + " |  | " + " | ".join(c[2:6]) + " |  |")

    arm("the advisory row leaves the answer blank", "check-pins-doc.py", 1,
        blank_answer, "records nothing")

    def break_date(r):
        """Break whatever date the document carries, not a date I typed here.

        The ledger commit rewrites this line to the day the advisory answers
        were obtained, so a fixture naming a literal date goes stale the first
        time the ledger is refreshed and the arm silently stops testing.
        """
        text = (r / DOC_REL).read_text(encoding="utf-8")
        m = re.search(r"\*\*Date:\*\*\s*\d{4}-\d{2}-\d{2}", text)
        if not m:
            sys.exit("REFUSING: no date line to break, so this arm would "
                     "mutate nothing and pass.")
        sub(r, DOC_REL, m.group(0), "**Date:** sometime")

    arm("the document records no query DATE", "check-pins-doc.py", 1,
        break_date, "no '**date:** yyyy-mm-dd' line")

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

    def rename_header(r):
        """Rename the SECOND column, whatever it is currently called.

        Not a literal header string: the header itself is something the ledger
        commit changes - `Was` became `Before pinning (2026-09-10)` so the column
        says when it was true. A fixture quoting the old spelling would silently
        mutate nothing and the arm would pass on an unmodified tree.
        """
        for line in (r / DOC_REL).read_text(encoding="utf-8").splitlines():
            if line.startswith("| Action |") and "Tag at that SHA" in line:
                c = cells_of(line)
                sub(r, DOC_REL, line,
                    "| " + c[0] + " | Renamed Column | " + " | ".join(c[2:]) + " |")
                return
        sys.exit("REFUSING: the pin table header was not found, so this arm "
                 "would mutate nothing and pass.")

    arm("the pin table header was renamed", "check-pins-doc.py", 3,
        rename_header, "found zero rows under the pin table header")

    print()
    print("scripts/check-action-pins.py")
    arm("every reference is a SHA with its tag", "check-action-pins.py", 0,
        None, "references scanned")

    def drop_tag_comment(r):
        rel = ".github/workflows/release.yml"
        text = (r / rel).read_text(encoding="utf-8")
        m = re.search(r"(\S+@[0-9a-f]{40})(\s*#\s*\S+)", text)
        if not m:
            sys.exit("REFUSING: no pinned reference with a tag comment in " + rel)
        sub(r, rel, m.group(0), m.group(1))

    arm("a reference lost its tag comment", "check-action-pins.py", 1,
        drop_tag_comment, "no trailing comment")

    def moving_tag(r):
        rel = ".github/workflows/release.yml"
        text = (r / rel).read_text(encoding="utf-8")
        m = re.search(r"(\S+)@[0-9a-f]{40}(\s*#\s*(\S+))", text)
        if not m:
            sys.exit("REFUSING: no pinned reference found in " + rel)
        sub(r, rel, m.group(0), m.group(1) + "@" + m.group(3) + m.group(2))

    arm("a reference went back to a moving tag", "check-action-pins.py", 1,
        moving_tag, "not a 40-character commit sha")

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
