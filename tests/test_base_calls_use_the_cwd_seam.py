"""Every base call for a firm runs in a directory the seam chose. Issue #136.

One leg per call site (C1 to C6), plus the byte-identity assertion that rule 2
actually rests on. The guard that stops a NEW call escaping the seam lives in
`test_base_calls_name_their_cwd.py`.

WHY BYTE-IDENTITY, AND NOT "IT POINTS AT THE TIER"
--------------------------------------------------
base finds its workspace tier by walking up from the process's directory
(`config.rs:49-59` at `0ace1bae`), and short-circuits to the global tier's own
`.base` when -- and only when -- the directory EQUALS `home_root()/.base-gbl`.
That comparison is `std::path::Path` equality, and it was measured on this
machine on 2026-09-21 with the real `base.exe` 0.15.2:

  * a `BASE_HOME` differing from the on-disk form in the case of ONE directory
    name made base WALK, straight past the tier, into the `.base` above it;
  * an extended-length `\\\\?\\` prefix did the same;
  * an 8.3 short form did the same;
  * forward slashes and a trailing separator did NOT -- `Path` compares
    components, so those spell the same path.

`home_root()` takes `BASE_HOME` verbatim (`home.rs:30-34`) and the child's
working directory is whatever string it was given (`os.getcwd()` does not
canonicalise; measured the same day). So "point base at the tier" is not the
property. **The property is that the two strings are the same**, and Cadre can
hold it because both come from one `firm_base_home(workspace)` value.

THE TRAP THESE LEGS EXIST TO CATCH
-----------------------------------
`Path.resolve()` DOES canonicalise case on Windows. So resolving one side and
not the other breaks rule 2 -- and it looks like a tidy-up. That is why the
primary arm below spells the `workspace` argument NON-canonically: with a
canonical input, a `.resolve()` added later to either side is a no-op and no
assertion here could fire (osprey's verdict, R2-b).

The non-canonical spelling is a `..` round-trip rather than a case flip,
because `..` is non-canonical on every platform CI runs. A case flip does not
name the same directory on Linux at all, so as the primary arm it would be void
on two of the three legs. The case flip is kept as a second arm, gated on a
PROBE of whether this filesystem is case-insensitive -- the register's house
rule is that a skip tests for the defect and not for the platform, so it
disappears by itself where the mechanism exists.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from firm.services import base_domain, base_extension, base_ready
from firm.services.graph_isolation import firm_base_home

_TIER = ".base-gbl"


# ---------------------------------------------------------------------------
# The spy. It records what the PRODUCTION code produced and nothing else.
# ---------------------------------------------------------------------------

class _BaseCalls:
    """Stands in for `subprocess.run`, records every base call, answers by verb.

    Patched at `subprocess.run` rather than at each module's `run_utf8` so that
    one spy sees every module's calls: `run_utf8` forwards `cwd` and `env`
    straight through, so nothing is lost and nothing is module-specific.

    It answers the way the real base did in the probe recorded in
    `base_ready.py`: `extension install` copies the staged manifest into the
    extensions directory of the `BASE_HOME` it was called with, and
    `cadre --help` succeeds only when that file is there. Without that, install
    stops at its read-back and the later calls are never reached -- so the legs
    for C4 and C1 would pass having exercised nothing.
    """

    #: What `base rule list` prints when the leg does not ask for something else.
    DEFAULT_LISTING = ("[acme] 1 rules across both tiers:\n"
                       "  workspace 0. a rule this manifest did not write\n")

    def __init__(self, *, rule_listing: str | None = None) -> None:
        self.seen: list[dict[str, str | None]] = []
        # `is None`, NEVER `or`. An EMPTY listing is a legitimate thing for a
        # leg to ask for -- it is how `require_output=True` in `graph_rules` is
        # reached -- and `rule_listing or DEFAULT` silently replaced it with the
        # default, because "" is falsy. Measured: it made
        # `test_the_graph_read_failure_text_names_the_directory` fail on its own
        # precondition ("the graph read was expected to fail; it did not"),
        # which is the leg telling me the instrument was wrong rather than the
        # product. Absent, empty and zero are three different states (law 7).
        self._rule_listing = (self.DEFAULT_LISTING if rule_listing is None
                              else rule_listing)

    def __call__(self, cmd, **kwargs):
        argv = [str(c) for c in cmd]
        env = dict(kwargs.get("env") or {})
        raw_cwd = kwargs.get("cwd")
        self.seen.append({
            "verb": " ".join(argv[1:3]) if len(argv) > 2 else " ".join(argv[1:]),
            "argv": " ".join(argv),
            # Recorded exactly as passed. None and "" are DIFFERENT findings:
            # None is "no cwd was given", "" is "a cwd was given and it is
            # empty", and collapsing them would hide which one happened.
            "cwd": None if raw_cwd is None else str(raw_cwd),
            "base_home": env.get("BASE_HOME"),
        })
        rest = argv[1:]
        rc, out = 0, ""
        if rest[:1] == ["--version"]:
            out = "base 0.15.2 (build a61117b0a535)"
        elif rest[:2] == ["extension", "validate"]:
            out = "ok"
        elif rest[:2] == ["extension", "install"] and len(rest) > 2:
            home = env.get("BASE_HOME", "")
            if home:
                dest = Path(home) / _TIER / "extensions" / "cadre.toml"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(rest[2], dest)
                out = "Installed cadre"
            else:
                rc, out = 1, "base: refused, nothing written"
        elif rest[:2] == ["cadre", "--help"]:
            home = env.get("BASE_HOME", "")
            ok = bool(home) and (
                Path(home) / _TIER / "extensions" / "cadre.toml").is_file()
            rc = 0 if ok else 127
            out = "usage: cadre" if ok else "base: unknown command 'cadre'"
        elif rest[:3] == ["rule", "list", "--domain"]:
            out = self._rule_listing
        elif rest[:1] == ["scaffold"]:
            out = "Scaffolded"
        elif rest[:2] == ["rule", "add"]:
            out = "Rule 0 added"

        class _R:
            returncode = rc
            stdout = out
            stderr = ""
        return _R()

    # -- readers, so a leg never re-derives what was recorded ---------------

    def of(self, verb_prefix: str) -> list[dict[str, str | None]]:
        return [c for c in self.seen if str(c["verb"]).startswith(verb_prefix)]

    def one(self, verb_prefix: str) -> dict[str, str | None]:
        hits = self.of(verb_prefix)
        assert hits, (
            "no base call matching %r was recorded, so this leg measured "
            "nothing. Recorded: %s"
            % (verb_prefix, [c["argv"] for c in self.seen]))
        return hits[0]


# ---------------------------------------------------------------------------
# Fixtures and spellings
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _every_leg_controls_its_cwd(monkeypatch, tmp_path):
    """Nothing here runs from the directory pytest was started in.

    Same reason as `test_founding_validates_base.py`: base walks up from the
    current directory for a workspace tier and `BASE_HOME` never touches that
    walk, so an uncontrolled process cwd makes the leg's subject ambiguous. The
    box is also what every leg asserts the recorded cwd is NOT.
    """
    box = tmp_path / "process-cwd"
    box.mkdir(exist_ok=True)
    monkeypatch.chdir(box)
    return box


@pytest.fixture
def stub_base(monkeypatch, tmp_path) -> str:
    """A real file carrying this host's magic bytes, never the string "/fake/base".

    A path that is not a real image passes `base_can_honour_tier` today and
    fails open the moment a guard identifies the binary, which has already
    happened twice in this repo (#75, #87). Copied from
    `test_founding_validates_base.stub_base` for that reason.
    """
    from firm.sysconfig.binaries import native_image_format

    magic = {"pe": b"MZ\x90\x00", "macho": b"\xcf\xfa\xed\xfe"}.get(
        native_image_format(), b"\x7fELF")
    stub = tmp_path / "hostbin" / "base"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_bytes(magic + b"\x00" * 128)
    stub.chmod(0o755)
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: str(stub))
    return str(stub)


@pytest.fixture
def calls(monkeypatch) -> _BaseCalls:
    spy = _BaseCalls()
    monkeypatch.setattr(subprocess, "run", spy)
    return spy


def _firm(tmp_path: Path, *, name: str = "acme", with_base_dir: bool) -> Path:
    """A firm workspace, with or without a `.base` of its own.

    `with_base_dir` picks which D-A rule applies: rule 1 when the firm has its
    own `.base` (the walk starts inside the firm and stops there), rule 2 when
    it does not (the walk would climb OUT of the firm, so the call is aimed at
    the firm's own global tier instead).
    """
    ws = tmp_path / "firms" / name
    ws.mkdir(parents=True, exist_ok=True)
    if with_base_dir:
        (ws / ".base").mkdir(exist_ok=True)
    return ws


def non_canonical(path: Path) -> str:
    """The same directory, spelled so that `resolve()` is NOT a no-op.

    A `..` round-trip: `<parent>/<name>/../<name>`. pathlib keeps `..` in a
    path it constructs (unlike `.`, which it drops at construction), every
    platform CI runs treats it as the same directory, and `resolve()` collapses
    it. That last property is the whole point -- it is what lets mutations MC
    and MD redden instead of reading INERT.
    """
    return str(path / ".." / path.name)


def _filesystem_is_case_insensitive(tmp_path: Path) -> bool:
    """Probe it, never assume it from `os.name`.

    The platform register's house rule: a skip tests for the defect rather than
    for the platform, so it clears itself wherever the mechanism turns up. A
    case-flipped path names the same directory only on a case-insensitive
    filesystem; on Linux it names nothing, and an arm built on it there would
    be void rather than passing.
    """
    probe = tmp_path / "CaseProbe"
    probe.mkdir(exist_ok=True)
    return (tmp_path / "caseprobe").is_dir()


def case_flipped(path: Path) -> str:
    """One path segment's case flipped -- the spelling that made base walk (A5)."""
    return str(path.parent / path.name.upper())


# ---------------------------------------------------------------------------
# C1 to C4 -- the four calls inside `install`
# ---------------------------------------------------------------------------

def _install_in(workspace: Path | str, calls: _BaseCalls) -> dict:
    result = base_extension.install(workspace=workspace)
    assert result["installed"], (
        "install did not reach its base calls, so nothing was measured: %r"
        % result.get("reason"))
    return result


@pytest.mark.parametrize("verb,label", [
    ("rule list", "C1 the graph read behind foreign_rules"),
    ("extension validate", "C2"),
    ("extension install", "C3"),
    ("cadre --help", "C4, today cwd=str(Path.cwd())"),
])
def test_every_install_call_runs_in_the_firms_own_tier(
        verb, label, tmp_path, stub_base, calls, _every_leg_controls_its_cwd):
    """Each of C1 to C4 names a directory inside the firm, never the caller's."""
    ws = _firm(tmp_path, with_base_dir=False)
    _install_in(ws, calls)
    call = calls.one(verb if verb != "cadre --help" else "cadre")
    expected = str(firm_base_home(ws) / _TIER)
    assert call["cwd"] == expected, (
        "%s ran in %r; rule 2 says %r. base takes its workspace tier from the "
        "directory it runs in, so this call reads whatever .base sits above "
        "wherever the caller stood." % (label, call["cwd"], expected))
    assert call["cwd"] != str(_every_leg_controls_its_cwd), (
        "%s ran in the process's own directory" % label)


def test_install_calls_run_in_the_firm_itself_when_it_has_a_base(
        tmp_path, stub_base, calls):
    """Rule 1: a firm with its own `.base` gets the firm directory."""
    ws = _firm(tmp_path, with_base_dir=True)
    _install_in(ws, calls)
    for call in calls.seen:
        assert call["cwd"] == str(ws), (
            "%s ran in %r, not in the firm %r" % (call["verb"], call["cwd"], ws))


# ---------------------------------------------------------------------------
# C5 -- scaffold
# ---------------------------------------------------------------------------

def test_c5_scaffold_runs_in_the_firms_own_tier(
        tmp_path, stub_base, calls, monkeypatch, _every_leg_controls_its_cwd):
    ws = _firm(tmp_path, with_base_dir=False)
    monkeypatch.setattr("firm.sysconfig.binaries.base_can_honour_tier",
                        lambda binary, expected: (True, ""))
    base_domain.scaffold_tier(ws)
    call = calls.one("scaffold")
    expected = str(firm_base_home(ws) / _TIER)
    assert call["cwd"] == expected, (
        "C5 `base scaffold` ran in %r; rule 2 says %r"
        % (call["cwd"], expected))
    assert call["cwd"] != str(_every_leg_controls_its_cwd)


# ---------------------------------------------------------------------------
# C6 -- the readiness probe with NO workspace (rule 3)
# ---------------------------------------------------------------------------

def test_c6_the_version_probe_with_no_workspace_never_uses_the_process_cwd(
        tmp_path, stub_base, calls, monkeypatch, _every_leg_controls_its_cwd):
    """Rule 3: no firm yet, so the call is aimed at the tier its env names.

    This is the call `founding.py:1082` makes before a workspace exists, and
    today it stands in `Path.cwd()` -- on the operator's Windows hub that is
    `C:\\Users\\Chris\\.base-gbl\\scripts`, one level under his real global graph.
    """
    home = tmp_path / "envhome"
    (home / _TIER).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BASE_HOME", str(home))
    base_ready.check()
    call = calls.one("--version")
    assert call["cwd"] != str(_every_leg_controls_its_cwd), (
        "C6 ran in the process's own directory %r, which is exactly what the "
        "issue is about" % call["cwd"])
    assert call["cwd"] == str(home / _TIER), (
        "C6 ran in %r; rule 3 says the tier its env names, %r"
        % (call["cwd"], home / _TIER))


# ---------------------------------------------------------------------------
# R2-a -- the property rule 2 actually rests on, on PRODUCTION values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spelling,arm", [
    ("non_canonical", "PRIMARY: a `..` round-trip, so resolve() is not a no-op"),
    ("canonical", "CONTROL: the plain spelling"),
])
def test_the_recorded_cwd_is_byte_identical_to_base_home_plus_the_tier(
        spelling, arm, tmp_path, stub_base, calls):
    """`recorded_cwd == recorded_BASE_HOME + sep + ".base-gbl"`, as strings.

    Both sides come from the values the production code produced -- the spy
    records what `_base_env` and the seam actually emitted, and this leg
    computes neither of them (osprey's verdict, R2-a). A test that built either
    side itself would be the fix grading its own homework.

    The primary arm hands `install` a NON-canonical spelling. That is what makes
    the assertion able to fail: with a canonical input, a `.resolve()` added to
    one side later changes nothing and this leg could never fire.
    """
    ws = _firm(tmp_path, with_base_dir=False)
    given = non_canonical(ws) if spelling == "non_canonical" else str(ws)
    _install_in(given, calls)

    for call in calls.seen:
        recorded_cwd, recorded_home = call["cwd"], call["base_home"]
        assert recorded_home, (
            "%s recorded no BASE_HOME, so there is nothing to compare against"
            % call["verb"])
        assert recorded_cwd == recorded_home + os.sep + _TIER, (
            "%s\n"
            "  recorded cwd       : %r\n"
            "  recorded BASE_HOME : %r\n"
            "  expected cwd       : %r\n"
            "base compares these two as Path equality and short-circuits only "
            "when they are the same. They are not, so base walked out of the "
            "tier -- which is what probe arm A5 measured on 2026-09-21."
            % (arm, recorded_cwd, recorded_home,
               str(recorded_home) + os.sep + _TIER))


