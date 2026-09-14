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
    # "Every command succeeds" includes the headless self-test a Windows install
    # runs first (#119 A7). Tests that exercise that self-test replace this.
    monkeypatch.setattr(winsched_mod, "_headless_self_test",
                        lambda: (True, ""), raising=False)
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


# ---------------------------------------------------------------------------
# D1a (#119): the task runs its launcher under a console nobody can see
# ---------------------------------------------------------------------------
#
# A task pointed straight at a .cmd gets a console, and a console gets a
# window: every pulse drew one on the operator's desktop and closing it killed
# the pulse. The task command is now conhost.exe --headless running cmd.exe on
# the launcher. Before a single file is written, the install proves the flag
# works on this machine (A7) and that the command fits schtasks' cap; the
# launcher sends every byte of output to a log file so a headless console is
# never left full (A6). Nothing here starts conhost: the self-test is faked.
# The real run belongs to M-W1, on a private desktop first (verdict A1).

_ROOT = "C:\\Windows"


def _install(kind, s, tmp_path):
    common = dict(description="d", workdir=tmp_path, env={"FIRM_ID": "lab"},
                  argv=["py", "-m", "firm", "pulse"])
    if kind == "timer":
        return s.install_timer("cadre-heartbeat-lab", interval="30m", **common)
    return s.install_service("cadre-heartbeat-lab", **common)


def _tr(create):
    return create[create.index("/TR") + 1]


@pytest.mark.parametrize("kind", ["timer", "service"])
def test_winsched_task_runs_its_launcher_under_a_headless_console(
        tmp_path, ok_cmd, monkeypatch, kind):
    monkeypatch.setenv("SystemRoot", _ROOT)
    s = WindowsScheduler(launcher_dir=tmp_path)
    _install(kind, s, tmp_path)
    launcher = tmp_path / "cadre-heartbeat-lab.cmd"
    create = next(c for c in ok_cmd if "/Create" in c)
    # The root comes from the same SystemRoot lookup; every other character of
    # the command is this test's own. (On Linux the join is a forward slash.)
    sys32 = winsched_mod._system32()
    assert str(sys32).startswith(_ROOT), sys32
    assert _tr(create) == (f'"{sys32 / "conhost.exe"}" --headless '
                           f'"{sys32 / "cmd.exe"}" /d /c "{launcher}"'), _tr(create)


@pytest.mark.parametrize("kind", ["timer", "service"])
def test_winsched_launcher_sends_every_command_line_to_its_log(
        tmp_path, ok_cmd, monkeypatch, kind):
    monkeypatch.setenv("SystemRoot", _ROOT)
    s = WindowsScheduler(launcher_dir=tmp_path)
    _install(kind, s, tmp_path)
    text = (tmp_path / "cadre-heartbeat-lab.cmd").read_text(encoding="utf-8")
    runs = [ln for ln in text.splitlines() if "firm pulse" in ln]
    log = tmp_path / "cadre-heartbeat-lab.log"
    assert runs, text
    for ln in runs:
        assert ln.endswith(f'> "{log}" 2>&1'), (
            f"a command line writes to the console, not the log: {ln!r}")


@pytest.mark.parametrize("kind", ["timer", "service"])
def test_winsched_refuses_a_task_command_past_the_schtasks_cap(
        tmp_path, ok_cmd, monkeypatch, kind):
    monkeypatch.setenv("SystemRoot", _ROOT)
    deep = tmp_path / ("d" * 200)
    s = WindowsScheduler(launcher_dir=deep)
    with pytest.raises(winsched_mod.SchedulerError) as caught:
        _install(kind, s, tmp_path)
    assert "261" in str(caught.value), str(caught.value)
    assert not [c for c in ok_cmd if "/Create" in c], "a task was created anyway"
    assert not deep.exists(), "a launcher was written for a task never created"


@pytest.mark.parametrize("kind", ["timer", "service"])
def test_winsched_refuses_to_install_when_the_headless_self_test_fails(
        tmp_path, ok_cmd, monkeypatch, kind):
    monkeypatch.setenv("SystemRoot", _ROOT)
    monkeypatch.setattr(
        winsched_mod, "_headless_self_test",
        lambda: (False, "conhost.exe --headless returned 0, expected 7"))
    s = WindowsScheduler(launcher_dir=tmp_path / "sched")
    with pytest.raises(winsched_mod.SchedulerError) as caught:
        _install(kind, s, tmp_path)
    message = str(caught.value)
    assert "returned 0, expected 7" in message, message
    assert "pythonw" in message, "the named fallback is missing from the refusal"
    assert not [c for c in ok_cmd if "/Create" in c], "a task was created anyway"
    assert not (tmp_path / "sched").exists(), "files were written anyway"


