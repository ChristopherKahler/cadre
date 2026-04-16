"""Stage dispatch — resolve a stage name to a skill command via Contract.skill_loadout."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from firm.core import repo


def resolve_stage(
    conn: sqlite3.Connection,
    member_id: str,
    stage: str,
) -> str:
    """Resolve a stage name to a skill command path.

    Reads the member's contract, extracts skill_loadout, and maps
    the stage to the corresponding command.

    Returns:
        The command string (e.g. "/blog:write").

    Raises:
        ValueError: If member, contract, loadout, or stage mapping not found.
    """
    member = repo.get(conn, "member", member_id)
    if not member:
        raise ValueError(f"Member '{member_id}' not found")

    contract_id = member.get("contract_id")
    if not contract_id:
        raise ValueError(f"Member '{member_id}' has no contract")

    contract = repo.get(conn, "contract", contract_id)
    if not contract:
        raise ValueError(f"Contract '{contract_id}' not found")

    loadout = contract.get("skill_loadout")
    if isinstance(loadout, str):
        try:
            loadout = json.loads(loadout)
        except (json.JSONDecodeError, TypeError):
            raise ValueError(
                f"Contract '{contract_id}' has invalid skill_loadout JSON"
            )

    if not isinstance(loadout, dict):
        raise ValueError(f"Contract '{contract_id}' has no skill_loadout")

    stages = loadout.get("stages", {})
    if stage not in stages:
        available = ", ".join(sorted(stages.keys()))
        raise ValueError(
            f"Stage '{stage}' not found in skill_loadout. "
            f"Available: {available}"
        )

    return stages[stage]


def list_stages(
    conn: sqlite3.Connection,
    member_id: str,
) -> dict[str, str]:
    """List all available stages for a member.

    Returns:
        Dict mapping stage_name -> command_path. Empty dict if member
        has no contract or skill_loadout.
    """
    member = repo.get(conn, "member", member_id)
    if not member:
        return {}

    contract_id = member.get("contract_id")
    if not contract_id:
        return {}

    contract = repo.get(conn, "contract", contract_id)
    if not contract:
        return {}

    loadout = contract.get("skill_loadout")
    if isinstance(loadout, str):
        try:
            loadout = json.loads(loadout)
        except (json.JSONDecodeError, TypeError):
            return {}

    if not isinstance(loadout, dict):
        return {}

    return loadout.get("stages", {})