def test_a_case_flipped_workspace_still_holds_byte_identity(
        tmp_path, stub_base, calls):
    """The spelling that actually made base walk, where the filesystem allows it.

    Gated on a probe of case-insensitivity rather than on `os.name`, so it runs
    wherever the mechanism exists and skips only where a case-flipped path
    names no directory at all.
    """
    if not _filesystem_is_case_insensitive(tmp_path):
        pytest.skip(
            "this filesystem is case-sensitive, so a case-flipped path names a "
            "different directory and the arm would measure nothing")
    ws = _firm(tmp_path, with_base_dir=False)
    _install_in(case_flipped(ws), calls)
    for call in calls.seen:
        assert call["cwd"] == str(call["base_home"]) + os.sep + _TIER, (
            "%s: cwd %r is not BASE_HOME %r plus the tier"
            % (call["verb"], call["cwd"], call["base_home"]))


# ---------------------------------------------------------------------------
# The OS side. THE ONLY LEG HERE THAT SEES IT.
# ---------------------------------------------------------------------------

def test_the_directory_the_seam_hands_out_survives_being_set_on_a_child(
        tmp_path, stub_base):
    """A child started in the seam's directory must REPORT that same directory.

    THIS IS THE ONE LEG THAT SEES THE OPERATING SYSTEM. Every other leg in this
    file reads the string Cadre PASSED, through a spy on `subprocess.run`, and
    can never see what the child ends up with. So an OS that rewrites the
    working directory it is handed leaves every one of them green while base
    walks out of the tier in production.

    That is not hypothetical. Probe arm A10, measured 2026-09-21 with the real
    `base.exe` 0.15.2: Windows COLLAPSES a `..` component when it sets a child's
    working directory, while `BASE_HOME` keeps the `..` verbatim
    (`home.rs:30-34`). Both of Cadre's own strings agreed, byte for byte, and
    base still walked -- because the OS had rewritten one of them after Cadre
    let go of it. Arm A10n, same tree with both sides normalised, did not walk.

    So the seam normalises `..` away itself, before the OS can do it to one side
    only, and this leg is what proves the normalisation is still there. It is
    the leg mutation (e) has to redden: with the `abspath` removed the seam hands
    out a `..`-carrying directory, the child reports the collapsed form, and
    these two strings stop matching.

    A real child, deliberately: `subprocess.run` is spied everywhere else in
    this file, and a spy cannot tell you anything about the OS.
    """
    ws = _firm(tmp_path, with_base_dir=False)
    handed = base_domain.base_cwd(non_canonical(ws))

    probe = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getcwd())"],
        capture_output=True, text=True, cwd=handed, timeout=60)

    # Control first: a leg that reports nothing about a child that never ran is
    # not a measurement (law 23).
    assert probe.returncode == 0, (
        "the probe child did not run, so this leg measured nothing: rc %s, %r"
        % (probe.returncode, probe.stderr))
    reported = (probe.stdout or "").strip()
    assert reported, "the probe child printed nothing"

    assert reported == handed, (
        "the seam handed out %r and the child ended up in %r.\n"
        "base compares the directory it runs in against BASE_HOME plus the "
        "tier, as Path equality, so a directory the OS rewrote after Cadre "
        "chose it makes those two disagree and base walks -- with Cadre's own "
        "two strings byte-identical. This is probe arm A10."
        % (handed, reported))