def test_winsched_remove_takes_the_launcher_log_too(tmp_path, ok_cmd):
    s = WindowsScheduler(launcher_dir=tmp_path / "sched")
    (tmp_path / "sched").mkdir()
    (tmp_path / "sched" / "cadre-heartbeat-lab.cmd").write_text("@echo off\r\n")
    (tmp_path / "sched" / "cadre-heartbeat-lab.log").write_text("pulse output\n")
    s.remove("cadre-heartbeat-lab")
    assert not (tmp_path / "sched" / "cadre-heartbeat-lab.log").exists()


class _SelfTestRun:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return type("P", (), {"returncode": self.result})()


def _system32_with_conhost(tmp_path, monkeypatch, present=True):
    sys32 = tmp_path / "System32"
    sys32.mkdir()
    if present:
        (sys32 / "conhost.exe").write_bytes(b"MZ")
    monkeypatch.setattr(winsched_mod, "_system32", lambda: sys32,
                        raising=False)
    return sys32


def test_headless_self_test_passes_only_when_the_exit_code_comes_back(
        tmp_path, monkeypatch):
    import subprocess as sp

    sys32 = _system32_with_conhost(tmp_path, monkeypatch)
    run = _SelfTestRun(7)
    assert winsched_mod._headless_self_test(run=run) == (True, "")
    argv, kwargs = run.calls[0]
    assert argv == [str(sys32 / "conhost.exe"), "--headless",
                    str(sys32 / "cmd.exe"), "/d", "/c", "exit 7"]
    for stream in ("stdin", "stdout", "stderr"):
        assert kwargs.get(stream) is sp.DEVNULL, (stream, kwargs.get(stream))
    assert kwargs.get("timeout"), "a self-test that can hang the install"
    if hasattr(sp, "STARTUPINFO"):
        si = kwargs.get("startupinfo")
        assert si is not None and si.dwFlags & sp.STARTF_USESHOWWINDOW \
            and si.wShowWindow == 0, "the self-test is not started hidden"


@pytest.mark.parametrize("result, words", [
    (0, "returned 0, expected 7"),
    (1, "returned 1, expected 7"),
    (OSError("not found"), "could not start"),
    (__import__("subprocess").TimeoutExpired("conhost", 30), "did not finish"),
])
def test_headless_self_test_fails_on_anything_but_the_exit_code(
        tmp_path, monkeypatch, result, words):
    _system32_with_conhost(tmp_path, monkeypatch)
    ok, why = winsched_mod._headless_self_test(run=_SelfTestRun(result))
    assert ok is False and words in why, why


def test_headless_self_test_fails_when_conhost_is_missing(tmp_path, monkeypatch):
    sys32 = _system32_with_conhost(tmp_path, monkeypatch, present=False)
    run = _SelfTestRun(7)
    ok, why = winsched_mod._headless_self_test(run=run)
    assert ok is False and str(sys32 / "conhost.exe") in why, why
    assert run.calls == [], "ran a conhost that is not there"


# A real `schtasks /Query /TN <task> /FO LIST /V` block, captured on Windows 10
# 19045 during issue #119 (lane F arm B). Every key, the spacing and the line
# order are verbatim. Only the host name, the account and the launcher path
# are replaced, because they identify the machine and are not what status()
# reads. Status / Last Run Time / Last Result are filled per case below.
_WINSCHED_V_BLOCK = r"""
Folder: \Cadre
HostName:                             WINHOST
TaskName:                             \Cadre\cadre-heartbeat-lab
Next Run Time:                        9/14/2026 12:52:00 PM
Status:                               {status}
Logon Mode:                           Interactive only
Last Run Time:                        {last_run}
Last Result:                          {last_result}
Author:                               WINHOST\operator
Task To Run:                          "C:\Users\operator\.cadre\sched\cadre-heartbeat-lab.cmd"
Start In:                             N/A
Comment:                              N/A
Scheduled Task State:                 Enabled
Idle Time:                            Disabled
Power Management:                     Stop On Battery Mode, No Start On Batteries
Run As User:                          operator
Delete Task If Not Rescheduled:       Disabled
Stop Task If Runs X Hours and X Mins: 72:00:00
Schedule:                             Scheduling data is not available in this format.
Schedule Type:                        One Time Only, Minute
Start Time:                           12:48:00 PM
Start Date:                           9/14/2026
End Date:                             N/A
Days:                                 N/A
Months:                               N/A
Repeat: Every:                        0 Hour(s), 1 Minute(s)
Repeat: Until: Time:                  None
Repeat: Until: Duration:              Disabled
Repeat: Stop If Still Running:        Disabled
"""


