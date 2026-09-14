"""Founding checks base before it makes a firm, and says what is missing (#118).

Chris's words: "firms should be fully put together through founding, not have to
be worked on to get working when they just got founded".

What this file pins, and each fails separately:

1. The base reading is taken BEFORE the workspace exists.
2. Founding NEVER refuses over base -- degraded, never broken.
3. `base_cadre_runs` is read in the FIRM'S OWN TIER, the one every Member is
   spawned into since #117. An install that only exists in the operator's tier
   leaves every Member with rc 127 (measured), so it must read False.
4. Founding writes nothing into the operator's tier. The repair that used to
   live here installed Cadre's manifest there; it fixed nothing for a Member
   and was removed on that measurement.
5. `readiness` names the gap, non-blocking and read-only.

RETIRED: the leg that asserted a healthy machine ends founding with the
extension resolving (the old L10). It was true only because the fake base
answered from the operator's tier, where the removed repair put the manifest.
With the tier Members actually get, founding cannot make that true yet -- the
firm-tier install is not built -- and a leg asserting it would have to lie.
DoD 4 is parked in the build record with its owner named, not faked here.
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
    """Nothing here runs from the directory pytest was started in: base walks up
    from the current directory for a workspace tier, which BASE_HOME never touches."""
    box = tmp_path / "cwd"
    box.mkdir(exist_ok=True)
    monkeypatch.chdir(box)


def _proposal(fid: str, name: str = "Zed Base") -> dict:
    """Same minimal shape tests/services/test_base_export.py founds with."""
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
    """Every `base` call a founding makes; `base cadre` answers BY TIER.

    rc 0 only when the manifest sits in the extensions directory of the
    BASE_HOME the call was made with -- what the real base did in the probe
    recorded in src/firm/services/base_ready.py.

    `anywhere=True`: `base cadre --help` exits 0 whatever BASE_HOME says -- a
    base resolving the command from outside the firm's tier.
    """

    def __init__(self, *, anywhere: bool = False) -> None:
        self.anywhere = anywhere
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        argv = [str(c) for c in cmd]
        self.calls.append(argv)
        env = dict(kwargs.get("env") or {})
        rest = argv[1:]
        rc, out = 0, ""
        if rest[:1] == ["--version"]:
            out = "base 0.15.2"
        elif rest[:2] == ["cadre", "--help"]:
            home = env.get("BASE_HOME", "")
            ok = self.anywhere or bool(home) and (
                Path(home) / ".base-gbl" / "extensions" / "cadre.toml").is_file()
            rc = 0 if ok else 127
            out = "usage: cadre" if ok else "base: unknown command 'cadre'"

        class R:
            returncode = rc
            stdout = out
            stderr = ""
        return R()


@pytest.fixture
def stub_base(monkeypatch, tmp_path) -> Path:
    """A real file with this host's magic bytes, and an OPERATOR tier that
    already holds a live manifest -- the machine the first version misread."""
    from firm.sysconfig.binaries import native_image_format

    magic = {"pe": b"MZ\x90\x00", "macho": b"\xcf\xfa\xed\xfe"}.get(
        native_image_format(), b"\x7fELF")
    operator = tmp_path / "operator-home"
    stub = operator / "bin" / "base"
    stub.parent.mkdir(parents=True)
    stub.write_bytes(magic + b"\x00" * 128)
    stub.chmod(0o755)
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: str(stub))
    monkeypatch.setenv("BASE_HOME", str(operator))
    ext = operator / ".base-gbl" / "extensions"
    ext.mkdir(parents=True)
    (ext / "cadre.toml").write_text('name = "cadre"\nframework_dir = "/opt/cadre"\n',
                                    encoding="utf-8")
    return operator


# ---------------------------------------------------------------------------
# L7 — base absent: founded anyway, and the truth is in the result
# ---------------------------------------------------------------------------

def test_l7_a_firm_is_founded_without_base_and_the_result_says_so(monkeypatch, tmp_path):
    """Degraded is reported as degraded, and the firm still lands on disk."""
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)

    result = founding.commit(tmp_path, _proposal("zqnobase"))

    assert result.get("ok") is True, result
    assert get_db_path(tmp_path / "zqnobase").exists(), "the firm must exist"
    assert result["base_present"] is False
    assert result["base_cadre_runs"] is False
    assert result["base_ready"]["skipped"] is True
    assert base_ready.MISSING_BASE in result["base_ready"]["missing"]


def test_the_base_reading_is_taken_before_the_workspace_exists(monkeypatch, tmp_path):
    """DoD 1, pinned by ORDER rather than by the presence of a call."""
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
    assert seen[0] is False, f"the first base reading came after the workspace existed: {seen}"


# ---------------------------------------------------------------------------
# L8 — the operator's tier has it, the firm's tier does not
# ---------------------------------------------------------------------------

def test_l8_an_install_only_in_the_operators_tier_is_reported_as_not_running(
        monkeypatch, tmp_path, stub_base):
    """The machine the first version reported as ready: a live manifest in the
    operator's tier, none in the firm's. Every Member gets 127 here."""
    monkeypatch.setattr(subprocess, "run", _Machine())

    result = founding.commit(tmp_path, _proposal("zqtier"))

    assert result.get("ok") is True, result
    assert result["base_present"] is True
    assert result["base_cadre_runs"] is False, (
        "founding reported base cadre running from the operator's tier")
    assert base_ready.MISSING_EXTENSION in result["base_ready"]["missing"]
    assert result["base_ready"]["reason"], "the gap must be named"


def test_founding_writes_nothing_into_the_operators_tier(monkeypatch, tmp_path, stub_base):
    """The removed repair installed Cadre's manifest into the operator's tier.
    It fixed nothing for a Member, and this is what keeps it removed."""
    monkeypatch.setattr(subprocess, "run", _Machine())

    def _never(*args, **kwargs):
        raise AssertionError("founding reached base_extension.install")

    monkeypatch.setattr(base_extension, "install", _never)
    ext = stub_base / ".base-gbl" / "extensions"
    before = {p.name: p.read_bytes() for p in ext.iterdir()}

    result = founding.commit(tmp_path, _proposal("zqclean"))

    after = {p.name: p.read_bytes() for p in ext.iterdir()}
    assert result.get("ok") is True, result
    assert after == before, "founding changed the operator's extensions directory"
    assert len(before) == 1, f"visited {len(before)} files; the fixture must hold one"


# ---------------------------------------------------------------------------
# L9 — the readiness screen names it, and never repairs while rendering
# ---------------------------------------------------------------------------

def test_l9_readiness_names_base_and_the_command_without_blocking_the_firm(
        monkeypatch, tmp_path):
    """A firm with no base is an ordinary firm. Only its law may block it."""
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    assert founding.commit(tmp_path, _proposal("zqready9"))["ok"] is True

    state = founding.readiness(tmp_path, "zqready9")

    keys = [c["key"] for c in state["checks"]]
    assert "base" in keys and "base_cadre" in keys, keys
    assert set(state["blocking"]) & {"base", "base_cadre"} == set(), state["blocking"]
    named = [c for c in state["checks"] if c["key"] in ("base", "base_cadre")]
    assert len(named) == 2, f"visited {len(named)} of 2 rows"
    for check in named:
        assert check["ok"] is False
        assert check["detail"], f"{check['key']} names nothing an operator can act on"


def test_readiness_never_installs_anything(monkeypatch, tmp_path, stub_base):
    """A read-only check that quietly repairs is worse than one that reports."""
    monkeypatch.setattr(subprocess, "run", _Machine())
    assert founding.commit(tmp_path, _proposal("zqro"))["ok"] is True

    def _never(*args, **kwargs):
        raise AssertionError("readiness reached base_extension.install")

    monkeypatch.setattr(base_extension, "install", _never)

    state = founding.readiness(tmp_path, "zqro")

    row = [c for c in state["checks"] if c["key"] == "base_cadre"]
    assert len(row) == 1 and row[0]["ok"] is False, row


# ---------------------------------------------------------------------------
# G2 F1 / F2 / F4 — the screen and the result follow check's own verdict
# ---------------------------------------------------------------------------

def _magic(native: bool) -> bytes:
    from firm.sysconfig.binaries import native_image_format

    kind = native_image_format()
    if native:
        return {"pe": b"MZ\x90\x00", "macho": b"\xcf\xfa\xed\xfe"}.get(kind, b"\x7fELF")
    return b"\x7fELF" if kind == "pe" else b"MZ\x90\x00"


def _point_at_stub(monkeypatch, tmp_path: Path, *, native: bool) -> Path:
    stub = tmp_path / ("native-bin" if native else "foreign-bin") / "base"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_bytes(_magic(native) + b"\x00" * 128)
    stub.chmod(0o755)
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: str(stub))
    return stub


def _bare_firm(tmp_path: Path, fid: str) -> Path:
    """A workspace with the firm's tier and nothing else. `readiness` guards
    its database reads, so no founding is needed -- and without one, a
    mutation of `commit` cannot reach these legs."""
    from firm.services.graph_isolation import ensure_tier

    ws = tmp_path / fid
    ws.mkdir(parents=True)
    ensure_tier(ws)
    return ws


def _rows(state: dict) -> dict:
    rows = {c["key"]: c for c in state["checks"] if c["key"] in ("base", "base_cadre")}
    assert len(rows) == 2, f"visited {len(rows)} of the 2 base rows"
    return rows


def test_a_refused_base_reads_unhealthy_on_both_readiness_rows(monkeypatch, tmp_path):
    """G2 F1 on the screen: a base this host cannot run is not "base at ...".
    The command runs in any tier, which is what a Windows base under WSL does,
    so only the refusal keeps either row from reading healthy."""
    _bare_firm(tmp_path, "zqrefusedrows")
    _point_at_stub(monkeypatch, tmp_path, native=False)
    monkeypatch.setattr(subprocess, "run", _Machine(anywhere=True))

    rows = _rows(founding.readiness(tmp_path, "zqrefusedrows"))

    assert rows["base"]["ok"] is False, rows["base"]
    assert "Refused before anything ran" in rows["base"]["detail"], rows["base"]
    assert rows["base_cadre"]["ok"] is False, rows["base_cadre"]
    assert "installed" not in rows["base_cadre"]["detail"], rows["base_cadre"]


def test_founding_with_a_refused_base_never_reports_the_command_running(
        monkeypatch, tmp_path):
    _point_at_stub(monkeypatch, tmp_path, native=False)
    monkeypatch.setattr(subprocess, "run", _Machine(anywhere=True))

    result = founding.commit(tmp_path, _proposal("zqrefused"))

    assert result.get("ok") is True, result
    assert result["base_present"] is True
    assert result["base_cadre_runs"] is False, result["base_ready"]