# ---------------------------------------------------------------------------
# C8 to C13 -- already inside the firm. NON-DISCRIMINATING, and labelled so.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("drive", ["rule_count", "seed_rule"])
def test_the_calls_that_already_start_inside_the_firm_keep_doing_so(
        drive, tmp_path, stub_base, calls, monkeypatch):
    """A live regression guard for C8 to C13, NOT evidence this fix works.

    These calls pass `str(workspace)` today, and rule 1 returns the same string,
    so this leg is green on both sides of the change. Law 45: grading it by the
    other question ("does it discriminate the fix?") would manufacture a false
    pass out of a true measurement. It is here so that moving them onto the seam
    cannot silently change where they run.
    """
    ws = _firm(tmp_path, with_base_dir=True)
    if drive == "rule_count":
        base_domain.rule_count(ws, "acme")
    else:
        base_domain._seed_rule_detail(ws, "acme")
    assert calls.seen, "no base call was made, so nothing was measured"
    for call in calls.seen:
        assert call["cwd"] == str(ws), (
            "%s ran in %r, not in the firm %r" % (call["verb"], call["cwd"], ws))


# ---------------------------------------------------------------------------
# D-C -- every call records the directory it used
# ---------------------------------------------------------------------------

def test_install_records_the_directory_its_calls_ran_in(
        tmp_path, stub_base, calls, _every_leg_controls_its_cwd):
    """`install()["cwd"]` is where the calls ran, not where the process stood.

    Today it is `os.getcwd()` (`base_extension.py:567`), so it reports the
    process's directory while the calls may have run somewhere else entirely --
    a recorded value that cannot be used to check the thing it names.
    """
    ws = _firm(tmp_path, with_base_dir=False)
    result = _install_in(ws, calls)
    expected = str(firm_base_home(ws) / _TIER)
    assert result["cwd"] == expected, (
        "install reported cwd %r but its calls ran in %r"
        % (result["cwd"], expected))
    assert result["cwd"] != str(_every_leg_controls_its_cwd), (
        "install reported the process's own directory")


