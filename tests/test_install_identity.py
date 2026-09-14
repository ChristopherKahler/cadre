"""Issue #120 — an install must be able to say what commit it is.

The defect these pin: the version was the frozen literal ``0.1.0`` and had
never moved, so ``pip install`` of a freshly built wheel into an existing
environment printed "Requirement already satisfied" and installed nothing,
while ``--version`` agreed with the old bytes and the new ones alike. A fix
could be declared landed while the old code was still running.

Hermetic: no pip, no network, no real installs. The build stamp is injected as
a module, which only works because ``_build_info`` imports it with
``importlib.import_module`` -- the ``from firm import _build_stamp`` form
re-imports the real submodule and rebinds it on the package, so an injected
fake is silently ignored and every case reads the same on-disk value. That was
measured: four different synthetic stamps all produced one string.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest

from firm import _build_info
from firm import identity as ident_mod
from firm.cli import install as install_mod
from firm.cli import install_doctor

COMMIT_A = "11c530b7da4fbf4f5d171324f6ea253c6cb5499d"
COMMIT_B = "6cab301a564ec35a159b12c166d3d7b6312343c7"


@pytest.fixture
def stamp(monkeypatch):
    """Install a synthetic build stamp, and take it away again.

    monkeypatch.setitem on sys.modules rather than a bare assignment, so a
    failing test cannot leak a fake stamp into every test that follows it.
    """
    def _set(commit=COMMIT_A, count=419, dirty=False, tag="", describe="",
             built_at="2026-09-14T00:00:00Z", remote_distance=""):
        mod = types.ModuleType("firm._build_stamp")
        mod.COMMIT = commit
        mod.COMMIT_COUNT = count
        mod.DIRTY = dirty
        mod.TAG = tag
        mod.DESCRIBE = describe
        mod.BUILT_AT = built_at
        mod.REMOTE_DISTANCE = remote_distance
        monkeypatch.setitem(sys.modules, "firm._build_stamp", mod)
        # Running from a git checkout, the live commit is read first (G2 F1) and
        # this synthetic stamp would never be reached. These tests are about the
        # stamp, so the checkout source is switched off for them.
        monkeypatch.setattr(_build_info, "_from_checkout", lambda: None, raising=False)
        return mod

    yield _set


@pytest.fixture
def no_stamp(monkeypatch):
    """No stamp at all, and no archive substitution: the honest-unknown case."""
    monkeypatch.delitem(sys.modules, "firm._build_stamp", raising=False)
    monkeypatch.setattr(_build_info, "_ARCHIVE_COMMIT", "$Format:%H$")
    monkeypatch.setattr(_build_info, "_from_checkout", lambda: None, raising=False)

    def _raise(name):
        raise ImportError(name)

    monkeypatch.setattr(_build_info.importlib, "import_module", _raise)


# ---------------------------------------------------------------------------
# DoD 1 — the version moves with the code
# ---------------------------------------------------------------------------

def test_two_commits_produce_two_versions(stamp):
    """THE requirement. If this ever passes vacuously, #120 is back."""
    stamp(commit=COMMIT_A, count=419)
    a = _build_info.version_string()
    stamp(commit=COMMIT_B, count=412)
    b = _build_info.version_string()
    assert a != b, (
        f"two different commits produced the same version {a!r}; pip will "
        f"short-circuit the second install and the new bytes will never land")
    assert COMMIT_A[:7] in a and COMMIT_B[:7] in b


def test_the_same_commit_produces_the_same_version(stamp):
    """Derived, not generated: no clock, no counter, no randomness."""
    stamp(commit=COMMIT_A, count=419)
    first = _build_info.version_string()
    stamp(commit=COMMIT_A, count=419)
    assert _build_info.version_string() == first


def test_a_dirty_tree_is_marked_in_the_version(stamp):
    stamp(commit=COMMIT_A, count=419, dirty=True)
    assert _build_info.version_string().endswith(".dirty")


def test_a_tagged_build_is_the_plain_release_version(stamp):
    stamp(commit=COMMIT_A, count=419, tag="v1.2.3")
    assert _build_info.version_string() == "1.2.3"


def test_a_dirty_tagged_build_is_not_the_plain_version(stamp):
    """A tag on a dirty tree describes code that is not what is being built."""
    stamp(commit=COMMIT_A, count=419, dirty=True, tag="v1.2.3")
    assert _build_info.version_string() != "1.2.3"


def test_a_bare_sha_from_describe_is_never_read_as_a_tag(stamp):
    """`git describe --tags --always` returns a bare short sha when the repo
    has no tags, and this repository has none. Inferring tag-ness from the
    shape of that string made the raw sha the version; it stayed hidden because
    an unrelated dirty-tree clause short-circuited ahead of it."""
    stamp(commit=COMMIT_A, count=419, tag="", describe="11c530b")
    version = _build_info.version_string()
    assert version != "11c530b"
    assert version.startswith("0.1.0.dev419")


