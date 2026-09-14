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
from types import SimpleNamespace

import pytest

from firm.services import base_extension
from firm.services import firm_relay, graph_isolation

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

    #: What `base rule list --domain <d>` answers. Issue #115 put a second
    #: base call inside install(), and this one is READ rather than merely
    #: counted, so the stub has to answer it in base's real shape. It does NOT
    #: consume a code: every arm below sets its codes for validate / install /
    #: `base cadre --help`, and a rule listing quietly eating one would shift
    #: all of them and change what each arm is testing without saying so.
    listing = "No rules for domain 'ext:cadre:cadre-firm' in either tier."

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        #: The kwargs of the LAST call, so an arm can assert how the child was
        #: decoded rather than only which verb it ran. Issue #114 is invisible
        #: at the argv level and lives entirely in these.
        self.kwargs = dict(kwargs)
        if list(cmd)[1:3] == ["rule", "list"]:
            class L:
                returncode = 0
                stdout = self.listing
                stderr = ""
            return L()
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
    # A REAL file carrying this host's magic bytes.
    #
    # This used to be the string "/fake/base", which is not a file at all. Every
    # control arm using this fixture therefore proved install() works against
    # something that could never have executed on any host. Since issue #75
    # install() identifies its binary before running it, and an unidentified
    # binary is refused rather than admitted -- so the stand-in has to be
    # identifiable, and the arms below got stronger for it.
    from firm.sysconfig.binaries import native_image_format

    _MAGIC = {"pe": b"MZ\x90\x00", "macho": b"\xcf\xfa\xed\xfe"}
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "base"
    stub.write_bytes(_MAGIC.get(native_image_format(), b"\x7fELF") + b"\x00" * 128)
    stub.chmod(0o755)
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: str(stub))
    monkeypatch.setenv("BASE_HOME", str(tmp_path))
    (tmp_path / ".base-gbl" / "extensions").mkdir(parents=True)
    return tmp_path


def _land(home: Path,
          text: str = '[extension]\nname = "cadre"\nframework_dir = "/opt/cadre"\n') -> None:
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
    assert run.verbs[:3] == ["validate", "install", "--help"], (
        "validate must come first, install second, and the handler must be "
        f"exercised third — got {run.verbs}")
    # Issue #115 added a fourth call, `base rule list`, and its POSITION is an
    # assertion of its own: reading what the graph will serve is only
    # meaningful once the manifest has landed and its handler has been proved
    # to run. A graph read before `--help` would be reporting on an install
    # that might not exist. Slicing the list above without pinning the tail
    # would have quietly dropped that.
    assert run.verbs[3:] == ["list"], (
        f"unexpected base calls after the handler proof: {run.verbs[3:]}")
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
    _land(fake_base, '[extension]\nname = "cadre"\nframework_dir = "{{framework_dir}}"\n')
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
    # #117: a firm's manifest belongs in the firm's own tier, and this is the
    # flag that names the firm from a shell.
    assert "--workspace" in done.stdout


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


# ---------------------------------------------------------------------------
# The graph copy — issue #115
#
# base matches this manifest's keywords but SERVES the rule text from its
# graph when the graph holds a copy of the domain, and the copy wins outright.
# Measured on base 0.15.2, 2026-09-14, on the maintainer's machine: a keyword
# that exists ONLY in this manifest (`unit complete`) fired the domain, and
# what arrived were three rules from a 2026-08 manifest naming one firm's WSL
# paths. None of this file's rules reached the session.
#
# base owns the repair and offers no verb for it — `rule remove --index N`
# matches an `index` triple domain-sync rules do not carry, `domain sync`
# appends rather than replaces on a populated graph, `graph supersede` does
# not index rules, `graph apply-ops` retires only facts with a sync id. So
# these tests do not pin a cleanup. They pin that it cannot happen QUIETLY.
# ---------------------------------------------------------------------------

# Copied out of a real run, not typed from memory. Law 32: a literal going into
# an assertion comes from the source that PRINTS it.
REAL_LISTING = """[ext:cadre:cadre-firm] 3 rules across both tiers:
  workspace 0. Cadre firm active. chrisai firm root: /home/chriskahler/firms/chrisai (WSL).
  workspace 1. CLI: /home/chriskahler/firms/chrisai/.venv/bin/firm {init|pulse|unit|run}.
  workspace 2. Board rules bind every session: never approve/reject Gates.
  (global: none)

Indices are per tier; `rule remove` takes the index shown beside its own tier."""