def test_scaffold_tier_records_its_cwd(tmp_path, stub_base, calls, monkeypatch):
    ws = _firm(tmp_path, with_base_dir=False)
    monkeypatch.setattr("firm.sysconfig.binaries.base_can_honour_tier",
                        lambda binary, expected: (True, ""))
    result = base_domain.scaffold_tier(ws)
    assert "cwd" in result, (
        "scaffold_tier's result does not say which directory base ran in, so a "
        "caller cannot check it")
    assert result["cwd"] == str(firm_base_home(ws) / _TIER)


def test_a_foreign_rules_finding_names_the_directory_it_read(
        tmp_path, stub_base, calls):
    """A rule count without its directory is not a measurement.

    `graph_rules`' own docstring says so. The finding carries the domain, what
    is served and what is foreign; without the directory, an operator cannot
    tell a real collision from a call that read the wrong tier.
    """
    ws = _firm(tmp_path, with_base_dir=False)
    result = _install_in(ws, calls)
    assert result["foreign_rules"], (
        "the spy's rule listing should have produced a finding; none did, so "
        "this leg measured nothing")
    finding = result["foreign_rules"][0]
    assert "cwd" in finding, (
        "a foreign-rules finding does not name the directory it read: %r"
        % sorted(finding))
    assert finding["cwd"] == str(firm_base_home(ws) / _TIER)


