"""Tests for firm.pulse.validate and firm.pulse.budget modules."""

from __future__ import annotations

import json
import sqlite3
from unittest import mock

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find, get
from firm.pulse.budget import (
    BudgetCheck,
    check_budget_preflight,
    check_rate_limit,
    update_budget_postrun,
)
from firm.pulse.validate import (
    ValidationResult,
    retry_on_failure,
    validate_output,
)
from firm.services._id import PREFIX_REGISTRY, next_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


def _add_contract(
    conn: sqlite3.Connection,
    contract_id: str,
    *,
    validation_config: dict | None = None,
    budget_config: dict | None = None,
) -> dict:
    return create(conn, "contract", {
        "id": contract_id,
        "firm_id": "chrisai",
        "name": f"Contract {contract_id}",
        "runtime_type": "claude_code",
        "validation_config": validation_config,
        "budget_config": budget_config,
    })


def _add_member(
    conn: sqlite3.Connection,
    member_id: str,
    *,
    contract_id: str | None = None,
) -> dict:
    return create(conn, "member", {
        "id": member_id,
        "firm_id": "chrisai",
        "name": f"Member {member_id}",
        "role": "worker",
        "status": "active",
        "contract_id": contract_id,
    })


def _add_budget_period(
    conn: sqlite3.Connection,
    bp_id: str,
    member_id: str,
    *,
    run_count: int = 0,
    total_cost_usd: float = 0.0,
    total_input_tokens: int = 0,
    total_output_tokens: int = 0,
    status: str = "active",
) -> dict:
    return create(conn, "budget_period", {
        "id": bp_id,
        "firm_id": "chrisai",
        "member_id": member_id,
        "period_start": "2026-04-01T00:00:00+00:00",
        "period_end": "2026-04-30T23:59:59+00:00",
        "run_count": run_count,
        "total_cost_usd": total_cost_usd,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "status": status,
    })


def _mock_result(text: str = "Some output text", **kwargs) -> dict:
    """Build a minimal parsed result dict."""
    result = {
        "session_id": "ses-001",
        "text": text,
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read": 0,
            "cache_create": 0,
        },
        "total_cost_usd": 0.05,
        "is_error": False,
        "stop_reason": "end_turn",
        "tool_calls": [],
        "rate_limit_events": [],
    }
    result.update(kwargs)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Validation: validate_output
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateDisabled:

    def test_none_config(self):
        result = validate_output(_mock_result(), None, "/tmp")
        assert result.passed is True

    def test_enabled_false(self):
        config = {"enabled": False, "validators": ["min_word_count"]}
        result = validate_output(_mock_result(), config, "/tmp")
        assert result.passed is True

    def test_empty_validators_list(self):
        config = {"enabled": True, "validators": []}
        result = validate_output(_mock_result(), config, "/tmp")
        assert result.passed is True


class TestValidateMinWordCount:

    def test_passes_above_threshold(self):
        text = " ".join(["word"] * 200)
        config = {"enabled": True, "validators": ["min_word_count"]}
        result = validate_output(_mock_result(text=text), config, "/tmp")
        assert result.passed is True
        assert result.details[0]["name"] == "min_word_count"
        assert result.details[0]["passed"] is True

    def test_fails_below_threshold(self):
        config = {"enabled": True, "validators": ["min_word_count"]}
        result = validate_output(_mock_result(text="short"), config, "/tmp")
        assert result.passed is False
        assert result.details[0]["passed"] is False


class TestValidateAcSelfReport:

    def test_passes_with_marker(self):
        config = {"enabled": True, "validators": ["ac_self_report"]}
        result = validate_output(
            _mock_result(text="All work done. AC-1 satisfied: tests pass"),
            config, "/tmp",
        )
        assert result.passed is True

    def test_passes_with_full_phrase(self):
        config = {"enabled": True, "validators": ["ac_self_report"]}
        result = validate_output(
            _mock_result(text="ACCEPTANCE CRITERIA met"),
            config, "/tmp",
        )
        assert result.passed is True

    def test_fails_without_marker(self):
        config = {"enabled": True, "validators": ["ac_self_report"]}
        result = validate_output(
            _mock_result(text="I did some work and it looks good"),
            config, "/tmp",
        )
        assert result.passed is False


