#!/usr/bin/env python3
"""Grade the acceptance scoreboard, so CI reds on a FAIL and stays green on a BLOCKED.

scripts/acceptance-e2e.py answers "can a real user install Cadre and USE it, on
this OS" and prints a scoreboard. Until issue #49 no workflow ran it, so that
scoreboard only existed when a person typed the command. The cost of that shows
up in the sibling script: scripts/e2e-test.sh:274 asserts a fixed MCP tool count
and its own comment records that the number had drifted, because nothing
executed the assertion between hand-runs.

This script is the SINGLE DECISION POINT for the `acceptance` job in
.github/workflows/tests.yml. The step that runs the harness deliberately does not
decide: if both the harness step and this one could turn the job red, the job's
colour comes from whichever fires first, which is the same
which-check-actually-decided-this ambiguity that #49 is about.

    python scripts/grade-acceptance.py acceptance.json --harness-rc 0

Exit 0 when the run is usable on this OS. BLOCKED and SKIP rows are printed with
the reason they carry and do NOT red the build: a genuinely-pending row is
information, not a regression, and a swallowed SKIP is indistinguishable from a
row that quietly vanished.

Exit 1, naming what happened, on any of:

  * a FAIL row
  * the summary file is missing, unreadable or not valid JSON
  * the summary says `"completed": false`
  * the summary carries no rows at all
  * the summary carries fewer rows than --min-rows
  * the harness exited non-zero while reporting no FAIL row
  * the counts in the summary disagree with the rows in the same summary

The third and sixth are the same defect caught twice, on purpose, and they are
why this reads two inputs rather than one. acceptance-e2e.py writes its JSON from
a `finally:` block, so a harness that dies half way through still leaves a
summary behind. Measured 2026-09-10: an exception injected after the first of 26
rows produced exit 1 and a file reading `1 pass, 0 fail` -- green-looking, from a
run that never got started. Trusting the exit code alone misses a `report()` that
stops returning 1; trusting the summary alone misses the crash. The harness now
also records `"completed"` so the file says so itself, for the readers that are
not this script.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PASS, FAIL, BLOCKED, SKIP = "PASS", "FAIL", "BLOCKED", "SKIP"
ORDER = (PASS, FAIL, BLOCKED, SKIP)


def _make_output_utf8_safe() -> None:
    """Never let the grader die printing someone else's text.

    Row names and reasons come from the harness, which echoes child process
    output. On Windows a redirected stdout is cp1252, so one non-ASCII byte in a
    reason would raise UnicodeEncodeError and take this grader down - which
    reads, to anyone looking at the run, exactly like the build failing. Same
    call and same reason as acceptance-e2e.py.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def load_summary(path: Path) -> tuple[dict | None, str | None]:
    """Return (summary, problem). Exactly one of the two is None."""
    if not path.exists():
        return None, (
            f"no acceptance summary at {path}. The harness writes this file from "
            f"a finally: block, so its absence means the harness never reached "
            f"that block - it was killed, or it never started.")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"could not read {path}: {exc}"
    if not raw.strip():
        return None, f"{path} is empty, so no scoreboard was recorded"
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, (
            f"{path} is not valid JSON ({exc}). A summary truncated mid-write is "
            f"the signature of a harness killed while reporting.")
    if not isinstance(data, dict):
        return None, f"{path} holds {type(data).__name__}, not a JSON object"
    return data, None


def grade(data: dict, harness_rc: int, min_rows: int) -> tuple[list[str], list[str]]:
    """Return (problems, notices). An empty problems list means exit 0."""
    problems: list[str] = []
    notices: list[str] = []

    rows = data.get("rows")
    if not isinstance(rows, list):
        problems.append(
            "the summary carries no 'rows' list, so there is no scoreboard here "
            "to grade")
        rows = []

    tally = {status: [r for r in rows if isinstance(r, dict)
                      and r.get("status") == status] for status in ORDER}
    unknown = [r for r in rows if isinstance(r, dict)
               and r.get("status") not in ORDER]

    # The harness sets this on its success path only. False means the summary
    # describes a run that stopped early, and every count in it is a floor rather
    # than a result - most importantly "fail": 0, which is the same value a
    # completely clean run writes.
    completed = data.get("completed")
    if completed is False:
        problems.append(
            "the summary says completed=false. The harness did not reach the end "
            "of its own report, so the counts below describe only the rows it got "
            "to, and a fail count of zero means 'it never got there', not "
            "'nothing failed'. The harness log has the traceback.")
    elif completed is None:
        notices.append(
            "NOTE     this summary predates the completed flag, so it cannot say "
            "whether the run finished. The harness exit code is the only evidence "
            "here that it did.")

    # A run that measured nothing is not a pass. This is the whole failure mode
    # #49 describes, one level up: a check that exists, looks authoritative, and
    # is never executed in the way that would make it authoritative.
    if not rows:
        problems.append(
            "the harness recorded zero rows. A green run that measured nothing "
            "is not evidence that anything works.")
    elif len(rows) < min_rows:
        problems.append(
            f"the harness recorded {len(rows)} rows, fewer than the floor of "
            f"{min_rows}. Rows have disappeared rather than failed, which no "
            f"FAIL count can show you. If the harness legitimately lost rows, "
            f"lower --min-rows in the acceptance job deliberately and say why.")

    for row in unknown:
        problems.append(
            f"row {row.get('name', '(unnamed)')!r} carries status "
            f"{row.get('status')!r}, which is not one of {', '.join(ORDER)}")

    # The counts the harness wrote about itself must match the rows it wrote.
    # They are computed from the same list, so a disagreement means the summary
    # was edited after the fact or written by something other than this harness.
    for status in ORDER:
        key = status.lower()
        if key in data and data[key] != len(tally[status]):
            problems.append(
                f"the summary says {data[key]} {key} but carries "
                f"{len(tally[status])} {status} rows. The counts and the rows "
                f"disagree, so at least one of them is not describing this run.")

    if tally[FAIL]:
        problems.append(
            f"{len(tally[FAIL])} FAIL row(s). These are things that should work "
            f"on this OS and do not:")
        for row in tally[FAIL]:
            problems.append(f"    FAIL  {row.get('name', '(unnamed)')}")
            for line in str(row.get("detail", "")).splitlines():
                if line.strip():
                    problems.append(f"          {line[:150]}")

    # Two ways the exit code and the scoreboard can contradict each other, and
    # both matter. Neither is reachable by reading one of them.
    if harness_rc != 0 and not tally[FAIL]:
        problems.append(
            f"the harness exited {harness_rc} but recorded no FAIL row. It did "
            f"not finish: the summary comes from a finally: block and describes "
            f"only the rows reached before it died. Read the harness log above "
            f"for the traceback or the signal.")
    if harness_rc == 0 and tally[FAIL]:
        problems.append(
            f"the harness exited 0 while recording {len(tally[FAIL])} FAIL "
            f"row(s). Its own report() is supposed to return 1 on any FAIL, so "
            f"the exit code is no longer telling the truth about the scoreboard.")

    # Printed, never swallowed. A BLOCKED row names an open defect and a SKIP
    # row names a fact about the host; a reader who cannot see them cannot tell
    # either one from a row that silently stopped being emitted.
    for status in (BLOCKED, SKIP):
        for row in tally[status]:
            notices.append(f"{status:<8} {row.get('name', '(unnamed)')}")
            for line in str(row.get("detail", "")).splitlines():
                if line.strip():
                    notices.append(f"         {line[:150]}")

    return problems, notices


