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
    green  the_refusal_wording_is_load_bearing...       <- REGRESSION GUARD, see below
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
        "absence must keep the substring run_install branches on to stay rc 0")


# ---------------------------------------------------------------------------
# THE WORDING IS LOAD-BEARING
# ---------------------------------------------------------------------------

def test_the_refusal_wording_is_load_bearing_because_run_install_exits_on_it(
        monkeypatch, home, tmp_path, capsys):
    """`run_install` returns 0 for ANY reason containing "not installed".

    `base_absence_reason()` puts that phrase in deliberately and its docstring
    says callers branch on it to mean "skip, do not fail". So a refusal worded
    "the base on PATH is not installed for this platform" would exit 0 -- a
    refusal reported through a success code, which is the #62 defect wearing a
    different hat.

    This test is why the refusal sentence may never contain those two words.

    NOT A RED ARM. It passes on the unfixed tree too, because the reason the old
    code happens to produce ("base reported success but ... does not exist")
    also lacks those two words. It guards the wording of the sentence I am
    adding, against a later edit that reaches for the phrase "not installed"
    because it reads naturally -- at which point the refusal would start
    exiting 0 and nothing else in the suite would notice.
    """
    foreign = _write_binary(tmp_path / "base-foreign", _foreign_magic())
    _point_which_base_at(monkeypatch, foreign)
    monkeypatch.setattr(subprocess, "run", _CountingRun())

    res = base_extension.install("/opt/cadre")
    assert "not installed" not in res["reason"], (
        "the refusal reason contains the substring run_install treats as "
        f"'skip, stay rc 0'. Reason was: {res['reason']}")

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