# (Status, Last Run Time, Last Result) exactly as schtasks printed them, each
# read off a live task in lane F arms B and E, then what status() must say.
_WINSCHED_STATUS_CASES = [
    pytest.param("Ready", "11/30/1999 12:00:00 AM", "267011",
                 {"failed": False, "never_run": True, "dropped_tick": False,
                  "last_result": 0x41303},
                 "last_fire", id="never-run-sentinel"),
    pytest.param("Ready", "9/14/2026 12:51:00 PM", "0",
                 {"failed": False, "never_run": False, "dropped_tick": False,
                  "last_result": 0, "last_fire": "9/14/2026 12:51:00 PM"},
                 None, id="finished-ok"),
    pytest.param("Running", "9/14/2026 1:08:01 PM", "267009",
                 {"failed": False, "state": "running", "never_run": False,
                  "dropped_tick": False, "last_result": 0x41301},
                 None, id="running-is-not-a-failure"),
    pytest.param("Running", "9/14/2026 1:10:00 PM", "-2147020576",
                 {"failed": False, "dropped_tick": True,
                  "last_result": 0x800710E0},
                 None, id="trigger-refused-while-running"),
    pytest.param("Ready", "9/14/2026 1:11:00 PM", "-1073741510",
                 {"failed": True, "dropped_tick": False,
                  "last_result": 0xC000013A},
                 None, id="console-closed-is-a-failure"),
]


@pytest.mark.parametrize("status_text,last_run,last_result,expected,absent",
                         _WINSCHED_STATUS_CASES)
def test_winsched_status_tells_running_never_run_and_dropped_apart(
        tmp_path, monkeypatch, status_text, last_run, last_result, expected,
        absent):
    """status() used to report a normally running pulse as failed (267009),
    a never-run task as having fired in 1999, and a dropped tick not at all."""
    block = _WINSCHED_V_BLOCK.format(status=status_text, last_run=last_run,
                                     last_result=last_result)

    def fake(argv, timeout=30):
        return (0, block) if "/Query" in argv else (0, "")

    monkeypatch.setattr(winsched_mod, "run_cmd", fake)
    st = WindowsScheduler(launcher_dir=tmp_path).status("cadre-heartbeat-lab")

    assert st["installed"] is True
    for key, value in expected.items():
        assert st.get(key) == value, (key, st)
    if absent:
        assert absent not in st, st


def test_winsched_status_unreadable_result_fails_toward_failed(tmp_path,
                                                               monkeypatch):
    """A Last Result that is not a number must never read as healthy.

    Synthetic input on purpose: this is the branch for output nobody has seen
    yet, so no captured example of it can exist."""
    block = _WINSCHED_V_BLOCK.format(status="Ready",
                                     last_run="9/14/2026 12:51:00 PM",
                                     last_result="not-a-number")
    monkeypatch.setattr(winsched_mod, "run_cmd",
                        lambda argv, timeout=30: (0, block))
    st = WindowsScheduler(launcher_dir=tmp_path).status("cadre-heartbeat-lab")
    assert st["failed"] is True
    assert st["last_result"] == "not-a-number"


# remove() and the shared Task Scheduler folder. Every firm's task lives in the
# same folder, so the folder may only go when nothing is left in it. Measured
# during #119: schtasks /Delete cannot remove a folder at all (rc 1, folder
# still there) and a folder query answers rc 0 with no rows while an EMPTY
# folder still exists; only rc 1 means it is gone.
_CADRE_FOLDER = "\\Cadre"


def _scripted_remove(monkeypatch, *, folder_reply, folder_query_rc=1):
    calls: list[list[str]] = []

    def fake(argv, timeout=30):
        calls.append(list(argv))
        if argv[0] == "schtasks" and "/Query" in argv:
            return folder_query_rc, ""
        if argv[0].lower().startswith("powershell"):
            return folder_reply
        return 0, ""

    monkeypatch.setattr(winsched_mod, "run_cmd", fake)
    return calls