def test_the_graph_read_failure_text_names_the_directory(
        tmp_path, stub_base, monkeypatch):
    """`GraphReadFailed` says WHERE it read, or an operator cannot act on it."""
    spy = _BaseCalls(rule_listing="")          # empty stdout: require_output fires
    monkeypatch.setattr(subprocess, "run", spy)
    ws = _firm(tmp_path, with_base_dir=False)
    result = base_extension.install(workspace=ws)
    assert result["graph_read"] is False, (
        "the graph read was expected to fail on empty output; it did not, so "
        "this leg measured nothing")
    expected = str(firm_base_home(ws) / _TIER)
    # KEYED ON THE PHRASE, NOT ON THE BARE PATH (law 40). `expected in reason`
    # passed on the UNFIXED tree: the reason already interpolates the manifest
    # path `<tier>/.base-gbl/extensions/cadre.toml`, which CONTAINS the tier
    # directory as a substring. A true substring match, a false conclusion --
    # the assertion could not fail, and it read green over a message that named
    # no directory at all. The phrase can only come from the site that emits it.
    assert f"read in {expected}" in result["reason"], (
        "the failure text does not say WHERE it read. An operator cannot tell a "
        "real collision from a call that read the wrong tier without it.\n"
        "  reason: %s\n  wanted the phrase: 'read in %s'"
        % (result["reason"], expected))


