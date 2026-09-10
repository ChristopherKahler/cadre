"""Every verb the assembled Member prompt names must actually resolve.

This is the test the member-write-surface lane exists for. The field lesson it
enforces is ENGINEERING.md:259 — *"Every rule of the form 'always do X' needs a
matching verb, and the check for whether one exists is ``--help``, not the
briefing."* The prompt told every Member to call ``unit_create`` and
``firm_escalate``; neither is a command, and six of twelve firms load no firm
MCP server at all, so in half the estate the instruction named nothing that
existed in any form.

Two detectors, deliberately independent (a mutation must fire exactly one):

* **A — the verb resolves.** ``firm <verb> --help`` returns 0.
* **B — the flag exists on that verb.** Every ``--flag`` the prompt writes on a
  command line appears in that verb's own help text.

Renaming a verb in the prompt fires A alone; misspelling a flag fires B alone.
Two assertions that both fire on the same mutation would be one detector wearing
a hat.

**Why this file is so careful about zero.** Its core operation is a *search* over
prompt text, and a search that finds nothing is the one result that cannot
distinguish *"the prompt names no verbs"* from *"my extractor is blind"*. So the
floor (:data:`MIN_INVOCATIONS`) is enforced as a failure, never a pass, and two
canaries prove the machinery can still say no: a fabricated verb must come back
unresolved, and a prompt whose anchor has been broken must be reported as
blindness rather than quietly returning an empty list.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.pulse.prompt import assemble_prompt

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The execution directive names at least this many runnable ``firm`` verbs.
#: Falling below it is a FAILURE, never a pass: an extractor that returns an
#: empty list has proved nothing, and "proved nothing" must never read green.
MIN_INVOCATIONS = 4

#: A command line, once its bullet and indentation are stripped, starts with
#: exactly this. Prose mentioning "the firm MCP server" never does.
_COMMAND_ANCHOR = "firm "

_BULLET = re.compile(r"^[-*]\s+")
_SUBCOMMAND = re.compile(r"^[a-z][a-z0-9-]*$")
_LONG_FLAG = re.compile(r"^--[a-z][a-z0-9-]+$")

#: A backtick span is the author explicitly marking text as code, so it is a
#: second unambiguous anchor. It is needed because Member-facing MARKDOWN (the
#: charter, the boardroom template) writes commands inline inside a numbered
#: list item and wraps them across lines, where the line-start anchor cannot
#: see them. ``re.S`` so a span that spans a newline is still one command.
_BACKTICK_SPAN = re.compile(r"`([^`]+)`", re.S)

#: The MCP tool that never existed. ``firm_create_unit`` is the real one; this
#: literal must not survive anywhere in an assembled prompt.
DEAD_MCP_LITERAL = "unit_create"


class Invocation:
    """One ``firm ...`` command line lifted out of a prompt."""

    def __init__(self, words: tuple[str, ...], flags: tuple[str, ...], line: str):
        self.words = words
        self.flags = flags
        self.line = line

    def __repr__(self) -> str:  # pragma: no cover - failure messages only
        return f"Invocation(firm {' '.join(self.words)}, flags={list(self.flags)})"


def extract_invocations(prompt: str) -> list[Invocation]:
    """Every ``firm <verb ...>`` invocation *prompt* names, in order.

    Anchored on a line that STARTS with ``firm `` after its indentation and any
    bullet marker are stripped. That anchor is chosen because prose cannot
    produce it: "raise it with the firm MCP tool" has ``firm`` mid-sentence, and
    a Contract's ``sanctioned_commands`` entry renders as ``- firm unit create
    ...``, which SHOULD be checked and therefore should match.
    """
    candidates: list[str] = []
    for raw in prompt.splitlines():
        line = _BULLET.sub("", raw.strip())
        if line.startswith(_COMMAND_ANCHOR):
            candidates.append(line)
    for span in _BACKTICK_SPAN.findall(prompt):
        text = " ".join(span.split())
        if text.startswith(_COMMAND_ANCHOR):
            candidates.append(text)

    found: list[Invocation] = []
    seen: set[str] = set()
    for line in candidates:
        if line in seen:
            continue
        seen.add(line)
        tokens = line[len(_COMMAND_ANCHOR):].split()
        words: list[str] = []
        for token in tokens:
            if len(words) == 2 or not _SUBCOMMAND.match(token):
                break
            words.append(token)
        if not words:
            continue
        flags = tuple(t for t in tokens if _LONG_FLAG.match(t))
        found.append(Invocation(tuple(words), flags, line))
    return found


def require_floor(invocations: list[Invocation], floor: int = MIN_INVOCATIONS) -> None:
    """Refuse a reading that found too little to be a reading.

    A grader that prints counts beside a broken anchor has laundered blindness
    into data, so this raises instead of returning a number.
    """
    if len(invocations) < floor:
        raise AssertionError(
            f"extractor found {len(invocations)} firm invocations, floor is {floor} "
            f"— this run proved NOTHING about whether the prompt's verbs resolve. "
            f"Either the execution directive names no runnable verb, or the "
            f"anchor {_COMMAND_ANCHOR!r} no longer matches how commands are "
            f"rendered. Found: {invocations}"
        )


def _clean_env() -> dict[str, str]:
    """An explicit child environment — never ambient inheritance.

    ``CADRE_MEMBER_ID``/``FIRM_ID`` are stripped so a verb's help can never
    depend on the identity of whoever ran the suite.
    """
    env = dict(os.environ)
    env.pop("CADRE_MEMBER_ID", None)
    env.pop("FIRM_ID", None)
    return env


def run_help(words: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    """``firm <words> --help`` through argparse exactly as a Member's shell sees it."""
    return subprocess.run(
        [sys.executable, "-m", "firm", *words, "--help"],
        capture_output=True, text=True, cwd=REPO_ROOT, env=_clean_env(),
        timeout=60,
    )