CLEAN_LISTING = "No rules for domain 'ext:cadre:cadre-firm' in either tier."


def _manifest_rules() -> list[str]:
    """This manifest's own rules for its one domain, read from the file."""
    rendered = base_extension.render("/opt/cadre")
    domains = base_extension.manifest_domains(rendered)
    assert len(domains) == 1, f"expected one domain, got {domains}"
    return domains[0][1]


def _listing_of(rules: list[str]) -> str:
    body = "\n".join(f"  workspace {i}. {r}" for i, r in enumerate(rules))
    return (f"[ext:cadre:cadre-firm] {len(rules)} rules across both tiers:\n"
            f"{body}\n  (global: none)\n\nIndices are per tier;")


def test_the_listing_parser_reads_a_real_listing():
    rules = base_extension.parse_rule_listing(REAL_LISTING)
    assert len(rules) == 3
    assert rules[0].startswith("Cadre firm active. chrisai firm root:")
    assert "(global: none)" not in rules


def test_the_listing_parser_reads_the_no_rules_sentence():
    assert base_extension.parse_rule_listing(CLEAN_LISTING) == []


def test_the_listing_parser_refuses_what_it_cannot_read():
    """A zero that means 'I could not see' reads exactly like a zero that means
    'nothing is there', and only one of those is good news (law 48)."""
    with pytest.raises(base_extension.GraphReadFailed):
        base_extension.parse_rule_listing("ok")


def test_the_listing_parser_refuses_a_count_that_does_not_reconcile():
    """The listing states its own total, so the rows are checked against it.
    A parser that silently returns fewer rules than base reported is a blind
    detector that reads CLEAN — the worst direction to fail in."""
    short = REAL_LISTING.replace(
        "  workspace 2. Board rules bind every session: never approve/reject Gates.\n", "")
    with pytest.raises(base_extension.GraphReadFailed) as exc:
        base_extension.parse_rule_listing(short)
    assert "3" in str(exc.value) and "2" in str(exc.value)


def test_the_domain_name_is_namespaced_the_way_base_names_it():
    """`ext:<extension>:<domain>`. The name is DERIVED, which is why two
    generations cannot rename their way out of a collision."""
    names = [n for n, _ in base_extension.manifest_domains(
        base_extension.render("/opt/cadre"))]
    assert names == ["ext:cadre:cadre-firm"]


def test_a_clean_graph_leaves_the_collision_field_unset(fake_base, monkeypatch):
    run = _Run(0, 0, 0)
    run.listing = CLEAN_LISTING
    monkeypatch.setattr(subprocess, "run", run)
    _land(fake_base)
    res = base_extension.install("/opt/cadre")
    assert res["ok"] is True
    assert res["graph_read"] is True
    assert res["collision"] is False
    assert res["foreign_rules"] == []


def test_a_graph_copy_that_matches_this_manifest_is_not_a_collision(
        fake_base, monkeypatch):
    """The control that keeps the arm below honest. A graph copy is not the
    defect — a graph copy carrying SOMEONE ELSE'S rules is. Without this arm,
    an implementation that flagged the mere presence of a copy would pass."""
    run = _Run(0, 0, 0)
    run.listing = _listing_of(_manifest_rules())
    monkeypatch.setattr(subprocess, "run", run)
    _land(fake_base)
    res = base_extension.install("/opt/cadre")
    assert res["collision"] is False, res["foreign_rules"]


def test_a_foreign_rule_in_the_graph_sets_the_collision_field(
        fake_base, monkeypatch):
    run = _Run(0, 0, 0)
    run.listing = REAL_LISTING
    monkeypatch.setattr(subprocess, "run", run)
    _land(fake_base)
    res = base_extension.install("/opt/cadre")
    assert res["collision"] is True
    assert len(res["foreign_rules"]) == 1
    finding = res["foreign_rules"][0]
    assert finding["domain"] == "ext:cadre:cadre-firm"
    assert len(finding["foreign"]) == 3
    assert any("chrisai firm root" in r for r in finding["foreign"])
    assert res["cwd"], "a rule count with no directory beside it is not a measurement"


