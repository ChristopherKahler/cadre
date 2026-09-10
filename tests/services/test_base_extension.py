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

import subprocess
import tomllib
from pathlib import Path

import pytest

from firm.services import base_extension


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
    parsed = _parsed()
    commands = parsed["commands"]
    assert len(commands) == 1
    assert commands[0]["name"] == "cadre"
    assert commands[0]["handler"] == "bin/cadre"


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


def test_the_shipped_manifest_is_inside_the_package():
    src = base_extension.manifest_source()
    assert src.exists(), f"{src} is missing, so pip install cadre ships no manifest"
    assert src.parent.name == "base_ext"
    assert src.parent.parent.name == "firm"


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
    assert run.verbs == ["validate", "install"], "validate must come first, every time"
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