# ---------------------------------------------------------------------------
# A real assembled prompt
# ---------------------------------------------------------------------------

def _seed(workspace: Path) -> sqlite3.Connection:
    db = workspace / ".firm" / "firm.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    create(conn, "member", {"id": "MEM-001", "firm_id": "chrisai",
                            "name": "Cooper", "role": "Ops Engineer",
                            "status": "active"})
    create(conn, "operation", {"id": "OPS-001", "firm_id": "chrisai",
                               "name": "Ops", "status": "active"})
    create(conn, "project", {"id": "PRJ-010", "firm_id": "chrisai",
                             "operation_id": "OPS-001", "name": "Inbox",
                             "status": "in_progress", "due_date": "2026-12-31"})
    create(conn, "unit", {"id": "UNIT-100", "firm_id": "chrisai",
                          "project_id": "PRJ-010", "name": "Existing work",
                          "status": "pending"})
    return conn


@pytest.fixture()
def prompt(tmp_path: Path) -> str:
    conn = _seed(tmp_path)
    try:
        return assemble_prompt(conn, "chrisai", "MEM-001", "UNIT-100",
                               cwd=str(tmp_path))
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Detector A — the verb resolves
# ═══════════════════════════════════════════════════════════════════════════

def test_every_verb_the_prompt_names_resolves(prompt: str) -> None:
    invocations = extract_invocations(prompt)
    require_floor(invocations)

    unresolved = []
    for inv in invocations:
        result = run_help(inv.words)
        if result.returncode != 0:
            unresolved.append(
                f"firm {' '.join(inv.words)} -> rc {result.returncode}: "
                f"{result.stderr.strip().splitlines()[-1] if result.stderr.strip() else ''}"
            )
    assert not unresolved, (
        f"the prompt names {len(unresolved)} verb(s) that do not exist "
        f"(checked {len(invocations)} invocations): " + "; ".join(unresolved)
    )


# ═══════════════════════════════════════════════════════════════════════════
# Detector B — the flag exists on that verb
# ═══════════════════════════════════════════════════════════════════════════

def test_every_flag_the_prompt_names_exists_on_that_verb(prompt: str) -> None:
    invocations = extract_invocations(prompt)
    require_floor(invocations)

    missing = []
    for inv in invocations:
        result = run_help(inv.words)
        if result.returncode != 0:
            continue  # detector A owns this failure; do not double-report
        for flag in inv.flags:
            if flag not in result.stdout:
                missing.append(f"firm {' '.join(inv.words)} has no {flag}")
    assert not missing, (
        "the prompt writes flags that the verb does not accept: " + "; ".join(missing)
    )


# ═══════════════════════════════════════════════════════════════════════════
# Controls — this file's own instrument must be shown able to fail
# ═══════════════════════════════════════════════════════════════════════════

def test_canary_a_fabricated_verb_is_reported_unresolved() -> None:
    """MUST-FAIL canary: proves the resolver can still say no."""
    invocations = extract_invocations("  firm frobnicate widget --now\n")
    assert len(invocations) == 1, invocations
    assert invocations[0].words == ("frobnicate", "widget")
    assert run_help(invocations[0].words).returncode != 0, (
        "a verb that does not exist resolved cleanly — the resolver cannot fail, "
        "so a green run of detector A would mean nothing"
    )