# ---------------------------------------------------------------------------
# C7 -- the READER arms (`create=False`). WHERE A READER STANDS WHEN THERE IS
# NO TIER YET.  (G2 finding 1)
# ---------------------------------------------------------------------------
#
# `create=True` is the writing path and it makes the tier, so it always has a
# directory inside the firm to hand back. A READER must not create what it is
# reporting on (`base_ready`'s contract, and
# `test_a_firm_with_no_tier_is_reported_and_no_tier_is_created`), so with the
# tier absent it has to fall back to something else -- and WHICH something is
# what these legs pin.
#
# The fallback used to be derived from the tier path's own NAME:
# `tier.parent.parent.parent if tier.name == ".base-gbl"`. That is correct for
# rule 2, whose tier is `<firm>/.firm/base-home/.base-gbl` and whose firm is
# exactly three parents up. Rule 3's tier is `<BASE_HOME>/.base-gbl`, whose
# name is ALSO `.base-gbl`, and three parents up from there is two levels ABOVE
# `BASE_HOME`. Measured on the unfixed head (`probe144.py`, temp directories
# only): with `BASE_HOME` set to an empty directory, `base_cwd(None,
# create=False)` returned the system temp root -- outside everything the env
# named. On a Windows box whose global tier does not exist yet, the readiness
# probe would have stood in the drive root.
#
# So the caller passes the fallback and `_existing` derives nothing from a
# name. These legs assert the RELATION (the returned directory IS the root, and
# is never an ancestor of it) rather than a spelling, so they are independent of
# `_one_spelling` -- a leg that recomputed the seam's own normalisation would be
# the fix grading its own homework.


def _contents(root: Path) -> set:
    """Everything under `root`, so "nothing was created" is read, not asserted."""
    return set(root.rglob("*")) if root.exists() else set()


def test_c7_rule_3_reader_with_no_tier_stands_in_the_root_the_env_names(
        tmp_path, monkeypatch, _every_leg_controls_its_cwd):
    """Rule 3 + `create=False` + no tier: the root `BASE_HOME` names, never above it.

    The discriminating assertion is the second one. `Path(got) in root.parents`
    is true for EVERY ancestor, so it catches the name arithmetic at any depth,
    not only at three parents -- an arithmetic changed to two or four would
    still redden this leg.
    """
    root = tmp_path / "envhome-empty"
    root.mkdir()
    monkeypatch.setenv("BASE_HOME", str(root))
    before = _contents(root)

    got = base_domain.base_cwd(None, create=False)

    assert Path(got) == root, (
        "rule 3's reader stood in %r; the tier is absent, so it must stand in "
        "the root the env names, %r. base takes its workspace tier by walking "
        "up from the directory it runs in, so a directory outside the root is "
        "a reading of whatever .base happens to sit above it."
        % (got, str(root)))
    assert Path(got) not in root.parents, (
        "rule 3's reader stood in %r, which is an ANCESTOR of the root %r it "
        "was given" % (got, str(root)))
    assert _contents(root) == before, (
        "a reader created something under %r: %r"
        % (str(root), sorted(str(p) for p in _contents(root) - before)))
    assert not (root / _TIER).exists(), (
        "a reader created the tier it was reporting on")


