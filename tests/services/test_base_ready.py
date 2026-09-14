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

THE REPAIR IS BACK, IN THE FIRM'S OWN TIER ONLY (#118 DoD 4, the founding step).
PR 133 gave `base_extension.install` a workspace, so an install that helps a
Member now exists: base writes the manifest into the tier its BASE_HOME names,
and `ensure` hands it the firm. The retired legs STAY retired, because each one
asserted an install into the operator's tier. U1 to U8 below pin the firm-tier
install. `test_ensure_never_installs_into_any_tier` is gone because the
behaviour it kept absent is now the product. Its reason lives on in U1-OP and
U8, which fail if the operator's tier is ever the target.

EVERY FAILING LEG HAS ITS HEALTHY CONTROL BESIDE IT, named in its docstring, so
a red proves discrimination rather than only that the leg can print red.

THE STUB BASE IS A REAL FILE carrying this host's magic bytes, never a bare
string -- tests/test_no_weak_base_stubs.py fails the build over one.
"""

from __future__ import annotations

import shutil
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

    `anywhere=True` makes `base cadre --help` exit 0 whatever BASE_HOME says:
    a base resolving the command from somewhere other than the firm's tier,
    which is what a Windows base under WSL does with the operator's own tier.

    `extension install <staged>` copies the staged manifest into the
    extensions directory of the BASE_HOME the call carries, and nowhere else.
    With no BASE_HOME it writes nothing and exits 1, and it never falls back to
    a home directory. That is what base does (#117's spec, section 2, and PR
    133's real-base leg). It is the ONE verb the founding step's G0 ruling (R4)
    added to this fake. `lands=b"..."` makes that verb write those bytes
    instead of the staged manifest: a file base wrote that nobody can read.
    """

    def __init__(self, *, version_rc: int = 0, dead: bool = False,
                 anywhere: bool = False, lands: bytes | None = None) -> None:
        self.version_rc = version_rc
        self.dead = dead
        self.anywhere = anywhere
        self.lands = lands
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
            installed = self.anywhere or bool(home) and (
                Path(home) / ".base-gbl" / "extensions" / "cadre.toml").is_file()
            rc = 0 if installed and not self.dead else 127
            out = ("usage: cadre [-h] [--version] <command> ..." if rc == 0
                   else "base: unknown command 'cadre'")
        elif rest[:2] == ["extension", "install"] and len(rest) > 2:
            home = env.get("BASE_HOME", "")
            if not home:
                rc, out = 1, "base: no BASE_HOME, nothing installed"
            else:
                dest = Path(home) / ".base-gbl" / "extensions" / "cadre.toml"
                dest.parent.mkdir(parents=True, exist_ok=True)
                if self.lands is None:
                    shutil.copyfile(rest[2], dest)
                else:
                    dest.write_bytes(self.lands)
                out = "Installed cadre"

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


# The [extension] table the real manifest carries. `check` reads the PARSED
# extension name (F-F), so a bare top-level `name = "cadre"` no longer models a
# landed manifest -- the same change PR 133 made to its own fixtures, with no
# assertion changed.
MANIFEST = '[extension]\nname = "cadre"\nframework_dir = "/opt/cadre"\n'


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
# F1 — a base this host cannot run is refused before ANY subprocess
# ---------------------------------------------------------------------------

def _stub_with(tmp_path: Path, magic: bytes) -> Path:
    stub = tmp_path / "other-bin" / "base"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_bytes(magic + b"\x00" * 128)
    stub.chmod(0o755)
    return stub


def _native_magic() -> bytes:
    from firm.sysconfig.binaries import native_image_format

    return {"pe": b"MZ\x90\x00", "macho": b"\xcf\xfa\xed\xfe"}.get(
        native_image_format(), b"\x7fELF")


def _foreign_magic() -> bytes:
    """An image format this host does not run: ELF on Windows, PE elsewhere."""
    from firm.sysconfig.binaries import native_image_format

    return b"\x7fELF" if native_image_format() == "pe" else b"MZ\x90\x00"