def write_step_summary(data: dict, problems: list[str], min_rows: int) -> None:
    """Put the scoreboard in the GitHub run summary, so the numbers outlive the log.

    Without this the acceptance numbers exist only inside a step log that ages
    out, which is a smaller version of the problem #49 is about.
    """
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    rows = [r for r in (data.get("rows") or []) if isinstance(r, dict)]
    counts = {s: sum(1 for r in rows if r.get("status") == s) for s in ORDER}
    verdict = "FAILED" if problems else "usable on this OS"
    if data.get("completed") is False:
        verdict = "FAILED - the run stopped early, these counts are partial"
    lines = [
        f"### Acceptance: {data.get('platform', 'unknown platform')} "
        f"(python {data.get('python', '?')}) - {verdict}",
        "",
        f"{counts[PASS]} pass, {counts[FAIL]} fail, {counts[BLOCKED]} blocked, "
        f"{counts[SKIP]} skip, {len(rows)} rows (floor {min_rows})",
        "",
        "| status | row |",
        "| --- | --- |",
    ]
    for row in rows:
        name = str(row.get("name", "(unnamed)")).replace("|", "\\|")
        lines.append(f"| {row.get('status')} | {name} |")
    lines.append("")
    try:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        # A summary we cannot write is not a reason to fail a build.
        pass


def main() -> int:
    _make_output_utf8_safe()
    ap = argparse.ArgumentParser(
        description="Grade a scripts/acceptance-e2e.py --json summary.")
    ap.add_argument("summary", help="the --json file the harness wrote")
    ap.add_argument("--harness-rc", type=int, required=True,
                    help="the exit code the harness itself returned")
    ap.add_argument("--min-rows", type=int, default=1,
                    help="floor on the number of rows; guards against a "
                         "scoreboard that quietly shrank (default 1)")
    a = ap.parse_args()

    path = Path(a.summary)
    data, problem = load_summary(path)
    if problem is not None:
        print("ACCEPTANCE FAILED", file=sys.stderr)
        print(f"  {problem}", file=sys.stderr)
        print(f"  harness exit code was {a.harness_rc}", file=sys.stderr)
        return 1

    assert data is not None
    problems, notices = grade(data, a.harness_rc, a.min_rows)
    rows = [r for r in (data.get("rows") or []) if isinstance(r, dict)]
    counts = {s: sum(1 for r in rows if r.get("status") == s) for s in ORDER}

    completed = data.get("completed")
    print(f"acceptance summary: {path}")
    print(f"  platform      {data.get('platform', '?')}")
    print(f"  python        {data.get('python', '?')}")
    print(f"  harness rc    {a.harness_rc}")
    print("  completed     " + {True: "yes", False: "NO - the run stopped early",
                                None: "not recorded"}[completed
                                                       if completed in (True, False)
                                                       else None])
    print(f"  scoreboard    {counts[PASS]} pass, {counts[FAIL]} fail, "
          f"{counts[BLOCKED]} blocked, {counts[SKIP]} skip "
          f"({len(rows)} rows, floor {a.min_rows})")
    if data.get("pythonioencoding"):
        print(f"  NOTE: PYTHONIOENCODING was {data['pythonioencoding']} for that "
              f"run, which masks locale-encoding defects.")

    if notices:
        print()
        print("Rows that are not a pass and are not a failure:")
        for line in notices:
            print(f"  {line}")

    write_step_summary(data, problems, a.min_rows)

    if problems:
        print(file=sys.stderr)
        print("ACCEPTANCE FAILED", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1

    print()
    print("acceptance is usable on this OS: no FAIL row, and the harness "
          "finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
