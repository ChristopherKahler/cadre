"""The shipped base extension manifest, and the install that refuses to lie.

The manifest this replaces named one firm — its root path, its virtual
environment, a board-pack path on another operating system — inside a file that
base loads for every workspace on the machine. So every Member of every firm was
told about that one firm. That is issue #4.

The measurement that shapes the fix: a domain block in an extension manifest
CANNOT be limited to a folder. Both `paths` and `file_keywords` are accepted,
`base extension validate` returns 0, and the block fires in every firm anyway
(1/1 in two firms side by side for all three shapes tried, negative control 0).
The same block in a firm's own .base/domains.toml fires in one firm and not the
other. So anything naming a firm or a member must not be in this file, and the
first test below is what stops that coming back.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from firm.services import base_extension

ROOT_FOR_SOURCE = Path(__file__).resolve().parent.parent.parent


def _parsed() -> dict:
    raw = base_extension.manifest_source().read_text(encoding="utf-8")
    return tomllib.loads(raw.replace(base_extension.PLACEHOLDER, "/tmp/framework"))


# ---------------------------------------------------------------------------
# Nothing in here may name a firm or a member — heron's condition 2
# ---------------------------------------------------------------------------

# Every firm that exists on the maintainer's machine, plus the shapes the old
# manifest used. A new firm added later is covered by the structural tests
# below; these are the names that actually leaked.
LEAKED_NAMES = [
    "chrisai", "chief-of-staff", "backstage", "crows-and-pawns", "dnd-table",
    "downstream", "partner-media-desk", "reading-desk", "table-online",
    "the-wastelander-novel", "wastelander", "lab",
]


@pytest.mark.parametrize("name", LEAKED_NAMES)
def test_the_manifest_names_no_firm(name):
    text = base_extension.manifest_source().read_text(encoding="utf-8").lower()
    body = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
    assert name not in body, (
        f"{name!r} appears in the shipped manifest. base loads this file for "
        "every workspace on the machine and a domain in it cannot be limited to "
        "a folder, so one firm's name here reaches every other firm. That is "
        "issue #4 exactly.")


def test_the_manifest_carries_no_absolute_path_outside_the_framework():
    """The old manifest hard-coded one firm's root, one virtual environment and
    a board-pack path on the operator's other operating system."""
    text = base_extension.manifest_source().read_text(encoding="utf-8")
    body = [l for l in text.splitlines() if not l.strip().startswith("#")]
    offenders = []
    for line in body:
        if base_extension.PLACEHOLDER in line:
            continue
        for bad in ("/home/", "/mnt/", "C:/", "C:\\", "/Users/"):
            if bad in line:
                offenders.append(line.strip())
    assert offenders == [], f"absolute paths in the shipped manifest: {offenders}"


def test_the_manifest_teaches_no_tool_that_does_not_exist():
    """Six of twelve firms load no firm MCP server, so these names resolve to
    nothing for half the fleet. Teaching them is issue #2."""
    text = base_extension.manifest_source().read_text(encoding="utf-8")
    body = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
    for absent in ("unit_create", "firm_escalate", "firm_request_gate"):
        assert absent not in body, f"{absent} is taught by the manifest and does not exist everywhere"


def test_the_manifest_declares_the_cli_verbs_that_do_exist():
    """The control for the test above: it must not pass by saying nothing."""
    body = base_extension.manifest_source().read_text(encoding="utf-8")
    for verb in ("firm unit create", "firm doc register", "firm unit complete",
                 "firm escalation raise", "base learn"):
        assert verb in body, f"{verb} is missing, so the rules name no route at all"


# ---------------------------------------------------------------------------
# Shape — the traps that pass validation and then do nothing
# ---------------------------------------------------------------------------

def test_state_dir_sits_above_the_first_command():
    """A bare key binds to the most recent table header. Written below
    [[commands]] this becomes a field of the command, gets dropped as unknown,
    and every ingest path silently falls back to the working directory — with
    validation passing the whole way."""
    parsed = _parsed()
    assert parsed["extension"]["state_dir"] == ".firm/"
    for command in parsed.get("commands", []):
        assert "state_dir" not in command, "state_dir fell into a command block"


