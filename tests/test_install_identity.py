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
import re
import sys
import types
import zipfile

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
        return mod

    yield _set


@pytest.fixture
def no_stamp(monkeypatch):
    """No stamp at all, and no archive substitution: the honest-unknown case."""
    monkeypatch.delitem(sys.modules, "firm._build_stamp", raising=False)
    monkeypatch.setattr(_build_info, "_ARCHIVE_COMMIT", "$Format:%H$")

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


def test_the_archive_placeholder_can_never_be_read_as_a_commit():
    """The whole no-git fallback rests on this being un-mistakable.

    In a plain checkout the literal stays ``$Format:%H$``. If it ever became
    sha-shaped, an unsubstituted placeholder would be reported as a real
    commit and every downstream reader would believe it.
    """
    raw = _build_info._ARCHIVE_COMMIT
    assert not _build_info._is_sha(raw)
    assert not re.match(r"^[0-9a-f]{40}$", raw)


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