# ---------------------------------------------------------------------------
# The honest-absence invariant
# ---------------------------------------------------------------------------

def test_with_no_stamp_and_no_archive_the_commit_is_unknown(no_stamp):
    info = _build_info.build_info()
    assert info["source"] == "unknown"
    assert info["commit"] is None
    assert _build_info.version_string() == _build_info.BASE_VERSION


# The unsubstituted placeholder must never read as a commit: the whole no-git
# fallback rests on that. It is pinned by the "$Format:%H$" case below, a
# literal, and deliberately NOT by reading _build_info._ARCHIVE_COMMIT: git
# archive substitutes that constant with the real commit, so a test reading it
# failed on every tree made by git archive, GitHub's source tarballs included
# (#120 G2 F6, avocet's L10). The case below still fails if _is_sha ever
# accepts the placeholder.
@pytest.mark.parametrize("value,expected", [
    ("$Format:%H$", False),
    ("", False),
    (None, False),
    ("11c530b", False),                       # short sha: not a full commit
    (COMMIT_A.upper(), False),                # uppercase: not the canonical form
    (COMMIT_A, True),
])
def test_is_sha_accepts_only_a_full_lowercase_commit(value, expected):
    assert _build_info._is_sha(value) is expected


# ---------------------------------------------------------------------------
# DoD 2 — a live install answers "what commit are you?"
# ---------------------------------------------------------------------------

def test_identity_carries_the_commit_and_keeps_the_sources_apart(stamp, monkeypatch):
    stamp(commit=COMMIT_A, count=419)
    monkeypatch.setattr(ident_mod, "_metadata_version", lambda: "0.1.0.dev419+g11c530b")
    monkeypatch.setattr(ident_mod, "_direct_url", lambda: None)
    out = ident_mod.installed_identity()
    assert out["sources"]["build"]["commit"] == COMMIT_A
    assert out["sources"]["build"]["commit_short"] == COMMIT_A[:7]
    # The three sources stay separate: merging them would hide the one thing
    # worth knowing, which is when they disagree.
    assert set(out["sources"]) == {"build", "metadata", "wheel"}


@pytest.mark.parametrize("meta,source,state,ok", [
    (None, "git", "not-installed", True),
    ("0.1.0", "unknown", "source-tree", True),
    ("0.1.0.dev419+g11c530b", "git", "agree", True),
    ("0.9.9", "git", "disagree", False),
])
def test_agreement_states(meta, source, state, ok):
    build = {"source": source}
    got = ident_mod._agreement("0.1.0.dev419+g11c530b", meta, build)
    assert got["state"] == state
    assert got["ok"] is ok


def test_a_stamped_package_disagreeing_with_the_installer_is_a_finding():
    """The arm that keeps the check honest after it was narrowed.

    The source-tree state was added because an unstamped tree beside a
    leftover egg-info was being reported as a mismatch. Narrowing it must not
    have removed its ability to fail on the case that matters: installed bytes
    that are not what the installer recorded.
    """
    got = ident_mod._agreement("0.1.0.dev419+g11c530b", "0.1.0.dev1+gdeadbee",
                               {"source": "git"})
    assert got["ok"] is False
    assert got["state"] == "disagree"


def test_the_wheel_filename_is_percent_decoded(monkeypatch):
    """direct_url.json holds a URL, so the ``+`` in every stamped local
    version arrives encoded as %2B. A live install reported its own wheel as
    cadre-0.1.0.dev421%2Bg3392d00-py3-none-any.whl."""
    raw = json.dumps({
        "url": "file:///tmp/cadre-0.1.0.dev421%2Bg3392d00-py3-none-any.whl",
        "archive_info": {"hashes": {"sha256": "abc123"}},
    })

    class FakeDist:
        def read_text(self, name):
            return raw if name == "direct_url.json" else None

    import importlib.metadata as md
    monkeypatch.setattr(md, "distribution", lambda name: FakeDist())
    out = ident_mod._direct_url()
    assert out["filename"] == "cadre-0.1.0.dev421+g3392d00-py3-none-any.whl"
    assert "%2B" not in out["filename"]
    assert out["sha256"] == "abc123"


def test_the_older_pip_hash_form_is_read_too(monkeypatch):
    raw = json.dumps({"url": "file:///tmp/c.whl",
                      "archive_info": {"hash": "sha256=deadbeef"}})

    class FakeDist:
        def read_text(self, name):
            return raw

    import importlib.metadata as md
    monkeypatch.setattr(md, "distribution", lambda name: FakeDist())
    assert ident_mod._direct_url()["sha256"] == "deadbeef"


# ---------------------------------------------------------------------------
# DoD 3 — an install cannot silently do nothing
# ---------------------------------------------------------------------------

def _wheel(tmp_path, name, commit, version):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("firm/_build_stamp.py", f"COMMIT = '{commit}'\n")
        zf.writestr(f"cadre-{version}.dist-info/METADATA",
                    f"Name: cadre\nVersion: {version}\n")
    return path


