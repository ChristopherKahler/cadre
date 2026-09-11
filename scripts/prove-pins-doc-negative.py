#!/usr/bin/env python3
"""The prover must REFUSE when pointed at a tree that does not have the fix.

A prover that runs happily against the pre-fix tree cannot tell fixed from
unfixed, so its green says nothing. This builds a tree from `origin/main` --
which has `check-action-pins.py` but no `check-pins-doc.py` -- points
`prove-pins-doc.py` at it, and requires a refusal.

Usage:  python3 prove-pins-doc-negative.py <worktree>
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

W = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                 else "/home/chriskahler/dev/curlew-wt-pins")
PROVER = W / "scripts" / "prove-pins-doc.py"
ENV = {k: v for k, v in os.environ.items()
       if k in ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP")}


def git(*args: str) -> str:
    p = subprocess.run(["git", "-C", str(W), *args], capture_output=True,
                       text=True, timeout=120)
    if p.returncode != 0:
        sys.exit(f"REFUSING: git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def main() -> int:
    if not PROVER.is_file():
        sys.exit(f"REFUSING: no prover at {PROVER}")

    with tempfile.TemporaryDirectory() as t:
        root = pathlib.Path(t) / "pre-fix"
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / "docs").mkdir(parents=True)
        (root / "scripts").mkdir(parents=True)
        for rel in (".github/workflows/tests.yml", ".github/workflows/release.yml",
                    "docs/ACTION-PINS.md", "scripts/check-action-pins.py"):
            (root / rel).write_text(git("show", f"origin/main:{rel}"),
                                    encoding="utf-8", newline="\n")

        # The assertion that makes this test mean something: the tree really is
        # the pre-fix one. If check-pins-doc.py were present, the refusal below
        # would be measuring nothing.
        if (root / "scripts" / "check-pins-doc.py").exists():
            sys.exit("REFUSING: origin/main already has check-pins-doc.py, so "
                     "this is no longer a pre-fix tree and proves nothing.")
        print(f"built a pre-fix tree from origin/main "
              f"{git('rev-parse', '--short=9', 'origin/main').strip()}")
        print("  it has check-action-pins.py and NO check-pins-doc.py")

        p = subprocess.run([sys.executable, str(PROVER), str(root)],
                           capture_output=True, text=True, env=ENV,
                           timeout=300, stdin=subprocess.DEVNULL)
        out = (p.stdout + p.stderr).strip()

    print()
    print("rc =", p.returncode)
    print(out[:400])
    print()
    refused = p.returncode != 0 and "REFUSING" in out
    print("VERDICT:", "correct - the prover refuses on a pre-fix tree"
          if refused else
          "WRONG - the prover ran anyway; it cannot tell fixed from unfixed")
    return 0 if refused else 1


if __name__ == "__main__":
    sys.exit(main())
