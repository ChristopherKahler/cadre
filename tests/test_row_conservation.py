"""The acceptance scoreboard conserves its rows, and its exit code says so.

THE DEFECT. `scripts/acceptance-e2e.py` has a base-engine section with eighteen
rows and several arms that run none of them. The skipped arms recorded ONE row
and dropped the other seventeen: they did not fail, they ceased to exist. The
same harness therefore wrote a forty-one-row scoreboard on a host with a native
`base` and a twenty-three-row scoreboard on a host without one, and nothing in
the shorter one said the difference was eighteen unmeasured rows rather than a
smaller job. `report()` then returned 0, so the shorter run also EXITED CLEAN.

A row that is absent says nothing at all. A SKIP row says what this host could
not check and why. The two are not close, and only one of them survives being
read by a script.

Measured after the fix, both arms of the same harness on the same machine:

    run A (native base)   exit 0   41 rows   18/18 base rows PASS
    run B (no base)       exit 2   41 rows   18/18 base rows SKIP

Same forty-one rows, no name present in one and absent from the other.

These tests are the cheap half. The expensive half is
scripts/verify/verify_row_conservation.sh, which runs both arms for real and
then breaks the guard to prove it can go red.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HARNESS = REPO / "scripts" / "acceptance-e2e.py"


def _harness():
    """Import the harness by path. Its filename has a hyphen in it."""
    spec = importlib.util.spec_from_file_location("acceptance_e2e", HARNESS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


h = _harness()


# ---------------------------------------------------------------------------
# the declared list
# ---------------------------------------------------------------------------

def test_the_declared_row_list_has_no_duplicates():
    """A repeated name makes `skip_rest` silently emit one row for two rows."""
    names = h.BASE_SECTION_ROWS
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"BASE_SECTION_ROWS repeats: {dupes}"


def test_every_declared_name_appears_in_the_harness_source():
    """The list is a spec, not a wish. A name nothing emits is already drift."""
    src = HARNESS.read_text(encoding="utf-8")
    for name in h.BASE_SECTION_ROWS:
        assert src.count(name) >= 2, (
            f"{name!r} is declared in BASE_SECTION_ROWS but appears nowhere else "
            f"in the harness, so no path can ever emit it")


# ---------------------------------------------------------------------------
# skip_rest
# ---------------------------------------------------------------------------

def test_skip_rest_fills_only_what_is_missing():
    b = h.Board()
    b.add(h.PASS, "one")
    b.skip_rest(["one", "two", "three"], "because")
    assert [(r["name"], r["status"]) for r in b.rows] == [
        ("one", h.PASS), ("two", h.SKIP), ("three", h.SKIP)]


def test_skip_rest_emits_in_declared_order():
    """So a skipped run and a full run put the same names in the same places."""
    b = h.Board()
    b.skip_rest(["a", "b", "c"], "because")
    assert [r["name"] for r in b.rows] == ["a", "b", "c"]


def test_skip_rest_carries_the_reason_onto_every_row():
    b = h.Board()
    b.skip_rest(["a", "b"], "no base on this host")
    assert all("no base on this host" in r["detail"] for r in b.rows)


# ---------------------------------------------------------------------------
# assert_section — the half that keeps the list honest
# ---------------------------------------------------------------------------

def test_assert_section_is_silent_when_the_rows_match():
    b = h.Board()
    start = len(b.rows)
    for n in ("a", "b"):
        b.add(h.PASS, n)
    b.assert_section(start, ["a", "b"], "demo")
    assert b.count(h.FAIL) == 0


def test_assert_section_fails_on_a_declared_row_that_was_never_emitted():
    """The original defect, aimed at one row instead of a whole section."""
    b = h.Board()
    start = len(b.rows)
    b.add(h.PASS, "a")
    b.assert_section(start, ["a", "b"], "demo")
    assert b.count(h.FAIL) == 1
    assert "DECLARED BUT NEVER EMITTED: b" in b.rows[-1]["detail"]


def test_assert_section_fails_on_a_row_the_list_does_not_declare():
    """Drift is symmetric: a row added to the code and not the list is drift."""
    b = h.Board()
    start = len(b.rows)
    for n in ("a", "b"):
        b.add(h.PASS, n)
    b.assert_section(start, ["a"], "demo")
    assert b.count(h.FAIL) == 1
    assert "EMITTED BUT NOT DECLARED: b" in b.rows[-1]["detail"]


def test_assert_section_fails_on_a_row_emitted_twice():
    b = h.Board()
    start = len(b.rows)
    for n in ("a", "a"):
        b.add(h.PASS, n)
    b.assert_section(start, ["a"], "demo")
    assert b.count(h.FAIL) == 1
    assert "EMITTED TWICE: a" in b.rows[-1]["detail"]


def test_assert_section_ignores_rows_recorded_before_the_section():
    """`start` is an index, not a name filter, so earlier sections do not leak in."""
    b = h.Board()
    b.add(h.PASS, "an earlier section's row")
    start = len(b.rows)
    b.add(h.PASS, "a")
    b.assert_section(start, ["a"], "demo")
    assert b.count(h.FAIL) == 0


# ---------------------------------------------------------------------------
# the exit code
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("statuses,want", [
    ([], 0),
    ([("PASS",)], 0),
    ([("PASS",), ("SKIP",)], 2),
    ([("PASS",), ("FAIL",)], 1),
    ([("PASS",), ("FAIL",), ("SKIP",)], 1),
    # THIS CASE USED TO EXPECT 0, and that expectation was the defect rather
    # than an oversight: it PINNED report() returning "every row measured,
    # nothing skipped" for a run a known defect had stopped. Anyone fixing
    # report() would have watched this line go red and could reasonably have
    # concluded the fix was wrong. Changed on purpose, with the reason here.
    ([("PASS",), ("BLOCKED",)], 4),
    ([("PASS",), ("BLOCKED",), ("SKIP",)], 4),
    ([("PASS",), ("FAIL",), ("BLOCKED",)], 1),
])
def test_report_returns_the_four_way_answer(statuses, want, capsys):
    """A run that skipped rows, or was blocked, must not exit 0.

    The headline already refused to say USABLE over a skipped section. The exit
    code is the half CI and the next verifier actually read, and it said 0 --
    so run B exited clean having measured eighteen fewer rows than run A.

    The precedence is FAIL, then BLOCKED, then SKIP, and it is the headline's
    own order rather than a second one invented here. FAIL outranks both: 1
    means something that should work on this host does not, and that is the
    most urgent of the three. BLOCKED outranks SKIP because a known defect
    stopping a row is a fact about the product, while a skip is a fact about
    the host.
    """
    b = h.Board()
    for (status,) in statuses:
        b.add(getattr(h, status), f"row {status}")
    assert b.report() == want
    capsys.readouterr()


def test_a_skipped_run_and_a_full_run_record_the_same_names():
    """The property the whole change exists to hold.

    Run A emits the declared rows; run B skips them. Neither may leave a name
    off the board, so the two scoreboards line up row for row and a reader can
    subtract them.
    """
    full = h.Board()
    start = len(full.rows)
    for name in h.BASE_SECTION_ROWS:
        full.add(h.PASS, name)
    full.assert_section(start, h.BASE_SECTION_ROWS, "base as the engine")

    skipped = h.Board()
    start = len(skipped.rows)
    skipped.add(h.SKIP, h.BASE_SECTION_ROWS[0], "this host has no base")
    skipped.skip_rest(h.BASE_SECTION_ROWS, "not measured on this host")
    skipped.assert_section(start, h.BASE_SECTION_ROWS, "base as the engine")

    assert [r["name"] for r in full.rows] == [r["name"] for r in skipped.rows]
    assert full.count(h.FAIL) == 0 and skipped.count(h.FAIL) == 0
    assert full.report() == 0
    assert skipped.report() == 2


# ---------------------------------------------------------------------------
# run() — a command that cannot start is a row, not a traceback
# ---------------------------------------------------------------------------

def test_a_command_that_cannot_be_executed_returns_127(tmp_path, monkeypatch):
    """It caught FileNotFoundError only, so EACCES killed the whole harness.

    Measured 2026-09-11: a directory named `base` on PATH raised PermissionError
    out of run() at the base section's first call and took the run down. The
    eighteen rows were not skipped, they were never reached, and the summary
    said completed=false. That is the same row loss through a door the declared
    list cannot close, because no row runs at all.

    WHAT THE OS RAISES HERE IS NOT THE SAME EVERYWHERE, which is why this
    asserts the harness's own answer and not the exception's class. A directory
    named `base` on PATH is EACCES on Linux; on Windows the loader searches
    PATHEXT, finds no executable of that name, and raises ENOENT. Identical host
    condition, two different exceptions, and the first version of this test
    pinned the Linux wording and went red on windows-latest.
    """
    (tmp_path / "base").mkdir()            # exists, not executable as a command
    monkeypatch.setenv("PATH", str(tmp_path))
    rc, out = h.run(["base", "--version"])
    assert rc == 127, f"rc={rc} out={out!r}"
    assert "could not be run" in out


def test_a_command_that_does_not_exist_returns_the_same_127_and_the_same_words(
        tmp_path, monkeypatch):
    """Absent and unrunnable are ONE answer, worded identically on every OS.

    They used to be two arms with two messages, so the same fact read
    differently depending on the machine and two runs could not be compared.
    Both mean "this host cannot start that command", both are 127, and the OS's
    own errno text is appended either way, so nothing diagnostic is lost.
    """
    monkeypatch.setenv("PATH", str(tmp_path))
    rc, out = h.run(["definitely-not-a-command-xyz"])
    assert rc == 127
    assert "could not be run" in out


def test_the_unrunnable_message_is_not_platform_specific():
    """RED ARM for the two above, and the reason #65 went red on Windows.

    The harness must carry exactly one phrase for this condition. A second arm
    with its own wording is how the same host fact came out as "not found" on
    Windows and "could not be run" on Linux.
    """
    src = HARNESS.read_text(encoding="utf-8")
    assert src.count('return 127, f"could not be run: {exc}"') == 1, (
        "there should be exactly one unrunnable-command message in the harness")
    assert 'return 127, f"not found: {exc}"' not in src, (
        "the second, differently-worded arm is back; the same host condition "
        "will again read one way on Linux and another on Windows")
# ---------------------------------------------------------------------------
# the exit code carries BLOCKED too
# ---------------------------------------------------------------------------

def test_report_returns_4_when_a_row_is_blocked():
    """It returned 0, which report()'s own comment defines as "every row
    measured, nothing skipped". A run stopped by a known defect is not that."""
    b = h.Board()
    b.add(h.PASS, "one")
    b.add(h.BLOCKED, "two", "a known defect")
    assert b.report() == 4


def test_a_fail_still_outranks_a_blocked():
    b = h.Board()
    b.add(h.FAIL, "one")
    b.add(h.BLOCKED, "two", "a known defect")
    assert b.report() == 1


def test_a_blocked_outranks_a_skip():
    """The headline has always ranked BLOCKED above SKIP. The exit code now
    agrees with it instead of inventing a second precedence."""
    b = h.Board()
    b.add(h.BLOCKED, "one", "a known defect")
    b.add(h.SKIP, "two", "not on this host")
    assert b.report() == 4


def test_a_clean_board_still_returns_0():
    b = h.Board()
    b.add(h.PASS, "one")
    assert b.report() == 0