def test_the_wheel_identity_comes_from_the_archive_not_the_filename(tmp_path):
    """A filename is a label anyone can rename. The point of the command is
    that what lands is what was meant, so it reads the archive."""
    liar = _wheel(tmp_path, "cadre-9.9.9-py3-none-any.whl", COMMIT_A, "0.1.0.dev419")
    got = install_mod._wheel_identity(liar)
    assert got["commit"] == COMMIT_A
    assert got["version"] == "0.1.0.dev419"
    assert "9.9.9" not in str(got["version"])


def test_an_unreadable_wheel_is_reported_as_unreadable_not_as_empty(tmp_path):
    """absent != empty != zero. A wheel with no commit and a wheel that could
    not be opened are different facts and must not collapse into one."""
    broken = tmp_path / "broken-py3-none-any.whl"
    broken.write_bytes(b"not a zip at all")
    got = install_mod._wheel_identity(broken)
    assert got["readable"] is False
    assert got["commit"] is None


def test_install_refuses_a_path_that_is_not_a_wheel(tmp_path, capsys):
    notawheel = tmp_path / "notawheel.txt"
    notawheel.write_text("nope")
    rc = install_mod.run_install(notawheel)
    assert rc != 0
    assert "not a wheel" in capsys.readouterr().err


def test_install_refuses_a_missing_file(tmp_path, capsys):
    rc = install_mod.run_install(tmp_path / "nothing-here.whl")
    assert rc != 0
    assert "no such file" in capsys.readouterr().err


def test_install_refuses_an_unreadable_wheel_without_running_pip(tmp_path, capsys, monkeypatch):
    """The refusal has to happen BEFORE pip is asked to do anything, or the
    environment can be disturbed by an install that was never going to work."""
    called = []
    # Patch the name install.py CALLS. It calls run_utf8 now; a spy left on
    # subprocess.run would never be reached, and "pip was not invoked" would
    # pass because the spy was wired to nothing.
    monkeypatch.setattr(install_mod, "run_utf8",
                        lambda *a, **k: called.append(a) or None)
    broken = tmp_path / "cadre-0.1.0-py3-none-any.whl"
    broken.write_bytes(b"not a zip")
    rc = install_mod.run_install(broken)
    assert rc != 0
    assert called == [], "pip was invoked for a wheel that cannot be read"
    assert "not a valid zip" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# DoD 5 — the surfaces agree, because there is one producer
# ---------------------------------------------------------------------------

def test_the_hub_serves_the_shared_identity_unchanged(stamp, monkeypatch, tmp_path):
    stamp(commit=COMMIT_A, count=419)
    monkeypatch.setattr(ident_mod, "_metadata_version", lambda: "0.1.0.dev419+g11c530b")
    monkeypatch.setattr(ident_mod, "_direct_url", lambda: None)

    shared = ident_mod.installed_identity()
    served = ident_mod.hub_identity(tmp_path, {"acme": {}, "beta": {}})

    # Everything the shared producer says, the hub says identically. The hub
    # only ADDS what it alone knows.
    for key, value in shared.items():
        assert served[key] == value, f"the hub changed {key!r} on its way out"
    assert served["reaches"]["firms"] == ["acme", "beta"]
    assert served["reaches"]["firms_root"] == str(tmp_path)


def test_install_doctor_reports_on_the_same_identity(stamp, monkeypatch):
    stamp(commit=COMMIT_A, count=419)
    monkeypatch.setattr(ident_mod, "_metadata_version", lambda: "0.1.0.dev419+g11c530b")
    monkeypatch.setattr(ident_mod, "_direct_url", lambda: None)

    shared = ident_mod.installed_identity()
    checks = install_doctor.diagnose_install(shared)

    assert checks, "diagnose_install produced no checks at all"
    keys = {c["key"] for c in checks}
    assert "install-commit" in keys
    assert "install-agreement" in keys
    commit_card = next(c for c in checks if c["key"] == "install-commit")
    assert commit_card["ok"] is True
    assert COMMIT_A in commit_card["detail"]


def test_install_doctor_says_undeterminable_rather_than_failing_on_a_source_tree(no_stamp, monkeypatch):
    """A source checkout is a state, not a fault. Reporting it as a failure is
    how a check becomes noise that people learn to scroll past."""
    monkeypatch.setattr(ident_mod, "_metadata_version", lambda: None)
    monkeypatch.setattr(ident_mod, "_direct_url", lambda: None)
    checks = install_doctor.diagnose_install()
    commit_card = next(c for c in checks if c["key"] == "install-commit")
    assert commit_card["state"] == "undeterminable"


