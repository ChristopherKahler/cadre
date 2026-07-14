"""Tests for firm.sched — the platform scheduler backends.

Every backend runs through ``run_cmd`` (mocked here), so what these tests pin
is the part that breaks in the field: the artifacts each backend writes
(unit files, plists, launcher scripts), the CLI argv it issues, and what its
``status()`` honestly reports back.
"""

from __future__ import annotations

import plistlib

import pytest

from firm.sched import resolve_scheduler
from firm.sched.base import interval_to_seconds
from firm.sched.launchd import LaunchdScheduler
from firm.sched.systemd import SystemdScheduler
from firm.sched.winsched import WindowsScheduler
import firm.sched.launchd as launchd_mod
import firm.sched.systemd as systemd_mod
import firm.sched.winsched as winsched_mod


@pytest.fixture
def ok_cmd(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake(argv, timeout=30):
        calls.append(tuple(argv))
        return 0, ""

    for mod in (systemd_mod, launchd_mod, winsched_mod):
        monkeypatch.setattr(mod, "run_cmd", fake)
    return calls


def test_interval_to_seconds():
    assert interval_to_seconds("90s") == 90
    assert interval_to_seconds("15m") == 900
    assert interval_to_seconds("15min") == 900
    assert interval_to_seconds("1h") == 3600
    assert interval_to_seconds("2d") == 172800
    for bad in ("", "30", "1h30m", "weekly"):
        with pytest.raises(ValueError):
            interval_to_seconds(bad)


def test_resolver_honors_env_override(monkeypatch):
    for forced, cls in (("systemd", SystemdScheduler),
                        ("launchd", LaunchdScheduler),
                        ("winsched", WindowsScheduler)):
        monkeypatch.setenv("CADRE_SCHEDULER", forced)
        assert isinstance(resolve_scheduler(), cls)


# ---------------------------------------------------------------------------
# systemd
# ---------------------------------------------------------------------------


def test_systemd_timer_artifacts_and_status(tmp_path, ok_cmd, monkeypatch):
    s = SystemdScheduler(unit_dir=tmp_path)
    s.install_timer("cadre-heartbeat-lab", description="d", workdir=tmp_path,
                    env={"FIRM_ID": "lab"}, argv=["py", "-m", "firm", "pulse"],
                    interval="30m")
    timer = (tmp_path / "cadre-heartbeat-lab.timer").read_text()
    assert "OnUnitActiveSec=30m" in timer
    assert ("systemctl", "--user", "enable", "--now",
            "cadre-heartbeat-lab.timer") in ok_cmd

    def fake(argv, timeout=30):
        if "is-active" in argv:
            return 0, "active"
        if "is-failed" in argv:
            return 1, ""
        return 0, ""
    monkeypatch.setattr(systemd_mod, "run_cmd", fake)
    st = s.status("cadre-heartbeat-lab")
    assert st["installed"] and st["state"] == "active" and not st["failed"]
    assert st["interval"] == "30m"
    assert s.list_installed("cadre-") == ["cadre-heartbeat-lab"]

    s2 = SystemdScheduler(unit_dir=tmp_path)
    monkeypatch.setattr(systemd_mod, "run_cmd", lambda a, timeout=30: (0, ""))
    s2.remove("cadre-heartbeat-lab")
    assert s2.list_installed("cadre-") == []


def test_systemd_service_has_restart_policy(tmp_path, ok_cmd):
    s = SystemdScheduler(unit_dir=tmp_path)
    s.install_service("cadre-rail", description="d", workdir=tmp_path,
                      env={}, argv=["py", "-m", "firm", "slack", "serve"])
    service = (tmp_path / "cadre-rail.service").read_text()
    assert "Restart=on-failure" in service


# ---------------------------------------------------------------------------
# launchd
# ---------------------------------------------------------------------------


def test_launchd_timer_plist(tmp_path, ok_cmd):
    s = LaunchdScheduler(agent_dir=tmp_path)
    s.install_timer("cadre-heartbeat-lab", description="d", workdir=tmp_path,
                    env={"FIRM_ID": "lab"}, argv=["py", "-m", "firm", "pulse"],
                    interval="30m")
    payload = plistlib.loads((tmp_path / "cadre-heartbeat-lab.plist").read_bytes())
    assert payload["Label"] == "cadre-heartbeat-lab"
    assert payload["StartInterval"] == 1800
    assert payload["ProgramArguments"] == ["py", "-m", "firm", "pulse"]
    assert payload["EnvironmentVariables"] == {"FIRM_ID": "lab"}
    assert any("bootstrap" in c for c in ok_cmd)

    st = s.status("cadre-heartbeat-lab")
    assert st["installed"] and st["interval"] == "1800s"
    assert s.list_installed("cadre-") == ["cadre-heartbeat-lab"]


def test_launchd_service_keeps_alive_on_failure(tmp_path, ok_cmd):
    s = LaunchdScheduler(agent_dir=tmp_path)
    s.install_service("cadre-rail", description="d", workdir=tmp_path,
                      env={}, argv=["py", "-m", "firm", "slack", "serve"])
    payload = plistlib.loads((tmp_path / "cadre-rail.plist").read_bytes())
    assert payload["KeepAlive"] == {"SuccessfulExit": False}
    assert payload["RunAtLoad"] is True


# ---------------------------------------------------------------------------
# windows
# ---------------------------------------------------------------------------


def test_winsched_timer_launcher_and_flags(tmp_path, ok_cmd):
    s = WindowsScheduler(launcher_dir=tmp_path)
    s.install_timer("cadre-heartbeat-lab", description="d", workdir=tmp_path,
                    env={"FIRM_ID": "lab"}, argv=["py", "-m", "firm", "pulse"],
                    interval="30m")
    launcher = (tmp_path / "cadre-heartbeat-lab.cmd").read_text(encoding="utf-8")
    assert 'set "FIRM_ID=lab"' in launcher
    assert "rem interval=30m" in launcher
    assert f'cd /d "{tmp_path}"' in launcher
    assert ":loop" not in launcher                      # timers don't supervise
    create = next(c for c in ok_cmd if "/Create" in c)
    assert "/SC" in create and "MINUTE" in create and "30" in create

    assert s.list_installed("cadre-") == ["cadre-heartbeat-lab"]


def test_winsched_schedule_flag_tiers():
    f = WindowsScheduler._schedule_flags
    assert f("90s") == ["/SC", "MINUTE", "/MO", "2"]     # rounds to the floor of 1m
    assert f("30m") == ["/SC", "MINUTE", "/MO", "30"]
    assert f("2h") == ["/SC", "HOURLY", "/MO", "2"]
    assert f("1d") == ["/SC", "DAILY", "/MO", "1"]


def test_winsched_service_launcher_supervises(tmp_path, ok_cmd):
    s = WindowsScheduler(launcher_dir=tmp_path)
    s.install_service("cadre-rail", description="d", workdir=tmp_path,
                      env={"CADRE_CLAUDE_BIN": "C:/claude"},
                      argv=["py", "-m", "firm", "slack", "serve"])
    launcher = (tmp_path / "cadre-rail.cmd").read_text(encoding="utf-8")
    assert ":loop" in launcher and "goto loop" in launcher
    create = next(c for c in ok_cmd if "/Create" in c)
    assert "ONLOGON" in create