@pytest.mark.parametrize("where", ["firm", "no_firm"])
def test_a_base_this_host_cannot_run_is_refused_before_anything_runs(
        monkeypatch, tmp_path, machine, where):
    """G2 F1. The gate refused and the probes ran anyway; now nothing runs.

    `--version` COUNTS. A foreign base under interop executes and ignores a
    POSIX BASE_HOME, so even its version line is a reading of some other tier.
    The fake answers `base cadre` in ANY tier, so a probe that slipped past the
    gate would read as a running command -- the state the grader measured.
    Control: the leg below, identical but for the stub's magic bytes.
    """
    _, firm = machine
    stub = _stub_with(tmp_path, _foreign_magic())
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: str(stub))
    fake = _Base(anywhere=True)
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.check(firm if where == "firm" else None)

    assert fake.calls == [], f"a refused base was run: {[a for a, _ in fake.calls]}"
    assert state["base_present"] is True
    assert state["base_runs"] is False
    assert state["base_may_write_the_tier"] is False
    assert state["skipped"] is False, "refused is not skipped"
    assert state["extension_installed"] is False
    assert state["extension_runs"] is False
    assert state["ok"] is False
    assert state["missing"] == [base_ready.MISSING_BASE]
    assert "Refused before anything ran" in state["reason"], state["reason"]


def test_control_a_base_this_host_can_run_is_let_through_and_probed(
        monkeypatch, tmp_path, machine):
    """Control for the leg above: the same machine with native magic bytes is
    not refused, and both probes run exactly once."""
    _, firm = machine
    stub = _stub_with(tmp_path, _native_magic())
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: str(stub))
    fake = _Base(anywhere=True)
    monkeypatch.setattr(subprocess, "run", fake)

    state = base_ready.check(firm)

    assert fake.count("--version") == 1
    assert fake.count("cadre --help") == 1
    assert state["base_may_write_the_tier"] is True
    assert state["base_runs"] is True
    assert "Refused" not in state["reason"], state["reason"]


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
    _install_into(tier_extensions_dir(firm), '[extension]\nname = "somethingelse"\n')
    monkeypatch.setattr(subprocess, "run", _Base(dead=True))

    assert base_ready.check(firm)["extension_installed"] is False


def test_another_extensions_manifest_with_a_cadre_command_does_not_count_as_installed(
        monkeypatch, machine):
    """F-F leg (c), the founding step. `check` tested `'name = "cadre"' in landed`,
    and that substring matches Cadre's COMMAND block as well as its [extension]
    table. So another extension's manifest that declares a `cadre` command read as
    Cadre's. After the founding step this reading decides `repaired`, so the blind
    spot would decide a founding. The two preconditions prove the planted file really
    has the blind shape: the substring is present, and the parsed name is not cadre.
    Control: `test_l2_control_the_manifest_in_the_firms_tier_reads_every_key_true`."""
    import tomllib

    _, firm = machine
    other = ('[extension]\nname = "somethingelse"\nversion = "0.1.0"\n\n'
             '[[commands]]\nname = "cadre"\nhandler = "/opt/other/bin/cadre"\n')
    _install_into(tier_extensions_dir(firm), other)
    landed = (tier_extensions_dir(firm) / "cadre.toml").read_text(encoding="utf-8")
    assert 'name = "cadre"' in landed, "precondition: the blind substring is present"
    assert tomllib.loads(landed)["extension"]["name"] != "cadre", "precondition: not Cadre's"
    monkeypatch.setattr(subprocess, "run", _Base())

    state = base_ready.check(firm)

    assert state["extension_installed"] is False, state
    assert state["ok"] is False, state


