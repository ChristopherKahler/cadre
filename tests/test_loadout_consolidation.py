"""Unified loadout: game-role loadouts render on the Floor and boot into the
prompt with no drift between the two (fork cadre-loadout-consolidation).

The bug this guards: game-role contracts (dnd-table) store
{scope, duties, sanctioned_commands, style_contract, policies}, which the tool
sockets rendered as empty and which _render_contract only partly booted (the
sanctioned_commands LIST was dropped — it reached the Member only if a duty
restated it). Now the Floor surfaces every dimension and the boot prompt carries
every dimension: UI == filesystem config == what the run consumes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.dashboard.server import floor_state
from firm.pulse.prompt import _render_contract

GAME_ROLE = {
    "scope": "table-artist",
    "duties": ["render art for registered entities", "log prompts to dm-notes"],
    "sanctioned_commands": [
        "base nano-banana generate --prompt '<p>' --json --out .firm/game/art"],
    "style_contract": ["painted illustration, warm-light wasteland palette",
                       "NO text, NO watermarks"],
    "policies": ["art serves the registry — never invent entities"],
}


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    apply_migrations(c)
    create(c, "firm", {"id": "dnd", "name": "The Table"})
    create(c, "contract", {
        "id": "CON-A", "firm_id": "dnd", "name": "Sable Contract",
        "runtime_type": "claude_code", "skill_loadout": json.dumps(GAME_ROLE)})
    create(c, "member", {
        "id": "MEM-005", "firm_id": "dnd", "name": "Sable",
        "role": "Table Artist", "status": "active", "contract_id": "CON-A"})
    return c


def test_render_contract_boots_every_game_role_dimension():
    out = _render_contract(_conn(), "MEM-005") or ""
    assert "table-artist" in out                       # scope
    assert "base nano-banana generate" in out          # sanctioned_commands (was dropped)
    assert "render art for registered entities" in out  # duties
    assert "warm-light wasteland palette" in out       # style_contract
    assert "never invent entities" in out              # policies


def test_floor_surfaces_game_role_loadout_with_no_drift():
    c = _conn()
    card = next(m for m in floor_state(c, Path("/tmp"), "dnd")["members"]
                if m["id"] == "MEM-005")
    rl = card["role_loadout"]
    assert rl["scope"] == "table-artist"
    assert rl["sanctioned_commands"] == GAME_ROLE["sanctioned_commands"]
    assert rl["duties"] == GAME_ROLE["duties"]
    assert rl["style_contract"] == GAME_ROLE["style_contract"]

    # No drift: every dimension the UI shows also boots into the run's prompt.
    prompt = _render_contract(c, "MEM-005") or ""
    assert rl["scope"] in prompt
    for key in ("duties", "sanctioned_commands", "style_contract", "policies"):
        for item in rl[key]:
            assert item in prompt, f"{key} item missing from boot prompt: {item}"


def test_tool_socket_contract_has_empty_role_loadout():
    c = _conn()
    create(c, "contract", {
        "id": "CON-B", "firm_id": "dnd", "name": "Std", "runtime_type": "claude_code",
        "skill_loadout": json.dumps({"cli": ["jq"], "skills": ["voice-system"]})})
    create(c, "member", {
        "id": "MEM-009", "firm_id": "dnd", "name": "Cooper",
        "role": "Ops", "status": "active", "contract_id": "CON-B"})
    card = next(m for m in floor_state(c, Path("/tmp"), "dnd")["members"]
                if m["id"] == "MEM-009")
    assert card["role_loadout"]["scope"] == ""
    assert card["role_loadout"]["duties"] == []
    assert card["role_loadout"]["sanctioned_commands"] == []
    assert card["loadout"]["skills"] == ["voice-system"]   # tool sockets still carry the load
