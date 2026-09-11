"""The isolation watched set is two tiers, and neither row overstates itself.

THE DEFECT. `scripts/acceptance-e2e.py` hashed twelve operator files at run
start and again at run end, and failed the run if the hash moved. Three of
those twelve are written by ANY live base session, not only by the run being
measured. Measured at `5d19e1b45f75` on a Windows host with eleven live
sessions and the harness NOT running, polling every five seconds for 240
seconds and counting content changes rather than mtime touches:

    ~/.base-gbl/.base/graph.nq        13 content changes   +27,742 bytes
    ~/.base-gbl/.base/changes.jsonl   13 content changes   +23,564 bytes
    ~/.base/graph.nq                   9 content changes    +2,318 bytes
    the other nine watched paths       0 content changes        +0 bytes

So the row that outranks every other row in the harness went red whenever
somebody else was working. It failed RED, not green -- which sounds like the
safe direction and is not: a guard that fails on a correct run gets edited to
match the code, and that is how it stops guarding.

THE FIX IS TWO TIERS AND TWO ROWS. The nine stable paths keep the exact hash
and keep their row, wording unchanged. The three churning paths are scanned
for THIS RUN'S fingerprint instead, and get a row of their own that says only
what it checked. One row carrying both claims would report more than it
verified.

The expensive half of the proof is CI's `acceptance (windows-latest)`, which
runs the harness end to end. This file is the cheap half, and it is the half
that carries the red arms.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARNESS = REPO / "scripts" / "acceptance-e2e.py"

FOREIGN = ('<http://ops-sys.local/ontology#note/another-session> '
           '<http://ops-sys.local/ontology#body> '
           '"a different session was working while this ran" '
           '<http://ops-sys.local/ontology#graph/ws/base-gbl> .\n')


def _harness():
    """Import the harness by path. Its filename has a hyphen in it."""
    spec = importlib.util.spec_from_file_location("acceptance_e2e", HARNESS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


h = _harness()

RUN = "/tmp/cadre-accept-abc123"        # stands in for the mkdtemp sandbox
FIXED = "/tmp/cadre-accept-base"        # stands in for BASE_HOME, same every run


# ---------------------------------------------------------------------------
# the two tiers are actually two
# ---------------------------------------------------------------------------

def test_the_churning_set_is_not_empty():
    """An empty set satisfies every claim about its members.

    If this ever becomes empty the scan below runs over nothing and reports
    CLEAN forever, which is the no-op-that-reads-green this whole area exists
    to stamp out.
    """
    assert h.churning_files(), "nothing is being append-scanned"


def test_the_tiers_do_not_overlap():
    """A path in both tiers gets two verdicts about it and one of them is wrong."""
    both = set(h.operator_files()) & set(h.churning_files())
    assert not both, f"watched by both tiers: {sorted(str(p) for p in both)}"


def test_the_exact_hash_tier_no_longer_carries_a_churning_path():
    """The three measured churners must not be back under an exact hash.

    Asserted by NAME rather than by count, because a count passes happily while
    one churner is swapped for another.
    """
    exact = {p.name for p in h.operator_files()}
    assert "graph.nq" not in exact
    assert "changes.jsonl" not in exact


def test_the_exact_hash_tier_still_watches_the_registry_that_was_polluted():
    """The registry is the thing six `cadre-accept-*` workspaces landed in.

    Narrowing the set is only safe if what it was protecting is still in it.
    """
    exact = {p.name for p in h.operator_files()}
    assert "base.toml" in exact
    assert "CLAUDE.md" in exact


# ---------------------------------------------------------------------------
# the scan, both directions
# ---------------------------------------------------------------------------

def test_the_scan_reports_an_append_carrying_this_runs_marker(tmp_path):
    f = tmp_path / "graph.nq"
    f.write_text("<a> <b> <c> .\n", encoding="utf-8")
    base = h.churn_baseline([f])
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(f"<contaminated> <by> <{RUN}> .\n")
    hits = h.fingerprint_hits(base, RUN, (), [f])
    assert hits and RUN in hits[0]


def test_the_scan_ignores_a_foreign_append(tmp_path):
    """THE CONTROL. This is the defect being closed, stated as a test.

    Four kilobytes of another session's triples, carrying nothing of this run,
    must not be reported. Before the fix this was a FAIL on the row that
    outranks every other row in the harness.
    """
    f = tmp_path / "graph.nq"
    f.write_text("<a> <b> <c> .\n", encoding="utf-8")
    base = h.churn_baseline([f])
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(FOREIGN * 25)
    assert f.stat().st_size > 4096, "the foreign append was not big enough to matter"
    assert not h.fingerprint_hits(base, RUN, (), [f])


def test_that_silence_was_the_missing_marker_and_not_a_dead_scanner(tmp_path):
    """RED ARM for the control above.

    A scanner that never reports anything passes the foreign-append test
    perfectly. So hand the SAME foreign append to the SAME scanner with the
    foreign session's own string as the marker, and require a hit. Silence in
    the test above is then attributable to the marker being absent rather than
    to the scan being blind.
    """
    f = tmp_path / "graph.nq"
    f.write_text("<a> <b> <c> .\n", encoding="utf-8")
    base = h.churn_baseline([f])
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(FOREIGN * 25)
    assert h.fingerprint_hits(base, "another-session", (), [f])


def test_a_baseline_taken_after_the_write_sees_nothing(tmp_path):
    """RED ARM. The window has to open BEFORE the run, not after it.

    This is the same failure the harness already paid for once and wrote down
    at the refusal above `run_start_digest`: widening WHAT was watched would
    not have helped, because the window itself was in the wrong place and the
    contamination happened before it opened. Encoded here so the next person to
    move the baseline sees a red test rather than a clean run.
    """
    f = tmp_path / "graph.nq"
    f.write_text("<a> <b> <c> .\n", encoding="utf-8")
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(f"<contaminated> <by> <{RUN}> .\n")
    too_late = h.churn_baseline([f])          # opened AFTER the write
    assert not h.fingerprint_hits(too_late, RUN, (), [f])


# ---------------------------------------------------------------------------
# the compaction fallback, and the trap inside it
# ---------------------------------------------------------------------------

def test_the_append_window_does_match_the_fixed_base_home(tmp_path):
    """Inside the window those bytes are this run's by construction."""
    f = tmp_path / "graph.nq"
    f.write_text("<a> <b> <c> .\n", encoding="utf-8")
    base = h.churn_baseline([f])
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(f"<wrote> <to> <{FIXED}> .\n")
    hits = h.fingerprint_hits(base, RUN, (FIXED,), [f])
    assert hits and "appended" in hits[0]


