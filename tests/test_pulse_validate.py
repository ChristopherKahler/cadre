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


# ---------------------------------------------------------------------------
# sql_guard — generic DB-state validator (turn-close guard; ESC-010 lineage)
# ---------------------------------------------------------------------------

def _workspace_db(tmp_path):
    """A real file-backed firm workspace so db_connection(cwd) resolves."""
    from firm.core.db import connect, get_db_path

    (tmp_path / ".firm").mkdir()
    conn = connect(get_db_path(tmp_path))
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    conn.commit()
    conn.close()
    return tmp_path


class TestSqlGuard:
    def test_passes_when_query_returns_row(self, tmp_path):
        ws = _workspace_db(tmp_path)
        cfg = {"validators": [
            {"name": "sql_guard", "query": "SELECT 1", "expect": "nonempty"}]}
        assert validate_output(_mock_result(), cfg, str(ws)).passed is True

    def test_fails_with_message_when_no_row(self, tmp_path):
        ws = _workspace_db(tmp_path)
        cfg = {"validators": [{
            "name": "sql_guard", "query": "SELECT 1 WHERE 1=0",
            "expect": "nonempty", "message": "turn not closed"}]}
        r = validate_output(_mock_result(), cfg, str(ws))
        assert r.passed is False
        assert r.details[0]["message"] == "turn not closed"

    def test_expect_empty_inverts(self, tmp_path):
        ws = _workspace_db(tmp_path)
        cfg = {"validators": [{
            "name": "sql_guard", "query": "SELECT 1 WHERE 1=0", "expect": "empty"}]}
        assert validate_output(_mock_result(), cfg, str(ws)).passed is True

    def test_broken_query_fails_loud(self, tmp_path):
        ws = _workspace_db(tmp_path)
        cfg = {"validators": [{
            "name": "sql_guard", "query": "SELECT * FROM does_not_exist",
            "expect": "nonempty"}]}
        r = validate_output(_mock_result(), cfg, str(ws))
        assert r.passed is False
        assert "error" in r.details[0]["message"].lower()


# ═══════════════════════════════════════════════════════════════════════════
# Validation: file_exists — cleanup is not failure (RUN-007, 2026-07-13)
# ═══════════════════════════════════════════════════════════════════════════

class TestFileExistsCleanup:
    """A member that deletes its own scratch files did its job. Wrench was
    failed and Ralph-Wiggum retried ($7.76) for deleting _tmp_ files it
    correctly cleaned up (crows-and-pawns RUN-007, 2026-07-13)."""

    def test_deleted_scratch_passes(self, tmp_path):
        deliverable = tmp_path / "report.md"
        deliverable.write_text("findings")
        scratch = tmp_path / "_tmp_scratch.gd"
        result = _mock_result(text="done", tool_calls=[
            {"name": "Write", "input": {"file_path": str(deliverable)}},
            {"name": "Write", "input": {"file_path": str(scratch)}},
            {"name": "Bash", "input": {"command": f"rm {scratch}"}},
        ])
        config = {"validators": [{"name": "file_exists", "require_written": True}]}
        out = validate_output(result, config, str(tmp_path))
        assert out.passed is True
        assert "cleaned up" in out.details[0]["message"]

    def test_basename_deletion_evidence_counts(self, tmp_path):
        deliverable = tmp_path / "report.md"
        deliverable.write_text("findings")
        scratch = tmp_path / "test_zzscratch.gd"
        result = _mock_result(text="done", tool_calls=[
            {"name": "Write", "input": {"file_path": str(deliverable)}},
            {"name": "Write", "input": {"file_path": str(scratch)}},
            {"name": "Bash", "input": {"command": "rm test_zzscratch.gd"}},
        ])
        config = {"validators": ["file_exists"]}
        out = validate_output(result, config, str(tmp_path))
        assert out.passed is True

    def test_missing_without_deletion_evidence_fails(self, tmp_path):
        ghost = tmp_path / "never_landed.md"
        result = _mock_result(text="done", tool_calls=[
            {"name": "Write", "input": {"file_path": str(ghost)}},
        ])
        config = {"validators": ["file_exists"]}
        out = validate_output(result, config, str(tmp_path))
        assert out.passed is False
        assert "Missing" in out.details[0]["message"]

    def test_all_written_deleted_fails_when_required(self, tmp_path):
        scratch = tmp_path / "_tmp_only.md"
        result = _mock_result(text="done", tool_calls=[
            {"name": "Write", "input": {"file_path": str(scratch)}},
            {"name": "Bash", "input": {"command": f"rm {scratch}"}},
        ])
        config = {"validators": [{"name": "file_exists", "require_written": True}]}
        out = validate_output(result, config, str(tmp_path))
        assert out.passed is False
        assert "deleted" in out.details[0]["message"]

    def test_unrelated_bash_is_not_deletion_evidence(self, tmp_path):
        ghost = tmp_path / "gone.md"
        result = _mock_result(text="done", tool_calls=[
            {"name": "Write", "input": {"file_path": str(ghost)}},
            {"name": "Bash", "input": {"command": f"cat {ghost} | head -5"}},
        ])
        config = {"validators": ["file_exists"]}
        out = validate_output(result, config, str(tmp_path))
        assert out.passed is False