def test_one_changed_character_is_still_foreign(fake_base, monkeypatch):
    """The detector's resolution. Every rule but one is this manifest's own,
    and the one that differs by a single character is reported. A comparison
    that matched loosely — by count, by prefix, by domain name — would pass
    every other arm here and miss a rewritten rule, which is the exact shape
    the collision takes."""
    mine = _manifest_rules()
    tampered = list(mine)
    tampered[0] = tampered[0].replace("never opened directly.",
                                      "never opened directlY.")
    run = _Run(0, 0, 0)
    run.listing = _listing_of(tampered)
    monkeypatch.setattr(subprocess, "run", run)
    _land(fake_base)
    res = base_extension.install("/opt/cadre")
    assert res["collision"] is True
    assert res["foreign_rules"][0]["foreign"] == [tampered[0]]


def test_the_install_still_succeeds_over_a_collision(fake_base, monkeypatch,
                                                     capsys):
    """Land and shout, never refuse. The manifest DID install and its handler
    DOES run; what shadows it is a base defect the operator has no verb to fix.
    A refusal that can never pass is not a safety check, it is a brick — and it
    would take founding down with it on the one machine that has the problem."""
    run = _Run(0, 0, 0)
    run.listing = REAL_LISTING
    monkeypatch.setattr(subprocess, "run", run)
    _land(fake_base)
    code = base_extension.run_install("/opt/cadre")
    assert code == 0
    assert "installed" in capsys.readouterr().out


def test_the_collision_report_names_base_as_the_owner(fake_base, monkeypatch,
                                                      capsys):
    """The report has to survive being read by someone who did not measure
    this. Without the owner line the next reader spends a day inside Cadre
    looking for a bug that is not here."""
    run = _Run(0, 0, 0)
    run.listing = REAL_LISTING
    monkeypatch.setattr(subprocess, "run", run)
    _land(fake_base)
    base_extension.run_install("/opt/cadre")
    err = capsys.readouterr().err
    assert "COLLISION on domain ext:cadre:cadre-firm" in err
    assert "OWNER: base" in err
    assert "chrisai firm root" in err, "the report did not print the actual rule"
    assert "base rule list --domain ext:cadre:cadre-firm" in err
    assert "workspace tier resolved from:" in err


def test_a_clean_install_prints_no_collision_block(fake_base, monkeypatch,
                                                   capsys):
    """The must-stay-quiet control. Without it, an implementation that printed
    the block unconditionally would pass the arm above."""
    run = _Run(0, 0, 0)
    run.listing = CLEAN_LISTING
    monkeypatch.setattr(subprocess, "run", run)
    _land(fake_base)
    base_extension.run_install("/opt/cadre")
    assert "COLLISION" not in capsys.readouterr().err


def test_an_unreadable_graph_is_reported_as_unknown_never_as_clean(
        fake_base, monkeypatch, capsys):
    run = _Run(0, 0, 0)
    run.listing = "some future version of base says something else entirely"
    monkeypatch.setattr(subprocess, "run", run)
    _land(fake_base)
    res = base_extension.install("/opt/cadre")
    assert res["graph_read"] is False
    assert res["collision"] is False
    assert "UNKNOWN" in res["reason"]
    code = base_extension.run_install("/opt/cadre")
    assert code == 0
    assert "could not be read" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Reading the graph is a #114 surface too
#
# I wrote this call with `subprocess.run(..., text=True)` and shipped it in my
# own first commit, one file away from the fix for exactly that. `text=True`
# decodes with the host locale — cp1252 on the operator's Windows install — and
# the rule text being read here is full of what that mangles: the shipped rules
# carry backticks and an em dash, the stale ones carry `§`.
#
# The crash half is the visible one. The half that would have gone unnoticed is
# worse: every graph rule decoded differently from the manifest rule it is a
# copy of, so `r not in mine` is true for all of them and a perfectly clean
# machine reports a collision on every domain. A false-positive generator, from
# a decode nobody would think to look at.
# ---------------------------------------------------------------------------