def _line_index(text: str, prefix: str) -> int:
    """Line number of the first line that STARTS with prefix.

    Anchored at column 0 on purpose. The first version of this test searched
    the whole file for "[[commands]]" and matched the header comment explaining
    the trap, 400 bytes before the real header, so it failed on a correct file.
    A substring can always match a different, legitimate line.
    """
    for i, line in enumerate(text.splitlines()):
        if line.startswith(prefix):
            return i
    return -1


def test_the_raw_text_puts_state_dir_before_the_commands_header():
    """The parsed check above passes either way if TOML happens to forgive the
    order, so this one reads the file as written."""
    text = base_extension.manifest_source().read_text(encoding="utf-8")
    state_at = _line_index(text, "state_dir =")
    command_at = _line_index(text, "[[commands]]")
    assert state_at != -1, "no state_dir at column 0"
    assert command_at != -1, "no [[commands]] header at column 0"
    assert state_at < command_at, (
        f"state_dir is on line {state_at}, the commands header on line "
        f"{command_at}; below it the key becomes a command field and is dropped")


def test_the_ordering_check_can_actually_fail():
    """The control for the test above. It was green over a mis-anchored search
    once already, so prove the comparison discriminates."""
    swapped = "[[commands]]\nname = \"x\"\nstate_dir = \".firm/\"\n"
    assert _line_index(swapped, "state_dir =") > _line_index(swapped, "[[commands]]")
    correct = "state_dir = \".firm/\"\n\n[[commands]]\nname = \"x\"\n"
    assert _line_index(correct, "state_dir =") < _line_index(correct, "[[commands]]")


def test_the_column_zero_anchor_ignores_comments():
    """A comment mentioning the header must not be mistaken for the header."""
    text = "# talks about [[commands]] and state_dir =\nstate_dir = \".f/\"\n[[commands]]\n"
    assert _line_index(text, "state_dir =") == 1
    assert _line_index(text, "[[commands]]") == 2


def test_no_post_tool_pattern_contains_a_wildcard():
    """`pattern` is a plain substring of the path, not a glob. Measured on base
    0.15.0: "*", "*.md" and "projects/*" never fire, and "*.registry.json" does
    not match content.registry.json. A pattern with a star in it is dead."""
    parsed = _parsed()
    handlers = parsed["hooks"]["post_tool"]["handlers"]
    assert handlers, "no handlers to check — this test would pass on an empty file"
    for handler in handlers:
        assert "*" not in handler["pattern"], (
            f"{handler['pattern']!r} contains a star, which base treats as a "
            "literal character, so this handler can never fire")


def test_the_briefing_is_not_attempted_in_the_manifest():
    """`inject` does not put query results into its text — {total}, ${total} and
    {{total}} all arrive unchanged. A briefing written here renders a template
    with holes in it. That is issue #6."""
    parsed = _parsed()
    inject = parsed["hooks"]["session_start"].get("inject", "")
    assert "{" not in inject.replace("{{", "").replace("}}", ""), (
        f"the session_start inject carries a placeholder: {inject!r}")
    assert "queries" not in parsed["hooks"]["session_start"], (
        "the manifest declares queries whose results cannot reach inject")


def test_the_ingest_reads_exports_not_the_database():
    """base ingests JSON. It cannot read .firm/firm.db."""
    parsed = _parsed()
    entries = parsed["hooks"]["session_start"]["ingest"]
    assert len(entries) == 3, f"expected 3 ingest entries, found {len(entries)}"
    for entry in entries:
        assert entry["file"].endswith(".json"), entry["file"]
        assert not entry["file"].startswith("/"), "ingest paths are relative to state_dir"
        assert entry["strategy"] in ("upsert", "replace")


def test_exactly_one_prompt_domain_and_it_is_firm_neutral():
    parsed = _parsed()
    domains = parsed["hooks"]["user_prompt"]["domains"]
    assert len(domains) == 1, f"expected 1 domain, found {[d['name'] for d in domains]}"
    assert domains[0]["name"] == "cadre-firm"
    assert domains[0]["rules"], "a domain with no rules is dropped by base entirely"