class TestFileExistsDeclaredOutputs:
    """When the unit declares outputs, THOSE are the contract — incidental
    writes are the member's own business (tapir, 2026-07-13)."""

    def test_declared_deliverable_present_ignores_scratch(self, tmp_path):
        (tmp_path / "reports").mkdir()
        (tmp_path / "reports" / "out.md").write_text("real")
        unit = {"id": "UNT-X", "outputs": json.dumps(["reports/out.md"])}
        ghost = tmp_path / "scratch.md"
        result = _mock_result(text="done", tool_calls=[
            {"name": "Write", "input": {"file_path": str(ghost)}},
        ])
        config = {"validators": [{"name": "file_exists", "require_written": True}]}
        out = validate_output(result, config, str(tmp_path), unit=unit)
        assert out.passed is True
        assert "declared" in out.details[0]["message"]

    def test_declared_deliverable_missing_fails(self, tmp_path):
        unit = {"id": "UNT-X", "outputs": json.dumps(["reports/out.md"])}
        result = _mock_result(text="done", tool_calls=[])
        config = {"validators": ["file_exists"]}
        out = validate_output(result, config, str(tmp_path), unit=unit)
        assert out.passed is False

    def test_declared_empty_file_fails(self, tmp_path):
        (tmp_path / "out.md").write_text("")
        unit = {"id": "UNT-X", "outputs": json.dumps(["out.md"])}
        result = _mock_result(text="done", tool_calls=[])
        config = {"validators": ["file_exists"]}
        out = validate_output(result, config, str(tmp_path), unit=unit)
        assert out.passed is False
        assert "empty" in out.details[0]["message"]

    def test_declared_dict_entries_supported(self, tmp_path):
        (tmp_path / "post.md").write_text("content")
        unit = {"id": "UNT-X", "outputs": json.dumps([{"path": "post.md"}])}
        result = _mock_result(text="done", tool_calls=[])
        config = {"validators": ["file_exists"]}
        out = validate_output(result, config, str(tmp_path), unit=unit)
        assert out.passed is True


# ═══════════════════════════════════════════════════════════════════════════
# Validation: ac_script — ACs are executable law (UNT-TOOLING, 2026-07-13)
# ═══════════════════════════════════════════════════════════════════════════