def test_the_graph_read_is_decoded_as_utf8_never_by_the_host_locale(
        fake_base, monkeypatch):
    """Asserted on the kwargs the child was actually launched with. The argv is
    identical either way, so an arm that only checked the verb would pass over
    a locale-decoded read."""
    run = _Run(0, 0, 0)
    run.listing = CLEAN_LISTING
    monkeypatch.setattr(subprocess, "run", run)
    _land(fake_base)
    base_extension.install("/opt/cadre")
    assert run.kwargs.get("encoding") == "utf-8", (
        "the rule listing is decoded with the host locale, which is #114 "
        f"reopened in the reader: {run.kwargs!r}")
    assert "text" not in run.kwargs, (
        "text=True beside an explicit encoding means a future edit can drop "
        "the encoding and look unchanged")


def test_a_rule_carrying_non_ascii_does_not_read_as_foreign(fake_base,
                                                            monkeypatch):
    """The false-positive arm, and the reason the one above matters.

    A graph copy that IS this manifest, carrying the characters cp1252 would
    have mangled, must compare equal. Under the decode bug every one of these
    rules read as a rule the manifest did not write.
    """
    mine = _manifest_rules()
    assert any("`" in r for r in mine), (
        "precondition: the shipped rules no longer carry a character the host "
        "locale would mangle, so this arm has nothing to discriminate")
    run = _Run(0, 0, 0)
    run.listing = _listing_of(mine)
    monkeypatch.setattr(subprocess, "run", run)
    _land(fake_base)
    res = base_extension.install("/opt/cadre")
    assert res["collision"] is False, (
        f"a faithful graph copy read as foreign: {res['foreign_rules']}")


def test_an_empty_listing_is_a_failed_read_never_a_clean_one(fake_base,
                                                             monkeypatch):
    """base always prints either a listing or the "No rules" sentence, so an
    empty stdout is a failed read. Parsed rather than refused it would come
    back as [] — no rules, nothing wrong, machine clean — which is the exact
    shape of a zero that means "I could not see"."""
    run = _Run(0, 0, 0)
    run.listing = ""
    monkeypatch.setattr(subprocess, "run", run)
    _land(fake_base)
    res = base_extension.install("/opt/cadre")
    assert res["graph_read"] is False, (
        "an empty rule listing was read as a clean graph")
    assert res["collision"] is False
    assert "UNKNOWN" in res["reason"]


# ---------------------------------------------------------------------------
# #117, spec item 5: the manifest goes into the FIRM's own tier
#
# A firm gets its own base tier at `<firm>/.firm/base-home`, and a Member
# spawned in the firm runs `base cadre` with that tier as its BASE_HOME
# (`firm/pulse/spawn.py`). A manifest installed anywhere else is a command no
# Member can run.
#
# These arms were pre-registered before the code existed, each on its own
# channel and each with the mutation that must redden it. None of them uses
# `_land()`. That helper puts the manifest in place BEFORE the call, so an arm
# built on it passes whichever tier the install aimed at: it cannot see a wrong
# destination. `_TierBase` writes where the call's BASE_HOME points, as base
# does, so the destination is a consequence of the env the install really
# handed to base.
#
# Every arm controls three things: BASE_HOME (a stand-in operator tier seeded
# with another extension and an OLDER Cadre manifest), the firm, and the
# working directory. The older operator manifest is what makes the silent
# failure reachable: a read-back aimed at the operator's tier, over an install
# that went to the firm's, would find that file and call it proof.
# ---------------------------------------------------------------------------

_OPERATOR_OLDER_MANIFEST = (
    '[extension]\nname = "cadre"\nversion = "0.0.1-operator"\n'
    'framework_dir = "/somewhere/else"\n')
_ANOTHER_EXTENSION = '[extension]\nname = "lore"\nversion = "9.9.9"\n'
#: Present in this install's render and in no seeded file.
_RENDERED_MARK = 'framework_dir = "/opt/cadre"'