def test_ff2_a_landed_manifest_that_is_not_utf8_is_reported_and_nothing_raises(
        monkeypatch, machine):
    """F-F2, avocet's PR 133 note N2. `install`'s read-back decoded the landed file
    inside a `try` whose only handler caught OSError. A UnicodeDecodeError is a
    ValueError, so a file that is not UTF-8 escaped a function whose contract is that
    it never raises. The shared reader decodes the bytes itself and names the file.
    Both callers are driven: `install` through the fake base, which lands bytes that
    are not UTF-8 in the firm's tier, and `check`, reading that same file."""
    _, firm = machine
    bad = b'[extension]\nname = "cadre"\n\xff\xfe\n'
    monkeypatch.setattr(subprocess, "run", _Base(lands=bad))

    result = base_extension.install(workspace=firm)

    landed = tier_extensions_dir(firm) / "cadre.toml"
    assert landed.read_bytes() == bad, "precondition: the fake landed the undecodable bytes"
    assert result["read_back"] is False, result
    assert result["ok"] is False, result
    assert str(landed) in result["reason"], result["reason"]
    assert "not a readable manifest" in result["reason"], result["reason"]

    state = base_ready.check(firm)

    assert state["extension_installed"] is False, state


def test_an_unrendered_placeholder_does_not_count_as_installed(monkeypatch, machine):
    """A placeholder still in the manifest names a handler on no machine. The
    fixture carries the [extension] table so this leg still reaches the
    placeholder check under F-F, instead of stopping at the name."""
    _, firm = machine
    _install_into(tier_extensions_dir(firm),
                  '[extension]\nname = "cadre"\nframework_dir = "'
                  + base_extension.PLACEHOLDER + '"\n')
    monkeypatch.setattr(subprocess, "run", _Base(dead=True))

    assert base_ready.check(firm)["extension_installed"] is False


def test_a_command_resolved_from_outside_the_firms_tier_is_not_an_install(
        monkeypatch, machine):
    """G2 F3: the third reason branch, reached. Before this leg no test did.

    The firm's tier is EMPTY and `base cadre` still exits 0 -- the command is
    coming from somewhere this check cannot see. Both readings are reported
    as they are, and the verdict is not ok.
    Control: `test_l2_control_the_manifest_in_the_firms_tier_reads_every_key_true`,
    where the same command runs over a manifest that IS in the firm's tier.
    """
    _, firm = machine
    monkeypatch.setattr(subprocess, "run", _Base(anywhere=True))

    state = base_ready.check(firm)

    assert state["extension_runs"] is True, "the command does run"
    assert state["extension_installed"] is False, "and nothing is in the firm's tier"
    assert state["ok"] is False
    assert state["missing"] == [base_ready.MISSING_EXTENSION]
    assert "somewhere this check cannot see" in state["reason"], state["reason"]


# ---------------------------------------------------------------------------
# G2 F6 — the reason quotes the line that names the failure
# ---------------------------------------------------------------------------

#: Copied line for line from real output, never typed from memory (law 38).
#: The first two: base 0.15.2 (ELF md5 b9043abf2e4b7baed539148ce55dee43),
#: `base cadre --help`, rc 127, stderr, 2026-09-14 -- an empty extensions
#: directory, and a manifest naming a handler that does not exist. The third:
#: CPython 3.12.3 `raise ValueError("boom")`, stderr. Each entry is
#: (lines, the line that names the failure, a line that must NOT be quoted).
SHAPES = {
    "unknown_command": (
        ["base: unknown command 'cadre'",
         "  No plugin commands installed. Run `base ext list` to see extensions.",
         "  Run `base --help` for core commands."],
        "base: unknown command 'cadre'",
        "Run `base --help` for core commands."),
    "handler_not_found": (
        ["base: command 'cadre' (ext:cadre) — handler not found: /nonexistent/lapwing127/cadre",
         "  Check the extension's [[commands]] handler path."],
        "base: command 'cadre' (ext:cadre) — handler not found: /nonexistent/lapwing127/cadre",
        "Check the extension's [[commands]] handler path."),
    "traceback": (
        ["Traceback (most recent call last):",
         '  File "<string>", line 1, in <module>',
         "ValueError: boom"],
        "ValueError: boom",
        "Traceback (most recent call last):"),
}


