"""base is here, and `base cadre` runs — two facts, never one flag (#118).

Founding never asked either question. The module under test is what asks them,
and this file is what stops the answers from collapsing into each other: the
failure this product has already had is `base extension install` exiting 0 over
a `base cadre` that exits 127, so a manifest on disk and a command that resolves
must stay two separate readings.

EVERY FAILING LEG HERE HAS ITS HEALTHY CONTROL BESIDE IT. A leg that only ever
sees the broken input cannot show that it discriminates -- it can only show that
it prints red. The controls are named in their docstrings so a future reader
cannot mistake one for a redundant test and delete it.

THE STUB BASE IS A REAL FILE carrying this host's magic bytes, never the string
"/fake/base". `install` identifies the binary it resolved before running it
(#75) and refuses an unidentified one, so a string stub would make every arm
here unable to tell REFUSED CORRECTLY from NEVER REACHED THE CHECK.
tests/test_no_weak_base_stubs.py fails the build over it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from firm.services import base_extension, base_ready


@pytest.fixture(autouse=True)
def _every_leg_controls_its_cwd(monkeypatch, tmp_path):
    """No leg here runs from whatever directory pytest was started in.

    `BASE_HOME` isolates the HOME tier. base ALSO resolves a WORKSPACE tier by
    walking up from the current directory, which no environment variable
    touches -- so a leg that inherits the developer's checkout is measuring
    that checkout while claiming to measure a fixture. That is how a "fresh
    install is UNHEALTHY" line once got reported about an operator's own
    workspace graph.
    """
    box = tmp_path / "cwd"
    box.mkdir(exist_ok=True)
    monkeypatch.chdir(box)


# ---------------------------------------------------------------------------
# The machine, faked at the process boundary
# ---------------------------------------------------------------------------

class _Base:
    """Answers `base` calls by VERB, and records every one of them.

    By verb rather than by position, because `check` makes one or two calls
    depending on what it finds and `ensure` makes up to five. A recorder that
    pops return codes in order silently re-maps every answer the moment a
    branch changes, which is a test that passes for a reason nobody chose.

    `extension install` COPIES the staged file into the tier, because that is
    what base does. Without it `install`'s read-back would fail for a reason
    this file is not testing, and the arms below would all be measuring the
    same missing file.
    """

    def __init__(self, *, version_rc: int = 0, cadre_rc: int = 0,
                 cadre_rc_after_install: int | None = None,
                 cadre_rc_seq: list[int] | None = None,
                 validate_rc: int = 0, install_rc: int = 0) -> None:
        self.version_rc = version_rc
        self.cadre_rc = cadre_rc
        self.cadre_rc_after_install = (cadre_rc if cadre_rc_after_install is None
                                       else cadre_rc_after_install)
        # One answer per `base cadre --help` call, in order. The only way to
        # model an installer whose OWN probe succeeds while the next reading
        # disagrees -- which is the state that separates "re-read the machine"
        # from "believe the installer".
        self.cadre_rc_seq = list(cadre_rc_seq) if cadre_rc_seq else None
        self.validate_rc = validate_rc
        self.install_rc = install_rc
        self.installed_once = False
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        argv = [str(c) for c in cmd]
        self.calls.append(argv)
        rest = argv[1:]
        out, rc = "", 0
        if rest[:1] == ["--version"]:
            rc, out = self.version_rc, "base 0.15.0"
        elif rest[:2] == ["cadre", "--help"]:
            if self.cadre_rc_seq:
                rc = self.cadre_rc_seq.pop(0)
            elif self.cadre_rc_seq is not None:
                rc = self.cadre_rc          # sequence exhausted: hold the last stance
            else:
                rc = (self.cadre_rc_after_install if self.installed_once
                      else self.cadre_rc)
            out = ("usage: cadre [-h] [--version] <command> ..." if rc == 0
                   else "base: command 'cadre' (ext:cadre) — handler not found")
        elif rest[:2] == ["extension", "validate"]:
            rc = self.validate_rc
            out = "ok" if rc == 0 else "invalid manifest"
        elif rest[:2] == ["extension", "install"]:
            rc = self.install_rc
            if rc == 0:
                staged = Path(rest[2])
                landed = base_extension._installed_path()
                landed.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(staged, landed)
                self.installed_once = True
            out = "Installed cadre v0.2.0" if rc == 0 else "install failed"

        class R:
            returncode = rc
            stdout = out
            stderr = ""
        return R()

    def verbs(self) -> list[str]:
        """What was actually asked of base, in order, for readable assertions."""
        seen = []
        for c in self.calls:
            rest = c[1:]
            seen.append(" ".join(rest[:2]) if len(rest) > 1 else " ".join(rest[:1]))
        return seen

    def count(self, verb: str) -> int:
        return sum(1 for v in self.verbs() if v == verb)


@pytest.fixture
def stub_base(monkeypatch, tmp_path) -> Path:
    """A base this host could actually identify, plus a throwaway tier."""
    from firm.sysconfig.binaries import native_image_format

    magic = {"pe": b"MZ\x90\x00", "macho": b"\xcf\xfa\xed\xfe"}.get(
        native_image_format(), b"\x7fELF")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "base"
    stub.write_bytes(magic + b"\x00" * 128)
    stub.chmod(0o755)
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: str(stub))
    monkeypatch.setenv("BASE_HOME", str(tmp_path))
    (tmp_path / ".base-gbl" / "extensions").mkdir(parents=True)
    return tmp_path


def _land(home: Path, text: str = 'name = "cadre"\nframework_dir = "/opt/cadre"\n') -> None:
    (home / ".base-gbl" / "extensions" / "cadre.toml").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# L1 / L2 — base absent, and the control that proves the leg discriminates
# ---------------------------------------------------------------------------

def test_l1_base_absent_is_named_and_spawns_nothing(monkeypatch):
    """No base: say so in the operator's words, and run no subprocess at all.

    The count matters as much as the words. A check that shells out to a binary
    it has already decided is missing is a check that will one day run the
    operator's real base from a screen render.
    """
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    fake = _Base()
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.check()

    assert state["base_present"] is False
    assert state["ok"] is False
    assert state["missing"] == [base_ready.MISSING_BASE]
    assert "not installed" in state["reason"], state["reason"]
    assert fake.calls == [], f"subprocesses were spawned with no base: {fake.verbs()}"


def test_base_absent_is_skipped_not_failed_and_it_is_a_field(monkeypatch):
    """SKIPPED and FAILED are different facts, and the caller reads a FIELD.

    `run_install` once decided its exit code by looking for "not installed"
    inside a human sentence, and a base under a directory of that name carried
    the phrase into a genuine refusal, which then reported success (#83). So
    nothing downstream of this module may branch on the prose.
    """
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    monkeypatch.setattr(subprocess, "run", _Base())

    checked = base_ready.check()
    ensured = base_ready.ensure()

    assert checked["skipped"] is True
    assert ensured["skipped"] is True
    assert ensured["install"] == {}, "install was called with no base to call it on"
    assert ensured["ok"] is False


def test_the_reading_says_which_directory_it_was_taken_from(monkeypatch, tmp_path,
                                                            stub_base):
    """base resolves a workspace tier by walking up from cwd, so a reading
    whose directory nobody recorded is a reading nobody can attribute."""
    monkeypatch.setattr(subprocess, "run", _Base())
    firm_dir = tmp_path / "a-firm"
    firm_dir.mkdir()

    assert base_ready.check(firm_dir)["probe_cwd"] == str(firm_dir)
    assert base_ready.check()["probe_cwd"] == str(Path.cwd())


def test_l2_control_a_healthy_machine_reads_every_key_true(monkeypatch, stub_base):
    """The control for L1 and L4. Without it, red is the only colour this prints."""
    fake = _Base()
    monkeypatch.setattr(subprocess, "run", fake)
    _land(stub_base)

    state = base_ready.check()

    assert state["base_present"] is True
    assert state["base_runs"] is True
    assert state["extension_installed"] is True
    assert state["extension_runs"] is True
    assert state["ok"] is True, state["reason"]
    assert fake.count("--version") == 1
    assert fake.count("cadre --help") == 1


def test_a_base_that_does_not_run_is_named_rather_than_assumed_absent(
        monkeypatch, stub_base):
    """On PATH and broken is a third state, and it is not "not installed"."""
    fake = _Base(version_rc=1)
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.check()

    assert state["base_present"] is True
    assert state["base_runs"] is False
    assert state["ok"] is False
    assert "does not run" in state["reason"], state["reason"]
    assert fake.count("cadre --help") == 0, (
        "the extension was probed with a base that cannot run")


# ---------------------------------------------------------------------------
# L4 / L5 — the F1 shape: installed and dead, against installed and alive
# ---------------------------------------------------------------------------

def test_l4_a_manifest_on_disk_over_a_dead_command_reads_as_two_facts(
        monkeypatch, stub_base):
    """THE defect this lane exists for, and the reason for two keys.

    `base extension install` once returned rc 0, printed "Installed cadre
    v0.2.0", listed the extension, and left `base cadre` exiting 127. One flag
    covering both readings reports that machine as ready.
    """
    fake = _Base(cadre_rc=127)
    monkeypatch.setattr(subprocess, "run", fake)
    _land(stub_base)

    state = base_ready.check()

    assert state["extension_installed"] is True, "the manifest IS on disk"
    assert state["extension_runs"] is False, "and the command does NOT run"
    assert state["ok"] is False
    assert "does not run" in state["reason"]
    assert base_ready.MISSING_EXTENSION in state["missing"]


def test_l5_control_the_same_manifest_with_a_live_command_is_ok(
        monkeypatch, stub_base):
    """Control for L4: same file on disk, only the command's answer differs."""
    fake = _Base(cadre_rc=0)
    monkeypatch.setattr(subprocess, "run", fake)
    _land(stub_base)

    state = base_ready.check()

    assert state["extension_installed"] is True
    assert state["extension_runs"] is True
    assert state["ok"] is True


def test_a_manifest_that_is_not_ours_does_not_count_as_installed(
        monkeypatch, stub_base):
    """A file at the path is not the same claim as our manifest at the path."""
    fake = _Base(cadre_rc=127)
    monkeypatch.setattr(subprocess, "run", fake)
    _land(stub_base, 'name = "somethingelse"\n')

    state = base_ready.check()

    assert state["extension_installed"] is False
    assert state["ok"] is False


def test_an_unrendered_placeholder_does_not_count_as_installed(
        monkeypatch, stub_base):
    """F1 again, one layer down: a landed manifest still carrying a placeholder
    names a handler that exists on no machine."""
    fake = _Base(cadre_rc=127)
    monkeypatch.setattr(subprocess, "run", fake)
    _land(stub_base,
          'name = "cadre"\nframework_dir = "' + base_extension.PLACEHOLDER + '"\n')

    state = base_ready.check()

    assert state["extension_installed"] is False


def test_a_command_that_runs_with_no_manifest_here_says_which_it_is(
        monkeypatch, stub_base):
    """The two observers disagreeing IS the finding, not noise to average out.

    `base cadre` running while this tier holds no manifest means base resolved
    the command from a different tier. Reporting that as a clean pass would
    make the firm's own tier invisible the day it starts mattering.
    """
    fake = _Base(cadre_rc=0)
    monkeypatch.setattr(subprocess, "run", fake)
    # nothing landed

    state = base_ready.check()

    assert state["extension_runs"] is True
    assert state["extension_installed"] is False
    assert state["ok"] is False
    assert "another tier" in state["reason"], state["reason"]


# ---------------------------------------------------------------------------
# L3 / L6 — the repair, and the repair that does not take
# ---------------------------------------------------------------------------

def test_l3_a_missing_extension_is_installed_once_and_then_re_read(
        monkeypatch, stub_base):
    """DoD 4: no manual follow-up command. Founding runs it.

    Exactly one install, and a fresh reading afterwards rather than the
    installer's own verdict.
    """
    fake = _Base(cadre_rc=127, cadre_rc_after_install=0)
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.ensure()

    assert fake.count("extension install") == 1, fake.verbs()
    assert fake.count("extension validate") == 1, "validate must precede install"
    assert state["repaired"] is True
    assert state["ok"] is True
    assert state["extension_runs"] is True
    # The re-read is a SECOND process, after the installer had finished.
    assert fake.count("cadre --help") >= 2, fake.verbs()


def test_ensure_installs_nothing_when_the_command_already_runs(
        monkeypatch, stub_base):
    """Control for L3. A repair that fires on a healthy machine is a defect:
    it would reinstall the manifest on every founding forever."""
    fake = _Base(cadre_rc=0)
    monkeypatch.setattr(subprocess, "run", fake)
    _land(stub_base)

    state = base_ready.ensure()

    assert state["ok"] is True
    assert state["repaired"] is False
    assert fake.count("extension install") == 0, fake.verbs()


def test_l6_an_install_that_reports_success_over_a_dead_command_is_not_ok(
        monkeypatch, stub_base):
    """The masking leg. Without it, "repaired" and "silenced" look identical.

    base answers 0 to validate and to install, the file lands and reads back
    correctly, and `base cadre` still does not run. The only honest answer is
    not-ok carrying the reason.
    """
    fake = _Base(cadre_rc=127, cadre_rc_after_install=127)
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.ensure()

    assert fake.count("extension install") == 1
    assert state["ok"] is False
    assert state["repaired"] is False
    assert state["extension_runs"] is False
    assert state["reason"], "a failed repair must say why"
    assert "does not run" in state["reason"], state["reason"]


def test_an_installer_that_claims_success_is_not_taken_at_its_word(
        monkeypatch, stub_base):
    """The leg that separates "re-read the machine" from "believe the writer".

    base answers the installer's own handler probe with 0, so `install` returns
    ok True and the manifest is genuinely on disk -- and the very next reading
    of the same command says 127. Whatever the cause, the honest report is the
    disagreement, and the state a Member will meet is the SECOND reading.

    Remove the re-read from `ensure` and this is the only leg that notices,
    which is exactly why it exists: under L3 and L6 the installer's verdict and
    the re-read agree, so those two cannot see the difference.
    """
    # first check -> dead, install's own probe -> alive, ensure's re-read -> dead
    fake = _Base(cadre_rc=127, cadre_rc_seq=[127, 0, 127])
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.ensure()

    assert state["install"]["ok"] is True, "precondition: the installer was happy"
    assert state["ok"] is False, "ensure believed the installer over the machine"
    assert state["extension_runs"] is False
    assert "disagrees" in state["reason"], state["reason"]


def test_ensure_with_no_base_installs_nothing_and_spawns_nothing(monkeypatch):
    """base absent is skipped, not failed -- and it costs no subprocess."""
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    fake = _Base()
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.ensure()

    assert state["ok"] is False
    assert state["repaired"] is False
    assert state["install"] == {}
    assert fake.calls == [], fake.verbs()


def test_a_validation_failure_stops_before_the_install(monkeypatch, stub_base):
    """`install` refuses to install a manifest it could not validate, and
    `ensure` must report that refusal rather than a bare "not ready"."""
    fake = _Base(cadre_rc=127, validate_rc=1)
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.ensure()

    assert fake.count("extension validate") == 1
    assert fake.count("extension install") == 0, "installed after a failed validate"
    assert state["ok"] is False
    assert "did not validate" in state["reason"], state["reason"]


# ---------------------------------------------------------------------------
# The contract the callers depend on
# ---------------------------------------------------------------------------

def test_neither_function_raises_when_base_explodes(monkeypatch, stub_base):
    """`check` is called from a screen render and `ensure` from mid-founding.
    An exception in either costs a firm over a host problem."""
    def _boom(cmd, **kwargs):
        raise OSError("the binary vanished mid-call")

    monkeypatch.setattr(subprocess, "run", _boom)

    state = base_ready.check()
    assert state["ok"] is False
    assert state["reason"]

    state = base_ready.ensure()
    assert state["ok"] is False
    assert state["reason"]


def test_the_tier_is_resolved_in_exactly_one_place(monkeypatch, stub_base):
    """#118's interim, pinned so it cannot spread.

    Founding installs into the AMBIENT tier today, because
    `base_extension._installed_path()` takes no argument and the firm-private
    tier (`<firm>/.firm/base-home`) is gadwall's lane. This asserts the whole
    decision still lives in `_tier`, so landing that change stays a
    one-function edit rather than a hunt.
    """
    import ast

    source = Path(base_ready.__file__).read_text(encoding="utf-8")
    # Parsed, not grepped. This module's docstrings NAME `_installed_path`
    # several times while explaining the interim, and a substring count cannot
    # tell prose from a call -- the same trap tests/test_no_weak_base_stubs.py
    # solves with tokenize.
    calls = [node for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.Attribute) and node.attr == "_installed_path"]
    assert len(calls) == 1, (
        f"visited {len(calls)} real uses of _installed_path in base_ready.py, "
        "expected exactly 1: the tier decision must stay inside _tier()")

    manifest, kwargs = base_ready._tier(stub_base)
    assert manifest == base_extension._installed_path()
    assert kwargs == {}