class _Done:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _TierBase:
    """A stub base that writes where the call's BASE_HOME points, as base does.

    Records argv, env and cwd for every call. It never falls back to HOME: a
    call carrying no BASE_HOME writes nothing and fails, because on a
    developer's machine HOME is the real operator tier.
    """

    listing = CLEAN_LISTING

    def __init__(self, landed_text=None) -> None:
        self.calls: list[dict] = []
        #: Optional change to the staged manifest before it lands, standing in
        #: for a base that wrote something other than what it was given.
        self.landed_text = landed_text

    def __call__(self, cmd, **kwargs):
        argv = [str(a) for a in cmd]
        env = dict(kwargs.get("env") or {})
        self.calls.append({"argv": argv, "env": env, "cwd": kwargs.get("cwd")})
        if argv[1:3] == ["rule", "list"]:
            return _Done(0, self.listing)
        if argv[1:3] == ["extension", "install"]:
            home = env.get("BASE_HOME")
            if not home:
                return _Done(1, "", "stub base: no BASE_HOME in the env, wrote nothing")
            staged = Path(argv[3]).read_text(encoding="utf-8")
            dest = Path(home) / ".base-gbl" / "extensions" / "cadre.toml"
            dest.parent.mkdir(parents=True, exist_ok=True)
            text = self.landed_text(staged) if self.landed_text else staged
            dest.write_text(text, encoding="utf-8")
            return _Done(0, "Installed cadre")
        return _Done(0, "ok")


def _stub_binary(path: Path, native: bool = True) -> str:
    """A file carrying this host's magic bytes, or another platform's."""
    from firm.sysconfig.binaries import native_image_format

    magics = {"pe": b"MZ\x90\x00", "macho": b"\xcf\xfa\xed\xfe", "elf": b"\x7fELF"}
    host = native_image_format()
    fmt = host if native else ("elf" if host == "pe" else "pe")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(magics[fmt] + b"\x00" * 128)
    path.chmod(0o755)
    return str(path)


def _make_firm(root: Path) -> Path:
    (root / ".firm").mkdir(parents=True)
    (root / ".firm" / "firm.db").write_bytes(b"")
    return root


def _snapshot(directory: Path) -> dict[str, bytes]:
    """Every file's name and bytes. The caller checks how many it visited."""
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()}


@pytest.fixture
def two_tiers(monkeypatch, tmp_path):
    operator = tmp_path / "operator"
    operator_ext = operator / ".base-gbl" / "extensions"
    operator_ext.mkdir(parents=True)
    (operator_ext / "lore.toml").write_text(_ANOTHER_EXTENSION, encoding="utf-8")
    (operator_ext / "cadre.toml").write_text(_OPERATOR_OLDER_MANIFEST, encoding="utf-8")
    monkeypatch.setenv("BASE_HOME", str(operator))

    firm = _make_firm(tmp_path / "firm")
    (firm / "sub").mkdir()

    standing = tmp_path / "standing"
    standing.mkdir()
    monkeypatch.chdir(standing)
    assert firm_relay.resolve_firm(standing) is None, (
        "precondition: a directory above the standing directory holds a firm, "
        "so no arm here can tell 'no firm' from 'a firm'")

    stub = _stub_binary(tmp_path / "bin" / "base")
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: stub)
    return SimpleNamespace(tmp=tmp_path, operator=operator, operator_ext=operator_ext,
                           firm=firm, standing=standing)


def test_a1_destination_the_manifest_lands_in_the_firms_tier_and_nowhere_else(
        two_tiers, monkeypatch):
    """A1, the filesystem, both tiers. Reads no field of the result.

    Must catch the delegating `_base_env` dropping the workspace (M2), which
    sends the install to the operator's tier.
    """
    before = _snapshot(two_tiers.operator_ext)
    print(f"A1: the operator snapshot visited {len(before)} file(s)")
    assert len(before) == 2, (
        f"the operator snapshot visited {len(before)} file(s), not the 2 seeded, "
        "so a byte-identity check over it proves NOTHING")
    monkeypatch.setattr(subprocess, "run", _TierBase())

    base_extension.install("/opt/cadre", workspace=two_tiers.firm)

    firm_ext = graph_isolation.tier_extensions_dir(two_tiers.firm)
    tomls = sorted(p.name for p in firm_ext.glob("*.toml")) if firm_ext.is_dir() else []
    print(f"A1: the firm's tier holds {tomls}")
    assert tomls == ["cadre.toml"], (
        f"the firm's tier holds {tomls}; it must hold exactly this manifest")
    assert _RENDERED_MARK in (firm_ext / "cadre.toml").read_text(encoding="utf-8"), (
        "the firm's cadre.toml is not this install's render")
    after = _snapshot(two_tiers.operator_ext)
    changed = sorted(n for n in set(before) | set(after) if before.get(n) != after.get(n))
    assert changed == [], f"the install changed the operator's tier: {changed}"


