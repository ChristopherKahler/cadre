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

DOC_REL = "docs/ACTION-PINS.md"


def _root() -> pathlib.Path:
    """The tree to grade. Derived, never hard-coded.

    This used to fall back to one developer's absolute worktree path. Run from
    any other checkout with no argument, it would have graded THAT tree and
    reported all arms passing about a repository the caller was not asking
    about - a green over the wrong thing, which is the defect family these
    provers exist to catch. Same class as #64 and as the fallback godwit
    removed from scripts/verify/verify_packaging.py.

    The path is not quoted here on purpose. A comment naming the bad literal is
    still that literal in the file, and the next grep for it cannot tell an
    explanation from an offence.

    An explicit argument, or CADRE_PROVER_ROOT. Nothing else. No default and no
    fallback, not even one derived from __file__: a default is a way for this to
    run against a tree nobody named, and the whole defect was running against a
    tree nobody named. If neither is given, REFUSE. If what is given is not a
    tree these provers can grade, REFUSE rather than assert about it.

    Note the empty-string case. An environment variable set to "" is SET, so a
    plain lookup with a default would sail past it and hand back a useless root.
    It is treated as absent here, on purpose.
    """
    env = (os.environ.get("CADRE_PROVER_ROOT") or "").strip()
    if len(sys.argv) > 1 and sys.argv[1].strip():
        root, how = pathlib.Path(sys.argv[1]), "argument"
    elif env:
        root, how = pathlib.Path(env), "CADRE_PROVER_ROOT"
    else:
        sys.exit("REFUSING: no worktree root given. Pass it as the first "
                 "argument or set CADRE_PROVER_ROOT. There is deliberately no "
                 "default - a prover that picks its own tree can report all "
                 "arms passing about a repository nobody asked about.")
    root = root.resolve()
    missing = [p for p in (DOC_REL, ".github/workflows", "scripts")
               if not (root / p).exists()]
    if missing:
        sys.exit(f"REFUSING: {root} ({how}) is not a tree these provers can "
                 f"grade - missing {missing}. Pass the worktree as an argument "
                 f"or set CADRE_PROVER_ROOT.")
    return root


W = _root()
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
