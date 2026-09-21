"""The firm's base wire, and the three ways it used to lie about itself.

``base rule list`` exits 0 for a populated domain, an empty one, and a domain
that never existed — measured against base 0.15.0 on 2026-09-10 — so the exit
code discriminates nothing and the output text is the only signal. Every
fixture string below was COPIED OUT OF REAL OUTPUT, not typed from memory: an
assertion keyed on a string somebody invented is a guard that cannot fire.

The property under test throughout is that ABSENT, EMPTY and ZERO stay three
different answers. A reader that collapses "I could not tell" into "there are
none" fails a firm for the operator's install; one that collapses it the other
way ships a firm whose graph reaches nobody.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from firm.services import base_domain

# --- real output, copied verbatim from base 0.15.0 --------------------------
OUT_ONE_RULE = (
    "[zqdom] 1 rules across both tiers:\n"
    "  workspace 0. godwit probe rule one\n"
    "  (global: none)\n"
    "\n"
    "Indices are per tier; `rule remove` takes the index shown beside its own tier.\n"
)
OUT_TWO_RULES = (
    "[zqdom] 2 rules across both tiers:\n"
    "  workspace 0. godwit probe rule one\n"
    "  workspace 1. godwit probe rule two\n"
    "  (global: none)\n"
)
OUT_EMPTY = "No rules for domain 'zqdom' in either tier.\n"
OUT_NEVER_EXISTED = "No rules for domain 'zqneverexisted' in either tier.\n"
OUT_UNRECOGNISED = "some future base wording nobody predicted\n"


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _Recorder:
    """Stands in for subprocess.run and records the argv it was handed."""

    def __init__(self, *results: _Result) -> None:
        self._results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if not self._results:
            raise AssertionError(f"unexpected extra subprocess call: {cmd}")
        return self._results.pop(0)

    @property
    def verbs(self) -> list[str]:
        return [c[1] + " " + c[2] for c in self.calls if len(c) > 2]


def _make_stub_base() -> str:
    """A REAL file carrying this host's magic bytes, standing in for `base`.

    These tests used the string "/fake/base". That is not a file, so they were
    proving the code works against something no host could execute. Since #75
    and #87, `base_extension.install` and `base_domain.scaffold_tier` identify
    the binary they resolved BEFORE running it and refuse an unidentified one.
    Nothing in THIS file reaches those guards today, which is why these stubs
    still pass -- but a string stub is what makes the shape fail-open the moment
    a guard arrives on the path under test, and that has now happened twice.
    """
    import tempfile
    from pathlib import Path

    from firm.sysconfig.binaries import native_image_format

    magic = {"pe": b"MZ\x90\x00", "macho": b"\xcf\xfa\xed\xfe"}.get(
        native_image_format(), b"\x7fELF")
    b = Path(tempfile.mkdtemp()) / "base"
    b.write_bytes(magic + b"\x00" * 128)
    b.chmod(0o755)
    return str(b)


STUB_BASE = _make_stub_base()


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """A workspace with .base/domains.toml and base pretending to be installed."""
    monkeypatch.setattr("firm.sysconfig.service.which_base",
                        lambda: STUB_BASE)
    ws = tmp_path / "firm"
    (ws / ".base").mkdir(parents=True)
    (ws / ".base" / "domains.toml").write_text("# scaffolded\n", encoding="utf-8")
    return ws


class _BlindConn:
    """A conn whose every query fails, so _roster_words returns [firm_id] flat."""

    def execute(self, *args, **kwargs):
        raise RuntimeError("no database in this test")


# ---------------------------------------------------------------------------
# rule_count — the reader
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stdout,expected", [
    (OUT_ONE_RULE, 1),
    (OUT_TWO_RULES, 2),
    (OUT_EMPTY, 0),
])
def test_rule_count_reads_real_output(monkeypatch, wired, stdout, expected):
    monkeypatch.setattr(subprocess, "run", _Recorder(_Result(stdout)))
    assert base_domain.rule_count(wired, "zqdom") == expected


def test_rule_count_is_none_when_base_is_absent(monkeypatch, wired):
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    # None, never 0 — a machine without base has no ruleless domain to report.
    assert base_domain.rule_count(wired, "zqdom") is None


def test_rule_count_is_none_on_unrecognised_output(monkeypatch, wired):
    """The blind axis. If base rewords its output this must go UNKNOWN, not clean.

    An earlier version tested for the ABSENCE of "No rules for domain", which
    reads "has rules" the day that sentence changes — a failure mode pointing
    at the clean answer, which is the one nobody investigates.
    """
    monkeypatch.setattr(subprocess, "run", _Recorder(_Result(OUT_UNRECOGNISED)))
    assert base_domain.rule_count(wired, "zqdom") is None


def test_rule_count_is_none_on_nonzero_rc(monkeypatch, wired):
    monkeypatch.setattr(subprocess, "run", _Recorder(_Result(OUT_ONE_RULE, returncode=3)))
    assert base_domain.rule_count(wired, "zqdom") is None


def test_rule_count_is_none_when_base_times_out(monkeypatch, wired):
    def _boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 60)
    monkeypatch.setattr(subprocess, "run", _boom)
    assert base_domain.rule_count(wired, "zqdom") is None


def test_rule_count_ignores_a_count_for_a_different_domain(monkeypatch, wired):
    """Law 40: check what ELSE can emit your shape before keying on it.

    base prints the domain name it counted. Reading 1 off another domain's
    header would report a live wire on a domain that has none.
    """
    monkeypatch.setattr(subprocess, "run", _Recorder(_Result(OUT_ONE_RULE)))
    assert base_domain.rule_count(wired, "some-other-firm") is None


def test_the_two_shapes_do_not_match_each_other():
    """The empty sentence must not read as a count, and vice versa."""
    checked = 0
    for text in (OUT_EMPTY, OUT_NEVER_EXISTED):
        assert base_domain._COUNT_RE.search(text) is None
        assert base_domain._EMPTY_RE.search(text) is not None
        checked += 1
    for text in (OUT_ONE_RULE, OUT_TWO_RULES):
        assert base_domain._COUNT_RE.search(text) is not None
        assert base_domain._EMPTY_RE.search(text) is None
        checked += 1
    assert checked == 4, f"visited {checked} shapes, expected 4"


# ---------------------------------------------------------------------------
# _seed_rule — separating "repaired" from "silenced"
# ---------------------------------------------------------------------------

def test_seed_rule_adds_when_empty_and_reads_back(monkeypatch, wired):
    rec = _Recorder(_Result(OUT_EMPTY), _Result(""), _Result(OUT_ONE_RULE))
    monkeypatch.setattr(subprocess, "run", rec)
    assert base_domain._seed_rule(wired, "zqdom") is True
    assert rec.verbs == ["rule list", "rule add", "rule list"]


def test_seed_rule_is_false_when_the_read_back_still_shows_zero(monkeypatch, wired):
    """The control that separates a repair from a silence (law 25).

    `base rule add` exiting 0 is the writer's own opinion. If the rule did not
    land, the only thing that can say so is asking for it again — and the
    caller must be told False so founding and doctor can report it.
    """
    rec = _Recorder(_Result(OUT_EMPTY), _Result(""), _Result(OUT_EMPTY))
    monkeypatch.setattr(subprocess, "run", rec)
    assert base_domain._seed_rule(wired, "zqdom") is False
    assert rec.verbs == ["rule list", "rule add", "rule list"]


def test_seed_rule_is_false_when_rule_add_fails(monkeypatch, wired):
    rec = _Recorder(_Result(OUT_EMPTY), _Result("", returncode=1))
    monkeypatch.setattr(subprocess, "run", rec)
    assert base_domain._seed_rule(wired, "zqdom") is False


def test_seed_rule_is_idempotent_and_never_adds_twice(monkeypatch, wired):
    rec = _Recorder(_Result(OUT_TWO_RULES))
    monkeypatch.setattr(subprocess, "run", rec)
    assert base_domain._seed_rule(wired, "zqdom") is True
    assert rec.verbs == ["rule list"], "an existing rule set must be left alone"


def test_seed_rule_is_false_when_the_count_cannot_be_read(monkeypatch, wired):
    rec = _Recorder(_Result(OUT_UNRECOGNISED))
    monkeypatch.setattr(subprocess, "run", rec)
    assert base_domain._seed_rule(wired, "zqdom") is False
    assert rec.verbs == ["rule list"], "never write blind into a domain we cannot read"


def test_seed_rule_is_false_when_base_is_absent(monkeypatch, wired):
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    assert base_domain._seed_rule(wired, "zqdom") is False


def test_a_call_against_a_firm_is_pinned_to_that_firms_own_tier(monkeypatch, wired):
    """#117 REVERSED THIS ASSERTION, and the reason belongs here.

    It used to read: "a harness pointing base at a scratch tier must not be
    re-pointed home", and it asserted that an ambient BASE_HOME survived a call
    made against a firm. That passthrough is the hole. base takes its entire
    global tier from BASE_HOME -- extensions, domains, rules, the relay store --
    so whatever BASE_HOME happens to say while Cadre is working a firm decides
    what gets written into that firm's graph. Measured on the operator's machine
    2026-09-14: one session-start against his own tier put 13,564 lines of an
    unrelated extension's data and 116 lines of his own domain definitions into
    `firms/seedwin`, a firm that owned 86 of its 13,774 lines.

    The old claim did not disappear, it moved down one function: the passthrough
    still stands for a call with no firm to isolate, which is what keeps
    `cadre extension install` and this suite's own BASE_HOME fence working.
    """
    monkeypatch.setenv("BASE_HOME", "/tmp/scratch-tier")
    seen: dict = {}

    def _run(cmd, **kwargs):
        seen.update(kwargs.get("env") or {})
        return _Result(OUT_ONE_RULE)

    monkeypatch.setattr(subprocess, "run", _run)
    base_domain.rule_count(wired, "zqdom")
    assert seen.get("BASE_HOME") == str(wired / ".firm" / "base-home")


def test_a_call_with_no_firm_still_carries_the_ambient_tier(monkeypatch, tmp_path):
    """The half of the old claim that survives, and the control on the one above.

    Without this, "the firm always wins" could be built as an unconditional
    overwrite, and every caller with no firm to isolate -- `extension install`,
    the fence in conftest -- would silently lose the tier it deliberately set.
    """
    ambient = str(tmp_path / "scratch-tier")
    monkeypatch.setenv("BASE_HOME", ambient)

    # A CANONICAL ABSOLUTE PATH FOR THE PLATFORM THIS RUNS ON, and no longer
    # the literal "/tmp/scratch-tier". That literal is rooted with no DRIVE,
    # and since #136 the passthrough sends `BASE_HOME` through
    # `_one_spelling`, so `os.path.abspath` attaches the process's current
    # drive on Windows and the value comes back `D:\tmp\scratch-tier`.
    # Measured: CI run 35662050493, job `suite (windows-latest)`, the only
    # failure in 2571 passed; ubuntu and macos were green because there the
    # literal is already complete.
    #
    # `tmp_path` is absolute and drive-qualified on every platform CI runs, so
    # normalisation leaves it byte-identical and the assertion still fails the
    # moment an ambient tier is overwritten or dropped -- which is the whole
    # point of the leg. It deliberately does NOT run its own input through
    # `abspath` or `_one_spelling`: a leg that computed the fix's answer could
    # never fail, whatever the fix did.
    assert base_domain._base_env().get("BASE_HOME") == ambient


def test_a_rooted_ambient_tier_with_no_drive_comes_back_drive_qualified(
        monkeypatch):
    """Windows: `/tmp/x` gains a drive, and it is the drive this process is on.

    The behaviour the leg above stopped asserting, said out loud here rather
    than left as something only a CI failure knows about. `BASE_HOME` and the
    working directory `base_cwd` hands the child are compared by base as
    `std::path::Path` equality, so a value missing a drive is not one spelling:
    the child would resolve it against whatever drive IT was on. Attaching the
    drive here removes that ambiguity at the only point where both strings are
    still in Cadre's hands.

    Gated on the MECHANISM rather than on a platform name: a rooted path with
    no drive is a Windows shape, and on POSIX `/tmp/scratch-tier` is already
    complete, so this arm would assert that nothing happened.
    """
    if os.name != "nt":
        pytest.skip(
            "a rooted path with no drive is a Windows shape; on POSIX "
            "'/tmp/scratch-tier' is already a complete path and this arm would "
            "be asserting that nothing happened")
    driveless = "/tmp/scratch-tier"
    monkeypatch.setenv("BASE_HOME", driveless)

    got = base_domain._base_env().get("BASE_HOME")
    drive, tail = os.path.splitdrive(got or "")

    assert drive, (
        "a rooted BASE_HOME with no drive came back with no drive either (%r), "
        "so base and the child could resolve it against different drives" % got)
    assert drive == os.path.splitdrive(os.getcwd())[0], (
        "the drive attached (%r) is not the one this process is on (%r); the "
        "value has to land on the same drive the child's working directory "
        "gets, and the process's own is the only source for one the value does "
        "not carry" % (drive, os.path.splitdrive(os.getcwd())[0]))
    assert tail == os.path.normpath(driveless), (
        "the path under the drive is %r, not %r -- the drive was attached but "
        "the directory named is a different one"
        % (tail, os.path.normpath(driveless)))


# ---------------------------------------------------------------------------
# assess — the check that used to pass a dead wire, and used to pass
# an unreadable one as healthy (#62)
# ---------------------------------------------------------------------------

def _write_perfect_block(ws: Path, firm_id: str) -> None:
    block = base_domain.render(ws, firm_id, [firm_id])
    (ws / ".base" / "domains.toml").write_text(block + "\n", encoding="utf-8")


def test_assess_fails_a_perfect_block_with_zero_rules(monkeypatch, wired):
    """The whole point. base drops a matched domain carrying no rules, so a
    structurally perfect block that injects nothing must be a FINDING."""
    _write_perfect_block(wired, "zqdom")
    monkeypatch.setattr(subprocess, "run", _Recorder(_Result(OUT_EMPTY)))
    verdict, detail = base_domain.assess(wired, "zqdom", _BlindConn())
    assert verdict is base_domain.Verdict.STALE
    assert "no rules" in detail


def test_assess_passes_the_same_block_with_one_rule(monkeypatch, wired):
    """The control. Same block, same everything, one rule — must go green, or
    the test above is passing for some other reason."""
    _write_perfect_block(wired, "zqdom")
    monkeypatch.setattr(subprocess, "run", _Recorder(_Result(OUT_ONE_RULE)))
    verdict, detail = base_domain.assess(wired, "zqdom", _BlindConn())
    assert verdict is base_domain.Verdict.CURRENT
    assert "1 rule" in detail


def test_assess_reports_a_missing_base_as_undeterminable_not_current(
        monkeypatch, wired):
    """THE #62 assertion, inverted, and it is the only one that inverts.

    This test used to require ``ok is True`` -- it was the defect written
    down and passing. Its intent was right and is kept: do not blame the
    firm for the operator's install. UNDETERMINABLE is what lets that
    intent survive without the lie. It does not say the firm is broken; it
    says nothing was established, which routes to the operator rather than
    to the mechanical fixer and prints "?" rather than a tick.

    The old name said the old answer, so the name moved with it.
    """
    _write_perfect_block(wired, "zqdom")
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    verdict, detail = base_domain.assess(wired, "zqdom", _BlindConn())
    assert verdict is base_domain.Verdict.UNDETERMINABLE
    assert "unread" in detail


def test_assess_still_fails_a_missing_block(monkeypatch, wired):
    monkeypatch.setattr(subprocess, "run", _Recorder(_Result(OUT_ONE_RULE)))
    verdict, detail = base_domain.assess(wired, "zqdom", _BlindConn())
    assert verdict is base_domain.Verdict.STALE
    assert "no domain block" in detail


def test_assess_still_fails_a_duplicated_name(monkeypatch, wired):
    block = base_domain.render(wired, "zqdom", ["zqdom"])
    hand = '[[domain]]\nname = "zqdom"\nrules = ["hand written"]\n'
    (wired / ".base" / "domains.toml").write_text(hand + "\n" + block + "\n",
                                                  encoding="utf-8")
    verdict, detail = base_domain.assess(wired, "zqdom", _BlindConn())
    assert verdict is base_domain.Verdict.STALE
    assert "matches none of them" in detail


def test_assess_still_fails_a_stale_block(monkeypatch, wired):
    stale = base_domain.render(wired, "zqdom", ["zqdom", "Someone Who Left"])
    (wired / ".base" / "domains.toml").write_text(stale + "\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _Recorder(_Result(OUT_ONE_RULE)))
    verdict, detail = base_domain.assess(wired, "zqdom", _BlindConn())
    assert verdict is base_domain.Verdict.STALE
    assert "stale against the roster" in detail


def test_assess_keeps_its_three_outcomes_distinct(monkeypatch, wired):
    """Two of the three must not be able to collapse into one.

    Asserting ``len({Verdict.CURRENT, Verdict.STALE, Verdict.UNDETERMINABLE})``
    is 3 would be VACUOUS -- enum members are distinct by construction, so it
    says nothing about this code. The assertion has to be on three verdicts
    actually RETURNED from three real situations, which is the shape
    tests/cli/test_init_wires_base.py already uses on the three absence
    reasons. Copying the shape is the point; copying the enum is not.
    """
    _write_perfect_block(wired, "zqdom")

    monkeypatch.setattr(subprocess, "run", _Recorder(_Result(OUT_ONE_RULE)))
    current = base_domain.assess(wired, "zqdom", _BlindConn())[0]

    stale_block = base_domain.render(wired, "zqdom", ["zqdom", "Someone Who Left"])
    (wired / ".base" / "domains.toml").write_text(stale_block + "\n",
                                                  encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _Recorder(_Result(OUT_ONE_RULE)))
    stale = base_domain.assess(wired, "zqdom", _BlindConn())[0]

    _write_perfect_block(wired, "zqdom")
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    undeterminable = base_domain.assess(wired, "zqdom", _BlindConn())[0]

    assert len({current, stale, undeterminable}) == 3, (
        "three different situations produced fewer than three verdicts: "
        f"{current} / {stale} / {undeterminable}")


def test_assess_will_not_call_an_unreadable_domains_file_stale(wired):
    """A file you could not read is not a file you found to be out of date.

    This branch answered False before, which ``doctor`` renders as a stale
    block routed to the mechanical fixer -- so ``--fix`` would rebuild a file
    it had just failed to read. That is #62's mistake pointing the other way:
    a verdict asserted from no evidence. It errs loud rather than quiet, which
    is why it was survivable, not why it was right.

    The OSError is real rather than injected: the path is a DIRECTORY, so the
    read raises IsADirectoryError on POSIX and PermissionError on Windows, both
    of which are OSError.
    """
    domains = wired / ".base" / "domains.toml"
    if domains.exists():
        domains.unlink()
    domains.mkdir()

    verdict, detail = base_domain.assess(wired, "zqdom", _BlindConn())
    assert verdict is base_domain.Verdict.UNDETERMINABLE, (
        f"an unreadable domains.toml came back as {verdict}: {detail!r}")

# ---------------------------------------------------------------------------
# sync — the result the callers must stop discarding
# ---------------------------------------------------------------------------

def test_sync_reports_rule_seeded(monkeypatch, wired):
    rec = _Recorder(_Result(OUT_EMPTY), _Result(""), _Result(OUT_ONE_RULE))
    monkeypatch.setattr(subprocess, "run", rec)
    res = base_domain.sync(wired, "zqdom", conn=_BlindConn())
    assert res["ok"] is True
    assert res["rule_seeded"] is True


def test_sync_reports_a_failed_seed_rather_than_hiding_it(monkeypatch, wired):
    rec = _Recorder(_Result(OUT_EMPTY), _Result("", returncode=1))
    monkeypatch.setattr(subprocess, "run", rec)
    res = base_domain.sync(wired, "zqdom", conn=_BlindConn())
    assert res["ok"] is True, "the block was still written — that part worked"
    assert res["rule_seeded"] is False, "and the wire is dead, which must be said"


def test_sync_without_a_base_tier_is_not_an_error(tmp_path):
    res = base_domain.sync(tmp_path / "nowhere", "zqdom")
    assert res["ok"] is False
    assert "not scaffolded" in res["reason"]
