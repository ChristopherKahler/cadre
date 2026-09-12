"""`install()` must refuse a base it cannot honour BEFORE it runs anything.

This is issue #75. The read-back at the end of ``install()`` is a DETECTOR: in
the 2026-09-11 incident it worked exactly as written, returned ``read_back:
False`` and named the missing path -- but by then base had already written the
operator's own tier. Detection after the fact is not prevention.

The difference between the defect and the fix is WHEN, not WHETHER. Both end in
a reported failure, so an end-state assertion cannot tell them apart. Every arm
below therefore pins the ORDERING: on refusal, ``subprocess.run`` is never
called at all.

Each arm names something that exists on the tree it runs against, so each one
MEASURES rather than raises.

WHICH ARMS WERE ACTUALLY RED, measured against the unfixed tree at 89bcf32
before a line of the fix was written -- 3 failed, 4 passed:

    RED    a_foreign_base_is_refused_before_any_subprocess_runs
    RED    an_unidentified_base_is_refused_rather_than_admitted
    RED    the_refusal_says_what_was_resolved_and_where_it_would_have_written
    green  a_native_base_still_installs                 <- CONTROL, green both sides
    green  base_absent_is_still_a_skip_and_not_a_refusal <- CONTROL, green both sides
    green  the_refusal_wording_avoids_the_skip_phrase... <- REGRESSION GUARD, see below
    green  install_never_raises_on_a_foreign_base       <- REGRESSION GUARD, see below

The two regression guards pass on the DEFECTIVE tree as well, so they prove
nothing about this change and are not counted as red arms. They are still worth
having: each one pins a property that a plausible future edit to the refusal
would silently break, and neither could be checked by reading the diff. They are
labelled rather than quietly listed among the passes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from firm.services import base_extension

# Magic bytes, by image format. Four bytes off the front of a file cannot be
# fooled by a filename -- the Windows base sits at
# /mnt/c/Users/<user>/.local/bin/base with no .exe suffix, which is the one
# case where a name test is wrong and it is the case that caused the incident.
PE = b"MZ\x90\x00"
ELF = b"\x7fELF"
MACHO = b"\xcf\xfa\xed\xfe"


def _native_magic() -> bytes:
    if sys.platform == "win32":
        return PE
    if sys.platform == "darwin":
        return MACHO
    return ELF


def _foreign_magic() -> bytes:
    """A magic this host's kernel cannot exec, whatever host that is."""
    return ELF if sys.platform == "win32" else PE


def _write_binary(path: Path, magic: bytes) -> str:
    path.write_bytes(magic + b"\x00" * 128)
    path.chmod(0o755)
    return str(path)


class _CountingRun:
    """Stands in for subprocess.run and records that it was reached at all.

    An arm that only checked the end state would pass against the OLD
    behaviour too, because the old behaviour also ends in a reported failure.
    The call count is the thing that separates preventing from detecting.
    """

    def __init__(self, code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.code = code

    def __call__(self, cmd, *a, **kw):
        self.calls.append(list(cmd))

        class R:
            returncode = self.code
            stdout = "ok"
            stderr = ""

        return R()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BASE_HOME", str(tmp_path))
    (tmp_path / ".base-gbl" / "extensions").mkdir(parents=True)
    return tmp_path


def _point_which_base_at(monkeypatch, path: str) -> None:
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: path)


# ---------------------------------------------------------------------------
# RED ARM
# ---------------------------------------------------------------------------

def test_a_foreign_base_is_refused_before_any_subprocess_runs(
        monkeypatch, home, tmp_path):
    """The arm. Red before the fix: today it validates, installs, then reports.

    A base built for another platform still RUNS here -- WSL interop executes a
    Windows binary quite happily -- and it ignores a POSIX BASE_HOME while every
    assertion downstream still passes. So it must never be reached.
    """
    foreign = _write_binary(tmp_path / "base-foreign", _foreign_magic())
    _point_which_base_at(monkeypatch, foreign)
    run = _CountingRun()
    monkeypatch.setattr(subprocess, "run", run)

    res = base_extension.install("/opt/cadre")

    assert run.calls == [], (
        "install() ran a subprocess with a base it cannot honour. The refusal "
        f"has to land BEFORE the first subprocess, not after it. Ran: {run.calls}")
    assert res["ok"] is False
    assert res["validated"] is False
    assert res["installed"] is False


