"""Every place that states the MCP tool count must agree with the registry.

The number lives in four places and only one of them is executable:

    src/firm/mcp/tools.py        the registry itself, the only real answer
    README.md                    three prose statements
    scripts/e2e-test.sh          an assertion, and a message that repeats it

scripts/e2e-test.sh:274 already asserts the count. It is not enough, and its
own comment says why: it read 33 until 2026-09-10 and the number had DRIFTED.
A checked-in assertion drifted, because no workflow runs that script -- see
issue #49. The suite is the only thing CI actually executes, so the pin has to
live here.

This does not replace the e2e assertion, it pins that assertion too. If
somebody moves the registry and updates the shell script but not the README,
or the README but not the script, this fails and names the file that
disagrees.

WHEN THE COUNT LEGITIMATELY CHANGES -- the MCP-to-CLI migration retires tools
in phases 3 to 5, docs/MCP-TO-CLI-MIGRATION.md -- update every source listed
below. That is the point: one of them can no longer be edited in isolation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
E2E = ROOT / "scripts" / "e2e-test.sh"

# Each pattern captures a stated count. Keep the label human: it is what the
# failure message names when a file disagrees.
_PATTERNS = (
    ("README: asserts N tools are registered", README,
     re.compile(r"asserts (\d+) tools are registered")),
    ("README: MCP surface (N tools) heading", README,
     re.compile(r"MCP surface \((\d+) tools\)")),
    ("README: N MCP tools in the service-module line", README,
     re.compile(r"(\d+) MCP tools")),
    ("e2e-test.sh: EXPECTED assignment", E2E,
     re.compile(r"^EXPECTED = (\d+)$", re.MULTILINE)),
    ("e2e-test.sh: the pass message", E2E,
     re.compile(r"MCP surface intact \((\d+) tools\)")),
)


def _registry_count() -> int:
    """The only non-prose answer. Imported the way scripts/e2e-test.sh imports
    it, so this test and that script cannot disagree about what they count."""
    from firm.mcp.tools import mcp
    return len(mcp._tool_manager._tools)


def _stated() -> list[tuple[str, int]]:
    """Every stated count, with the label of the thing that stated it. Missing
    files are skipped rather than raised so that collection still happens and
    the controls below produce the readable failure instead of a traceback."""
    out: list[tuple[str, int]] = []
    for label, path, pattern in _PATTERNS:
        if not path.is_file():
            continue
        for m in pattern.finditer(path.read_text(encoding="utf-8")):
            out.append((label, int(m.group(1))))
    return out


def test_the_files_this_test_reads_are_present():
    """Control. Every assertion below is vacuous if the files are absent: a
    README that is not there states no wrong number."""
    assert README.is_file(), "README.md not found at " + str(README)
    assert E2E.is_file(), "scripts/e2e-test.sh not found at " + str(E2E)


def test_the_patterns_still_match_something():
    """Control, and the one that matters most. If somebody rewords "37 MCP
    tools" the regexes stop matching, the parametrised test below collects an
    EMPTY set, and pytest reports that as a skip rather than a failure. The
    guard would go blind while the board stayed green. So this is louder than
    the thing it guards."""
    found = _stated()
    assert len(found) >= len(_PATTERNS), (
        "expected at least one match per pattern, got "
        + repr(found)
        + ". If the wording changed on purpose, update _PATTERNS in this file. "
        "Do NOT delete a pattern: a guard that matches nothing is worse than "
        "no guard, because it reports success."
    )


@pytest.mark.parametrize("label,stated", _stated())
def test_every_stated_mcp_tool_count_matches_the_registry(label, stated):
    actual = _registry_count()
    assert stated == actual, (
        label + " says " + str(stated) + " MCP tools; the registry has "
        + str(actual) + ". Update every source listed in this file, not only "
        "the one you were editing."
    )


def test_the_guard_would_fire_on_a_wrong_number(tmp_path):
    """Red arm. Prove the pattern-and-comparison chain rejects a wrong count
    instead of trusting that it would. A synthetic README carrying
    registry-plus-one must be matched by the same regexes and must disagree."""
    actual = _registry_count()
    wrong = actual + 1
    fake = tmp_path / "README.md"
    nl = chr(10)
    fake.write_text(
        "## MCP surface (" + str(wrong) + " tools)" + nl
        + "10. Imports the MCP module and asserts " + str(wrong)
        + " tools are registered" + nl
        + "- 10 service modules, " + str(wrong) + " MCP tools, gap detection"
        + nl,
        encoding="utf-8",
    )
    text = fake.read_text(encoding="utf-8")
    seen = [
        int(m.group(1))
        for label, path, pattern in _PATTERNS
        if path == README
        for m in pattern.finditer(text)
    ]
    assert len(seen) >= 3, (
        "the README patterns did not match the synthetic file at all: "
        + repr(seen) + " -- the red arm cannot prove anything it never read."
    )
    assert all(n != actual for n in seen), (
        "a deliberately wrong count compared EQUAL to the registry: " + repr(seen)
    )
