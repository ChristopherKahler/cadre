"""base is here, and `base cadre` runs FOR THE FIRM'S MEMBERS (#118).

Two facts, never one flag, and both read in the tier Members actually get.

RETIRED LEGS, AND WHY THEY ARE GONE RATHER THAN BENT. The first version of this
file pinned a repair: `ensure` installing the extension once (L3), a repair that
did not take (L6), and the installer not being taken at its word (ADIS). All
three assumed an install that helps a Member. Measured 2026-09-14 on the
operator's Windows base, only BASE_HOME differing: the operator's tier holding
the manifest -> rc 0; a firm tier with an empty extensions directory while the
operator's tier still held it -> rc 127; the same firm tier holding identical
manifest bytes -> rc 0. Since #117 every Member runs with the firm's tier, and
the only install that exists writes the operator's. So that repair fixed
nothing for any Member and wrote a tier it should not. Those legs were not made
to pass; the behaviour they described was removed, and the legs below pin its
absence instead.

EVERY FAILING LEG HAS ITS HEALTHY CONTROL BESIDE IT, named in its docstring, so
a red proves discrimination rather than only that the leg can print red.

THE STUB BASE IS A REAL FILE carrying this host's magic bytes, never a bare
string -- tests/test_no_weak_base_stubs.py fails the build over one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from firm.services import base_extension, base_ready
from firm.services.graph_isolation import tier_extensions_dir


@pytest.fixture(autouse=True)
def _every_leg_controls_its_cwd(monkeypatch, tmp_path):
    """No leg runs from the directory pytest was started in.

    BASE_HOME isolates the global tier; base ALSO resolves a workspace tier by
    walking up from the current directory, so a leg that inherits a developer's
    checkout is measuring that checkout.
    """
    box = tmp_path / "cwd"
    box.mkdir(exist_ok=True)
    monkeypatch.chdir(box)


# ---------------------------------------------------------------------------
# The machine, faked at the process boundary, modelled on what was MEASURED
# ---------------------------------------------------------------------------

class _Base:
    """Answers `base` by verb, and answers `base cadre` BY TIER.

    `base cadre --help` exits 0 only when the manifest is in the extensions
    directory of the BASE_HOME it was run with -- which is what the real base
    did in the three-arm probe recorded in the module docstring. A fake that
    answered by a fixed code would let a check pointed at the wrong tier pass,
    which is the precise defect this file now exists to catch.

    `dead=True` makes the command fail even where the manifest is present:
    the F1 shape, installed and listed and not running.
    """

    def __init__(self, *, version_rc: int = 0, dead: bool = False) -> None:
        self.version_rc = version_rc
        self.dead = dead
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, cmd, **kwargs):
        argv = [str(c) for c in cmd]
        env = dict(kwargs.get("env") or {})
        self.calls.append((argv, env))
        rest = argv[1:]
        rc, out = 0, ""
        if rest[:1] == ["--version"]:
            rc, out = self.version_rc, "base 0.15.2"
        elif rest[:2] == ["cadre", "--help"]:
            home = env.get("BASE_HOME", "")
            installed = bool(home) and (
                Path(home) / ".base-gbl" / "extensions" / "cadre.toml").is_file()
            rc = 0 if installed and not self.dead else 127
            out = ("usage: cadre [-h] [--version] <command> ..." if rc == 0
                   else "base: unknown command 'cadre'")

        class R:
            returncode = rc
            stdout = out
            stderr = ""
        return R()

    def count(self, verb: str) -> int:
        n = 0
        for argv, _ in self.calls:
            rest = argv[1:]
            seen = " ".join(rest[:2]) if len(rest) > 1 else " ".join(rest[:1])
            n += seen == verb
        return n

    def cadre_homes(self) -> list[str]:
        return [env.get("BASE_HOME", "") for argv, env in self.calls
                if argv[1:3] == ["cadre", "--help"]]


MANIFEST = 'name = "cadre"\nframework_dir = "/opt/cadre"\n'


@pytest.fixture
def machine(monkeypatch, tmp_path):
    """A base this host could identify, an OPERATOR tier, and a founded firm.

    Returns (operator_home, firm_workspace). The firm's tier is created the way
    founding creates it; nothing is installed in either tier.
    """
    from firm.services.graph_isolation import ensure_tier
    from firm.sysconfig.binaries import native_image_format

    magic = {"pe": b"MZ\x90\x00", "macho": b"\xcf\xfa\xed\xfe"}.get(
        native_image_format(), b"\x7fELF")
    stub = tmp_path / "bin" / "base"
    stub.parent.mkdir()
    stub.write_bytes(magic + b"\x00" * 128)
    stub.chmod(0o755)
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: str(stub))

    operator = tmp_path / "operator-home"
    (operator / ".base-gbl" / "extensions").mkdir(parents=True)
    monkeypatch.setenv("BASE_HOME", str(operator))

    firm = tmp_path / "firms" / "zqfirm"
    firm.mkdir(parents=True)
    ensure_tier(firm)
    return operator, firm


def _install_into(extensions: Path, text: str = MANIFEST) -> None:
    extensions.mkdir(parents=True, exist_ok=True)
    (extensions / "cadre.toml").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# base absent, and base without a firm
# ---------------------------------------------------------------------------

def test_l1_base_absent_is_named_skipped_and_spawns_nothing(monkeypatch):
    """No base: say so, mark it skipped as a FIELD (#83), run no subprocess."""
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    fake = _Base()
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.check()

    assert state["base_present"] is False
    assert state["skipped"] is True
    assert state["ok"] is False
    assert state["missing"] == [base_ready.MISSING_BASE]
    assert "not installed" in state["reason"], state["reason"]
    assert fake.calls == [], "subprocesses were spawned with no base"


def test_with_no_firm_only_base_is_answered_and_no_tier_is_probed(monkeypatch, machine):
    """Before a firm exists there is no Member tier. Probing the operator's
    tier instead is the defect this module's first version had."""
    fake = _Base()
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.check()

    assert state["base_present"] is True
    assert state["base_runs"] is True
    assert state["extension_runs"] is False
    assert fake.count("cadre --help") == 0, "the extension was probed with no firm"
    assert "no firm yet" in state["reason"], state["reason"]


def test_a_base_that_does_not_run_is_named_rather_than_assumed_absent(monkeypatch, machine):
    """On PATH and broken is its own state, and it is not "not installed"."""
    _, firm = machine
    fake = _Base(version_rc=1)
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.check(firm)

    assert state["base_present"] is True
    assert state["base_runs"] is False
    assert "does not run" in state["reason"]
    assert fake.count("cadre --help") == 0


# ---------------------------------------------------------------------------
# L11 — THE FINDING: installed in the operator's tier, absent in the firm's
# ---------------------------------------------------------------------------

def test_l11_an_install_in_the_operators_tier_does_not_count_for_the_firm(
        monkeypatch, machine):
    """The operator's tier has a live manifest; the firm's tier has none.

    Every Member runs with the firm's BASE_HOME, so every Member gets 127 here,
    and the only honest report is False for both readings. The first version
    of this module read True on exactly this machine.
    """
    operator, firm = machine
    _install_into(operator / ".base-gbl" / "extensions")
    fake = _Base()
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.check(firm)

    assert state["extension_installed"] is False
    assert state["extension_runs"] is False
    assert state["ok"] is False
    assert "firm's own tier" in state["reason"], state["reason"]
    assert fake.cadre_homes() == [str(firm / ".firm" / "base-home")], (
        f"`base cadre` was probed with the wrong BASE_HOME: {fake.cadre_homes()}")


def test_l2_control_the_manifest_in_the_firms_tier_reads_every_key_true(
        monkeypatch, machine):
    """Control for L11 and L4: same manifest, in the tier Members get."""
    _, firm = machine
    _install_into(tier_extensions_dir(firm))
    monkeypatch.setattr(subprocess, "run", _Base())

    state = base_ready.check(firm)

    assert state["base_present"] is True
    assert state["base_runs"] is True
    assert state["extension_installed"] is True
    assert state["extension_runs"] is True
    assert state["ok"] is True, state["reason"]


# ---------------------------------------------------------------------------
# L4 / L5 — the F1 shape inside the firm's tier
# ---------------------------------------------------------------------------

def test_l4_a_manifest_in_the_firms_tier_over_a_dead_command_reads_as_two_facts(
        monkeypatch, machine):
    """Installed and listed and not running: two keys, never one flag."""
    _, firm = machine
    _install_into(tier_extensions_dir(firm))
    monkeypatch.setattr(subprocess, "run", _Base(dead=True))

    state = base_ready.check(firm)

    assert state["extension_installed"] is True, "the manifest IS in the firm's tier"
    assert state["extension_runs"] is False, "and the command does NOT run there"
    assert state["ok"] is False
    assert "does not run" in state["reason"]


def test_l5_control_the_same_manifest_with_a_live_command_is_ok(monkeypatch, machine):
    """Control for L4: only the command's answer differs."""
    _, firm = machine
    _install_into(tier_extensions_dir(firm))
    monkeypatch.setattr(subprocess, "run", _Base(dead=False))

    assert base_ready.check(firm)["ok"] is True


def test_a_manifest_that_is_not_ours_does_not_count_as_installed(monkeypatch, machine):
    _, firm = machine
    _install_into(tier_extensions_dir(firm), 'name = "somethingelse"\n')
    monkeypatch.setattr(subprocess, "run", _Base(dead=True))

    assert base_ready.check(firm)["extension_installed"] is False


def test_an_unrendered_placeholder_does_not_count_as_installed(monkeypatch, machine):
    """A placeholder still in the manifest names a handler on no machine."""
    _, firm = machine
    _install_into(tier_extensions_dir(firm),
                  'name = "cadre"\nframework_dir = "' + base_extension.PLACEHOLDER + '"\n')
    monkeypatch.setattr(subprocess, "run", _Base(dead=True))

    assert base_ready.check(firm)["extension_installed"] is False


# ---------------------------------------------------------------------------
# Nothing here writes
# ---------------------------------------------------------------------------

def test_a_firm_with_no_tier_is_reported_and_no_tier_is_created(monkeypatch, tmp_path, machine):
    """`check` is drawn on every view of the readiness screen. The shared env
    builder creates a firm's tier when handed a workspace, so a firm without one
    must be reported as having none -- never given one by a screen render."""
    bare = tmp_path / "firms" / "zqnotier"
    bare.mkdir(parents=True)
    fake = _Base()
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.check(bare)

    assert state["ok"] is False
    assert "no base tier" in state["reason"], state["reason"]
    assert not (bare / ".firm").exists(), "check created the firm's tier"
    assert fake.count("cadre --help") == 0


def test_ensure_never_installs_into_any_tier(monkeypatch, machine):
    """The repair is gone, and this is what keeps it gone.

    `base_extension.install` writes the operator's tier, which no Member reads.
    Reaching it from `ensure` would report progress while every Member still
    got 127 and would put Cadre's manifest into a tier this product just
    finished cleaning.
    """
    operator, firm = machine
    monkeypatch.setattr(subprocess, "run", _Base())

    def _never(*args, **kwargs):
        raise AssertionError("ensure reached base_extension.install")

    monkeypatch.setattr(base_extension, "install", _never)
    before = sorted(p.name for p in (operator / ".base-gbl" / "extensions").iterdir())

    state = base_ready.ensure(firm)

    assert state["install"] == {}
    assert state["repaired"] is False
    assert state["ok"] is False
    assert "not built" in state["repair"], state["repair"]
    after = sorted(p.name for p in (operator / ".base-gbl" / "extensions").iterdir())
    assert after == before, f"the operator's tier changed: {before} -> {after}"


def test_ensure_on_a_firm_whose_command_runs_reports_nothing_to_repair(monkeypatch, machine):
    """Control for the leg above: a healthy firm tier carries no repair note."""
    _, firm = machine
    _install_into(tier_extensions_dir(firm))
    monkeypatch.setattr(subprocess, "run", _Base())

    state = base_ready.ensure(firm)

    assert state["ok"] is True
    assert state["repair"] == ""


def test_ensure_with_no_base_is_skipped_and_spawns_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    fake = _Base()
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.ensure(tmp_path)

    assert state["skipped"] is True
    assert state["repair"] == ""
    assert fake.calls == []


# ---------------------------------------------------------------------------
# The contract the callers depend on
# ---------------------------------------------------------------------------

def test_the_reading_says_which_directory_it_was_taken_from(monkeypatch, machine):
    _, firm = machine
    monkeypatch.setattr(subprocess, "run", _Base())

    assert base_ready.check(firm)["probe_cwd"] == str(firm)
    assert base_ready.check()["probe_cwd"] == str(Path.cwd())


def test_neither_function_raises_when_base_explodes(monkeypatch, machine):
    _, firm = machine

    def _boom(cmd, **kwargs):
        raise OSError("the binary vanished mid-call")

    monkeypatch.setattr(subprocess, "run", _boom)

    assert base_ready.check(firm)["ok"] is False
    assert base_ready.ensure(firm)["ok"] is False


def test_the_tier_is_decided_in_one_place_and_it_is_the_firms(monkeypatch, machine):
    """Pinned so the operator's tier cannot creep back in.

    Parsed with `ast`, not grepped: the docstrings name both tiers while
    explaining the finding, and a substring count cannot tell prose from a call.
    """
    import ast

    source = Path(base_ready.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    firm_uses = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.Name, ast.Attribute, ast.alias))
                 and getattr(n, "id", getattr(n, "attr", getattr(n, "name", "")))
                 == "tier_extensions_dir"]
    ambient_uses = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Attribute) and n.attr == "_installed_path"]
    assert ambient_uses == [], (
        f"visited {len(ambient_uses)} uses of the operator-tier path in base_ready.py")
    assert firm_uses, "base_ready.py no longer resolves the firm's tier at all"

    _, firm = machine
    assert base_ready._tier(firm) == tier_extensions_dir(firm)
    assert base_ready._tier(None) is None
