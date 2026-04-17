"""Contract runtime registry — resolves ``runtime_type`` to runtime instance."""

from __future__ import annotations

from typing import Any

from firm.contracts.claude_code import ClaudeCodeRuntime
from firm.contracts.interface import ContractRuntime

_RUNTIMES: dict[str, type] = {
    "claude_code": ClaudeCodeRuntime,
}

SUPPORTED_RUNTIMES: list[str] = list(_RUNTIMES.keys())


def resolve_runtime(contract: dict[str, Any]) -> ContractRuntime:
    """Return the correct ``ContractRuntime`` for a contract.

    Args:
        contract: Contract row dict with ``runtime_type`` field.

    Returns:
        Instantiated Contract runtime.

    Raises:
        ValueError: If ``runtime_type`` is not registered.
    """
    runtime_type = contract.get("runtime_type", "claude_code")
    cls = _RUNTIMES.get(runtime_type)
    if cls is None:
        raise ValueError(
            f"Unsupported runtime_type '{runtime_type}'. "
            f"Supported: {', '.join(SUPPORTED_RUNTIMES)}"
        )
    return cls()