class TestValidateMultiple:

    def test_all_pass(self):
        text = " ".join(["word"] * 200) + " AC-1 satisfied"
        config = {"enabled": True, "validators": ["min_word_count", "ac_self_report"]}
        result = validate_output(_mock_result(text=text), config, "/tmp")
        assert result.passed is True
        assert len(result.details) == 2

    def test_one_fails(self):
        config = {"enabled": True, "validators": ["min_word_count", "ac_self_report"]}
        result = validate_output(
            _mock_result(text="short AC-1"),
            config, "/tmp",
        )
        assert result.passed is False
        # ac_self_report should pass, min_word_count should fail
        by_name = {d["name"]: d for d in result.details}
        assert by_name["ac_self_report"]["passed"] is True
        assert by_name["min_word_count"]["passed"] is False

    def test_unknown_validator(self):
        config = {"enabled": True, "validators": ["nonexistent"]}
        result = validate_output(_mock_result(), config, "/tmp")
        assert result.passed is False
        assert "Unknown" in result.details[0]["message"]


class TestNonemptyFloor:
    """Always-on completion floor: a no-op run never completes a unit, even
    with no validation_config (the wastelander mis-seed guard)."""

    def test_empty_run_fails_even_with_none_config(self):
        result = _mock_result(text="", tool_calls=[])
        out = validate_output(result, None, "/tmp")
        assert out.passed is False
        assert out.details[0]["name"] == "nonempty_floor"

    def test_empty_run_fails_even_when_disabled(self):
        result = _mock_result(text="   ", tool_calls=[])
        out = validate_output(result, {"enabled": False}, "/tmp")
        assert out.passed is False

    def test_tool_action_satisfies_floor(self):
        # A file-writer with terse chatter still passes the floor.
        result = _mock_result(text="", tool_calls=[
            {"name": "Write", "input": {"file_path": "/tmp/x.md"}},
        ])
        out = validate_output(result, None, "/tmp")
        assert out.passed is True

    def test_text_satisfies_floor(self):
        out = validate_output(_mock_result(text="real work"), None, "/tmp")
        assert out.passed is True


class TestFileExistsRequireWritten:
    """require_written stops a blocked/no-op drafter from completing a unit
    it never wrote a file for (Wren, RUN-017, 2026-07-07)."""

    def test_no_file_written_fails_when_required(self):
        result = _mock_result(text="I am blocked and will not draft.", tool_calls=[])
        config = {"validators": [{"name": "file_exists", "require_written": True}]}
        out = validate_output(result, config, "/tmp")
        assert out.passed is False
        assert out.details[0]["name"] == "file_exists"

    def test_no_file_passes_by_default(self):
        result = _mock_result(text="did analysis, wrote nothing", tool_calls=[])
        config = {"validators": ["file_exists"]}
        out = validate_output(result, config, "/tmp")
        assert out.passed is True

    def test_written_file_that_exists_passes(self, tmp_path):
        fp = tmp_path / "chapter.md"
        fp.write_text("prose")
        result = _mock_result(text="drafted", tool_calls=[
            {"name": "Write", "input": {"file_path": str(fp)}},
        ])
        config = {"validators": [{"name": "file_exists", "require_written": True}]}
        out = validate_output(result, config, str(tmp_path))
        assert out.passed is True

    def test_dict_params_pass_through_threshold(self):
        text = " ".join(["word"] * 40)
        config = {"validators": [{"name": "min_word_count", "threshold": 30}]}
        assert validate_output(_mock_result(text=text), config, "/tmp").passed is True
        config = {"validators": [{"name": "min_word_count", "threshold": 60}]}
        assert validate_output(_mock_result(text=text), config, "/tmp").passed is False


# ═══════════════════════════════════════════════════════════════════════════
# Validation: retry_on_failure
# ═══════════════════════════════════════════════════════════════════════════


class TestRetryOnFailure:

    def test_appends_failure_context(self):
        captured_prompts: list[str] = []

        class FakeSpawn:
            stdout = '{"type":"result","usage":{},"stop_reason":"end_turn"}'

        def mock_spawn(prompt: str):
            captured_prompts.append(prompt)
            return FakeSpawn()

        def mock_parse(stdout: str) -> dict:
            return {"text": "retry output", "usage": {}}

        result = retry_on_failure(
            "original prompt",
            "Validator min_word_count failed: 10 words",
            mock_spawn,
            mock_parse,
        )

        assert len(captured_prompts) == 1
        assert "original prompt" in captured_prompts[0]
        assert "PREVIOUS ATTEMPT FAILED" in captured_prompts[0]
        assert "min_word_count" in captured_prompts[0]
        assert result["text"] == "retry output"


# ═══════════════════════════════════════════════════════════════════════════
# Budget: BP ID generation
# ═══════════════════════════════════════════════════════════════════════════


class TestBpIdGeneration:

    def test_bp_in_registry(self):
        assert PREFIX_REGISTRY["budget_period"] == "BP"

    def test_generates_bp_001(self):
        conn = _fresh_conn()
        bp_id = next_id(conn, "budget_period", "chrisai")
        assert bp_id == "BP-001"