def test_the_command_block_is_intact():
    """The handler is a PLACEHOLDER in the shipped manifest, never a path.

    It read `bin/cadre`, resolved against framework_dir. In a source checkout
    framework_dir is the repo root and that path exists, which is exactly why
    this assertion passed for the life of the file. From an installed wheel
    framework_dir is site-packages, and `site-packages/bin/cadre` exists on
    NEITHER platform — so `base cadre` validated, installed, read back clean,
    and was dead on every real install (F1, found by sandpiper on Windows and
    reproduced on a Linux venv from the same wheel).

    A literal path in the shipped file is the defect. Asserting the
    placeholder is what stops it coming back.
    """
    parsed = _parsed()
    commands = parsed["commands"]
    assert len(commands) == 1
    assert commands[0]["name"] == "cadre"
    raw = base_extension.manifest_source().read_text(encoding="utf-8")
    assert base_extension.HANDLER_PLACEHOLDER in raw, (
        "the shipped manifest hard-codes a handler path again; there is no one "
        "relative path that resolves in both a checkout and a wheel")
    assert 'handler = "bin/cadre"' not in raw, (
        "handler is back to bin/cadre, which resolves to site-packages/bin/cadre "
        "from an installed wheel and exists on no platform")


def test_the_version_moved_past_the_manifest_it_replaces():
    parsed = _parsed()
    assert parsed["extension"]["version"] != "0.1.0", (
        "0.1.0 is the manifest being replaced; a reader cannot tell them apart")


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def test_render_fills_in_the_framework_directory():
    out = base_extension.render("/opt/cadre")
    assert base_extension.PLACEHOLDER not in out
    assert 'framework_dir = "/opt/cadre"' in out


def test_render_refuses_a_manifest_with_no_placeholder(tmp_path, monkeypatch):
    """A manifest whose framework_dir was hard-coded is one machine's file, and
    installing it on another silently points the handler at nothing."""
    fake = tmp_path / "cadre.toml"
    fake.write_text('[extension]\nframework_dir = "/someone/elses/box"\n', encoding="utf-8")
    monkeypatch.setattr(base_extension, "manifest_source", lambda: fake)
    with pytest.raises(ValueError, match="hard-coded"):
        base_extension.render("/opt/cadre")


def test_the_wheel_is_told_to_carry_the_manifest():
    """A manifest in the repo that the wheel leaves behind ships nothing.

    `pip install cadre` copies only what package-data names. Without this entry
    the file sits in git, every test here passes, and the installed package has
    no manifest at all — the failure would only show on someone else's machine.
    Proven for real by scripts/verify/verify_packaging.py, which builds a wheel
    and opens it; this is the cheap guard that keeps the line from being lost.
    """
    import tomllib
    root = base_extension.manifest_source().parent.parent.parent.parent
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():          # installed, not a source checkout
        pytest.skip(f"no pyproject at {pyproject} — not a source checkout")
    parsed = tomllib.load(pyproject.open("rb"))
    package_data = parsed["tool"]["setuptools"]["package-data"]["firm"]

    # GLOBBED, not string-matched. `"base_ext/*.toml" in package_data` was true
    # and green while the shipped file was renamed to cadre.toml.template, which
    # that pattern does not match — the entry was present and carried nothing.
    # A packaging guard has to ask whether the real files are covered.
    import fnmatch
    base_ext = base_extension.manifest_source().parent
    on_disk = sorted(p.name for p in base_ext.iterdir() if p.is_file())
    assert on_disk, f"{base_ext} is empty, so there is no manifest to ship"
    patterns = [p.split("/", 1)[1] for p in package_data if p.startswith("base_ext/")]
    unmatched = [n for n in on_disk
                 if not any(fnmatch.fnmatch(n, pat) for pat in patterns)]
    assert not unmatched, (
        f"package-data patterns {patterns} match none of {unmatched}, so a wheel "
        f"drops them: {package_data}")


# ---------------------------------------------------------------------------
# pyproject.toml — derived guards, never frozen literals
# ---------------------------------------------------------------------------

def _dependencies_of(pyproject_text: str) -> list[str]:
    """The declared runtime dependencies in a pyproject.toml's text."""
    return tomllib.loads(pyproject_text)["project"]["dependencies"]


def _dependency_drift(branch_text: str, baseline_text: str) -> list[str]:
    """Every dependency that differs between two pyproject.toml texts.

    The symmetric difference, sorted, one line per side, so a moved bound
    (`mcp>=1.0` becoming `mcp>=1.0,<2`) shows up as both a `-` and a `+`.
    An empty list means this branch declares exactly what the baseline does.
    """
    branch = _dependencies_of(branch_text)
    baseline = _dependencies_of(baseline_text)
    added = [f"+{d}" for d in branch if d not in baseline]
    removed = [f"-{d}" for d in baseline if d not in branch]
    return sorted(added + removed)