class TestAcScript:
    """An AC that names a check script is run, exit 0 required. Paired with
    the artifact floor: a vacuous script must not bless missing artifacts."""

    def _unit(self, criteria, outputs=None):
        return {
            "id": "UNT-X",
            "acceptance_criteria": json.dumps(criteria),
            "outputs": json.dumps(outputs) if outputs else None,
        }

    def test_passing_script_passes(self, tmp_path):
        (tmp_path / "scripts").mkdir()
        script = tmp_path / "scripts" / "verify.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        unit = self._unit(["when scripts/verify.sh runs, then it exits 0"])
        out = validate_output(
            _mock_result(text="done"),
            {"validators": ["ac_script"]}, str(tmp_path), unit=unit,
        )
        assert out.passed is True

    def test_failing_script_fails(self, tmp_path):
        (tmp_path / "scripts").mkdir()
        script = tmp_path / "scripts" / "verify.sh"
        script.write_text("#!/bin/bash\necho 'templates missing' >&2\nexit 3\n")
        unit = self._unit(["when scripts/verify.sh runs, then it exits 0"])
        out = validate_output(
            _mock_result(text="done"),
            {"validators": ["ac_script"]}, str(tmp_path), unit=unit,
        )
        assert out.passed is False
        assert "exit 3" in out.details[0]["message"]

    def test_referenced_script_missing_fails(self, tmp_path):
        unit = self._unit(["when scripts/verify.sh runs, then it exits 0"])
        out = validate_output(
            _mock_result(text="done"),
            {"validators": ["ac_script"]}, str(tmp_path), unit=unit,
        )
        assert out.passed is False
        assert "does not exist" in out.details[0]["message"]

    def test_prose_only_acs_pass_with_note(self, tmp_path):
        unit = self._unit(["The Board is satisfied with the tone"])
        out = validate_output(
            _mock_result(text="done"),
            {"validators": ["ac_script"]}, str(tmp_path), unit=unit,
        )
        assert out.passed is True
        assert "unverified" in out.details[0]["message"]

    def test_no_unit_context_passes(self, tmp_path):
        out = validate_output(
            _mock_result(text="done"),
            {"validators": ["ac_script"]}, str(tmp_path),
        )
        assert out.passed is True

    def test_vacuous_script_cannot_bless_empty_artifact(self, tmp_path):
        """The UNT-TOOLING case: script checks [ -d dir ] on an empty dir and
        exits 0 — the artifact floor must still fail the unit."""
        (tmp_path / "scripts").mkdir()
        script = tmp_path / "scripts" / "verify.sh"
        script.write_text("#!/bin/bash\n[ -d templates ] && exit 0\nexit 1\n")
        (tmp_path / "templates").mkdir()  # exists, but EMPTY
        unit = self._unit(
            ["when scripts/verify.sh runs, then it exits 0"],
            outputs=["templates"],
        )
        out = validate_output(
            _mock_result(text="done"),
            {"validators": ["ac_script"]}, str(tmp_path), unit=unit,
        )
        assert out.passed is False
        assert "empty directory" in out.details[0]["message"]

    def test_path_escaping_workspace_ignored(self, tmp_path):
        unit = self._unit(["run ../../outside/evil.sh to verify"])
        out = validate_output(
            _mock_result(text="done"),
            {"validators": ["ac_script"]}, str(tmp_path), unit=unit,
        )
        assert out.passed is True  # nothing runnable in-workspace → prose note

    def test_zero_work_script_passes_without_declared_outputs(self, tmp_path):
        """Documented limitation (tapir, 2026-07-13): a check script that
        exits 0 having verified nothing (gdUnit4 runtest.sh: 'No test cases
        found, abort test run!' + exit 0) passes ac_script when the unit
        declares no outputs. The mitigation is declaring outputs — the
        artifact floor catches what the script's opinion cannot. Fix the
        script; this test pins the boundary of what ac_script can know."""
        (tmp_path / "scripts").mkdir()
        script = tmp_path / "scripts" / "runtest.sh"
        script.write_text("#!/bin/bash\necho 'No test cases found'\nexit 0\n")
        unit = self._unit(["when scripts/runtest.sh runs, then it exits 0"])
        out = validate_output(
            _mock_result(text="done"),
            {"validators": ["ac_script"]}, str(tmp_path), unit=unit,
        )
        assert out.passed is True  # ac_script cannot know the script ran nothing

        # Same script, but the unit declares its deliverable: floor catches it.
        unit_with_outputs = self._unit(
            ["when scripts/runtest.sh runs, then it exits 0"],
            outputs=["reports/results.xml"],
        )
        out = validate_output(
            _mock_result(text="done"),
            {"validators": ["ac_script"]}, str(tmp_path), unit=unit_with_outputs,
        )
        assert out.passed is False