def test_every_check_carries_the_keys_the_renderer_reads():
    """The renderer reads label, ok and detail on every card. A card missing
    one of them raises at print time, which is the worst place to find out."""
    checks = install_doctor.diagnose_install()
    assert checks, "no checks to inspect"
    visited = 0
    for card in checks:
        visited += 1
        for key in ("key", "label", "ok", "detail", "route"):
            assert key in card, f"card {card.get('key')!r} has no {key!r}"
    assert visited == len(checks)
    assert visited > 0, "visited zero cards, so this proved nothing"


# ---------------------------------------------------------------------------
# G2 F1 — running from a checkout reports the commit git reports NOW
# ---------------------------------------------------------------------------

def _scratch_git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=test@cadre.invalid", "-c", "user.name=test", *args],
        cwd=cwd, check=True, capture_output=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()


def _cadre_shaped(root):
    """The two things that make a directory a Cadre checkout: the build backend
    beside ``src/``, and the package directory."""
    (root / "_build").mkdir(parents=True)
    (root / "_build" / "backend.py").write_text("# stand-in\n", encoding="utf-8")
    (root / "src" / "firm").mkdir(parents=True)
    return root


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A scratch Cadre checkout, treated as the home of the running package."""
    if shutil.which("git") is None:
        pytest.skip("needs git on PATH")
    root = _cadre_shaped(tmp_path / "cadre")
    _scratch_git(root, "init", "-q")
    _scratch_git(root, "add", "-A")
    _scratch_git(root, "commit", "-q", "-m", "A")
    monkeypatch.setattr(_build_info, "_PACKAGE_DIR", root / "src" / "firm", raising=False)
    return root


def test_a_checkout_names_the_commit_it_is_on_now_not_the_one_a_leftover_stamp_names(checkout, monkeypatch):
    """avocet's L8: an editable install kept naming the commit it was installed
    at after the checkout moved, and said everything agreed. A stamp left behind
    by an older build must not decide what a checkout reports."""
    commit_a = _scratch_git(checkout, "rev-parse", "HEAD")
    leftover = types.ModuleType("firm._build_stamp")
    leftover.COMMIT, leftover.COMMIT_COUNT, leftover.DIRTY = commit_a, 1, False
    leftover.TAG = leftover.DESCRIBE = leftover.BUILT_AT = leftover.REMOTE_DISTANCE = ""
    monkeypatch.setitem(sys.modules, "firm._build_stamp", leftover)

    (checkout / "src" / "firm" / "moved.py").write_text("X = 1\n", encoding="utf-8")
    _scratch_git(checkout, "add", "-A")
    _scratch_git(checkout, "commit", "-q", "-m", "B")
    commit_b = _scratch_git(checkout, "rev-parse", "HEAD")

    info = _build_info.build_info()

    assert info["commit"] != commit_a, "reported the commit a leftover stamp names"
    assert info["commit"] == commit_b
    assert info["source"] == "checkout"
    assert info["dirty"] is False
    assert _build_info.version_string() == f"{_build_info.BASE_VERSION}.dev2+g{commit_b[:7]}"


def test_a_checkout_reads_dirty_live(checkout):
    assert _build_info.build_info()["dirty"] is False
    (checkout / "src" / "firm" / "work_in_progress.py").write_text("", encoding="utf-8")
    assert _build_info.build_info()["dirty"] is True


def test_a_tree_inside_another_repository_is_not_read_as_a_checkout(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("needs git on PATH")
    outer = tmp_path / "project"
    outer.mkdir()
    _scratch_git(outer, "init", "-q")
    _scratch_git(outer, "commit", "-q", "--allow-empty", "-m", "outer")
    inner = _cadre_shaped(outer / "vendor" / "cadre")
    monkeypatch.setattr(_build_info, "_PACKAGE_DIR", inner / "src" / "firm", raising=False)
    assert _build_info._from_checkout() is None


def test_a_repository_without_the_build_backend_is_not_a_checkout(tmp_path, monkeypatch):
    # An installed package that happens to sit inside some git repository (a
    # virtualenv in a project folder) is not a Cadre checkout.
    if shutil.which("git") is None:
        pytest.skip("needs git on PATH")
    root = tmp_path / "project"
    (root / "src" / "firm").mkdir(parents=True)
    _scratch_git(root, "init", "-q")
    _scratch_git(root, "commit", "-q", "--allow-empty", "-m", "x")
    monkeypatch.setattr(_build_info, "_PACKAGE_DIR", root / "src" / "firm", raising=False)
    assert _build_info._from_checkout() is None


@pytest.mark.parametrize("meta", ["0.1.0.dev9+gbbbbbbb", "0.1.0.dev1+gaaaaaaa"])
def test_a_checkout_is_never_reported_as_agreeing_with_the_installer(meta):
    """A checkout's commit is read live; the installer's version was recorded
    once, at install time. Calling that agreement is how F1 said "agree" over a
    moved checkout, so a checkout gets its own state whatever the two say."""
    build = {"source": "checkout", "commit": COMMIT_B, "dirty": False}
    ag = ident_mod._agreement("0.1.0.dev9+gbbbbbbb", meta, build)
    assert ag["state"] == "checkout"
    assert ag["ok"] is True
    assert meta in ag["detail"]


# ---------------------------------------------------------------------------
# G2 F4 — an editable install is labelled as one, never as a wheel
# ---------------------------------------------------------------------------

_EDITABLE = {"url": "file:///home/someone/dev/cadre", "dir_info": {"editable": True}}


def test_an_editable_install_is_labelled_editable_with_its_directory(monkeypatch):
    raw = json.dumps(_EDITABLE)

    class FakeDist:
        def read_text(self, name):
            return raw if name == "direct_url.json" else None

    import importlib.metadata as md
    monkeypatch.setattr(md, "distribution", lambda name: FakeDist())
    out = ident_mod._direct_url()
    assert out["filename"] is None, f"a directory was reported as a wheel: {out['filename']!r}"
    assert out["sha256"] is None
    assert out["kind"] == "editable"
    assert out["dir"].replace("\\", "/").endswith("/home/someone/dev/cadre")


def test_a_wheel_install_is_labelled_a_wheel(monkeypatch):
    raw = json.dumps({"url": "file:///tmp/cadre-0.1.0-py3-none-any.whl",
                      "archive_info": {"hashes": {"sha256": "abc123"}}})

    class FakeDist:
        def read_text(self, name):
            return raw

    import importlib.metadata as md
    monkeypatch.setattr(md, "distribution", lambda name: FakeDist())
    out = ident_mod._direct_url()
    assert out["kind"] == "wheel"
    assert out["filename"] == "cadre-0.1.0-py3-none-any.whl"


def _editable_record():
    return {"kind": "editable", "url": _EDITABLE["url"], "dir": "/home/someone/dev/cadre",
            "filename": None, "sha256": None}


def test_the_text_report_calls_an_editable_install_editable_and_prints_no_wheel_line(stamp, monkeypatch):
    stamp()
    monkeypatch.setattr(ident_mod, "_direct_url", _editable_record)
    text = ident_mod.render_text(ident_mod.installed_identity())
    assert "editable" in text
    assert not [line for line in text.splitlines() if line.strip().startswith("wheel")]


def test_doctor_reads_the_editable_record_instead_of_saying_none_was_written(stamp, monkeypatch):
    stamp()
    monkeypatch.setattr(ident_mod, "_direct_url", _editable_record)
    monkeypatch.setattr(ident_mod, "_metadata_version", lambda: "0.1.0.dev419+g11c530b")
    checks = install_doctor.diagnose_install(ident_mod.installed_identity())
    card = next(c for c in checks if "install-artifact" in c.values())
    assert "wrote no direct_url.json" not in card["detail"]
    assert "editable" in card["detail"]


# ---------------------------------------------------------------------------
# G2 F2 — a byte-identity claim only when both hashes were read and are equal
# ---------------------------------------------------------------------------

_DIRTY_VERSION = "0.1.0.dev441+g21aba4f.dirty"

#: What `cadre install` says when pip's record names the same wheel before and
#: after. It names what was compared, a recorded wheel hash, and nothing more.
_SAME_WHEEL = "pip's record already named this exact wheel"


def _installed(sha256, *, commit=COMMIT_A, version=_DIRTY_VERSION):
    wheel = None if sha256 is None else {"kind": "wheel", "url": "file:///w.whl",
                                         "filename": "w.whl", "sha256": sha256}
    return {"version": version, "sources": {"build": {"commit": commit}, "wheel": wheel}}


def _install_with(monkeypatch, before, after):
    import hashlib
    states = iter([before, after])
    monkeypatch.setattr(install_mod, "_installed_identity", lambda python_bin: next(states))
    monkeypatch.setattr(install_mod, "run_utf8",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    return hashlib


def _file_sha(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_install_never_claims_the_same_bytes_when_the_bytes_differ(tmp_path, capsys, monkeypatch):
    """avocet's L7: two dirty builds of one commit share a label and differ in
    bytes. The install used to print that the environment already held exactly
    these bytes. It did not."""
    wheel = _wheel(tmp_path, f"cadre-{_DIRTY_VERSION}-py3-none-any.whl", COMMIT_A, _DIRTY_VERSION)
    old = "1" * 64
    _install_with(monkeypatch, _installed(old), _installed(_file_sha(wheel)))

    rc = install_mod.run_install(wheel)
    out = capsys.readouterr().out

    assert rc == 0
    assert "exactly these bytes" not in out
    assert _SAME_WHEEL not in out
    assert old in out and _file_sha(wheel) in out


def test_install_names_the_same_wheel_only_when_both_hashes_match(tmp_path, capsys, monkeypatch):
    # Control: the same wheel twice. pip's record names this exact wheel before and
    # after, and that is said out loud. What is NOT said is that the environment
    # held these bytes: equal recorded hashes do not show the installed files were
    # unchanged, because a hand-edited install reinstalled from the same wheel
    # records the same hash (avocet's L7E, #120 re-grade N2). The old line claimed
    # "exactly these bytes"; this assertion replaced it and fails if it returns.
    wheel = _wheel(tmp_path, f"cadre-{_DIRTY_VERSION}-py3-none-any.whl", COMMIT_A, _DIRTY_VERSION)
    sha = _file_sha(wheel)
    _install_with(monkeypatch, _installed(sha), _installed(sha))

    assert install_mod.run_install(wheel) == 0
    out = capsys.readouterr().out
    assert _SAME_WHEEL in out and sha in out
    assert "exactly these bytes" not in out


def test_install_says_bytes_not_verified_when_the_old_install_recorded_no_hash(tmp_path, capsys, monkeypatch):
    wheel = _wheel(tmp_path, f"cadre-{_DIRTY_VERSION}-py3-none-any.whl", COMMIT_A, _DIRTY_VERSION)
    _install_with(monkeypatch, _installed(None), _installed(_file_sha(wheel)))

    assert install_mod.run_install(wheel) == 0
    out = capsys.readouterr().out
    assert "exactly these bytes" not in out
    assert _SAME_WHEEL not in out
    assert "not verified" in out


def test_install_fails_when_the_environment_recorded_a_different_file(tmp_path, capsys, monkeypatch):
    # The installed record must be THIS wheel file, not just its label.
    wheel = _wheel(tmp_path, f"cadre-{_DIRTY_VERSION}-py3-none-any.whl", COMMIT_A, _DIRTY_VERSION)
    _install_with(monkeypatch, _installed("1" * 64), _installed("2" * 64))

    assert install_mod.run_install(wheel) == 1
    assert "sha256" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# G2 F5 — tests that can see the surfaces the DoD rows cite. Each one is proven
# by avocet's own mutation anchors (M1, M2, M4, M5 in its mut132.py): the
# mutation turns it red.
# ---------------------------------------------------------------------------

def _shared_identity_as_served():
    return json.loads(json.dumps(ident_mod.installed_identity(), default=str))


def test_heartbeat_status_embeds_the_shared_identity(stamp, monkeypatch, tmp_path, capsys):
    from firm.cli import heartbeat as hb
    stamp(commit=COMMIT_A, count=419)
    monkeypatch.setattr(ident_mod, "_metadata_version", lambda: "0.1.0.dev419+g11c530b")
    monkeypatch.setattr(ident_mod, "_direct_url", lambda: None)
    (tmp_path / "units").mkdir()

    assert hb.run_status(unit_dir=tmp_path / "units") == 0
    out = json.loads(capsys.readouterr().out)

    assert out["cadre"] == _shared_identity_as_served()


def test_the_hub_route_serves_the_shared_identity(stamp, monkeypatch, tmp_path):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from firm.dashboard.server import make_hub_handler

    stamp(commit=COMMIT_A, count=419)
    monkeypatch.setattr(ident_mod, "_metadata_version", lambda: "0.1.0.dev419+g11c530b")
    monkeypatch.setattr(ident_mod, "_direct_url", lambda: None)
    monkeypatch.setenv("CADRE_HOME", str(tmp_path / "cadre-home"))
    root = tmp_path / "firms"
    root.mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_hub_handler(root))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/identity"
        with urllib.request.urlopen(url, timeout=30) as resp:
            served = json.loads(resp.read())
    finally:
        server.shutdown()
        server.server_close()

    shared = _shared_identity_as_served()
    for key, value in shared.items():
        assert served.get(key) == value, f"the route changed or dropped {key!r}"
    assert "reaches" in served


def test_install_fails_when_the_environment_does_not_take_the_wheel_identity(tmp_path, capsys, monkeypatch):
    wheel = _wheel(tmp_path, f"cadre-{_DIRTY_VERSION}-py3-none-any.whl", COMMIT_A, _DIRTY_VERSION)
    _install_with(monkeypatch, _installed(None), _installed(_file_sha(wheel), commit=COMMIT_B))

    assert install_mod.run_install(wheel) == 1
    assert f"commit: wheel says {COMMIT_A}" in capsys.readouterr().err


def test_install_makes_pip_reinstall_even_when_the_version_matches(tmp_path, capsys, monkeypatch):
    wheel = _wheel(tmp_path, f"cadre-{_DIRTY_VERSION}-py3-none-any.whl", COMMIT_A, _DIRTY_VERSION)
    states = iter([_installed(None), _installed(_file_sha(wheel))])
    monkeypatch.setattr(install_mod, "_installed_identity", lambda python_bin: next(states))
    calls = []

    def spy(argv, **kwargs):
        calls.append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(install_mod, "run_utf8", spy)

    assert install_mod.run_install(wheel) == 0
    pip = [argv for argv in calls if "pip" in argv]
    assert len(pip) == 1
    assert "--force-reinstall" in pip[0] and "--no-deps" in pip[0]


# ---------------------------------------------------------------------------
# G2 re-grade R1 — a checkout whose git cannot answer says unknown, never a stamp
# ---------------------------------------------------------------------------

#: The first line git prints when it refuses a repository another account owns,
#: the shape avocet's K3 shim printed. The advice lines follow it.
_DUBIOUS = ("fatal: detected dubious ownership in repository at '/srv/cadre'\n"
            "To add an exception for this directory, call:\n\n"
            "\tgit config --global --add safe.directory /srv/cadre\n")


def _leftover_stamp(monkeypatch, commit=COMMIT_A):
    """A real stamp in the pre-rework backend's format, naming *commit*."""
    leftover = types.ModuleType("firm._build_stamp")
    leftover.COMMIT, leftover.COMMIT_COUNT, leftover.DIRTY = commit, 440, False
    leftover.TAG = leftover.DESCRIBE = leftover.REMOTE_DISTANCE = ""
    leftover.BUILT_AT = "2026-09-14T15:00:00Z"
    monkeypatch.setitem(sys.modules, "firm._build_stamp", leftover)