def test_an_unidentified_base_is_refused_rather_than_admitted(
        monkeypatch, home, tmp_path):
    """Unknown means DENY, one level down from where #74 put it.

    A path that cannot be read at all is the shape the old `fake_base` fixture
    used. Admitting it is fail-open, and fail-open here writes the operator's
    tier.
    """
    _point_which_base_at(monkeypatch, str(tmp_path / "no-such-base"))
    run = _CountingRun()
    monkeypatch.setattr(subprocess, "run", run)

    res = base_extension.install("/opt/cadre")

    assert run.calls == [], (
        "an unreadable base was admitted and a subprocess ran against it")
    assert res["ok"] is False


# ---------------------------------------------------------------------------
# CONTROL -- a check only ever seen refusing has not been shown to discriminate
# ---------------------------------------------------------------------------

def test_a_native_base_still_installs(monkeypatch, home, tmp_path):
    """The control. If this goes red the guard refuses the legitimate case."""
    native = _write_binary(tmp_path / "base-native", _native_magic())
    _point_which_base_at(monkeypatch, native)
    run = _CountingRun(0)
    monkeypatch.setattr(subprocess, "run", run)
    (home / ".base-gbl" / "extensions" / "cadre.toml").write_text(
        'name = "cadre"\nframework_dir = "/opt/cadre"\n', encoding="utf-8")

    res = base_extension.install("/opt/cadre")

    assert len(run.calls) > 0, (
        "the guard refused a base built for this very host -- it is not "
        "discriminating, it is just refusing")
    assert res["validated"] is True
    assert res["read_back"] is True


def test_base_absent_is_still_a_skip_and_not_a_refusal(monkeypatch, home):
    """The guard must not turn 'no base on this machine' into a failure.

    Absence is degraded, never broken, and `run_install` keeps rc 0 for it.
    """
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    run = _CountingRun()
    monkeypatch.setattr(subprocess, "run", run)

    res = base_extension.install("/opt/cadre")

    assert run.calls == []
    assert "not installed" in res["reason"], (
        "absence must keep the phrase both absence sentences carry; "
        "run_install now switches on the skipped field")


# ---------------------------------------------------------------------------
# THE WORDING IS STILL GUARDED, THOUGH NO EXIT CODE READS IT
# ---------------------------------------------------------------------------

def _foreign(tmp_path):
    return _write_binary(tmp_path / "base-foreign", _foreign_magic())


def _unknown(tmp_path):
    """Four bytes that are no image format at all."""
    return _write_binary(tmp_path / "base-unknown", b"\x01\x02\x03\x04")


def _unreadable(tmp_path):
    """A path that cannot be opened. Nothing is created."""
    return str(tmp_path / "base-does-not-exist")