class _Says:
    """A base whose streams are the measured ones: `--version` exits
    `version_rc` printing `version_err`, `cadre --help` exits 127 printing
    `cadre_err`, in any tier. Real output is several lines on stderr; the
    `_Base` fake's single stdout line could never show which one was quoted."""

    def __init__(self, *, cadre_err: str = "", version_rc: int = 0,
                 version_err: str = "") -> None:
        self.cadre_err = cadre_err
        self.version_rc = version_rc
        self.version_err = version_err

    def __call__(self, cmd, **kwargs):
        rest = [str(c) for c in cmd][1:]
        rc, out, err = 0, "", ""
        if rest[:1] == ["--version"]:
            rc, err = self.version_rc, self.version_err
            out = "" if rc else "base 0.15.2"
        elif rest[:2] == ["cadre", "--help"]:
            rc, err = 127, self.cadre_err

        class R:
            returncode = rc
            stdout = out
            stderr = err
        return R()


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_reason_quotes_the_line_that_names_the_failure(monkeypatch, machine, shape):
    """G2 F6. Quoting the last line quoted base's advice instead of its error:
    "...failing:   Run `base --help` for core commands." The handler shape
    has its manifest in the firm's tier, so it reaches the installed-and-dead
    branch; the other two reach the not-installed branch."""
    _, firm = machine
    lines, names, advice = SHAPES[shape]
    if shape == "handler_not_found":
        _install_into(tier_extensions_dir(firm))
    monkeypatch.setattr(subprocess, "run", _Says(cadre_err="\n".join(lines) + "\n"))

    state = base_ready.check(firm)

    assert state["extension_runs"] is False
    assert names in state["reason"], state["reason"]
    assert advice not in state["reason"], state["reason"]


def test_a_base_that_does_not_run_is_quoted_by_the_line_that_names_it(monkeypatch, machine):
    """G2 F6 at the other quoting site. base's own output convention is the
    subject here, not the verb: the measured unknown-command lines stand in
    for whatever a failing `--version` prints, because both sites share one
    rule and this leg pins that the `--version` site uses it."""
    _, firm = machine
    lines, names, advice = SHAPES["unknown_command"]
    monkeypatch.setattr(subprocess, "run",
                        _Says(version_rc=1, version_err="\n".join(lines) + "\n"))

    state = base_ready.check(firm)

    assert state["base_runs"] is False
    assert names in state["reason"], state["reason"]
    assert advice not in state["reason"], state["reason"]


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


# ---------------------------------------------------------------------------
# The founding step (#118 DoD 4): `ensure` installs into the FIRM's tier
# ---------------------------------------------------------------------------
#
# TURNED AROUND ON PURPOSE (law 43). `test_ensure_never_installs_into_any_tier`
# pinned that `ensure` never reaches `install`, because the only install that
# existed wrote the operator's tier. PR 133 made a firm-tier install, so reaching
# it is now the product. The property that test protected -- the operator's tier
# is never the target -- is kept, and it can still fail: U1-OP goes red if the
# repair lands in the operator's tier, and U8 goes red if `ensure` installs with
# no firm at all.

def _spy_install(monkeypatch, answer=None) -> list[dict]:
    """Record every call to `base_extension.install`. Run the real one, unless
    `answer` is a dict to return or an exception to raise instead."""
    seen: list[dict] = []
    real = base_extension.install

    def _install(*args, **kwargs):
        seen.append({"args": args, "kwargs": kwargs})
        if answer is None:
            return real(*args, **kwargs)
        if isinstance(answer, BaseException):
            raise answer
        return dict(answer)

    monkeypatch.setattr(base_extension, "install", _install)
    return seen


