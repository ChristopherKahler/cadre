"""Founding checks base before it makes a firm, and says what is missing (#118).

Chris's words: "firms should be fully put together through founding, not have to
be worked on to get working when they just got founded".

Three things this file pins, and they fail separately:

1. The base reading is taken BEFORE the workspace exists. A check that runs
   after the firm is on disk is reporting on a machine the founding had already
   started changing.
2. Founding NEVER refuses over base. A licensee may not carry base at all, and a
   firm without it is degraded, never broken -- the same contract
   `base_domain.sync`, `base_export.export` and `base_extension.install` keep.
   What founding must not do is hand back a fresh-looking firm that is quietly
   incomplete.
3. `readiness` names the gap on the screen the Board is shown, and it does it
   READ-ONLY. A screen that repairs the machine while rendering it makes the
   before-state unknowable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from firm.core.db import get_db_path
from firm.dashboard import founding
from firm.services import base_extension, base_ready


@pytest.fixture(autouse=True)
def _every_leg_controls_its_cwd(monkeypatch, tmp_path):
    """Nothing here runs from the directory pytest was started in.

    `BASE_HOME` isolates the HOME tier only. base also resolves a WORKSPACE
    tier by walking up from the current directory, so a founding measured from
    a developer's checkout is measuring that checkout.
    """
    box = tmp_path / "cwd"
    box.mkdir(exist_ok=True)
    monkeypatch.chdir(box)


def _proposal(fid: str, name: str = "Zed Base") -> dict:
    """The smallest thing `commit` accepts, same shape tests/services/
    test_base_export.py uses, so these legs measure the wiring and not a
    proposal this file invented."""
    return {
        "firm_id": fid,
        "name": name,
        "premise": "a firm for measuring the base wiring with",
        "north_star": {"target": "prove founding checks base"},
        "operations": [{"name": "Running it", "purpose": "keep it running"}],
        "members": [{"name": "Vantage", "role": "Chief of Staff",
                     "owns": "everything", "operation": "Running it",
                     "leads": True, "model": "sonnet",
                     "skills": [], "gates": []}],
    }


class _Machine:
    """A stand-in for every `base` call a founding makes, answering by verb."""

    def __init__(self, *, cadre_rc: int = 0) -> None:
        self.cadre_rc = cadre_rc
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        argv = [str(c) for c in cmd]
        self.calls.append(argv)
        rest = argv[1:]
        rc, out = 0, ""
        if rest[:1] == ["--version"]:
            out = "base 0.15.0"
        elif rest[:2] == ["cadre", "--help"]:
            rc = self.cadre_rc
            out = ("usage: cadre [-h] [--version] <command> ..." if rc == 0
                   else "handler not found")
        elif rest[:2] == ["extension", "install"]:
            landed = base_extension._installed_path()
            landed.parent.mkdir(parents=True, exist_ok=True)
            landed.write_text(Path(rest[2]).read_text(encoding="utf-8"),
                              encoding="utf-8")

        class R:
            returncode = rc
            stdout = out
            stderr = ""
        return R()


@pytest.fixture
def stub_base(monkeypatch, tmp_path) -> Path:
    """A real file with this host's magic bytes, and a throwaway global tier."""
    from firm.sysconfig.binaries import native_image_format

    magic = {"pe": b"MZ\x90\x00", "macho": b"\xcf\xfa\xed\xfe"}.get(
        native_image_format(), b"\x7fELF")
    home = tmp_path / "basehome"
    bindir = home / "bin"
    bindir.mkdir(parents=True)
    stub = bindir / "base"
    stub.write_bytes(magic + b"\x00" * 128)
    stub.chmod(0o755)
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: str(stub))
    monkeypatch.setenv("BASE_HOME", str(home))
    (home / ".base-gbl" / "extensions").mkdir(parents=True)
    return home


# ---------------------------------------------------------------------------
# L7 — base absent: founded anyway, and the truth is in the result
# ---------------------------------------------------------------------------

def test_l7_a_firm_is_founded_without_base_and_the_result_says_so(
        monkeypatch, tmp_path):
    """The honesty envelope. Degraded is reported as degraded, and the firm
    still lands on disk -- refusing here would make a fact about the host read
    as a defect in Cadre."""
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)

    result = founding.commit(tmp_path, _proposal("zqnobase"))

    assert result.get("ok") is True, result
    assert get_db_path(tmp_path / "zqnobase").exists(), "the firm must exist"
    assert result["base_present"] is False
    assert result["base_cadre_runs"] is False
    assert result["base_ready"]["reason"], "an absent base must be named"
    assert base_ready.MISSING_BASE in result["base_ready"]["missing"]