# ═══════════════════════════════════════════════════════════════════════════
# Budget: pre-flight
# ═══════════════════════════════════════════════════════════════════════════


class TestBudgetPreflight:

    def test_no_config_allowed(self):
        conn = _fresh_conn()
        _add_member(conn, "MEM-001")
        check = check_budget_preflight(conn, "MEM-001")
        assert check.allowed is True

    def test_under_limit(self):
        conn = _fresh_conn()
        con = _add_contract(conn, "CON-001", budget_config={
            "limits": {"max_runs_per_period": 10, "max_total_cost_per_period_usd": 50.0},
        })
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_budget_period(conn, "BP-001", "MEM-001", run_count=5, total_cost_usd=20.0)
        check = check_budget_preflight(conn, "MEM-001")
        assert check.allowed is True

    def test_over_run_limit(self):
        conn = _fresh_conn()
        _add_contract(conn, "CON-001", budget_config={
            "limits": {"max_runs_per_period": 10},
        })
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_budget_period(conn, "BP-001", "MEM-001", run_count=10)
        check = check_budget_preflight(conn, "MEM-001")
        assert check.allowed is False
        assert "Run limit" in check.reason

    def test_over_cost_limit(self):
        conn = _fresh_conn()
        _add_contract(conn, "CON-001", budget_config={
            "limits": {"max_total_cost_per_period_usd": 50.0},
        })
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_budget_period(conn, "BP-001", "MEM-001", total_cost_usd=50.0)
        check = check_budget_preflight(conn, "MEM-001")
        assert check.allowed is False
        assert "Cost limit" in check.reason

    def test_no_active_period(self):
        conn = _fresh_conn()
        _add_contract(conn, "CON-001", budget_config={
            "limits": {"max_runs_per_period": 10},
        })
        _add_member(conn, "MEM-001", contract_id="CON-001")
        # No budget_period created
        check = check_budget_preflight(conn, "MEM-001")
        assert check.allowed is True


# ═══════════════════════════════════════════════════════════════════════════
# Budget: post-run
# ═══════════════════════════════════════════════════════════════════════════


class TestBudgetPostrun:

    def test_increments_totals(self):
        conn = _fresh_conn()
        _add_contract(conn, "CON-001", budget_config={"limits": {}})
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_budget_period(conn, "BP-001", "MEM-001",
                           run_count=5, total_input_tokens=5000,
                           total_output_tokens=2000, total_cost_usd=1.50)

        update_budget_postrun(conn, "MEM-001", "chrisai", _mock_result())

        period = get(conn, "budget_period", "BP-001")
        assert period["run_count"] == 6
        assert period["total_input_tokens"] == 6000
        assert period["total_output_tokens"] == 2500
        assert abs(period["total_cost_usd"] - 1.55) < 0.001

    def test_creates_usage_event(self):
        conn = _fresh_conn()
        _add_contract(conn, "CON-001", budget_config={"limits": {}})
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_budget_period(conn, "BP-001", "MEM-001")

        update_budget_postrun(conn, "MEM-001", "chrisai", _mock_result())

        events = find(conn, "usage_event", firm_id="chrisai")
        assert len(events) == 1
        assert events[0]["plan"] == "claude_pro_200"
        assert events[0]["tokens_in"] == 1000

    def test_sets_limit_reached(self):
        conn = _fresh_conn()
        _add_contract(conn, "CON-001", budget_config={
            "limits": {"max_runs_per_period": 5},
        })
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_budget_period(conn, "BP-001", "MEM-001", run_count=4)

        update_budget_postrun(conn, "MEM-001", "chrisai", _mock_result())

        period = get(conn, "budget_period", "BP-001")
        assert period["status"] == "limit_reached"

    def test_creates_period_if_none_exists(self):
        conn = _fresh_conn()
        _add_contract(conn, "CON-001", budget_config={"limits": {}})
        _add_member(conn, "MEM-001", contract_id="CON-001")

        update_budget_postrun(conn, "MEM-001", "chrisai", _mock_result())

        periods = find(conn, "budget_period", member_id="MEM-001")
        assert len(periods) == 1
        assert periods[0]["run_count"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# Budget: rate limit awareness
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckRateLimit:

    def test_above_threshold(self):
        events = [{"utilization": 0.92}]
        assert check_rate_limit(events, alert_threshold_pct=80) is True

    def test_below_threshold(self):
        events = [{"utilization": 0.5}]
        assert check_rate_limit(events, alert_threshold_pct=80) is False

    def test_empty_events(self):
        assert check_rate_limit([], alert_threshold_pct=80) is False

    def test_multiple_events_one_above(self):
        events = [{"utilization": 0.3}, {"utilization": 0.95}]
        assert check_rate_limit(events, alert_threshold_pct=80) is True
