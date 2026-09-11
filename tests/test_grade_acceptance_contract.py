"""The grader and the harness must agree about what an exit code means.

`scripts/grade-acceptance.py` is the SINGLE DECISION POINT for the acceptance
job, and until this file nothing tested it. That is the same shape as the defect
it was written to catch: a check that exists, looks authoritative, and is never
itself executed in the way that would make it authoritative.

WHAT CHANGED AND WHY IT HAD TO CHANGE TOGETHER. acceptance-e2e.py's `report()`
used to return 0 for a run that skipped a whole section, so a run that measured
eighteen fewer rows exited clean. It now answers three ways:

    0  every row measured, nothing skipped
    1  at least one FAIL row
    2  no FAIL, but rows were SKIPPED -- this host established less
    3  the harness REFUSED to start

The grader read any non-zero with no FAIL row as "it did not finish", so exit 2
would have turned an honest host skip into a red build carrying a FALSE
explanation. Changing one without the other ships a break; that is why the
harness and its only consumer move in one commit.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GRADER = REPO / "scripts" / "grade-acceptance.py"


def _grader():
    spec = importlib.util.spec_from_file_location("grade_acceptance", GRADER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


g = _grader()


def _rows(passes: int = 20, fails: int = 0, skips: int = 0,
          blocked: int = 0) -> list[dict]:
    out = [{"status": "PASS", "name": f"p{i}", "detail": ""} for i in range(passes)]
    out += [{"status": "FAIL", "name": f"f{i}", "detail": "broke"} for i in range(fails)]
    out += [{"status": "SKIP", "name": f"s{i}", "detail": "no base here"}
            for i in range(skips)]
    out += [{"status": "BLOCKED", "name": f"b{i}", "detail": "known"} for i in range(blocked)]
    return out


def _summary(rows: list[dict], completed: bool = True) -> dict:
    counts = {s.lower(): sum(1 for r in rows if r["status"] == s)
              for s in ("PASS", "FAIL", "BLOCKED", "SKIP")}
    return {"platform": "linux", "python": "3.12.3", "rows": rows,
            "completed": completed, **counts}


def _problems(rows: list[dict], rc: int, completed: bool = True,
              min_rows: int = 1) -> list[str]:
    problems, _ = g.grade(_summary(rows, completed), rc, min_rows)
    return problems


# ---------------------------------------------------------------------------
# the three codes the harness actually returns
# ---------------------------------------------------------------------------

def test_a_clean_run_at_rc_0_is_green():
    assert _problems(_rows(), 0) == []


def test_a_skipped_run_at_rc_2_is_GREEN():
    """A host that cannot check a section is information, not a regression.

    If this reds, the next person deletes the SKIP row rather than the cause,
    and the harness goes back to hiding what it did not measure.
    """
    assert _problems(_rows(skips=5), 2) == []


def test_a_skipped_run_at_rc_2_says_so_in_the_notices():
    _, notices = g.grade(_summary(_rows(skips=5)), 2, 1)
    joined = "\n".join(notices)
    assert "SKIPPED" in joined
    assert "exited 2" in joined


def test_a_failing_run_at_rc_1_is_red_and_names_the_rows():
    problems = _problems(_rows(fails=2), 1)
    assert problems
    assert any("FAIL row(s)" in p for p in problems)


# ---------------------------------------------------------------------------
# every way the exit code and the scoreboard can contradict each other
# ---------------------------------------------------------------------------

def test_rc_0_while_rows_were_skipped_is_red():
    """THE DEFECT THIS CHANGE FIXES, asserted from the grader's side.

    Before, `report()` returned 0 over a skipped section and the grader had no
    opinion about it, so a run that measured eighteen fewer rows was green at
    both ends.
    """
    problems = _problems(_rows(skips=5), 0)
    assert problems
    assert any("returns 2 when anything was skipped" in p for p in problems)


def test_rc_0_while_a_fail_row_exists_is_red():
    problems = _problems(_rows(fails=1), 0)
    assert any("exited 0 while recording" in p for p in problems)


def test_rc_1_with_no_fail_row_means_the_harness_died():
    problems = _problems(_rows(), 1)
    assert any("did not reach its own report" in p for p in problems)


def test_rc_2_with_no_skip_row_is_a_contradiction():
    problems = _problems(_rows(), 2)
    assert any("describing different runs" in p for p in problems)


def test_rc_3_is_reported_as_a_refusal_not_a_crash():
    """The harness exits 3 when its isolation check cannot see what it watches.

    That is a deliberate refusal and the operator needs to be told which path
    could not be resolved, not that something crashed.
    """
    problems = _problems(_rows(), 3)
    assert any("REFUSED to run" in p for p in problems)


def test_an_exit_code_report_never_returns_is_red():
    problems = _problems(_rows(), 137)
    assert any("which report() never returns" in p for p in problems)


def test_rc_2_is_never_described_as_a_harness_that_did_not_finish():
    """The RED ARM for this whole file.

    The old blanket rule would have produced exactly this sentence for exit 2.
    A red build with a false explanation sends the reader hunting a crash that
    never happened.
    """
    problems, notices = g.grade(_summary(_rows(skips=5)), 2, 1)
    assert not any("did not finish" in line for line in problems + notices)


# ---------------------------------------------------------------------------
# the checks that were already there must keep working
# ---------------------------------------------------------------------------

def test_a_summary_that_stopped_early_is_still_red():
    problems = _problems(_rows(), 1, completed=False)
    assert any("completed=false" in p for p in problems)


def test_a_scoreboard_below_the_row_floor_is_still_red():
    problems = _problems(_rows(passes=3), 0, min_rows=20)
    assert any("fewer than the floor" in p for p in problems)


def test_counts_that_disagree_with_the_rows_are_still_red():
    summary = _summary(_rows(skips=5))
    summary["skip"] = 99
    problems, _ = g.grade(summary, 2, 1)
    assert any("disagree" in p for p in problems)


# ---------------------------------------------------------------------------
# end to end, through the real command line
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rows,rc,want", [
    (_rows(), 0, 0),
    (_rows(skips=5), 2, 0),
    (_rows(skips=5), 0, 1),
    (_rows(fails=1), 1, 1),
    (_rows(blocked=2), 4, 0),
    (_rows(blocked=2), 0, 1),
])
def test_the_grader_exits_the_way_ci_will_read_it(tmp_path, rows, rc, want):
    """Through argv and the process exit code, which is what the job reads."""
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps(_summary(rows)), encoding="utf-8")
    p = subprocess.run([sys.executable, str(GRADER), str(path),
                        "--harness-rc", str(rc), "--min-rows", "1"],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == want, (p.stdout + p.stderr)[:1000]
# ---------------------------------------------------------------------------
# rc 4 -- a known defect BLOCKED rows
#
# THE DEFECT THIS CLOSES. report() returned 0 for a run whose only non-PASS
# rows were BLOCKED, and the grader had no rc-0-with-BLOCKED check, so the
# contradiction read GREEN in both instruments. That is the one mismatch
# neither half could catch, which is exactly why it survived.
# ---------------------------------------------------------------------------

def test_a_blocked_run_at_rc_4_is_GREEN():
    """CI stays green on BLOCKED. The code exists to stop the lie, not to red.

    A BLOCKED row names an open defect that is already tracked. Failing the
    build here punishes the harness for being honest and teaches the next
    person to delete the row instead of fixing the defect.
    """
    assert _problems(_rows(blocked=2), 4) == []


def test_a_blocked_run_at_rc_4_says_so_in_the_notices():
    _, notices = g.grade(_summary(_rows(blocked=2)), 4, 1)
    assert any("exited 4" in n for n in notices)
    assert any("2 row(s) were BLOCKED" in n for n in notices)


def test_rc_0_while_a_blocked_row_exists_is_red():
    """THE ARM FOR THE WHOLE CHANGE, and it was red before report() learned 4."""
    problems = _problems(_rows(blocked=2), 0)
    assert any("exited 0 while recording 2 BLOCKED" in p for p in problems)


def test_rc_4_with_no_blocked_row_is_a_contradiction():
    """The other direction, or the test above is satisfied by a rule that
    never fires."""
    problems = _problems(_rows(), 4)
    assert any("describing different runs" in p for p in problems)


def test_rc_4_is_not_reported_as_an_impossible_exit_code():
    """Before the grader learned 4 it fell through to the catch-all and called
    a legitimate exit a harness that was killed. A red build with a false
    explanation sends the reader hunting a crash that never happened."""
    problems = _problems(_rows(blocked=2), 4)
    assert not any("never returns" in p for p in problems)


def test_rc_4_is_never_described_as_a_harness_that_did_not_finish():
    problems, notices = g.grade(_summary(_rows(blocked=2)), 4, 1)
    assert not any("did not finish" in line for line in problems + notices)