def test_the_base_reading_is_taken_before_the_workspace_exists(
        monkeypatch, tmp_path):
    """DoD 1, pinned by ORDER rather than by the presence of a call.

    Moving the check below the mkdir would leave every other assertion in this
    file green while the product stopped answering the question the issue asks:
    does founding know before it starts making a firm.
    """
    workspace = tmp_path / "zqorder"
    seen: list[bool] = []
    real_check = base_ready.check

    def _spy(ws=None):
        seen.append(workspace.exists())
        return real_check(ws)

    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    monkeypatch.setattr(base_ready, "check", _spy)

    result = founding.commit(tmp_path, _proposal("zqorder"))

    assert result.get("ok") is True, result
    assert seen, "founding never asked whether base was there"
    assert seen[0] is False, (
        f"the first base reading was taken after the workspace existed: {seen}")


# ---------------------------------------------------------------------------
# L8 — base present, extension dead: founded, and the gap is named
# ---------------------------------------------------------------------------

def test_l8_an_extension_that_does_not_run_is_named_in_the_result(
        monkeypatch, tmp_path, stub_base):
    """`base cadre` exiting 127 is the failure this product has already had.
    The firm is still founded; what it must not do is look complete."""
    monkeypatch.setattr(subprocess, "run", _Machine(cadre_rc=127))

    result = founding.commit(tmp_path, _proposal("zqdead"))

    assert result.get("ok") is True, result
    assert result["base_present"] is True
    assert result["base_cadre_runs"] is False
    assert base_ready.MISSING_EXTENSION in result["base_ready"]["missing"]
    assert "does not run" in result["base_ready"]["reason"], (
        result["base_ready"]["reason"])


def test_l10_a_healthy_machine_founds_a_firm_whose_command_already_runs(
        monkeypatch, tmp_path, stub_base):
    """DoD 4, and the control for L8: no follow-up command for a human to type.

    The extension is absent when the founding starts and resolving when it
    finishes, with nothing typed in between.
    """
    machine = _Machine(cadre_rc=0)
    monkeypatch.setattr(subprocess, "run", machine)
    assert not base_extension._installed_path().exists(), "precondition"

    result = founding.commit(tmp_path, _proposal("zqready"))

    assert result.get("ok") is True, result
    assert result["base_present"] is True
    assert result["base_cadre_runs"] is True
    assert result["base_ready"]["ok"] is True, result["base_ready"]["reason"]
    assert base_extension._installed_path().exists(), (
        "founding left no manifest in the tier it validated")


# ---------------------------------------------------------------------------
# L9 — the readiness screen names it, and never repairs while rendering
# ---------------------------------------------------------------------------

def test_l9_readiness_names_base_and_the_command_without_blocking_the_firm(
        monkeypatch, tmp_path):
    """A firm with no base is an ordinary firm. Only its law may block it.

    Both new rows are non-blocking, which is the same ruling this screen
    already makes about MCP servers -- "you could have more" is a different
    sentence from "you may not start".
    """
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    assert founding.commit(tmp_path, _proposal("zqready9"))["ok"] is True

    state = founding.readiness(tmp_path, "zqready9")

    keys = [c["key"] for c in state["checks"]]
    assert "base" in keys, keys
    assert "base_cadre" in keys, keys
    assert set(state["blocking"]) & {"base", "base_cadre"} == set(), state["blocking"]
    named = [c for c in state["checks"] if c["key"] in ("base", "base_cadre")]
    assert len(named) == 2, f"visited {len(named)} of 2 rows"
    for check in named:
        assert check["ok"] is False
        assert check["detail"], f"{check['key']} reports nothing an operator can act on"


def test_readiness_never_installs_anything(monkeypatch, tmp_path, stub_base):
    """A read-only check that quietly repairs is worse than one that reports:
    nobody can say afterwards what the machine looked like before it rendered."""
    monkeypatch.setattr(subprocess, "run", _Machine(cadre_rc=0))
    assert founding.commit(tmp_path, _proposal("zqro"))["ok"] is True

    def _never(*args, **kwargs):
        raise AssertionError("readiness reached base_extension.install")

    monkeypatch.setattr(base_extension, "install", _never)

    state = founding.readiness(tmp_path, "zqro")

    assert [c["key"] for c in state["checks"] if c["key"] == "base"] == ["base"]