def test_winsched_remove_deletes_the_folder_when_it_is_empty(tmp_path,
                                                             monkeypatch):
    calls = _scripted_remove(monkeypatch, folder_reply=(0, "deleted"))
    launchers = tmp_path / "sched"
    launchers.mkdir()
    (launchers / "cadre-heartbeat-lab.cmd").write_text("x", encoding="utf-8")

    out = WindowsScheduler(launcher_dir=launchers).remove("cadre-heartbeat-lab")

    assert out["folder"] == {"path": _CADRE_FOLDER, "action": "deleted",
                             "verified_gone": True}
    assert not launchers.exists()              # its last launcher was removed
    query = next(c for c in calls if c[0] == "schtasks" and "/Query" in c)
    assert query[query.index("/TN") + 1] == _CADRE_FOLDER + "\\"


def test_winsched_remove_keeps_a_folder_another_firm_still_uses(tmp_path,
                                                                monkeypatch):
    calls = _scripted_remove(monkeypatch, folder_reply=(0, "kept 1"))
    launchers = tmp_path / "sched"
    launchers.mkdir()
    (launchers / "cadre-heartbeat-lab.cmd").write_text("x", encoding="utf-8")
    (launchers / "cadre-heartbeat-other.cmd").write_text("x", encoding="utf-8")

    out = WindowsScheduler(launcher_dir=launchers).remove("cadre-heartbeat-lab")

    assert out["folder"] == {"path": _CADRE_FOLDER, "action": "kept",
                             "tasks_remaining": 1}
    assert (launchers / "cadre-heartbeat-other.cmd").exists()
    assert not any(c[0] == "schtasks" and "/Query" in c for c in calls)


def test_winsched_remove_says_so_when_the_folder_did_not_go(tmp_path,
                                                            monkeypatch):
    _scripted_remove(monkeypatch, folder_reply=(0, "deleted"),
                     folder_query_rc=0)
    out = WindowsScheduler(launcher_dir=tmp_path).remove("cadre-heartbeat-lab")
    assert out["folder"]["verified_gone"] is False


def test_winsched_remove_reports_an_unreadable_folder_answer(tmp_path,
                                                             monkeypatch):
    _scripted_remove(monkeypatch,
                     folder_reply=(1, "Exception calling GetFolder"))
    out = WindowsScheduler(launcher_dir=tmp_path).remove("cadre-heartbeat-lab")
    assert out["folder"]["action"] == "unknown"
    assert "Exception calling GetFolder" in out["folder"]["detail"]


def test_winsched_remove_with_the_folder_already_gone(tmp_path, monkeypatch):
    calls = _scripted_remove(monkeypatch, folder_reply=(0, "absent"))
    out = WindowsScheduler(launcher_dir=tmp_path).remove("cadre-heartbeat-lab")
    assert out["folder"] == {"path": _CADRE_FOLDER, "action": "absent"}
    assert not any(c[0] == "schtasks" and "/Query" in c for c in calls)


def test_unit_files_are_utf8_whatever_the_locale_is(tmp_path, monkeypatch):
    """Unit files are UTF-8 by specification; the locale codec is not.

    description and workdir come from the firm, so an accented firm name or a
    home directory with one is ordinary input. write_text with no encoding
    encodes through the platform locale, which is cp1252 on Windows.

    Measured with the encoding argument removed: Windows raises
    UnicodeEncodeError on U+014C and the test fails; Linux passes either way,
    because CPython coerces the C locale to UTF-8 (PEP 540) so there is no
    red arm to be had there. The Windows leg is what makes this test mean
    something, which is one more reason the -k filter had to go.

    Asserting on the bytes rather than on read_text matters: read_text would
    decode with the same wrong codec that wrote them and agree with itself.
    """
    monkeypatch.setattr(systemd_mod.SystemdScheduler, "_ctl",
                        lambda self, *a: (0, ""))
    s = SystemdScheduler(unit_dir=tmp_path)
    name = "Cadre \u2014 Zo\u00eb \u014ctani nightly \u2192 pulse"
    s.install_timer("enc-probe", description=name, workdir=tmp_path,
                    env={"FIRM_ID": "chrisai"}, argv=["/bin/true"],
                    interval="1h")

    for suffix in (".service", ".timer"):
        raw = (tmp_path / ("enc-probe" + suffix)).read_bytes()
        assert name in raw.decode("utf-8"), (
            suffix + " was not written as UTF-8: " + repr(raw[:200]))