def test_c7_rule_2_reader_with_no_tier_stands_in_the_firm(
        tmp_path, monkeypatch, _every_leg_controls_its_cwd):
    """Rule 2 + `create=False` + no tier: the firm itself, and nothing made.

    This arm was already correct under the arithmetic, and it is pinned here so
    that removing the arithmetic cannot move it. Law 45: it is a regression
    guard, not evidence for the fix -- it is green on both sides of the change,
    and it is the reason the fix has to pass the firm as the fallback rather
    than simply return the tier.
    """
    ws = _firm(tmp_path, with_base_dir=False)
    before = _contents(ws)

    got = base_domain.base_cwd(ws, create=False)

    assert Path(got) == ws, (
        "rule 2's reader stood in %r, not in the firm %r" % (got, str(ws)))
    assert _contents(ws) == before, (
        "a reader created something under the firm: %r"
        % sorted(str(p) for p in _contents(ws) - before))
    assert not firm_base_home(ws).exists(), (
        "a reader created the firm's base-home directory")


def test_c7_a_reader_whose_root_does_not_exist_gets_the_root_and_never_raises(
        tmp_path, monkeypatch, stub_base, _every_leg_controls_its_cwd):
    """`BASE_HOME` naming a directory that is not there: still the root, still no write.

    Ruling 2 of the G2 finding leaves this arm to be measured rather than
    assumed. What is asserted here is the contract `base_ready` states in its
    own module docstring -- NEITHER FUNCTION RAISES, and nothing is written --
    and not the sentence `check` ends up producing, which belongs to whichever
    error the OS gives for a working directory that is absent.

    A cwd that does not exist makes `subprocess` raise `FileNotFoundError`,
    which is an `OSError`, which `check` already catches at its `--version`
    probe and turns into a reason. That is a better outcome than standing two
    levels above the root and succeeding, because it reports the host problem
    instead of silently reading a stranger's tier.
    """
    root = tmp_path / "envhome-absent"          # deliberately never created
    monkeypatch.setenv("BASE_HOME", str(root))
    assert not root.exists(), "the arm's own precondition failed"

    got = base_domain.base_cwd(None, create=False)

    assert Path(got) == root, (
        "the reader stood in %r rather than the absent root %r the env names"
        % (got, str(root)))
    assert Path(got) not in root.parents, (
        "the reader stood in an ancestor %r of the root it was given" % got)
    assert not root.exists(), "the seam created the root it was only reading"

    state = base_ready.check()                  # must not raise: module contract
    assert isinstance(state, dict), "check() did not return its result dict"
    assert state["probe_cwd"] == got, (
        "the probe reported standing in %r while the seam handed out %r"
        % (state["probe_cwd"], got))
    assert not root.exists(), (
        "the readiness check created %r; it writes nothing" % str(root))


# ---------------------------------------------------------------------------
# C8 -- RULE 3 RESTS ON THE SAME BYTE-IDENTITY RULE 2 DOES.  (G2 finding 2)
# ---------------------------------------------------------------------------
#
# R2-a above proves byte-identity for rule 2, and it can only ever prove it for
# rule 2: `install(workspace=...)` always takes `_base_env`'s WORKSPACE branch,
# which builds `BASE_HOME` through `_one_spelling`. Rule 3 has no workspace, so
# it takes the PASSTHROUGH branch instead, and that branch copied the ambient
# `BASE_HOME` through verbatim. Two different branches, one of them normalised.
#
# So the pair base compares -- the working directory Cadre chose and the
# `BASE_HOME` Cadre exported -- could disagree for rule 3 while every existing
# leg stayed green: R2-a never reaches this branch, and the C6 leg sets a
# canonical `BASE_HOME`, which is the one spelling for which verbatim and
# normalised are the same string.
#
# The leg below hands the branch a `..` round-trip, the same non-canonical
# spelling the R2-a primary arm uses and for the same reason: it is
# non-canonical on every platform CI runs, so the arm discriminates everywhere
# rather than only on a case-insensitive filesystem. Both sides of its
# assertion are values the PRODUCTION code emitted and the spy recorded.