def test_a2_honest_report_the_path_reported_is_the_file_really_read(
        two_tiers, monkeypatch):
    """A2, the returned dict checked against the disk.

    Leg (a) is a healthy install. In leg (b) base lands Cadre's manifest with
    its placeholder still in it, which must read back False: that leg catches a
    read-back swapped for an existence check (M6, the spec's "must not do").
    Both legs catch a read-back aimed at the operator's tier (M3), where the
    older operator manifest would be read as proof.
    """
    want = graph_isolation.tier_extensions_dir(two_tiers.firm) / "cadre.toml"

    monkeypatch.setattr(subprocess, "run", _TierBase())
    healthy = base_extension.install("/opt/cadre", workspace=two_tiers.firm)
    assert healthy["read_back"] is True, healthy["reason"]
    assert Path(healthy["path"]) == want, (
        f"the install reported {healthy['path']}, but the firm's manifest is {want}")
    assert _RENDERED_MARK in Path(healthy["path"]).read_text(encoding="utf-8"), (
        "read_back is True over a file that is not this install's render")

    unrendered = _TierBase(landed_text=lambda staged: staged.replace(
        _RENDERED_MARK, f'framework_dir = "{base_extension.PLACEHOLDER}"'))
    monkeypatch.setattr(subprocess, "run", unrendered)
    corrupt = base_extension.install("/opt/cadre", workspace=two_tiers.firm)
    assert base_extension.PLACEHOLDER in want.read_text(encoding="utf-8"), (
        "precondition: the stub did not land the unrendered manifest in the "
        "firm's tier, so leg (b) tests nothing")
    assert corrupt["read_back"] is False, (
        "read_back is True over a landed manifest that still carries its placeholder")
    assert corrupt["ok"] is False
    assert Path(corrupt["path"]) == want

    # Leg (c): base lands ANOTHER extension's manifest that still declares a
    # `cadre` command. Cadre's manifest carries `name = "cadre"` twice, under
    # [extension] and under [[commands]], so a substring read-back cannot tell
    # this file from Cadre's. Must catch the read-back going back to a
    # substring match (M12).
    renamed = _TierBase(landed_text=lambda staged: staged.replace(
        '[extension]\nname = "cadre"', '[extension]\nname = "somethingelse"', 1))
    monkeypatch.setattr(subprocess, "run", renamed)
    other = base_extension.install("/opt/cadre", workspace=two_tiers.firm)
    landed = want.read_text(encoding="utf-8")
    assert tomllib.loads(landed)["extension"]["name"] == "somethingelse", (
        "precondition: the stub did not land a manifest of another extension, "
        "so leg (c) tests nothing")
    assert 'name = "cadre"' in landed, (
        "precondition: the landed file no longer carries the [[commands]] name, "
        "so leg (c) cannot reach the shape a substring check misreads")
    assert other["read_back"] is False, (
        "read_back is True over a manifest whose [extension] is not cadre, "
        "because its [[commands]] block says name = \"cadre\"")
    assert other["ok"] is False
    assert Path(other["path"]) == want


def test_a3_no_workspace_control_installs_where_it_always_did(two_tiers, monkeypatch):
    """A3, the filesystem, operator tier. The blindness control: green under
    every mutation in the matrix, and the arm that keeps a workspace OPTIONAL."""
    lore = two_tiers.operator_ext / "lore.toml"
    lore_before = lore.read_bytes()
    monkeypatch.setattr(subprocess, "run", _TierBase())

    base_extension.install("/opt/cadre")

    landed = (two_tiers.operator_ext / "cadre.toml").read_text(encoding="utf-8")
    assert _RENDERED_MARK in landed and "0.0.1-operator" not in landed, (
        "an install with no workspace did not replace the operator tier's manifest")
    assert lore.read_bytes() == lore_before
    assert not graph_isolation.firm_base_home(two_tiers.firm).exists(), (
        "an install with no workspace created a firm tier")


