"""Contract entity service — create, view, update.

Contracts define how Members execute: runtime configuration, budget limits,
skill/domain loadouts, and validation rules. Tightly coupled with Members
(member.contract_id).

ID prefix: CON-NNN
Records events: contract.created, contract.updated
"""

from __future__ import annotations

import sqlite3
from typing import Any

from firm.core import repo
from firm.services._id import next_id
from firm.services._records import log_event
from firm.services._validate import require_exists, validate_fk, validate_status

RUNTIME_TYPES = [
    "claude_code", "openclaw", "codex", "cursor", "api_direct", "custom",
]


def create_contract(
    conn: sqlite3.Connection,
    firm_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Create a Contract with FK validation and Records entry.

    Args:
        conn: SQLite connection.
        firm_id: Firm scope.
        data: Must include 'name' and 'runtime_type'. Optional: member_id,
              runtime_config, skill_loadout, domain_loadout, pulse_config,
              validation_config, budget_config.

    Returns:
        The created contract row as a dict.

    Raises:
        ValueError: If required fields missing, member_id invalid, or
                    runtime_type not in allowed set.
    """
    if "name" not in data or "runtime_type" not in data:
        raise ValueError(
            "'name' and 'runtime_type' are required for contract creation"
        )

    # Validate runtime_type against allowed set
    validate_status(data["runtime_type"], RUNTIME_TYPES)

    # Validate member_id FK (if provided)
    validate_fk(conn, "member", data.get("member_id"))

    contract_id = next_id(conn, "contract", firm_id)

    # Build row
    row_data: dict[str, Any] = {
        "id": contract_id,
        "firm_id": firm_id,
        "name": data["name"],
        "runtime_type": data["runtime_type"],
    }
    for field in (
        "member_id",
        "runtime_config",
        "skill_loadout",
        "domain_loadout",
        "pulse_config",
        "validation_config",
        "budget_config",
    ):
        if field in data:
            row_data[field] = data[field]

    created = repo.create(conn, "contract", row_data)

    # Records entry
    log_event(
        conn,
        firm_id=firm_id,
        event_type="contract.created",
        actor={"type": "board", "id": None},
        target_ref={"type": "contract", "id": contract_id},
    )

    return created


def view_contract(
    conn: sqlite3.Connection,
    contract_id: str,
) -> dict[str, Any]:
    """View a contract by ID. Raises ValueError if not found."""
    return require_exists(conn, "contract", contract_id)


def update_contract(
    conn: sqlite3.Connection,
    contract_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Update a contract and log Records entry.

    Raises:
        ValueError: If contract not found.
    """
    existing = require_exists(conn, "contract", contract_id)

    updated = repo.update(conn, "contract", contract_id, data)
    assert updated is not None, "contract disappeared after require_exists"

    # Records entry
    log_event(
        conn,
        firm_id=existing["firm_id"],
        event_type="contract.updated",
        actor={"type": "board", "id": None},
        target_ref={"type": "contract", "id": contract_id},
    )

    return updated