def test_u1_ensure_installs_into_the_firms_own_tier_once_and_reads_it_back(
        monkeypatch, machine):
    """A firm whose tier holds no manifest, on a base that runs. `ensure` reaches
    the install exactly once and hands it THIS firm, and the reading taken after
    the install is healthy. Control: U7, where the tier is already healthy and
    nothing is installed."""
    _, firm = machine
    fake = _Base()
    monkeypatch.setattr(subprocess, "run", fake)
    seen = _spy_install(monkeypatch)

    state = base_ready.ensure(firm)

    assert len(seen) == 1, f"install was reached {len(seen)} times, not once"
    assert Path(seen[0]["kwargs"].get("workspace") or "") == firm, seen[0]
    landed = sorted(p.name for p in tier_extensions_dir(firm).glob("*.toml"))
    assert landed == ["cadre.toml"], f"the firm's tier holds {landed}"
    assert state["install"].get("read_back") is True, state["install"]
    assert state["repaired"] is True, state["repair"]
    assert state["ok"] is True, state["reason"]
    assert state["repair"] == ""


def test_u1_op_the_repair_leaves_the_operators_tier_byte_identical(monkeypatch, machine):
    """The half of U1 that catches an install aimed at the wrong tier. The operator's
    tier holds an unrelated extension, and after `ensure` its names and bytes are
    unchanged. It goes red if the repair lands in the operator's tier (MU1), and it
    stays green when no install happens at all (MU2). That difference is what keeps
    it a separate detector from U1."""
    operator, firm = machine
    ext = operator / ".base-gbl" / "extensions"
    (ext / "lore.toml").write_text('[extension]\nname = "lore"\n', encoding="utf-8")
    before = {p.name: p.read_bytes() for p in ext.iterdir()}
    monkeypatch.setattr(subprocess, "run", _Base())

    base_ready.ensure(firm)

    after = {p.name: p.read_bytes() for p in ext.iterdir()}
    assert len(before) == 1, f"visited {len(before)} files; the fixture must hold one"
    assert after == before, f"the operator's tier changed: {sorted(before)} -> {sorted(after)}"


def test_u2_an_install_that_says_ok_but_lands_nothing_is_not_a_repair(monkeypatch, machine):
    """Law 25: the installer is not taken at its word. `install` answers ok and
    read back, and the firm's tier is still empty. The second reading decides, so
    nothing is called repaired, and the note names the tier it read.
    Control: U1, the same machine with an install that really lands."""
    _, firm = machine
    monkeypatch.setattr(subprocess, "run", _Base())
    seen = _spy_install(monkeypatch, {"ok": True, "read_back": True, "skipped": False,
                                      "reason": "installed and read back"})

    state = base_ready.ensure(firm)

    assert len(seen) == 1, f"install was reached {len(seen)} times, not once"
    assert state["repaired"] is False, state
    assert state["ok"] is False, state
    assert str(tier_extensions_dir(firm)) in state["repair"], state["repair"]


def test_u3_a_base_this_host_cannot_run_gets_no_install(monkeypatch, tmp_path, machine):
    """A foreign-format base is refused before anything runs (F1), so `ensure`
    must not reach the install for it either. Control, in the same test: a native
    base on the same firm reaches it once, so a spy that sees nothing cannot pass."""
    _, firm = machine
    foreign = _stub_with(tmp_path, _foreign_magic())
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: str(foreign))
    monkeypatch.setattr(subprocess, "run", _Base(anywhere=True))
    seen = _spy_install(monkeypatch, {"ok": False, "skipped": False, "reason": "spy"})

    base_ready.ensure(firm)
    refused = len(seen)

    native = _stub_with(tmp_path / "native", _native_magic())
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: str(native))
    base_ready.ensure(firm)

    assert refused == 0, f"a refused base reached the install {refused} time(s)"
    assert len(seen) == 1, (
        f"control: the native base reached the install {len(seen)} time(s), not once")


def test_u5_a_skipped_install_is_read_from_the_field_never_from_the_sentence(
        monkeypatch, machine):
    """Issue #83's lesson, applied to the repair. `install` comes back skipped
    (base went away between the reading and the install) with a reason sentence
    that happens to contain "installed and read back". `ensure` reads the FIELD:
    the repair is reported as skipped, and nothing is called repaired."""
    _, firm = machine
    monkeypatch.setattr(subprocess, "run", _Base())
    seen = _spy_install(monkeypatch, {
        "ok": False, "skipped": True,
        "reason": "base went away: installed and read back never happened"})

    state = base_ready.ensure(firm)

    assert len(seen) == 1, f"install was reached {len(seen)} times, not once"
    assert state["repaired"] is False, state
    # Keyed on the sentence ONLY the skipped branch writes, never on the bare word.
    # The first version asserted `"skipped" in repair`, and MU5 (skipped read from
    # the sentence) left it GREEN: every other branch quotes the manifest path, and
    # pytest's tmp_path carries this test's own name, "test_u5_a_skipped_install...".
    # Law 40: a substring that a legitimate line also contains cannot fail.
    assert state["repair"].startswith("the install into the firm's own tier was skipped: "), (
        state["repair"])