def test_a4_prevention_a_refused_firm_install_runs_no_subprocess(
        two_tiers, monkeypatch, tmp_path):
    """A4, a spy counting subprocess calls. The refusal is decided on the
    binary's format, so the proof is that nothing ran.

    The control comes first: the same spy, a native base and the same
    workspace record calls, so the zero below is a reading and not blindness.
    """
    control = _TierBase()
    monkeypatch.setattr(subprocess, "run", control)
    base_extension.install("/opt/cadre", workspace=two_tiers.firm)
    print(f"A4 control: the spy recorded {len(control.calls)} call(s) on a native base")
    assert len(control.calls) >= 1, (
        "the spy recorded nothing on a native base, so a zero below proves NOTHING")

    foreign = _stub_binary(tmp_path / "foreign" / "base", native=False)
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: foreign)
    spy = _TierBase()
    monkeypatch.setattr(subprocess, "run", spy)
    res = base_extension.install("/opt/cadre", workspace=two_tiers.firm)
    print(f"A4: the spy recorded {len(spy.calls)} call(s) on a foreign base")
    assert spy.calls == [], f"a refused install ran {[c['argv'][1:3] for c in spy.calls]}"
    assert res["ok"] is False


def test_a5_handler_tier_every_base_call_carries_the_firms_base_home(
        two_tiers, monkeypatch):
    """A5, a spy on the env of each call. Four calls today: validate, install,
    `base cadre --help`, and one `rule list` per prompt domain. The count is
    derived from the manifest rather than typed, so a change that drops a call
    fails here instead of passing on fewer calls."""
    run = _TierBase()
    monkeypatch.setattr(subprocess, "run", run)
    base_extension.install("/opt/cadre", workspace=two_tiers.firm)

    want = str(graph_isolation.firm_base_home(two_tiers.firm))
    domains = base_extension.manifest_domains(base_extension.render("/opt/cadre"))
    expected = 3 + len(domains)
    seen = [(" ".join(c["argv"][1:3]), c["env"].get("BASE_HOME")) for c in run.calls]
    print(f"A5: visited {len(seen)} base call(s), expected {expected}")
    for verb, home in seen:
        print(f"  {verb:<20} BASE_HOME={home}")
    assert len(seen) == expected, f"visited {len(seen)} base calls, expected {expected}: {seen}"
    wrong = [verb for verb, home in seen if home != want]
    assert wrong == [], f"these base calls ran outside the firm's tier {want}: {wrong}"

    helps = [c for c in run.calls if c["argv"][1:3] == ["cadre", "--help"]]
    assert len(helps) == 1
    assert Path(helps[0]["cwd"]).resolve() == two_tiers.standing.resolve(), (
        "the handler proof did not run from the directory this leg set, so the "
        "leg is not controlling which workspace tier base resolves")


def test_a6_refusal_report_a_refused_firm_install_names_the_firms_tier(
        two_tiers, monkeypatch, tmp_path):
    """A6, the returned dict on the refusal path.

    The refusal is decided on the binary's format, so it happens whichever
    tier is passed and A4 cannot tell them apart. What has to be right is the
    tier the refusal NAMES. Must catch the refusal being checked against the
    operator's tier (M4).
    """
    foreign = _stub_binary(tmp_path / "foreign" / "base", native=False)
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: foreign)
    monkeypatch.setattr(subprocess, "run", _TierBase())

    res = base_extension.install("/opt/cadre", workspace=two_tiers.firm)

    want = graph_isolation.tier_extensions_dir(two_tiers.firm) / "cadre.toml"
    assert Path(res["path"]) == want, (
        f"the refusal reports {res['path']}, not the firm's tier {want}")
    assert str(want) in res["reason"], (
        f"the refusal does not name the firm's tier: {res['reason']}")
    assert str(two_tiers.operator) not in res["reason"], (
        "the refusal names the operator's tier while refusing an install into a firm")


def _cli_install(*extra: str) -> int:
    from firm.__main__ import main

    return main(["extension", "install", "--framework-dir", "/opt/cadre", *extra])


def _firm_tiers(root: Path) -> list[str]:
    """Every firm tier under root: a `base-home` directory inside a `.firm`.

    Only that shape. conftest points BASE_HOME at `<tmp_path>/base-home` for
    every test, so a search for the bare name could match a path this lane
    never made.
    """
    return sorted(str(p) for p in root.rglob(graph_isolation.TIER_DIRNAME)
                  if p.is_dir() and p.parent.name == ".firm")


