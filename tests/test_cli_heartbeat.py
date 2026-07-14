"""Tests for firm.cli.heartbeat — pulse cadence through the platform scheduler.

The CLI's own logic (env capture, DB schedule record, emit shapes) is tested
against the systemd backend with the scheduler CLI mocked at ``run_cmd`` —
the same seam every backend runs through (see test_sched.py for the
backend-by-backend behavior).
"""

from __future__ import annotations

import json

import pytest

import firm.cli.heartbeat as hb
import firm.sched.systemd as sysd


def _workspace_with_db(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".firm").mkdir(parents=True)
    (ws / ".firm" / "firm.db").touch()
    return ws


@pytest.fixture
def ctl(monkeypatch):
    """Record every scheduler CLI call; systemctl always succeeds."""
    calls: list[tuple[str, ...]] = []

    def fake(argv, timeout=30):
        calls.append(tuple(argv))
        return 0, ""

    monkeypatch.setattr(sysd, "run_cmd", fake)
    return calls


def test_validate_interval_accepts_simple_spans():
    for good in ("30m", "1h", "90s", "2d", "15min"):
        assert hb.validate_interval(good) == good


def test_validate_interval_rejects_garbage():
    for bad in ("", "30", "m30", "1h30m", "monthly", "30 m"):
        with pytest.raises(ValueError):
            hb.validate_interval(bad)


def test_capture_env_process_wins_over_dotenv(tmp_path, monkeypatch):
    ws = _workspace_with_db(tmp_path)
    (ws / ".env").write_text(
        'CADRE_TELEGRAM_TOKEN="file-token"\nCADRE_SLACK_TOKEN=file-slack\n'
    )
    monkeypatch.setenv("CADRE_TELEGRAM_TOKEN", "process-token")
    monkeypatch.delenv("CADRE_SLACK_TOKEN", raising=False)
    monkeypatch.delenv("CADRE_NOTIFY_WEBHOOK", raising=False)

    env = hb.capture_env(ws, "lab", "/usr/bin/claude")

    assert env["CADRE_TELEGRAM_TOKEN"] == "process-token"
    assert env["CADRE_SLACK_TOKEN"] == "file-slack"
    assert "CADRE_NOTIFY_WEBHOOK" not in env
    assert env["FIRM_ID"] == "lab"
    assert env["CADRE_CLAUDE_BIN"] == "/usr/bin/claude"


def test_enable_writes_units_and_starts_timer(tmp_path, capsys, monkeypatch, ctl):
    ws = _workspace_with_db(tmp_path)
    unit_dir = tmp_path / "units"
    monkeypatch.setattr(
        hb, "resolve_claude_bin", lambda: ("/usr/bin/claude", "test"),
    )

    rc = hb.run_enable(ws, "lab", "15m", unit_dir=unit_dir)

    assert rc == 0
    service = (unit_dir / "cadre-heartbeat-lab.service").read_text()
    timer = (unit_dir / "cadre-heartbeat-lab.timer").read_text()
    assert f"WorkingDirectory={ws}" in service
    assert 'Environment="CADRE_CLAUDE_BIN=/usr/bin/claude"' in service
    assert 'Environment="FIRM_ID=lab"' in service
    assert f"-m firm pulse --workspace {ws} --firm-id lab" in service
    assert "OnUnitActiveSec=15m" in timer
    assert ("systemctl", "--user", "daemon-reload") in ctl
    assert ("systemctl", "--user", "enable", "--now",
            "cadre-heartbeat-lab.timer") in ctl
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["interval"] == "15m"
    assert out["scheduler"] == "systemd"


def test_enable_fails_without_db(tmp_path, capsys, ctl):
    rc = hb.run_enable(tmp_path, "lab", "15m", unit_dir=tmp_path / "units")
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "db-not-found"


def test_enable_fails_on_bad_interval(tmp_path, capsys, ctl):
    ws = _workspace_with_db(tmp_path)
    rc = hb.run_enable(ws, "lab", "whenever", unit_dir=tmp_path / "units")
    assert rc == 1
    assert "invalid interval" in json.loads(capsys.readouterr().out)["reason"]


def test_enable_fails_without_claude(tmp_path, capsys, monkeypatch, ctl):
    ws = _workspace_with_db(tmp_path)
    monkeypatch.setattr(hb, "resolve_claude_bin", lambda: (None, "not wired"))
    rc = hb.run_enable(ws, "lab", "15m", unit_dir=tmp_path / "units")
    assert rc == 1
    assert "not wired" in json.loads(capsys.readouterr().out)["reason"]


def test_enable_surfaces_scheduler_failure(tmp_path, capsys, monkeypatch):
    ws = _workspace_with_db(tmp_path)
    monkeypatch.setattr(sysd, "run_cmd", lambda argv, timeout=30: (1, "no user bus"))
    monkeypatch.setattr(
        hb, "resolve_claude_bin", lambda: ("/usr/bin/claude", "test"),
    )
    rc = hb.run_enable(ws, "lab", "15m", unit_dir=tmp_path / "units")
    assert rc == 1
    assert "no user bus" in json.loads(capsys.readouterr().out)["reason"]


def test_disable_removes_units(tmp_path, capsys, ctl):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "cadre-heartbeat-lab.timer").write_text("t")
    (unit_dir / "cadre-heartbeat-lab.service").write_text("s")

    rc = hb.run_disable("lab", unit_dir=unit_dir)

    assert rc == 0
    assert not (unit_dir / "cadre-heartbeat-lab.timer").exists()
    assert not (unit_dir / "cadre-heartbeat-lab.service").exists()
    assert ("systemctl", "--user", "disable", "--now",
            "cadre-heartbeat-lab.timer") in ctl


def test_disable_unknown_firm_fails(tmp_path, capsys, ctl):
    rc = hb.run_disable("ghost", unit_dir=tmp_path)
    assert rc == 1
    assert "no heartbeat installed" in json.loads(capsys.readouterr().out)["reason"]


def test_status_reports_installed_timers(tmp_path, capsys, monkeypatch):
    ws = _workspace_with_db(tmp_path)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "cadre-heartbeat-lab.timer").write_text(
        "[Timer]\nOnUnitActiveSec=15m\n")
    (unit_dir / "cadre-heartbeat-lab.service").write_text(
        f"[Service]\nWorkingDirectory={ws}\n"
    )
    (ws / ".firm" / "last-pulse.json").write_text("{}")

    def fake(argv, timeout=30):
        if "is-active" in argv:
            return 0, "active"
        if "is-failed" in argv:
            return 1, "active"
        if "show" in argv:
            return 0, (
                "NextElapseUSecRealtime=Fri 2026-07-10 12:00:00 CDT\n"
                "LastTriggerUSec=Fri 2026-07-10 11:30:00 CDT"
            )
        return 0, ""

    monkeypatch.setattr(sysd, "run_cmd", fake)

    rc = hb.run_status(unit_dir=unit_dir)

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    entry = out["heartbeats"][0]
    assert entry["firm_id"] == "lab"
    assert entry["state"] == "active"
    assert entry["workspace"] == str(ws)
    assert "last_pulse" in entry
    assert "next_fire" in entry
    assert entry.get("scheduler") == "systemd"


def test_status_empty_ok(tmp_path, capsys, ctl):
    rc = hb.run_status(unit_dir=tmp_path)
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["heartbeats"] == []