def _subprocess_where_git(kind):
    """``subprocess`` as ``_build_info`` sees it when git cannot answer.

    Scoped to ``_build_info``: the stub replaces that module's name only, so the
    real ``_git`` error handling runs and nothing else in the process loses its
    subprocess module.
    """
    def run(argv, **kwargs):
        if kind == "missing":
            raise FileNotFoundError(2, "No such file or directory", "git")
        if kind == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        return subprocess.CompletedProcess(argv, 128, stdout="", stderr=_DUBIOUS)
    return types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired,
                                 CompletedProcess=subprocess.CompletedProcess)


def _checkout_git_cannot_read(tmp_path, monkeypatch, kind="missing"):
    """A Cadre checkout with a repository of its own, a leftover stamp beside it,
    and a git that cannot answer: avocet's K2 (git hidden), K3 (git refusing)."""
    root = _cadre_shaped(tmp_path / "cadre")
    (root / ".git").mkdir()
    monkeypatch.setattr(_build_info, "_PACKAGE_DIR", root / "src" / "firm", raising=False)
    _leftover_stamp(monkeypatch)
    monkeypatch.setattr(_build_info, "subprocess", _subprocess_where_git(kind))
    return root


@pytest.mark.parametrize("kind,named", [
    ("missing", "could not be started"),
    ("refused", "dubious ownership"),
    ("timeout", "did not answer"),
])
def test_a_checkout_whose_git_cannot_answer_says_unknown_never_the_leftover_stamp(
        tmp_path, monkeypatch, kind, named):
    """avocet's K2, K3 and W7. With git hidden or refusing, a stamp left by an
    older build made an editable install name the commit it was installed at,
    with source git and state agree, while the checkout ran other code. A tree
    with its own repository never reads a stamp: git answers, or it is unknown
    and says why."""
    _checkout_git_cannot_read(tmp_path, monkeypatch, kind)

    info = _build_info.build_info()

    assert info["commit"] != COMMIT_A, "a leftover stamp decided what a checkout reports"
    assert info["commit"] is None
    assert info["source"] == "unknown"
    assert named in (info.get("checkout_error") or ""), info
    assert _build_info.version_string() == _build_info.BASE_VERSION