def test_c1_standing_in_a_firm_installs_into_that_firms_tier(two_tiers, monkeypatch):
    """C1. No flag, standing in a subdirectory of a firm: the firm's tier.
    Must catch the dispatch passing no default (M8)."""
    monkeypatch.setattr(subprocess, "run", _TierBase())
    monkeypatch.chdir(two_tiers.firm / "sub")
    before = _snapshot(two_tiers.operator_ext)

    code = _cli_install()

    firm_manifest = graph_isolation.tier_extensions_dir(two_tiers.firm) / "cadre.toml"
    assert code == 0
    assert firm_manifest.is_file(), (
        "standing inside a firm with no flag did not install into that firm's tier")
    assert _snapshot(two_tiers.operator_ext) == before, (
        "standing inside a firm with no flag changed the operator's tier")


def test_c2_outside_any_firm_the_cli_keeps_todays_tier(two_tiers, monkeypatch):
    """C2, the control for C1 and C3. Standing outside every firm with no flag
    installs where it always did and creates no firm tier anywhere."""
    monkeypatch.setattr(subprocess, "run", _TierBase())

    code = _cli_install()

    assert code == 0
    landed = (two_tiers.operator_ext / "cadre.toml").read_text(encoding="utf-8")
    assert _RENDERED_MARK in landed, "the operator's tier did not receive this install"
    made = _firm_tiers(two_tiers.tmp)
    assert made == [], f"an install outside any firm created a firm tier: {made}"
    # The finder has to be able to see one, or the empty list above is blindness.
    graph_isolation.ensure_tier(two_tiers.firm)
    assert len(_firm_tiers(two_tiers.tmp)) == 1, (
        "the firm-tier finder did not see a tier that exists, so its empty answer "
        "above proves NOTHING")


def test_c3_an_explicit_workspace_beats_the_firm_you_stand_in(two_tiers, monkeypatch):
    """C3. Standing in one firm and naming another: the named one gets the
    manifest and the one you stand in is left alone. Must catch the dispatch
    ignoring the flag (M9)."""
    other = _make_firm(two_tiers.tmp / "other-firm")
    monkeypatch.setattr(subprocess, "run", _TierBase())
    monkeypatch.chdir(two_tiers.firm / "sub")

    code = _cli_install("--workspace", str(other))

    assert code == 0
    assert (graph_isolation.tier_extensions_dir(other) / "cadre.toml").is_file(), (
        "the firm named by --workspace did not receive the manifest")
    assert not graph_isolation.firm_base_home(two_tiers.firm).exists(), (
        "the firm you stood in was written despite an explicit --workspace")


def test_c4_a_workspace_that_is_not_a_firm_is_refused_before_anything_is_made(
        two_tiers, monkeypatch, capsys):
    """C4. A `--workspace` with no .firm/firm.db exits 2 and creates nothing.

    Naming a workspace creates its tier on disk, so a typo has to be refused
    before that can happen, the way `cadre relay --firm` refuses it. Leg 1 names
    a real directory that is not a firm (the likeliest typo: the firms root
    instead of a firm in it). Leg 2 names a path that does not exist. Must
    catch the refusal being removed (M10).
    """
    run = _TierBase()
    monkeypatch.setattr(subprocess, "run", run)
    before = _snapshot(two_tiers.operator_ext)

    typo = two_tiers.tmp / "typo"
    typo.mkdir()
    (typo / "notes.txt").write_text("not a firm\n", encoding="utf-8")
    code = _cli_install("--workspace", str(typo))
    err = capsys.readouterr().err
    walked = sorted(p.relative_to(typo).as_posix() for p in typo.rglob("*"))
    print(f"C4: the walk of the typo directory visited {len(walked)} entries: {walked}")
    assert walked, "the walk of the typo directory visited nothing, so it proves NOTHING"
    assert code == 2, f"a --workspace that is not a firm exited {code}, not 2"
    assert str(typo) in err and ".firm/firm.db" in err, (
        f"the refusal does not name the path and the missing database: {err!r}")
    assert walked == ["notes.txt"], f"the refused install created {walked} under the typo"

    nowhere = two_tiers.tmp / "nowhere"
    code = _cli_install("--workspace", str(nowhere))
    assert code == 2, f"a --workspace that does not exist exited {code}, not 2"
    assert not nowhere.exists(), "a refused --workspace created the directory it named"

    assert run.calls == [], f"a refused install ran base: {[c['argv'][1:3] for c in run.calls]}"
    assert _snapshot(two_tiers.operator_ext) == before