@pytest.mark.parametrize("make,label", [
    (_foreign, "foreign platform"),
    (_unknown, "unknown image format"),
    (_unreadable, "unreadable"),
])
def test_every_refusal_branch_exits_1_and_avoids_the_skip_substring(
        monkeypatch, home, tmp_path, make, label):
    """FOLLOW-UP 2 from avocet's PR 79 verdict.

    PR 79 added three refusal branches and guarded ONE, the foreign-platform
    case. All four passed at the time, so there was no live defect -- but
    UNKNOWN, UNREADABLE and ABSENT were unguarded against exactly the rewording
    this test exists to catch, and Finding 1 proved that failure mode is live in
    this file rather than theoretical.

    The fourth branch, ABSENT, is unreachable through install() because install()
    returns early when which_base() gives nothing. It is covered directly in
    test_the_absent_branch_wording_is_guarded_too.

    NOT A RED ARM, and avocet already said why: all four branches pass today.
    This is pure coverage. It is green on the tree before this change and green
    after it, so it proves nothing about the change and everything about the
    next rewording. Recorded here rather than counted among the red arms.
    """
    _point_which_base_at(monkeypatch, make(tmp_path))
    run = _CountingRun()
    monkeypatch.setattr(subprocess, "run", run)

    res = base_extension.install("/opt/cadre")

    assert res["ok"] is False, "%s was admitted" % label
    assert run.calls == [], "%s ran a subprocess" % label
    assert "not installed" not in res["reason"], (
        "the %s refusal carries the substring that run_install once read as "
        "'skip, stay rc 0'. Reason: %s" % (label, res["reason"]))

    monkeypatch.setattr(subprocess, "run", _CountingRun())
    assert base_extension.run_install("/opt/cadre") == 1, (
        "the %s refusal did not exit 1" % label)


def test_the_absent_branch_wording_is_guarded_too():
    """The fourth branch. Unreachable through install(), so called directly.

    base_can_honour_tier still has to answer for its own wording: a caller that
    reaches it with nothing resolved must not get a sentence that reads like
    the absence message. No caller branches on this prose for an exit code any
    more -- run_install switches on the skipped field -- but telling a refusal
    apart from a host with no base is worth keeping on its own.
    """
    from firm.sysconfig.binaries import base_can_honour_tier

    ok, reason = base_can_honour_tier(None, "/somewhere/cadre.toml")
    assert ok is False
    assert reason
    assert "not installed" not in reason, (
        "the ABSENT branch carries the skip substring: %s" % reason)


# ---------------------------------------------------------------------------
# FOLLOW-UP 1 -- the inherited reporting defect
# ---------------------------------------------------------------------------

def test_a_refusal_from_a_path_containing_the_skip_phrase_still_exits_1(
        monkeypatch, home, tmp_path):
    """FOLLOW-UP 1 from avocet's PR 79 verdict. Inherited, from 764d0d5.

    run_install used to pick its exit code by substring-matching the reason.
    The refusal sentences interpolate the resolved binary's path, so a base
    living under a directory literally named "not installed" carried the phrase
    into the reason, run_install read it as "skip, do not fail", and printed
    skipped: and returned 0 -- a genuine refusal reported as success.

    The severity is set by one measured fact, not by argument: in that hole the
    subprocess count is STILL ZERO. The refusal happens and the tier is
    protected. Only the exit code lied. This arm pins both halves.
    """
    odd = tmp_path / "not installed" / "bin"
    odd.mkdir(parents=True)
    _point_which_base_at(monkeypatch, _write_binary(odd / "base", _foreign_magic()))
    run = _CountingRun()
    monkeypatch.setattr(subprocess, "run", run)

    res = base_extension.install("/opt/cadre")
    assert res["ok"] is False
    assert run.calls == [], "the tier was NOT protected -- a subprocess ran"
    assert "not installed" in res["reason"], (
        "this arm is pointless unless the path really does carry the phrase")

    monkeypatch.setattr(subprocess, "run", _CountingRun())
    assert base_extension.run_install("/opt/cadre") == 1, (
        "a refusal was reported through exit 0 because its interpolated PATH "
        "contained the phrase run_install branched on")


def test_absence_is_still_a_skip_at_rc_0_after_the_exit_code_stopped_reading_prose(
        monkeypatch, home, capsys):
    """The control for follow-up 1, and the one that must not regress.

    Removing the substring branch must not turn "this machine has no base" into
    a failing exit code. Absence is degraded, never broken.
    """
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    monkeypatch.setattr(subprocess, "run", _CountingRun())

    assert base_extension.run_install("/opt/cadre") == 0
    assert "skipped" in capsys.readouterr().out