def test_identity_and_doctor_say_not_verified_when_a_checkout_cannot_be_read(tmp_path, monkeypatch):
    root = _checkout_git_cannot_read(tmp_path, monkeypatch, "missing")
    # What the pre-rework editable install recorded: the stamp's own label, so the
    # old code printed "agree" here (K2's exact symptom).
    monkeypatch.setattr(ident_mod, "_metadata_version", lambda: "0.1.0.dev440+g11c530b")
    monkeypatch.setattr(ident_mod, "_direct_url", lambda: {
        "kind": "editable", "url": root.as_uri(), "dir": str(root), "filename": None, "sha256": None})

    identity = ident_mod.installed_identity()
    text = ident_mod.render_text(identity)
    cards = {c["key"]: c for c in install_doctor.diagnose_install(identity)}

    assert identity["sources"]["build"]["commit"] is None
    assert identity["agreement"]["state"] != "agree", identity["agreement"]
    assert COMMIT_A not in text and "could not be started" in text, text
    assert cards["install-commit"]["state"] == "undeterminable", cards["install-commit"]
    assert "could not be started" in cards["install-commit"]["detail"]
    # Not a green tick: nothing about this install could be checked.
    assert cards["install-agreement"].get("state") == "undeterminable", cards["install-agreement"]


