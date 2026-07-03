"""Tests for firm.cli.pulse and session_pulse budget-health tag."""

from __future__ import annotations

import json
import sqlite3
from unittest import mock

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.hooks.session_pulse import render_budget_health
from firm.pulse.orchestrator import ActivationSummary


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fresh_conn(*, budget_members=False) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    return conn


def _add_contract(conn, contract_id, *, budget_config=None):
    return create(conn, "contract", {
        "id": contract_id,
        "firm_id": "chrisai",
        "name": f"Contract {contract_id}",
        "runtime_type": "claude_code",
        "budget_config": budget_config,
    })


def _add_member(conn, member_id, *, contract_id=None):
    return create(conn, "member", {
        "id": member_id,
        "firm_id": "chrisai",
        "name": f"Member {member_id}",
        "role": "worker",
        "status": "active",
        "contract_id": contract_id,
    })


def _add_budget_period(conn, bp_id, member_id, *, run_count=0, total_cost_usd=0.0):
    return create(conn, "budget_period", {
        "id": bp_id,
        "firm_id": "chrisai",
        "member_id": member_id,
        "period_start": "2026-04-01T00:00:00+00:00",
        "period_end": "2026-04-30T23:59:59+00:00",
        "run_count": run_count,
        "total_cost_usd": total_cost_usd,
        "status": "active",
    })


# ═══════════════════════════════════════════════════════════════════════════
# Budget-health tag
# ═══════════════════════════════════════════════════════════════════════════


class TestBudgetHealthTag:

    def test_renders_when_near_limit(self):
        conn = _fresh_conn()
        _add_contract(conn, "CON-001", budget_config={
            "limits": {"max_runs_per_period": 10, "max_total_cost_per_period_usd": 50.0},
        })
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_budget_period(conn, "BP-001", "MEM-001", run_count=9, total_cost_usd=42.0)

        result = render_budget_health(conn, "chrisai")
        assert result is not None
        assert "budget-health" in result
        assert "MEM-001" in result
        assert "90%" in result  # 9/10 = 90%
        assert "84%" in result  # 42/50 = 84%

    def test_silent_when_under_threshold(self):
        conn = _fresh_conn()
        _add_contract(conn, "CON-001", budget_config={
            "limits": {"max_runs_per_period": 100},
        })
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_budget_period(conn, "BP-001", "MEM-001", run_count=5)

        result = render_budget_health(conn, "chrisai")
        assert result is None

    def test_silent_when_no_budget_periods(self):
        conn = _fresh_conn()
        result = render_budget_health(conn, "chrisai")
        assert result is None

    def test_silent_when_no_budget_config(self):
        conn = _fresh_conn()
        _add_contract(conn, "CON-001")  # No budget_config
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_budget_period(conn, "BP-001", "MEM-001", run_count=99)

        result = render_budget_health(conn, "chrisai")
        assert result is None

    def test_multiple_members_only_shows_warnings(self):
        conn = _fresh_conn()
        _add_contract(conn, "CON-001", budget_config={
            "limits": {"max_runs_per_period": 10},
        })
        _add_contract(conn, "CON-002", budget_config={
            "limits": {"max_runs_per_period": 100},
        })
        _add_member(conn, "MEM-001", contract_id="CON-001")
        _add_member(conn, "MEM-002", contract_id="CON-002")
        _add_budget_period(conn, "BP-001", "MEM-001", run_count=9)  # 90% - warn
        _add_budget_period(conn, "BP-002", "MEM-002", run_count=5)  # 5% - silent

        result = render_budget_health(conn, "chrisai")
        assert result is not None
        assert "MEM-001" in result
        assert "MEM-002" not in result


# ═══════════════════════════════════════════════════════════════════════════
# CLI: run_pulse
# ═══════════════════════════════════════════════════════════════════════════


class TestRunPulseCli:

    @mock.patch("firm.cli.pulse.pulse")
    @mock.patch("firm.cli.pulse.connect")
    @mock.patch("firm.cli.pulse.get_db_path")
    def test_dry_run(self, mock_db_path, mock_connect, mock_pulse, tmp_path):
        from firm.cli.pulse import run_pulse

        mock_db_path.return_value = tmp_path / ".firm" / "firm.db"
        (tmp_path / ".firm").mkdir()
        (tmp_path / ".firm" / "firm.db").touch()
        mock_conn = mock.MagicMock()
        mock_connect.return_value = mock_conn
        mock_pulse.return_value = ActivationSummary(dry_run=True)

        exit_code = run_pulse(tmp_path, dry_run=True)

        assert exit_code == 0
        mock_pulse.assert_called_once()
        call_kwargs = mock_pulse.call_args
        assert call_kwargs.kwargs.get("dry_run") is True or call_kwargs[1].get("dry_run") is True

    @mock.patch("firm.cli.pulse.pulse")
    @mock.patch("firm.cli.pulse.connect")
    @mock.patch("firm.cli.pulse.get_db_path")
    def test_normal_run(self, mock_db_path, mock_connect, mock_pulse, tmp_path, capsys):
        from firm.cli.pulse import run_pulse

        mock_db_path.return_value = tmp_path / ".firm" / "firm.db"
        (tmp_path / ".firm").mkdir()
        (tmp_path / ".firm" / "firm.db").touch()
        mock_conn = mock.MagicMock()
        mock_connect.return_value = mock_conn
        mock_pulse.return_value = ActivationSummary(
            ran=[{"member": {"id": "MEM-001"}, "result": {"status": "completed"}}],
            skipped=[{"member": None, "reason": "test"}],
        )

        exit_code = run_pulse(tmp_path)

        assert exit_code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["ran"] == 1
        assert output["skipped"] == 1

    def test_db_not_found(self, tmp_path, capsys):
        from firm.cli.pulse import run_pulse

        exit_code = run_pulse(tmp_path)

        assert exit_code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is False
        assert output["reason"] == "db-not-found"

    def test_abort_no_processes(self, tmp_path, capsys):
        from firm.cli.pulse import run_pulse

        exit_code = run_pulse(tmp_path, abort=True)

        assert exit_code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["aborted"] == 0