_BASELINE_REFS = ("origin/main", "main")


def _baseline_pyproject(root: Path) -> tuple[str, str]:
    """This branch's fork point's pyproject.toml, and the ref it came from."""
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH, so there is no baseline to derive from")
    for ref in _BASELINE_REFS:
        done = subprocess.run(
            ["git", "show", f"{ref}:pyproject.toml"],
            cwd=root, capture_output=True, text=True,
        )
        if done.returncode == 0:
            return done.stdout, ref
    pytest.skip(
        "no baseline ref resolves here (tried " + ", ".join(_BASELINE_REFS) +
        "); a shallow checkout carries no main to compare against. "
        "test_the_baseline_resolver_reads_the_ref_it_names is the control that "
        "keeps this honest wherever a ref does exist.")


def test_the_packaging_change_added_no_dependency():
    """This branch touches the package-data section of pyproject.toml and
    nothing else in that file.

    Derived, not frozen. The first version of this guard asserted the
    dependency list equalled a literal, so when the packaging lane legitimately
    moved `mcp>=1.0` to `mcp>=1.0,<2` in #20 this went red on the correct
    state. Comparing against the baseline ref still catches "this branch moved
    a dependency" and stops firing every time somebody else's lane lands.
    """
    root = base_extension.manifest_source().parent.parent.parent.parent
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        pytest.skip("not a source checkout")
    baseline_text, ref = _baseline_pyproject(root)
    drift = _dependency_drift(pyproject.read_text(encoding="utf-8"), baseline_text)
    assert drift == [], (
        f"a dependency moved on this branch relative to {ref}: {drift}. "
        "That hunk belongs to the packaging lane, not to this one.")


def test_the_dependency_guard_catches_a_moved_dependency():
    """Control for the comparator, needing no git at all.

    The guard above skips wherever no baseline ref resolves, and a skip reads
    exactly like a pass. This fails the moment the comparator stops being able
    to tell a moved dependency from an unmoved one.
    """
    def doc(*deps: str) -> str:
        return "[project]\ndependencies = [" + ", ".join(repr(d) for d in deps) + "]\n"

    baseline = doc("mcp>=1.0", "cryptography>=42")

    assert _dependency_drift(doc("mcp>=1.0", "cryptography>=42"), baseline) == []
    assert _dependency_drift(doc("mcp>=1.0,<2", "cryptography>=42"), baseline) == [
        "+mcp>=1.0,<2", "-mcp>=1.0"]
    assert _dependency_drift(doc("mcp>=1.0", "cryptography>=42", "requests"), baseline) == [
        "+requests"]
    assert _dependency_drift(doc("mcp>=1.0"), baseline) == ["-cryptography>=42"]


