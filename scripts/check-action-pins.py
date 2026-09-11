#!/usr/bin/env python3
"""Every GitHub Action in this repository names a commit, not a moving ref.

A tag is mutable. Whoever controls an action can move `v4` onto different code,
and every workflow here picks that up on its next run with no diff for anyone to
review. A branch reference, which `release.yml` used for the PyPI publish step,
moves on every commit its maintainer pushes. A commit SHA cannot be moved.

Run it directly, or let the `action-pins` job in tests.yml run it:

    python scripts/check-action-pins.py

Exit 0 when every reference is a 40-character SHA carrying its resolved tag in a
trailing comment. Exit 1, naming each offender, otherwise.

This check FAILS. That matters here: `main` carries no branch protection at all
(measured 2026-09-10 — `GET /branches/main/protection` returns 404, the rulesets
list is empty), so nothing in GitHub blocks a merge on a red check. What blocks a
merge is the repo's own gate reading the check runs, and it can only read a run
that actually went red. A check wired `continue-on-error` would be invisible to
it, which is the shape of instrument this repository has spent a day removing.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

#: `- uses: owner/repo@ref` plus whatever trailing comment follows.
USES = re.compile(
    r"^\s*(?:-\s*)?uses:\s*(?P<action>[^@\s]+)@(?P<ref>\S+)(?P<rest>.*)$")

#: Exit code for "the scan found nothing", kept distinct from a real offence so
#: a caller can tell a check that failed from a check that never ran.
RC_NOTHING_SCANNED = 3

SHA = re.compile(r"^[0-9a-f]{40}$")
TAG_COMMENT = re.compile(r"#\s*(?P<tag>\S+)")


def offenders() -> tuple[list[str], int]:
    """Returns (problems, how many action references were actually seen).

    The count is not decoration. An empty set satisfies every assertion made
    about its members, so a version of this that returned only the problem list
    printed "every action reference names a commit SHA with its tag" after
    scanning nothing at all — a workflow reformat that stopped this regex
    matching, or a path change that emptied the glob, read as success. The
    caller refuses on a count of zero. The count is the evidence; the verdict
    on its own is not.
    """
    found: list[str] = []
    seen = 0
    for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            m = USES.match(line)
            if not m:
                continue
            seen += 1
            action, ref, rest = m.group("action"), m.group("ref"), m.group("rest")
            where = f"{path.relative_to(REPO_ROOT)}:{lineno}  {action}@{ref}"
            if not SHA.match(ref):
                found.append(f"{where}\n    not a 40-character commit SHA")
                continue
            if not TAG_COMMENT.search(rest):
                found.append(
                    f"{where}\n    pinned, but no trailing comment naming the "
                    f"tag it resolved from — the refresh mechanism and the next "
                    f"human both read that comment")
    return found, seen


def main() -> int:
    if not WORKFLOWS.is_dir():
        print(f"no workflows directory at {WORKFLOWS}", file=sys.stderr)
        return 1

    bad, seen = offenders()

    if seen == 0:
        print(f"found ZERO action references under {WORKFLOWS}.\n"
              f"Every assertion below is made about an empty set, so it would "
              f"pass.\nEither the glob stopped matching or the uses: shape "
              f"changed. Refusing rather than reporting success.",
              file=sys.stderr)
        return RC_NOTHING_SCANNED

    if bad:
        print("GitHub Actions are not pinned to commits:\n", file=sys.stderr)
        for item in bad:
            print(f"  {item}\n", file=sys.stderr)
        print(
            "Fix: resolve the ref to its current commit SHA and write the tag\n"
            "beside it, for example\n"
            "    uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
            "  # v4.4.0\n"
            "Record the advisory check for the new SHA in docs/ACTION-PINS.md.",
            file=sys.stderr)
        return 1

    print(f"every action reference names a commit SHA with its tag "
          f"({seen} references scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