def test_install_marks_the_absent_case_as_skipped_in_the_result(
        monkeypatch, home):
    """The exit code now reads a field, so the field is what must be right."""
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    res = base_extension.install("/opt/cadre")
    # .get, not [] -- on a tree without the field a KeyError would RAISE rather
    # than measure, and an arm that raises carries no before-and-after.
    assert res.get("skipped") is True, (
        "absence is not marked as a skip; got %r" % res.get("skipped"))


def test_a_refusal_is_not_marked_skipped(monkeypatch, home, tmp_path):
    """Control: the field must discriminate, not just exist."""
    _point_which_base_at(monkeypatch, _foreign(tmp_path))
    monkeypatch.setattr(subprocess, "run", _CountingRun())
    res = base_extension.install("/opt/cadre")
    assert res.get("skipped") is False, (
        "a refusal was marked as a skip; got %r" % res.get("skipped"))


def test_the_refusal_wording_avoids_the_skip_phrase_and_the_refusal_exits_1(
        monkeypatch, home, tmp_path, capsys):
    """The refusal sentence must not carry the phrase the absence ones carry.

    CORRECTED BY ISSUE #83. This docstring used to say `run_install` returned 0
    for ANY reason containing "not installed", and that the wording was
    load-bearing because the exit code branched on it. It did, and that was the
    defect: `install`'s refusal sentences interpolate the resolved binary's
    path, so a base under a directory named "not installed" carried the phrase
    into a genuine refusal and the refusal reported success. `run_install` now
    switches on the `skipped` field.

    The assertions below are kept because the operator-facing property is still
    worth having -- a refusal and a host with no base at all should not
    describe themselves in the same vocabulary -- but no exit code depends on
    the prose any more, and this test must not be read as if one does.

    NOT A RED ARM. It passes on the unfixed tree too, because the reason the old
    code happens to produce ("base reported success but ... does not exist")
    also lacks those two words. It guards the wording of the sentence I am
    adding, against a later edit that reaches for the phrase "not installed"
    because it reads naturally.
    """
    foreign = _write_binary(tmp_path / "base-foreign", _foreign_magic())
    _point_which_base_at(monkeypatch, foreign)
    monkeypatch.setattr(subprocess, "run", _CountingRun())

    res = base_extension.install("/opt/cadre")
    assert "not installed" not in res["reason"], (
        "the refusal reason carries the phrase the absence sentences use, "
        f"so nothing tells the two apart. Reason was: {res['reason']}")

    rc = base_extension.run_install("/opt/cadre")
    assert rc == 1, (
        "a refusal must exit non-zero. Exit 0 on a refusal is the same defect "
        "as #62 and as the BLOCKED row that exited 0 in #76")


def test_the_refusal_says_what_was_resolved_and_where_it_would_have_written(
        monkeypatch, home, tmp_path):
    """A person runs `cadre extension install` by hand, so the reason has to
    be actionable: which binary, what kind it is, and which tier was expected.
    """
    foreign = _write_binary(tmp_path / "base-foreign", _foreign_magic())
    _point_which_base_at(monkeypatch, foreign)
    monkeypatch.setattr(subprocess, "run", _CountingRun())

    reason = base_extension.install("/opt/cadre")["reason"]

    assert "base-foreign" in reason, "the reason does not name the binary it resolved"
    assert str(home) in reason, "the reason does not name the tier it expected"


def test_install_never_raises_on_a_foreign_base(monkeypatch, home, tmp_path):
    """`install()`'s contract is 'never raises'. A guard that raises breaks the
    API for every caller, including the hub route that only reads the dict.

    NOT A RED ARM. The unfixed tree does not raise either -- it does the wrong
    thing quietly. This guards the contract against a guard written with an
    assert or a bare raise, which is the obvious way to write this check and the
    way that would break every caller.
    """
    foreign = _write_binary(tmp_path / "base-foreign", _foreign_magic())
    _point_which_base_at(monkeypatch, foreign)
    monkeypatch.setattr(subprocess, "run", _CountingRun())

    res = base_extension.install("/opt/cadre")  # must not raise
    assert isinstance(res, dict)
    assert res["reason"]