def test_u6_an_install_that_raises_is_named_and_ensure_still_returns(monkeypatch, machine):
    """`install` promises never to raise, and founding must not depend on that
    promise. If it raises anyway, `ensure` returns a reading, repairs nothing, and
    names the exception."""
    _, firm = machine
    monkeypatch.setattr(subprocess, "run", _Base())
    seen = _spy_install(monkeypatch, RuntimeError("boom-u6"))

    state = base_ready.ensure(firm)

    assert len(seen) == 1, f"install was reached {len(seen)} times, not once"
    assert state["repaired"] is False, state
    assert "boom-u6" in state["repair"], state["repair"]


def test_ensure_on_a_firm_whose_command_runs_reports_nothing_to_repair(monkeypatch, machine):
    """U7. A healthy firm tier carries no repair note, and nothing is installed
    over it. Control for U1."""
    _, firm = machine
    _install_into(tier_extensions_dir(firm))
    monkeypatch.setattr(subprocess, "run", _Base())
    seen = _spy_install(monkeypatch, {"ok": True})

    state = base_ready.ensure(firm)

    assert state["ok"] is True
    assert state["repair"] == ""
    assert seen == [], f"a healthy firm reached the install {len(seen)} time(s)"


def test_ensure_with_no_base_is_skipped_and_spawns_nothing(monkeypatch, tmp_path):
    """U4. No base: skipped as a field, no subprocess, and no install."""
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    fake = _Base()
    monkeypatch.setattr(subprocess, "run", fake)
    seen = _spy_install(monkeypatch, {"ok": True})

    state = base_ready.ensure(tmp_path)

    assert state["skipped"] is True
    assert state["repair"] == ""
    assert fake.calls == []
    assert seen == [], f"install was reached {len(seen)} time(s) with no base"


def test_u8_ensure_with_no_workspace_never_installs(monkeypatch, machine):
    """No firm, no install. The operator's tier is never a founding target, so
    with no workspace `ensure` answers the base questions and stops."""
    monkeypatch.setattr(subprocess, "run", _Base())
    seen = _spy_install(monkeypatch, {"ok": True})

    state = base_ready.ensure()

    assert seen == [], f"install was reached {len(seen)} time(s) with no firm"
    assert state["install"] == {}
    assert state["repaired"] is False


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

    Every spelling that reaches the operator-tier path counts: an attribute
    (`base_extension._installed_path`), a from-import (`import _installed_path`)
    and the bare name it binds. The attribute alone was matched before, and a
    from-import walked past this assertion (PR 127 G2, F5).
    """
    import ast

    source = Path(base_ready.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    firm_uses = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.Name, ast.Attribute, ast.alias))
                 and getattr(n, "id", getattr(n, "attr", getattr(n, "name", "")))
                 == "tier_extensions_dir"]
    ambient_uses = [n for n in ast.walk(tree)
                    if (isinstance(n, ast.Attribute) and n.attr == "_installed_path")
                    or (isinstance(n, ast.alias) and n.name == "_installed_path")
                    or (isinstance(n, ast.Name) and n.id == "_installed_path")]
    assert ambient_uses == [], (
        f"visited {len(ambient_uses)} uses of the operator-tier path in base_ready.py")
    assert firm_uses, "base_ready.py no longer resolves the firm's tier at all"

    _, firm = machine
    assert base_ready._tier(firm) == tier_extensions_dir(firm)
    assert base_ready._tier(None) is None
