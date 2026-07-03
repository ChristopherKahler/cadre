"""Tests for firm.contracts.registry — runtime_type resolver."""

from __future__ import annotations

import pytest

from firm.contracts.claude_code import ClaudeCodeRuntime
from firm.contracts.interface import ContractRuntime
from firm.contracts.registry import SUPPORTED_RUNTIMES, resolve_runtime


class TestResolveRuntime:
    def test_claude_code(self):
        contract = {"runtime_type": "claude_code"}
        runtime = resolve_runtime(contract)
        assert isinstance(runtime, ClaudeCodeRuntime)

    def test_default_is_claude_code(self):
        runtime = resolve_runtime({})
        assert isinstance(runtime, ClaudeCodeRuntime)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported runtime_type 'custom'"):
            resolve_runtime({"runtime_type": "custom"})

    def test_error_lists_supported(self):
        with pytest.raises(ValueError, match="claude_code"):
            resolve_runtime({"runtime_type": "bad"})


class TestSupportedRuntimes:
    def test_contains_claude_code(self):
        assert "claude_code" in SUPPORTED_RUNTIMES

    def test_is_list(self):
        assert isinstance(SUPPORTED_RUNTIMES, list)