def test_after_a_compaction_the_fixed_base_home_is_NOT_a_hit(tmp_path):
    """THE TRAP IN THE FALLBACK, and the reason the fallback narrows its markers.

    BASE_HOME is `<drive>/cadre-accept-base` or `<tmp>/cadre-accept-base` --
    the same path on every run on this host. Once a compaction invalidates the
    offset and the whole file is scanned, a PREVIOUS run's leftover
    `cadre-accept-base` triples would be read as today's contamination and the
    row would go red for something that happened last week.
    """
    f = tmp_path / "graph.nq"
    f.write_text("<a> <b> <c> .\n" * 500, encoding="utf-8")
    base = h.churn_baseline([f])
    # base compacts: the file gets SMALLER and its head changes.
    f.write_text(f"<last> <week> <{FIXED}> .\n", encoding="utf-8")
    assert not h.fingerprint_hits(base, RUN, (FIXED,), [f])


def test_after_a_compaction_the_run_unique_marker_IS_still_a_hit(tmp_path):
    """The other direction, or the test above is satisfied by a dead fallback."""
    f = tmp_path / "graph.nq"
    f.write_text("<a> <b> <c> .\n" * 500, encoding="utf-8")
    base = h.churn_baseline([f])
    f.write_text(f"<today> <wrote> <{RUN}> .\n", encoding="utf-8")
    hits = h.fingerprint_hits(base, RUN, (FIXED,), [f])
    assert hits and "compacted" in hits[0]


def test_a_file_created_during_the_run_is_scanned_whole(tmp_path):
    """Absent at the start means everything in it belongs to this run's era."""
    f = tmp_path / "graph.nq"
    base = h.churn_baseline([f])               # it does not exist yet
    f.write_text(f"<new> <file> <{FIXED}> .\n", encoding="utf-8")
    hits = h.fingerprint_hits(base, RUN, (FIXED,), [f])
    assert hits and "created" in hits[0]


def test_an_unreadable_path_is_not_silently_a_pass_for_the_exact_tier():
    """`operator_digest` records "absent", which is a VALUE, not a skip.

    Kept as a live assertion because narrowing the set is exactly the kind of
    edit that would drop it.
    """
    missing = Path("/nonexistent-godwit-probe/never")
    assert h.operator_digest([missing]) == h.operator_digest([missing])
    assert h.operator_digest([missing]) != h.operator_digest([])


# ---------------------------------------------------------------------------
# the rows
# ---------------------------------------------------------------------------

def test_both_verdict_rows_are_declared_and_distinct():
    """Two claims, two rows. One row carrying both would overstate itself."""
    exact = "the operator's own graph and registry were never written to"
    scanned = "no appended write carries this run's fingerprint"
    assert exact in h.BASE_SECTION_ROWS
    assert scanned in h.BASE_SECTION_ROWS
    assert exact != scanned


def test_the_exact_hash_row_kept_its_wording():
    """It still means what it always meant, about the files it can still mean it about."""
    assert ("the operator's own graph and registry were never written to"
            in h.BASE_SECTION_ROWS)


def test_both_scan_arms_are_declared_rows():
    """An arm that is not on the scoreboard cannot be seen to have run."""
    for name in ("the append scan can actually detect a planted write",
                 "the append scan ignores another session's writes"):
        assert name in h.BASE_SECTION_ROWS


def test_the_fallback_comment_names_the_reason():
    """Condition 2 is a decision, and a decision with no reason on it gets undone."""
    src = HARNESS.read_text(encoding="utf-8")
    assert "ONLY `run_marker` IS MATCHED" in src
    assert "cadre-accept-base" in src