def test_c8_rule_3_exports_the_same_spelling_it_stands_in(
        tmp_path, stub_base, calls, monkeypatch, _every_leg_controls_its_cwd):
    """No workspace, a `..` in the ambient `BASE_HOME`: cwd and `BASE_HOME` still agree.

    base short-circuits to the global tier only when the directory it runs in
    EQUALS `BASE_HOME` plus the tier, compared as `std::path::Path` equality
    (`config.rs:49-59`). `Path` compares components, so a `..` component is not
    the same path as the directory it resolves to -- which is why this arm can
    fail at all, and why the C6 leg above (canonical `BASE_HOME`) cannot see it.

    The assertion is the same one R2-a makes, on the same kind of recorded
    values, for the branch R2-a can never reach.
    """
    home = tmp_path / "envhome"
    (home / _TIER).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BASE_HOME", non_canonical(home))

    base_ready.check()

    call = calls.one("--version")
    recorded_cwd, recorded_home = call["cwd"], call["base_home"]
    assert recorded_home, (
        "the --version probe recorded no BASE_HOME, so there is nothing to "
        "compare against and this leg measured nothing")
    assert recorded_cwd == recorded_home + os.sep + _TIER, (
        "rule 3 exported one spelling and stood in another.\n"
        "  recorded cwd       : %r\n"
        "  recorded BASE_HOME : %r\n"
        "  expected cwd       : %r\n"
        "base compares these two as Path equality and short-circuits only when "
        "they are the same, so base walked out of the tier -- with no workspace "
        "in play, which is the branch R2-a cannot reach."
        % (recorded_cwd, recorded_home, recorded_home + os.sep + _TIER))


def test_c8_the_passthrough_leaves_xdg_config_home_alone(
        tmp_path, stub_base, calls, monkeypatch, _every_leg_controls_its_cwd):
    """Only `BASE_HOME` is normalised; the other passthrough is carried verbatim.

    A control, and a boundary. Nothing base does with `XDG_CONFIG_HOME` is
    compared against a working directory, so normalising it would be a change
    with no measurement behind it -- and this leg is what would notice if a
    later tidy-up widened the rule to the whole loop.
    """
    home = tmp_path / "envhome"
    (home / _TIER).mkdir(parents=True, exist_ok=True)
    spelled = non_canonical(tmp_path / "xdg")
    (tmp_path / "xdg").mkdir(exist_ok=True)
    monkeypatch.setenv("BASE_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", spelled)

    env = base_domain._base_env(None)

    assert env.get("XDG_CONFIG_HOME") == spelled, (
        "XDG_CONFIG_HOME was rewritten to %r; it is passed through verbatim "
        "because nothing compares it with a working directory"
        % env.get("XDG_CONFIG_HOME"))


# ---------------------------------------------------------------------------
# C9 -- THE WRITING PATH WITH NO WORKSPACE MAKES A DIRECTORY, ON PURPOSE.
# ---------------------------------------------------------------------------
#
# `create=True` is the default, and with no workspace the seam makes
# `<BASE_HOME>/.base-gbl` -- a directory outside every firm, and under the
# user's own home when the environment names no `BASE_HOME`. That is reached
# from `install(workspace=None)`, the operator-level install.
#
# It is KEPT: `install` is a writing verb, its manifest has always gone to the
# tier the env names, and a writing verb that refused to make its own directory
# would fail on any machine where base has not run yet. What it must not do is
# write anywhere ELSE, and that is what this leg pins. Everything created under
# the whole temp tree is listed and required to be at or under the tier -- an
# allow-list of one directory, so a second one appearing anywhere is a failure
# rather than something a narrower assertion would step over.


def test_c9_a_writer_with_no_workspace_writes_only_inside_the_tier_the_env_names(
        tmp_path, stub_base, calls, monkeypatch, _every_leg_controls_its_cwd):
    """`install(workspace=None)`: `<BASE_HOME>/.base-gbl` and nothing outside it."""
    home = tmp_path / "envhome"
    home.mkdir()
    monkeypatch.setenv("BASE_HOME", str(home))
    tier = home / _TIER
    assert not tier.exists(), "the leg's own precondition failed"
    before = set(tmp_path.rglob("*"))

    result = base_extension.install(workspace=None)

    assert result["installed"], (
        "install did not reach its base calls, so this leg measured nothing: %r"
        % result.get("reason"))
    assert tier.is_dir(), (
        "the writing path did not make the tier it named, %r" % str(tier))

    made = sorted(set(tmp_path.rglob("*")) - before)
    outside = [p for p in made if p != tier and tier not in p.parents]
    assert not outside, (
        "a writer with no workspace created %d path(s) outside the tier the env "
        "names (%r):\n%s"
        % (len(outside), str(tier), "\n".join("  " + str(p) for p in outside)))
