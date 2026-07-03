"""End-to-end integration test — seed → dispatch → invoke → finalize.

Validates the full Quill dispatch chain works with seeded data.
Spawn is mocked (no real claude subprocess) but everything else is real.
"""

from __future__ import annotations

import json
import sqlite3
from unittest import mock

import pytest

from firm.contracts.claude_code import ClaudeCodeRuntime
from firm.contracts.dispatch import list_stages, resolve_stage
from firm.contracts.registry import resolve_runtime
from firm.core.migrate import apply_migrations
from firm.core.repo import create, get, find, update
from firm.pulse.runner import make_runner
from firm.pulse.spawn import SpawnResult
from firm.seed import QUILL_SKILL_LOADOUT, seed_chrisai


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    return conn


def _mock_spawn_result(text="Blog post complete. AC-1 satisfied.", cost=0.08):
    init_line = json.dumps({"type": "system", "subtype": "init", "session_id": "e2e"})
    assistant_line = json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    })
    result_line = json.dumps({
        "type": "result",
        "usage": {"input_tokens": 2000, "output_tokens": 1000,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        "total_cost_usd": cost,
        "stop_reason": "end_turn",
        "is_error": False,
    })
    return SpawnResult(
        returncode=0,
        stdout="\n".join([init_line, assistant_line, result_line]),
        stderr="",
        pid=42,
        timed_out=False,
    )


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

class TestSeedChrisAI:
    def test_creates_all_entities(self):
        conn = _fresh_conn()
        ids = seed_chrisai(conn)

        assert ids["firm"] == "chrisai"
        assert ids["member"] == "MEM-001"
        assert ids["contract"] == "CON-001"
        assert ids["operation"] == "OP-001"
        assert ids["project"] == "PRJ-001"
        assert ids["unit"] == "UNT-001"

        # Verify entities in DB
        assert get(conn, "firm", "chrisai") is not None
        assert get(conn, "member", "MEM-001")["name"] == "Quill"
        assert get(conn, "contract", "CON-001")["runtime_type"] == "claude_code"
        assert get(conn, "unit", "UNT-001")["claimed_by"] == "MEM-001"

    def test_idempotent(self):
        conn = _fresh_conn()
        ids1 = seed_chrisai(conn)
        ids2 = seed_chrisai(conn)
        assert ids1 == ids2

    def test_skill_loadout_stored(self):
        conn = _fresh_conn()
        seed_chrisai(conn)
        contract = get(conn, "contract", "CON-001")
        loadout = contract["skill_loadout"]
        # Repo auto-deserializes JSON columns
        if isinstance(loadout, str):
            loadout = json.loads(loadout)
        assert loadout["stages"]["full"] == "/blog:write"
        assert len(loadout["stages"]) == 11


# ---------------------------------------------------------------------------
# Dispatch chain
# ---------------------------------------------------------------------------

class TestDispatchChain:
    def test_resolve_stage_after_seed(self):
        conn = _fresh_conn()
        seed_chrisai(conn)
        assert resolve_stage(conn, "MEM-001", "write") == "/blog:write"
        assert resolve_stage(conn, "MEM-001", "full") == "/blog:write"
        assert resolve_stage(conn, "MEM-001", "research") == "/blog:research"

    def test_list_stages_after_seed(self):
        conn = _fresh_conn()
        seed_chrisai(conn)
        stages = list_stages(conn, "MEM-001")
        assert len(stages) == 11
        assert "full" in stages

    def test_resolve_runtime_from_seeded_contract(self):
        conn = _fresh_conn()
        seed_chrisai(conn)
        contract = get(conn, "contract", "CON-001")
        runtime = resolve_runtime(contract)
        assert isinstance(runtime, ClaudeCodeRuntime)


# ---------------------------------------------------------------------------
# Full E2E: seed → runner → member_run lifecycle
# ---------------------------------------------------------------------------

class TestE2ERunnerLifecycle:
    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_seeded_quill_runs_to_completion(self, mock_spawn):
        mock_spawn.return_value = _mock_spawn_result()

        conn = _fresh_conn()
        seed_chrisai(conn)

        # Run Quill through the PULSE runner
        runner = make_runner("chrisai", "/tmp")
        member = get(conn, "member", "MEM-001")
        result = runner(conn, member)

        # Runner completes successfully
        assert result["status"] == "completed"
        assert result["run_id"] is not None
        assert result["cost"] == 0.08

        # member_run in DB
        run = get(conn, "member_run", result["run_id"])
        assert run["status"] == "completed"
        assert run["member_id"] == "MEM-001"
        assert run["unit_id"] == "UNT-001"
        assert run["invocation_source"] == "pulse"
        assert run["prompt_snapshot"] is not None
        assert run["ended_at"] is not None

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_prompt_contains_quill_identity(self, mock_spawn):
        mock_spawn.return_value = _mock_spawn_result()

        conn = _fresh_conn()
        seed_chrisai(conn)

        runner = make_runner("chrisai", "/tmp")
        member = get(conn, "member", "MEM-001")
        runner(conn, member)

        # Verify the prompt passed to spawn includes Quill's identity
        prompt = mock_spawn.call_args[0][0]
        assert "Quill" in prompt
        assert "Blog Author" in prompt
        assert "MEM-001" in prompt

    @mock.patch("firm.contracts.claude_code.spawn_member_run")
    def test_prompt_contains_unit_briefing(self, mock_spawn):
        mock_spawn.return_value = _mock_spawn_result()

        conn = _fresh_conn()
        seed_chrisai(conn)

        runner = make_runner("chrisai", "/tmp")
        member = get(conn, "member", "MEM-001")
        runner(conn, member)

        prompt = mock_spawn.call_args[0][0]
        assert "Claude Code workflow automation" in prompt
        assert "UNT-001" in prompt
