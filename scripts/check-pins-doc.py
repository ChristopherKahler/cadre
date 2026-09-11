#!/usr/bin/env python3
"""`docs/ACTION-PINS.md` must still describe the workflows it claims to describe.

`scripts/check-action-pins.py` checks SHAPE: every reference is a 40-character
commit SHA carrying its tag in a trailing comment. Its docstring says so, and
that is the whole of what it can say. It cannot tell whether the pin ledger still
matches the pins, and it cannot tell whether one action is pinned to two
different commits in the same file. Both were live on 2026-09-11: five Dependabot
pull requests moved five pins and not one of them touched the ledger, and the
acceptance job landed carrying its own copy of two pins that a later bump to the
other sites would not have reached.

So this reads the two tables out of the document, reads every `uses:` line out of
the workflows, and fails when they disagree.

    python scripts/check-pins-doc.py

Exit 0 when they agree. Exit 1, naming every disagreement. Exit 3 when the scan
found nothing at all to check.

WHAT A GREEN HERE MEANS, AND WHAT IT DOES NOT
---------------------------------------------
The pin table is fully checkable, both directions. Every `owner/repo@sha  # tag`
in the workflows must appear as a row with the same SHA and the same tag, and
every row must correspond to something the workflows actually use. A stale row is
as wrong as a missing one.

The advisory table is checkable for COVERAGE, not for VERDICT. No script can
confirm that "no advisory" was true on the day somebody wrote it down. It can
confirm that every pinned action has a row, that the row names the version that
is actually pinned, and that the document records the query and the date the
answers came from.

**The check enforces that the record EXISTS and is COMPLETE. It does not and
cannot enforce that the record is TRUE.** Read a green here as "somebody looked
and wrote down what they found", never as "the answers were re-verified". That
distinction is the document's own reason for existing:

    A recorded negative is the point. "No advisory" with no query and no date
    beside it is indistinguishable from nobody having looked.

There is no network call here on purpose. A check that needs the internet goes
red for reasons that have nothing to do with the change under review.

AND IT MUST BE ABLE TO FAIL ON NOTHING
--------------------------------------
An empty set satisfies every assertion made about its members. If the glob stops
matching, or a reformat stops the `uses:` regex matching, a checker with no floor
reports success over a scan of zero things. So the counts are printed and a scan
that finds nothing REFUSES with its own exit code. The count is the evidence; the
verdict on its own is not.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
DOC = REPO_ROOT / "docs" / "ACTION-PINS.md"

#: Exit code for "the scan found nothing", kept distinct from a disagreement so
#: a caller can tell a real red from a check that never ran.
RC_NOTHING_SCANNED = 3

USES = re.compile(
    r"^\s*(?:-\s*)?uses:\s*(?P<action>[^@\s]+)@(?P<sha>[0-9a-f]{40})\s*#\s*(?P<tag>\S+)")

#: The two tables are found by their HEADER TEXT and read by COLUMN NAME, never
#: by counting cells from either end. A column inserted in the middle then moves
#: nothing silently: it either still has the named column or this refuses.
PIN_HEAD = ("Action", "Before pinning (2026-09-10)", "Now", "Tag at that SHA")
ADV_HEAD = ("Action", "Advisory", "Severity", "Vulnerable range",
            "First patched", "Pinned version", "Affected?")


def cells(line: str) -> list[str]:
    s = line.strip()
    if not s.startswith("|"):
        return []
    return [c.strip() for c in s.strip("|").split("|")]


def read_table(lines: list[str], header: tuple[str, ...]) -> list[dict[str, str]]:
    """Rows of the table whose header row is exactly `header`, keyed by column."""
    for i, line in enumerate(lines):
        if tuple(cells(line)) != header:
            continue
        rows: list[dict[str, str]] = []
        for row in lines[i + 2:]:          # +2 skips the |---|---| rule
            c = cells(row)
            if len(c) != len(header):
                break
            rows.append(dict(zip(header, c)))
        return rows
    return []


def bare(s: str) -> str:
    """A cell as written, minus the backticks and bold markers around it."""
    return s.strip().strip("`").replace("**", "").strip()


def workflow_pins() -> dict[str, set[tuple[str, str, str]]]:
    """Every pin in force, as action -> {(sha, tag, file:line)}."""
    found: dict[str, set[tuple[str, str, str]]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            m = USES.match(line)
            if m:
                found.setdefault(m.group("action"), set()).add((
                    m.group("sha"), m.group("tag"),
                    f"{path.relative_to(REPO_ROOT)}:{lineno}"))
    return found


def check() -> tuple[list[str], int, int, int]:
    """Returns (problems, actions scanned, sites scanned, doc rows read)."""
    if not WORKFLOWS.is_dir():
        return ([f"no workflows directory at {WORKFLOWS}"], 0, 0, 0)
    if not DOC.is_file():
        return ([f"no pin ledger at {DOC}"], 0, 0, 0)

    live = workflow_pins()
    sites = sum(len(v) for v in live.values())
    lines = DOC.read_text(encoding="utf-8").splitlines()
    pin_rows = read_table(lines, PIN_HEAD)
    adv_rows = read_table(lines, ADV_HEAD)

    # The floor, before any comparison. An empty scan agrees with everything.
    if sites == 0:
        return ([f"found ZERO pinned actions under {WORKFLOWS}. Either the glob "
                 f"matches nothing or the uses: shape changed. A checker that "
                 f"finds nothing to check has checked nothing, so this refuses "
                 f"rather than reporting agreement."], 0, 0, len(pin_rows))
    if not pin_rows:
        return ([f"found ZERO rows under the pin table header {PIN_HEAD} in "
                 f"{DOC.name}. Either the table moved or its columns were "
                 f"renamed; either way this check is blind and refuses."],
                len(live), sites, 0)
    if not adv_rows:
        return ([f"found ZERO rows under the advisory table header in "
                 f"{DOC.name}. The coverage half of this check is blind."],
                len(live), sites, len(pin_rows))

    pins = {bare(r["Action"]): (bare(r["Now"]), bare(r["Tag at that SHA"]))
            for r in pin_rows}
    adv = {bare(r["Action"]): r for r in adv_rows}
    bad: list[str] = []

    # 1. One action, one commit. check-action-pins.py cannot see this, because
    #    two different SHAs are both valid SHAs with tags beside them.
    for action, uses in sorted(live.items()):
        if len({sha for sha, _t, _w in uses}) > 1:
            detail = "\n".join(f"      {s}  # {t}   {w}"
                               for s, t, w in sorted(uses))
            bad.append(f"{action} is pinned to more than one commit:\n{detail}\n"
                       f"    A bump that moves one site and not the others "
                       f"leaves the workflows running two versions of it.")

    # 2. Every pin in force is described by the ledger, same SHA and same tag.
    for action, uses in sorted(live.items()):
        if action not in pins:
            bad.append(f"{action} is used at "
                       f"{sorted(w for _s, _t, w in uses)[0]} and has no row in "
                       f"{DOC.name}.")
            continue
        d_sha, d_tag = pins[action]
        for sha, tag, where in sorted(uses):
            if sha != d_sha:
                bad.append(f"{action} at {where} is pinned to {sha}, but "
                           f"{DOC.name} records {d_sha}.")
            if tag != d_tag:
                bad.append(f"{action} at {where} says it is {tag}, but "
                           f"{DOC.name} records {d_tag}.")

    # 3. No ledger row for a pin nothing uses any more.
    for action in sorted(pins):
        if action not in live:
            bad.append(f"{DOC.name} has a row for {action}, which no workflow "
                       f"uses. A row for a pin that is gone leaves the next "
                       f"reader believing something false.")

    # 4. Advisory COVERAGE. Not the verdict — see the module docstring.
    for action, (_sha, tag) in sorted(pins.items()):
        row = adv.get(action)
        if row is None:
            bad.append(f"{action} is pinned but has no advisory row. A pin with "
                       f"no recorded answer is indistinguishable from one "
                       f"nobody looked at.")
            continue
        if bare(row["Pinned version"]) != tag:
            bad.append(f"{action}: the pin table says {tag}, the advisory row "
                       f"says {bare(row['Pinned version'])}. The recorded answer "
                       f"belongs to a version that is not the one pinned.")
        for col in ("Advisory", "Affected?"):
            if not bare(row[col]) or bare(row[col]) in ("-", "—"):
                bad.append(f"{action}: the advisory row leaves {col!r} blank. "
                           f"An empty answer records nothing.")

    # 5. The document must say WHEN and WITH WHAT the answers were obtained.
    text = "\n".join(lines)
    if not re.search(r"\*\*Date:\*\*\s*\d{4}-\d{2}-\d{2}", text):
        bad.append(f"{DOC.name} has no '**Date:** YYYY-MM-DD' line. An answer "
                   f"with no date beside it cannot be told from no answer.")
    if "**Advisory query:**" not in text:
        bad.append(f"{DOC.name} does not record the advisory query it ran.")

    return bad, len(live), sites, len(pin_rows)


def main() -> int:
    bad, actions, sites, rows = check()
    if bad:
        print(f"{DOC.name} and the workflows disagree "
              f"({actions} actions, {sites} sites, {rows} ledger rows scanned):\n",
              file=sys.stderr)
        for item in bad:
            print(f"  {item}\n", file=sys.stderr)
        print("Fix: when a pin moves, update BOTH tables in "
              "docs/ACTION-PINS.md in the same pull request, and move every "
              "site of that action together. The document says so itself.",
              file=sys.stderr)
        return RC_NOTHING_SCANNED if sites == 0 or rows == 0 else 1

    print(f"{DOC.name} matches the workflows: {actions} actions across {sites} "
          f"sites, one commit each, {rows} ledger rows, advisory row per pin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
