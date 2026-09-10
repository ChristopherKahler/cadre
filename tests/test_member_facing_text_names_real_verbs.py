"""Every Member-facing document must name commands that exist.

The execution directive is not the only place a Member is told what to do. The
wire step writes a charter into each firm's CLAUDE.md, the boardroom command
briefs the Board seat on what to tell a Member mid-run, and two service hint
strings are handed to a Member at the exact moment it is denied. All four
carried ``unit_create`` — a name that has never been an MCP tool, a CLI verb, or
anything else.

Fixing the prompt and leaving these is the failure mode this file exists to
stop: a paragraph is a claim, but the same sentence inside a document someone
executes from is a *command*, and commands are read without their surrounding
story.

**The charter is checked as RENDERED, not as its source constants.** The drill
that names the command lives in ``_BASE_SECTION``, which ``_render_charter``
formats into ``_CHARTER``; the text a Member actually reads is the output of
that call. Asserting on either constant alone is a read of a private value
rather than of the shipping channel — and a guard pointed at ``_CHARTER`` by
itself reads perfectly clean over a defect sitting in ``_BASE_SECTION``, which
is exactly what the vacuity control below caught while this file was written.

Reuses the extractor and resolver from :mod:`tests.test_prompt_verbs_resolve`
deliberately — a second copy would drift, and the point is that one definition
of "a verb that resolves" governs every Member-facing surface.
"""

from __future__ import annotations

from importlib.resources import files

import pytest

from firm.dashboard.wiring import _render_charter
from firm.services.authority import ESCALATE_HINT, _GRANT_HINT
from firm.services.gate import _GATE_DECISION_HINT
from tests.test_prompt_verbs_resolve import (
    DEAD_MCP_LITERAL,
    extract_invocations,
    run_help,
)

#: Names that were never callable in any form. ``firm_create_unit`` is the real
#: MCP tool; ``unit_create`` is the invented one that shipped for months.
DEAD_LITERALS = (DEAD_MCP_LITERAL,)


def _rendered_charter() -> str:
    """The charter as the wire step writes it into a firm's CLAUDE.md.

    ``base.present`` is True because the timeout drill — the part that tells a
    Member to queue its own continuation — only reaches the file on that arm.
    A False render would skip the very text this file guards.
    """
    return _render_charter(
        firm={"name": "ChrisAI", "description": "A firm.",
              "north_star": "Ship one thing a week."},
        members=[{"name": "Cooper", "role": "Ops Engineer"}],
        plan={"firm_id": "chrisai", "members": [{"name": "Cooper"}], "mcp": []},
        base={"present": True, "extensions": []},
        today="2026-09-10",
    )


def _boardroom_template() -> str:
    return (files("firm.templates.boardroom") / "boardroom.md").read_text(
        encoding="utf-8",
    )


#: Surfaces that instruct a Member, and must therefore name only real commands.
SURFACES = {
    "wiring rendered charter": _rendered_charter,
    "templates/boardroom/boardroom.md": _boardroom_template,
    "authority.ESCALATE_HINT": lambda: ESCALATE_HINT,
    "authority._GRANT_HINT": lambda: _GRANT_HINT,
    "gate._GATE_DECISION_HINT": lambda: _GATE_DECISION_HINT,
}

#: EVERY surface must name at least one command, hints included.
#:
#: The hints were exempt in the first version of this file, and mutation M5 —
#: pointing the gate hint at a verb that does not exist — left the guard GREEN.
#: The hints write their commands mid-sentence after "use: ", so neither anchor
#: could see them and that guard row was inert: present, reached, and incapable
#: of failing. The fix was to backtick the commands inside the hints so the
#: extractor reaches them, not to write the exemption down as accepted. An
#: exemption would have documented the blind spot AND pre-excused the next
#: drift through it.
SURFACES_THAT_MUST_NAME_A_COMMAND = tuple(sorted(SURFACES))


@pytest.mark.parametrize("name", sorted(SURFACES))
def test_surface_names_no_dead_tool(name: str) -> None:
    text = SURFACES[name]()
    for dead in DEAD_LITERALS:
        assert dead not in text, (
            f"{name} still tells a Member to call {dead!r}, which does not "
            f"exist as an MCP tool, a CLI verb, or anything else"
        )


@pytest.mark.parametrize("name", sorted(SURFACES))
def test_every_firm_command_a_surface_names_resolves(name: str) -> None:
    text = SURFACES[name]()
    invocations = extract_invocations(text)
    unresolved = [
        f"firm {' '.join(inv.words)}"
        for inv in invocations
        if run_help(inv.words).returncode != 0
    ]
    assert not unresolved, (
        f"{name} names {len(unresolved)} command(s) that do not resolve "
        f"(of {len(invocations)} checked): {', '.join(unresolved)}"
    )


@pytest.mark.parametrize("name", SURFACES_THAT_MUST_NAME_A_COMMAND)
def test_the_resolve_guard_is_not_vacuous_on_this_surface(name: str) -> None:
    """Positive control for the test above, and the reason this file is right.

    ``test_every_firm_command_a_surface_names_resolves`` is a search, and a
    search over a surface that names nothing passes having checked nothing. The
    first version of this file pointed at ``_CHARTER``, whose commands live in
    ``_BASE_SECTION`` — it extracted zero and would have shipped green over an
    unguarded surface. Zero is the one reading that cannot tell "clean" from
    "blind", so it fails here instead.
    """
    invocations = extract_invocations(SURFACES[name]())
    assert invocations, (
        f"{name} names no firm command at all — the resolve guard over it "
        f"checked nothing and proved nothing"
    )


def test_the_dead_literal_guard_can_actually_fail() -> None:
    """Must-fail canary: the guard is a substring check, so prove it fires on
    text that really does carry the dead name."""
    poisoned = "3. Queue your own continuation: `unit_create`, assigned to YOURSELF"
    assert DEAD_MCP_LITERAL in poisoned, (
        "the canary text no longer carries the dead literal, so it cannot "
        "prove the guard discriminates"
    )