def test_an_unpacked_sdist_with_no_repository_of_its_own_keeps_its_stamp(tmp_path, monkeypatch):
    """The control for R1. An sdist carries the stamp its build wrote and has no
    .git, so the stamp IS its answer and must still be read."""
    root = _cadre_shaped(tmp_path / "cadre-0.1.0")
    monkeypatch.setattr(_build_info, "_PACKAGE_DIR", root / "src" / "firm", raising=False)
    _leftover_stamp(monkeypatch)
    monkeypatch.setattr(_build_info, "subprocess", _subprocess_where_git("missing"))

    info = _build_info.build_info()

    assert info["commit"] == COMMIT_A
    assert info["source"] == "git"


# ---------------------------------------------------------------------------
# G2 re-grade N1 — a live checkout has no build time
# ---------------------------------------------------------------------------

def _dirty_identity(source):
    return {"sources": {"build": {"commit": COMMIT_B, "source": source, "dirty": True},
                        "metadata": {"version": "0.1.0.dev9+g6cab301"}, "wheel": None},
            "agreement": {"ok": True, "state": "checkout", "detail": "running a git checkout"}}


def test_doctor_describes_a_dirty_checkout_as_changed_now_not_at_build_time():
    card = next(c for c in install_doctor.diagnose_install(_dirty_identity("checkout"))
                if c["key"] == "install-clean")
    assert card["ok"] is False
    assert "build time" not in card["detail"] and "Built" not in card["label"], card
    assert "now" in card["detail"], card