def _scratch_repo(root: Path, main_deps: list[str], branch_deps: list[str],
                  with_ref: bool = True) -> Path:
    """A throwaway repo shaped like this one: a baseline ref, and a lane on top."""
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)

    def write(deps: list[str]) -> None:
        (root / "pyproject.toml").write_text(
            '[project]\nname = "scratch"\nversion = "0"\ndependencies = ['
            + ", ".join(repr(d) for d in deps) + "]\n", encoding="utf-8")

    git("init", "-q", "-b", "main")
    git("config", "user.email", "control@cadre.test")
    git("config", "user.name", "control")
    write(main_deps)
    git("add", "-A")
    git("commit", "-q", "-m", "baseline")
    if with_ref:
        git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("checkout", "-q", "-b", "lane")
    write(branch_deps)
    # A real lane always touches other files, so the arm where the dependency
    # lists match deliberately still has something to commit.
    (root / "lane_touched.txt").write_text("not a dependency\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "lane")
    if not with_ref:
        git("branch", "-q", "-D", "main")
    return root


def test_the_baseline_resolver_reads_the_ref_it_names(tmp_path):
    """Control for the resolver, against real git.

    Without this, a resolver that stopped finding any ref would turn the guard
    above into a permanent skip, and the suite would stay green while the
    branch was no longer checked at all. Arm 1 is the exact state that broke
    the frozen-literal version: the packaging lane moved a bound and both sides
    carry the new value, so the guard must be silent.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    moved, old, cryp = "mcp>=1.0,<2", "mcp>=1.0", "cryptography>=42"

    def resolve_or_fail(repo: Path) -> tuple[str, str]:
        """`_baseline_pyproject`, but a skip in here is a failure.

        Every repo below is built WITH the ref, confirmed by a direct `git
        show` first. So a resolver that skips one of these is broken, and
        letting that come out as a skip is the whole trap: two mutations of
        this resolver (accept the refs git could not show; search for a ref
        nobody has) leave the suite green at 37 passed / 0 failed, because
        both simply stop checking. Turning the skip into a failure is what
        makes those two visible.
        """
        proof = subprocess.run(["git", "show", "origin/main:pyproject.toml"],
                               cwd=repo, capture_output=True, text=True)
        assert proof.returncode == 0, f"scratch repo has no origin/main: {proof.stderr}"
        try:
            return _baseline_pyproject(repo)
        except pytest.skip.Exception as exc:
            pytest.fail(f"the resolver skipped a ref that git can show: {exc}")

    def drift_of(name: str, main_deps, branch_deps):
        repo = _scratch_repo(tmp_path / name, [*main_deps], [*branch_deps])
        text, ref = resolve_or_fail(repo)
        assert ref == "origin/main", f"resolver named {ref!r}, not the ref that exists"
        return _dependency_drift((repo / "pyproject.toml").read_text(encoding="utf-8"), text)

    for name in ("same", "added", "dropped", "moved", "noref"):
        (tmp_path / name).mkdir()

    assert drift_of("same", [moved, cryp], [moved, cryp]) == [], (
        "the guard fires when the packaging lane moves a bound on both sides — "
        "that is the fault this replaced, and it is back")
    assert drift_of("added", [moved, cryp], [moved, cryp, "requests>=2"]) == ["+requests>=2"]
    assert drift_of("dropped", [moved, cryp], [moved]) == [f"-{cryp}"]
    assert drift_of("moved", [old, cryp], [moved, cryp]) == [f"+{moved}", f"-{old}"]

    # No ref anywhere: a skip, never a silent pass and never an error.
    bare = _scratch_repo(tmp_path / "noref", [moved, cryp], [moved, cryp], with_ref=False)
    with pytest.raises(pytest.skip.Exception) as raised:
        _baseline_pyproject(bare)
    assert "origin/main" in str(raised.value), "the skip does not say what it tried"


def test_the_shipped_manifest_is_inside_the_package():
    src = base_extension.manifest_source()
    assert src.exists(), f"{src} is missing, so pip install cadre ships no manifest"
    assert src.parent.name == "base_ext"
    assert src.parent.parent.name == "firm"


# ---------------------------------------------------------------------------
# F6 — the two install routes must agree, and the filename is what makes them
# ---------------------------------------------------------------------------

def test_the_package_ships_no_installable_cadre_toml():
    """THE F6 FIX, and the only part of it base can be made to enforce.

    Measured on base 0.15.0 under an isolated BASE_HOME: `base extension
    validate` and `base extension install` BOTH return 0 for a manifest whose
    handler points at a path that exists nowhere. base never looks. So there is
    no content this file could carry that would make the obvious hand install

        base extension install <site-packages>/firm/base_ext/cadre.toml

    refuse — it would report success, list the extension, and leave `base
    cadre` exiting 127.

    base DOES refuse a file it cannot read: rc 1, "Cannot install: invalid
    manifest: Cannot read file". The absence of this filename is therefore the
    refusal, which is why it is asserted rather than left to chance.
    """
    shipped = base_extension.manifest_source().parent / "cadre.toml"
    assert not shipped.exists(), (
        f"{shipped} is back. `base extension install` will accept it, report "
        f"success, and leave `base cadre` dead — that is F6 reopened. The "
        f"shipped file must be {base_extension.MANIFEST_TEMPLATE_NAME}.")


def test_the_shipped_manifest_is_named_as_a_template():
    src = base_extension.manifest_source()
    assert src.name == base_extension.MANIFEST_TEMPLATE_NAME
    assert src.name.endswith(".template"), (
        "the suffix is the refusal; without it the hand install succeeds over a "
        "dead command")


def test_the_handler_placeholder_tells_the_reader_what_to_do():
    """base prints an unresolved handler path back verbatim, so make it useful.

    Measured: `base <ext>` exits 127 with

        base: command 'x' (ext:x) — handler not found: <the literal token>

    A token reading `{{handler}}` makes that message a puzzle. This one makes it
    an instruction. The test asserts the words that carry the remedy, not the
    exact string, so the token can be reworded but not hollowed out.
    """
    token = base_extension.HANDLER_PLACEHOLDER
    assert token.startswith("{{") and token.endswith("}}")
    lowered = token.lower()
    assert "unrendered" in lowered, (
        f"{token!r} does not say the manifest was never rendered")
    assert "cadre" in lowered and "extension" in lowered and "install" in lowered, (
        f"{token!r} does not name `cadre extension install`, so base's error "
        f"message names the symptom and not the fix")
    assert token in base_extension.manifest_source().read_text(encoding="utf-8"), (
        "the shipped template does not carry the token the code renders")


def test_the_rename_did_not_follow_the_manifest_into_base():
    """base derives the extension name from the file it writes, not from ours.

    The staged temp file and the installed path both stay `cadre.toml`. If the
    `.template` suffix leaked into either, base would install an extension named
    after a template and `base cadre` would not resolve.
    """
    assert base_extension._installed_path().name == "cadre.toml"
    src = (ROOT_FOR_SOURCE / "src" / "firm" / "services" / "base_extension.py"
           ).read_text(encoding="utf-8")
    assert 'staged = Path(tmpdir) / "cadre.toml"' in src, (
        "the staged manifest is no longer named cadre.toml")


def test_the_packaging_glob_check_can_actually_fail():
    """RED ARM for test_the_wheel_is_told_to_carry_the_manifest.

    That test globs the package-data patterns against the real directory. This
    one runs the same logic over a package-data list with the template pattern
    removed and requires it to find the file uncovered. Without this, a globbing
    check that silently matched everything would read exactly like a passing one
    — which is the whole family of fault F6 belongs to.
    """
    import fnmatch
    base_ext = base_extension.manifest_source().parent
    on_disk = sorted(p.name for p in base_ext.iterdir() if p.is_file())
    crippled = ["migrations/*.sql", "base_ext/*.toml"]      # the pre-F6 line
    patterns = [p.split("/", 1)[1] for p in crippled if p.startswith("base_ext/")]
    unmatched = [n for n in on_disk
                 if not any(fnmatch.fnmatch(n, pat) for pat in patterns)]
    assert base_extension.MANIFEST_TEMPLATE_NAME in unmatched, (
        "the old `base_ext/*.toml` pattern appears to match "
        f"{base_extension.MANIFEST_TEMPLATE_NAME}, so the packaging guard above "
        "cannot fail and proves nothing")


# ---------------------------------------------------------------------------
# install — validate first, read back after
# ---------------------------------------------------------------------------

class _Run:
    """Records the base calls and answers them in order."""

    def __init__(self, *codes: int) -> None:
        self.codes = list(codes)
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        code = self.codes.pop(0) if self.codes else 0

        class R:
            returncode = code
            stdout = "boom" if code else "ok"
            stderr = ""
        return R()

    @property
    def verbs(self) -> list[str]:
        return [c[2] for c in self.calls if len(c) > 2]


@pytest.fixture
def fake_base(monkeypatch, tmp_path):
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: "/fake/base")
    monkeypatch.setenv("BASE_HOME", str(tmp_path))
    (tmp_path / ".base-gbl" / "extensions").mkdir(parents=True)
    return tmp_path


def _land(home: Path, text: str = 'name = "cadre"\nframework_dir = "/opt/cadre"\n') -> None:
    (home / ".base-gbl" / "extensions" / "cadre.toml").write_text(text, encoding="utf-8")


def test_install_validates_before_installing(monkeypatch, fake_base):
    run = _Run(0, 0)
    monkeypatch.setattr(subprocess, "run", run)
    _land(fake_base)
    res = base_extension.install("/opt/cadre")
    # Three verbs now, and the third is the point. `validate` then `install`
    # proved the file was acceptable and landed; neither of them proves the
    # command RUNS, which is how F1 shipped. `base cadre --help` is the arm
    # that fails exactly when a Member would fail.
    assert run.verbs == ["validate", "install", "--help"], (
        "validate must come first, install second, and the handler must be "
        f"exercised third — got {run.verbs}")
    assert res["ok"] is True
    assert res["read_back"] is True


def test_a_manifest_that_fails_validation_is_never_installed(monkeypatch, fake_base):
    run = _Run(1)
    monkeypatch.setattr(subprocess, "run", run)
    res = base_extension.install("/opt/cadre")
    assert run.verbs == ["validate"], "install ran anyway after a failed validate"
    assert res["ok"] is False
    assert res["installed"] is False
    assert "did not validate" in res["reason"]


def test_install_does_not_claim_success_it_did_not_read_back(monkeypatch, fake_base):
    """base reporting 0 is the writer's opinion of its own work. If the file is
    not on disk afterwards, the install did not happen."""
    run = _Run(0, 0)
    monkeypatch.setattr(subprocess, "run", run)
    # nothing landed
    res = base_extension.install("/opt/cadre")
    assert res["installed"] is True, "base did report success"
    assert res["read_back"] is False
    assert res["ok"] is False, "but nothing was read back, so it is not ok"
    assert "does not exist" in res["reason"]


def test_install_refuses_a_landed_file_that_still_has_the_placeholder(monkeypatch, fake_base):
    run = _Run(0, 0)
    monkeypatch.setattr(subprocess, "run", run)
    _land(fake_base, 'name = "cadre"\nframework_dir = "{{framework_dir}}"\n')
    res = base_extension.install("/opt/cadre")
    assert res["ok"] is False
    assert "never got filled in" in res["reason"]


def test_install_refuses_a_landed_file_that_is_not_ours(monkeypatch, fake_base):
    run = _Run(0, 0)
    monkeypatch.setattr(subprocess, "run", run)
    _land(fake_base, 'name = "somethingelse"\n')
    res = base_extension.install("/opt/cadre")
    assert res["ok"] is False
    assert "not Cadre" in res["reason"]


def test_base_absent_is_skipped_not_failed(monkeypatch):
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    res = base_extension.install("/opt/cadre")
    assert res["ok"] is False
    assert "skipped, not failed" in res["reason"]


def test_install_passes_base_home_through(monkeypatch, fake_base):
    seen: dict = {}

    def _run(cmd, **kwargs):
        seen.update(kwargs.get("env") or {})

        class R:
            returncode = 0
            stdout = stderr = ""
        return R()

    monkeypatch.setattr(subprocess, "run", _run)
    _land(fake_base)
    base_extension.install("/opt/cadre")
    assert seen.get("BASE_HOME") == str(fake_base)


# ---------------------------------------------------------------------------
# `cadre extension install` — the step that had no command behind it
# ---------------------------------------------------------------------------

def test_the_install_command_reports_the_path_it_read_back(fake_base, capsys,
                                                           monkeypatch):
    """Success prints what `install` proved, not what it attempted."""
    monkeypatch.setattr(subprocess, "run", _Run(0, 0))
    _land(fake_base)
    code = base_extension.run_install("/opt/cadre")
    out = capsys.readouterr().out
    assert code == 0
    assert "read back" in out, f"the command claims success it did not read back: {out!r}"


def test_base_absent_is_not_a_failure_of_this_firm(capsys, monkeypatch):
    """rc 0, on purpose, and this is the arm worth arguing about.

    A licensee may not carry base at all. Exiting non-zero there turns a host
    setup fact into a Cadre defect — the same shape as every instrument
    failure this repo has spent the week removing, where a check that could
    not run reported that the thing it checks is broken.
    """
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)
    code = base_extension.run_install()
    captured = capsys.readouterr()
    assert code == 0, "a machine without base was reported as a firm defect"
    assert "skipped" in captured.out
    assert captured.err == "", "an expected, benign outcome wrote to stderr"


def test_base_refusing_the_install_is_a_failure(fake_base, capsys, monkeypatch):
    """The other direction, and the reason the arm above is not just leniency.

    Without this, `run_install` could return 0 unconditionally and both the
    success test and the base-absent test would still pass. base being present
    and saying no is a real failure and has to be loud.
    """
    monkeypatch.setattr(subprocess, "run", _Run(1))
    code = base_extension.run_install("/opt/cadre")
    captured = capsys.readouterr()
    assert code == 1, "base refused the install and the command reported success"
    assert "Error" in captured.err


def test_the_install_command_is_reachable_from_the_cli():
    """A function nobody can call from a shell is not a command.

    The runbook step this replaces was a `python -c`, so the whole point is
    that `cadre extension install` resolves through argparse. Checked by
    running the parser rather than by reading __main__.py.
    """
    done = subprocess.run(
        [sys.executable, "-m", "firm", "extension", "install", "--help"],
        capture_output=True, text=True, timeout=60,
        cwd=str(Path(__file__).resolve().parents[2]))
    assert done.returncode == 0, done.stderr
    assert "--framework-dir" in done.stdout


def test_that_cli_reachability_check_can_fail():
    """Must-fail canary for the check above — a subprocess check that cannot
    fail proves nothing about the one that can."""
    done = subprocess.run(
        [sys.executable, "-m", "firm", "extension", "notaverb", "--help"],
        capture_output=True, text=True, timeout=60,
        cwd=str(Path(__file__).resolve().parents[2]))
    assert done.returncode != 0, (
        "the parser accepted an extension subcommand that does not exist")

# ---------------------------------------------------------------------------
# F1: installed, validated, read back — and dead
# ---------------------------------------------------------------------------

def test_the_handler_points_at_a_console_script_that_exists(tmp_path, monkeypatch):
    """`console_script()` must name a real file, in both shapes of install.

    Source checkout: `<root>/bin/cadre`, which is what a developer runs.
    Installed wheel: the console script beside the interpreter — `bin/cadre`
    on POSIX, `Scripts\\cadre.exe` on Windows. `sys.executable` is the honest
    anchor there, because a venv puts its scripts beside its python and
    site-packages has no `bin/` at all.
    """
    # Real checkout: the repo's own bin/cadre.
    resolved = base_extension.console_script()
    assert resolved.exists(), f"{resolved} does not exist in this checkout"

    # Installed shape: no bin/cadre beside the package, so fall to the venv.
    fake_site = tmp_path / "site-packages"
    (fake_site / "firm").mkdir(parents=True)
    monkeypatch.setattr(base_extension, "framework_root", lambda: fake_site)
    fake_venv = tmp_path / "venv"
    scripts = fake_venv / ("Scripts" if os.name == "nt" else "bin")
    scripts.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(scripts / "python"))
    installed = base_extension.console_script()
    assert installed.parent == scripts, (
        f"an installed package resolved its handler to {installed}, which is "
        "not beside the interpreter — this is F1's shape")
    assert "site-packages" not in str(installed), (
        "the handler resolved under site-packages again; site-packages/bin/cadre "
        "exists on neither platform")


def test_render_leaves_no_handler_placeholder():
    out = base_extension.render("/opt/cadre")
    assert base_extension.HANDLER_PLACEHOLDER not in out
    line = [l for l in out.splitlines() if l.startswith("handler")][0]
    assert "bin/cadre" not in line or Path(line.split('"')[1]).is_absolute(), (
        "the rendered handler is relative; base resolves it against "
        "framework_dir and that is what killed it from a wheel")


def test_install_refuses_when_the_handler_is_missing(fake_base, monkeypatch, tmp_path):
    """The check F1 got past, stated as a test.

    Everything about the TOML can be perfect — landed, ours, placeholders
    filled — while the command it points at does not exist. `install` used to
    return ok on exactly that state, and its docstring promised it never
    claims an install it did not read back. It was reading back the wrong noun.
    """
    monkeypatch.setattr(subprocess, "run", _Run(0, 0, 0))
    _land(fake_base)
    monkeypatch.setattr(base_extension, "console_script",
                        lambda: tmp_path / "nowhere" / "cadre")
    res = base_extension.install("/opt/cadre")
    assert res["ok"] is False
    assert res["read_back"] is True, "the TOML really did land; that was never the issue"
    assert res["handler_runs"] is False
    assert "does not exist" in res["reason"]


def test_install_refuses_when_the_handler_cannot_run(fake_base, monkeypatch):
    """The other half: the handler exists and `base cadre` still fails."""
    monkeypatch.setattr(subprocess, "run", _Run(0, 0, 1))
    _land(fake_base)
    res = base_extension.install("/opt/cadre")
    assert res["ok"] is False
    assert res["read_back"] is True
    assert res["handler_runs"] is False
    assert "does not run" in res["reason"]


def test_a_healthy_install_reports_the_handler_it_proved(fake_base, monkeypatch):
    """The positive control on the two above — without it they would both pass
    over an `install` that refuses everything."""
    monkeypatch.setattr(subprocess, "run", _Run(0, 0, 0))
    _land(fake_base)
    res = base_extension.install("/opt/cadre")
    assert res["ok"] is True
    assert res["handler_runs"] is True
    assert res["handler"], "a healthy install did not say which handler it ran"