def test_canary_a_real_verb_resolves() -> None:
    """POSITIVE control for the canary above: the same machinery says yes."""
    assert run_help(("escalation", "raise")).returncode == 0


def test_canary_a_broken_anchor_reads_as_blindness_not_as_a_pass() -> None:
    """The middle row: verbs present, anchor broken, extractor blind.

    A false absence does not necessarily look thin — the input below has MORE
    text than a healthy prompt and parses none of it. The floor must convert
    that into a failure rather than an empty, clean-looking list.
    """
    blinded = "FIRM UNIT CREATE --name x\nFIRM DOC REGISTER --unit U --path p\n"
    invocations = extract_invocations(blinded)
    assert invocations == [], "the anchor is meant to miss this input"
    with pytest.raises(AssertionError, match="proved NOTHING"):
        require_floor(invocations)


def test_canary_the_floor_passes_on_a_healthy_reading() -> None:
    """Blindness control: the floor must NOT fire on a genuine reading."""
    healthy = (
        "- firm escalation raise --title t\n"
        "- firm gate request --action a\n"
        "- firm unit create --name n\n"
        "- firm doc register --unit u\n"
    )
    require_floor(extract_invocations(healthy))


def test_extractor_ignores_prose_that_merely_mentions_the_word_firm() -> None:
    """Law 40: check what ELSE can produce the substring before keying on it."""
    prose = (
        "raise it with the firm MCP tool firm_escalate\n"
        "the firm's Board decides\n"
        "This firm runs on Claude Code.\n"
        "the `firm` MCP server is not a command\n"
    )
    assert extract_invocations(prose) == []


def test_extractor_reads_a_command_wrapped_across_lines_in_backticks() -> None:
    """Markdown surfaces write commands inline and wrap them; the line-start
    anchor cannot see those, and without this the resolve guard over the
    charter would pass having checked nothing."""
    markdown = (
        '3. **Queue your own continuation**: `firm unit create --name "<x>"\n'
        "   --project <PRJ-id> --assignee $CADRE_MEMBER_ID`\n"
    )
    invocations = extract_invocations(markdown)
    assert [i.words for i in invocations] == [("unit", "create")]
    assert "--assignee" in invocations[0].flags


def test_the_same_command_written_twice_is_reported_once() -> None:
    """A line-anchored command inside backticks matches both anchors; the
    resolver should not report it twice and inflate the floor."""
    both = "`firm gate request --action a`\nfirm gate request --action a\n"
    assert len(extract_invocations(both)) == 1


# ═══════════════════════════════════════════════════════════════════════════
# The MCP mention is conditional on the firm actually loading the server
# ═══════════════════════════════════════════════════════════════════════════

def test_no_mcp_json_means_the_prompt_promises_no_mcp_tools(tmp_path: Path) -> None:
    conn = _seed(tmp_path)
    try:
        text = assemble_prompt(conn, "chrisai", "MEM-001", "UNIT-100", cwd=str(tmp_path))
    finally:
        conn.close()
    assert not (tmp_path / ".mcp.json").exists()
    assert "mcp__firm__" not in text, (
        "a firm with no .mcp.json was told to call firm MCP tools it cannot load"
    )


def test_a_firm_server_in_mcp_json_brings_the_mcp_mention_back(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"firm": {"command": "python"}}}', encoding="utf-8",
    )
    conn = _seed(tmp_path)
    try:
        text = assemble_prompt(conn, "chrisai", "MEM-001", "UNIT-100", cwd=str(tmp_path))
    finally:
        conn.close()
    assert "mcp__firm__" in text, (
        "a firm that DOES load the firm MCP server lost the secondary path"
    )


def test_the_prompt_never_names_the_mcp_tool_that_never_existed(tmp_path: Path) -> None:
    """``unit_create`` is not, and never was, an MCP tool. The real one is
    ``firm_create_unit`` (mcp/tools.py:207). Asserted in BOTH arms, because the
    literal was wrong regardless of whether the server loads."""
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"firm": {"command": "python"}}}', encoding="utf-8",
    )
    conn = _seed(tmp_path)
    try:
        with_mcp = assemble_prompt(conn, "chrisai", "MEM-001", "UNIT-100", cwd=str(tmp_path))
    finally:
        conn.close()
    (tmp_path / ".mcp.json").unlink()
    conn = _seed(tmp_path / "bare")
    try:
        without_mcp = assemble_prompt(conn, "chrisai", "MEM-001", "UNIT-100",
                                      cwd=str(tmp_path / "bare"))
    finally:
        conn.close()
    assert DEAD_MCP_LITERAL not in with_mcp
    assert DEAD_MCP_LITERAL not in without_mcp