def test_doctor_still_says_build_time_for_a_stamped_build():
    # Control: a stamp really was written at build time, so there it is true.
    card = next(c for c in install_doctor.diagnose_install(_dirty_identity("git"))
                if c["key"] == "install-clean")
    assert "at build time" in card["detail"], card


# ---------------------------------------------------------------------------
# G2 re-grade N3 — importing firm starts no git, and reading never writes
# ---------------------------------------------------------------------------

#: Runs in a child: counts the git processes Python starts while `import firm`
#: runs, then, as the control that the counter can see git at all, while the
#: identity of a scratch checkout is read.
_COUNT_GIT = r"""
import json, os, subprocess, sys
from pathlib import Path
spawned = []
class _Counting(subprocess.Popen):
    def __init__(self, args, *a, **k):
        argv = [args] if isinstance(args, (str, bytes, os.PathLike)) else list(args)
        spawned.append(os.path.basename(os.fsdecode(argv[0])).lower())
        super().__init__(args, *a, **k)
subprocess.Popen = _Counting
import firm
at_import = sum(1 for name in spawned if name.startswith("git"))
from firm import _build_info
_build_info._PACKAGE_DIR = Path(sys.argv[1]) / "src" / "firm"
_build_info.build_info()
on_request = sum(1 for name in spawned if name.startswith("git")) - at_import
print(json.dumps({"firm": firm.__file__, "at_import": at_import, "on_request": on_request}))
"""


def test_importing_firm_starts_no_git_process(checkout):
    """avocet's L12: every firm process imports this package, and on main that
    ran no git. Reading the commit at import cost every one of them four git
    children, one of which rewrote the checkout's index. The identity and the
    version resolve when they are asked for."""
    src = Path(_build_info.__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, "-c", _COUNT_GIT, str(checkout)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=120,
        env=dict(os.environ, PYTHONPATH=str(src)), cwd=str(checkout.parent),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert Path(got["firm"]).resolve().parent == src / "firm", got
    assert got["on_request"] > 0, f"CONTROL: the counter saw no git on request: {got}"
    assert got["at_import"] == 0, got


def test_cadre_version_resolves_when_asked_for_not_when_the_cli_starts(monkeypatch, capsys):
    from firm import __main__ as cli
    calls = []
    monkeypatch.setattr(_build_info, "version_string",
                        lambda: calls.append("asked") or "0.1.0.dev7+gabcdef0")

    parser = cli._build_parser()
    assert calls == [], "building the parser resolved the version"

    with pytest.raises(SystemExit) as stopped:
        parser.parse_args(["--version"])

    assert stopped.value.code == 0
    assert capsys.readouterr().out.strip() == f"{parser.prog} 0.1.0.dev7+gabcdef0"
    assert calls == ["asked"]


def test_reading_a_checkout_identity_never_rewrites_its_git_index(checkout):
    """osprey's index leg for N3. git status refreshes a stat-stale index entry and
    writes the index back when it can take the lock, which can collide with a
    commit running in that checkout. The identity read must leave it alone."""
    index = checkout / ".git" / "index"
    stale = checkout / "_build" / "backend.py"
    os.utime(stale, (1_577_836_800, 1_577_836_800))   # 2020-01-01: stat no longer matches
    before = (index.stat().st_mtime_ns, index.read_bytes())

    info = _build_info.build_info()

    assert info["source"] == "checkout" and info["dirty"] is False, info
    assert (index.stat().st_mtime_ns, index.read_bytes()) == before, "reading rewrote the index"
    # CONTROL: a plain status on the same stat-stale entry does rewrite it, so the
    # assertion above was able to fail.
    _scratch_git(checkout, "status", "--porcelain")
    assert (index.stat().st_mtime_ns, index.read_bytes()) != before
