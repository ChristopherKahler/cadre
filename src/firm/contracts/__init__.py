"""Formal Contract runtime interface — invoke, status, cancel.

Adapters implement the ``ContractRuntime`` protocol to make the framework
runtime-agnostic.  Use ``resolve_runtime(contract)`` to get the right adapter.
"""

from firm.contracts.interface import (
    ContractRuntime,
    InvokeResult,
    RunHandle,
    RunStatus,
)
from firm.contracts.registry import resolve_runtime

__all__ = [
    "ContractRuntime",
    "InvokeResult",
    "RunHandle",
    "RunStatus",
    "resolve_runtime",
]
