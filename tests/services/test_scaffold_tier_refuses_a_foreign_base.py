"""``scaffold_tier`` must refuse a base it cannot honour, BEFORE it runs it.

Issue #87. ``base_can_honour_tier`` was added by #79 and had exactly one caller,
``base_extension.install()``. ``base_domain.scaffold_tier`` has the identical
shape -- it resolves a binary with ``which_base()``, checks only ``if not
base``, and then runs it -- and got no equivalent assertion. #75's fix landed on
install() and not on its neighbour.

SEVERITY, honestly: this is defence in depth, not a suite hole. ``conftest.py``
carries an autouse ``_no_ambient_base`` that makes ``which_base`` return None
under pytest, so the suite never reaches this path. The real exposure is an
operator running ``cadre init`` on WSL, where ``which_base`` resolves the
WINDOWS base across /mnt/c, that base ignores a POSIX path, and it writes the
operator's own global tier instead of the firm's workspace.

THE FIX IS NOT ``BASE_HOME``. Measured 2026-09-11 and recorded at
``binaries.py:92-93`` and ``service.py:54-56``: a POSIX ``BASE_HOME`` handed to
a Windows base is IGNORED and does not redirect the write. That is precisely why
#79 chose refusal over environment.

THE AUTOUSE FIXTURE IS THE OBSTACLE, not an accident. ``_no_ambient_base`` runs
first and sets ``which_base`` to None, so every arm below must monkeypatch it
back to a binary of its own. The conftest docstring says an autouse fixture runs
first so a test's own monkeypatch still wins, which is what makes these arms
possible at all.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from firm.services import base_domain

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
    """A magic this host's kernel cannot exec, whatever host it is."""
    return ELF if sys.platform == "win32" else PE


def _binary(path: Path, magic: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(magic + b"\x00" * 128)
    path.chmod(0o755)
    return str(path)


class _CountingRun:
    """Records that a subprocess was reached at all.

    An end-state assertion cannot separate this fix from the defect: the old
    code also ends in a reported failure when base writes the wrong tier. The
    call count is the thing that distinguishes preventing from detecting.
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


def _point_which_base_at(monkeypatch, path):
    # The autouse _no_ambient_base set this to None. An autouse fixture runs
    # first, so this monkeypatch wins -- which is the only reason a red arm for
    # this path can exist at all.
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: path)


def test_a_foreign_base_is_refused_before_scaffold_runs(monkeypatch, tmp_path):
    """THE RED ARM. Red before the fix: today it runs base scaffold and only
    then reports whatever came back.

    A base built for another platform still EXECUTES here -- WSL interop runs a
    Windows binary quite happily -- and it ignores the POSIX workspace path, so
    it writes the operator's own tier while this function reports success.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    _point_which_base_at(monkeypatch, _binary(tmp_path / "bin" / "base", _foreign_magic()))
    run = _CountingRun()
    monkeypatch.setattr(subprocess, "run", run)

    res = base_domain.scaffold_tier(ws)

    assert run.calls == [], (
        "scaffold_tier ran a subprocess with a base it cannot honour. The "
        f"refusal has to land BEFORE the process starts. Ran: {run.calls}")
    assert res["scaffolded"] is False
    assert res["detail"], "a refusal with no reason is not actionable"


def test_an_unidentified_base_is_refused_rather_than_admitted(monkeypatch, tmp_path):
    """Unknown means DENY, the same ruling #74 made for the harness."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _point_which_base_at(monkeypatch, str(tmp_path / "bin" / "nothing-here"))
    run = _CountingRun()
    monkeypatch.setattr(subprocess, "run", run)

    res = base_domain.scaffold_tier(ws)

    assert run.calls == [], "an unreadable base was admitted and a process ran"
    assert res["scaffolded"] is False


def test_a_native_base_still_scaffolds(monkeypatch, tmp_path):
    """THE CONTROL. If this goes red the guard refuses the legitimate case and
    is not discriminating, merely refusing."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _point_which_base_at(monkeypatch, _binary(tmp_path / "bin" / "base", _native_magic()))
    run = _CountingRun(0)
    monkeypatch.setattr(subprocess, "run", run)

    res = base_domain.scaffold_tier(ws)

    assert len(run.calls) == 1, (
        "the guard refused a base built for this very host")
    assert run.calls[0][1] == "scaffold"
    assert res["scaffolded"] is True


def test_base_absent_is_still_degraded_not_refused(monkeypatch, tmp_path):
    """The guard must not turn 'this machine has no base' into a new failure.

    Absence is degraded, never broken. This is also the state the autouse
    fixture puts every other test in, so a regression here would be wide.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    run = _CountingRun()
    monkeypatch.setattr(subprocess, "run", run)

    res = base_domain.scaffold_tier(ws)

    assert run.calls == []
    assert res["scaffolded"] is False
    assert "degraded" in res["detail"]


def test_the_refusal_names_the_binary_and_the_workspace(monkeypatch, tmp_path):
    """An operator runs `cadre init` by hand, so the reason has to say which
    binary was resolved and which tier it would have written."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _point_which_base_at(monkeypatch, _binary(tmp_path / "bin" / "base", _foreign_magic()))
    monkeypatch.setattr(subprocess, "run", _CountingRun())

    detail = base_domain.scaffold_tier(ws)["detail"]

    assert "base" in detail
    assert str(ws) in detail, "the refusal does not name the workspace it meant"


def test_scaffold_tier_never_raises_on_a_foreign_base(monkeypatch, tmp_path):
    """scaffold_tier's callers read a dict and branch on `scaffolded`. A guard
    that raises breaks both of them.

    NOT A RED ARM: the unfixed tree does not raise either, it does the wrong
    thing quietly. This guards the contract against a guard written with a bare
    raise or an assert, which is the obvious way to write it and the way that
    would break cli/init.py and wire_firm.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    _point_which_base_at(monkeypatch, _binary(tmp_path / "bin" / "base", _foreign_magic()))
    monkeypatch.setattr(subprocess, "run", _CountingRun())

    res = base_domain.scaffold_tier(ws)  # must not raise
    assert isinstance(res, dict)
    assert set(res) >= {"scaffolded", "detail"}
