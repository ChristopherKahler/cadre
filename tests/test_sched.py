"""Tests for firm.sched — the platform scheduler backends.

Every backend runs through ``run_cmd`` (mocked here), so what these tests pin
is the part that breaks in the field: the artifacts each backend writes
(unit files, plists, launcher scripts), the CLI argv it issues, and what its
``status()`` honestly reports back.
"""

from __future__ import annotations

import json
import plistlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from firm.sched import resolve_scheduler, winlaunch
from firm.sched.base import interval_to_seconds
from firm.sched.launchd import LaunchdScheduler
from firm.sched.systemd import SystemdScheduler
from firm.sched.winsched import WindowsScheduler
import firm.sched.launchd as launchd_mod
import firm.sched.systemd as systemd_mod
import firm.sched.winsched as winsched_mod

# The pythonw.exe an install resolves, wherever a test needs one to exist.
_PYTHONW = "C:\\Users\\operator\\cadre\\.venv\\Scripts\\pythonw.exe"


@pytest.fixture
def ok_cmd(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake(argv, timeout=30):
        calls.append(tuple(argv))
        return 0, ""

    for mod in (systemd_mod, launchd_mod, winsched_mod):
        monkeypatch.setattr(mod, "run_cmd", fake)
    # "Every command succeeds" includes the two things a Windows install checks
    # before it writes anything (#119, R3 and R4): a pythonw.exe beside the
    # installing Python, and the self-test that runs the launcher on it. Tests
    # that exercise either replace it.
    monkeypatch.setattr(winsched_mod, "_resolve_pythonw",
                        lambda: Path(_PYTHONW), raising=False)
    monkeypatch.setattr(winsched_mod, "_pythonw_self_test",
                        lambda pythonw: (True, ""), raising=False)
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


def _launcher_spec(directory, stem="cadre-heartbeat-lab"):
    return json.loads((directory / f"{stem}.json").read_text(encoding="utf-8"))


def test_winsched_timer_launcher_and_flags(tmp_path, ok_cmd):
    s = WindowsScheduler(launcher_dir=tmp_path)
    s.install_timer("cadre-heartbeat-lab", description="d", workdir=tmp_path,
                    env={"FIRM_ID": "lab"}, argv=["py", "-m", "firm", "pulse"],
                    interval="30m")
    assert _launcher_spec(tmp_path) == {
        "stem": "cadre-heartbeat-lab", "argv": ["py", "-m", "firm", "pulse"],
        "env": {"FIRM_ID": "lab"}, "cwd": str(tmp_path),
        "supervise": False,                             # timers don't supervise
        "interval": "30m"}
    assert (tmp_path / "cadre-heartbeat-lab.pyw").is_file()
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
    spec = _launcher_spec(tmp_path, "cadre-rail")
    assert spec["supervise"] is True and "interval" not in spec, spec
    assert spec["env"] == {"CADRE_CLAUDE_BIN": "C:/claude"}
    create = next(c for c in ok_cmd if "/Create" in c)
    assert "ONLOGON" in create


# ---------------------------------------------------------------------------
# D1a (#119, as ruled): the task runs pythonw.exe on the launcher's stub
# ---------------------------------------------------------------------------
#
# A task pointed at a console program gets a console, and a console gets a
# window: every pulse drew one on the operator's desktop and closing it killed
# the pulse. conhost.exe --headless hid the window but was measured throwing
# the exit code away, so the task command is now the pythonw.exe beside the
# installing Python, on a stub written by firm.sched.winlaunch (whose own
# tests are tests/test_winlaunch.py). Before a single file is written, an
# install refuses when there is no pythonw.exe (R4), runs the launcher on this
# machine and requires exit code 7 and the command's marker back (R3), and
# refuses a task command past schtasks' cap (R7). Nothing here starts pythonw:
# the self-test's runner is faked. The real run belongs to the window
# instrument, on a private desktop first.

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
def test_winsched_task_runs_its_stub_on_pythonw(tmp_path, ok_cmd, kind):
    s = WindowsScheduler(launcher_dir=tmp_path)
    _install(kind, s, tmp_path)
    create = next(c for c in ok_cmd if "/Create" in c)
    stub = tmp_path / "cadre-heartbeat-lab.pyw"
    assert _tr(create) == f'"{_PYTHONW}" "{stub}"', _tr(create)


@pytest.mark.parametrize("kind", ["timer", "service"])
def test_winsched_writes_the_launchers_own_stub_beside_its_spec_and_log(
        tmp_path, ok_cmd, kind):
    s = WindowsScheduler(launcher_dir=tmp_path)
    _install(kind, s, tmp_path)
    stub = tmp_path / "cadre-heartbeat-lab.pyw"
    assert stub.read_text(encoding="utf-8") == winlaunch.stub_text(
        tmp_path / "cadre-heartbeat-lab.json",
        tmp_path / "cadre-heartbeat-lab.log")


@pytest.mark.parametrize("kind", ["timer", "service"])
def test_winsched_refuses_a_task_command_past_the_schtasks_cap(
        tmp_path, ok_cmd, kind):
    deep = tmp_path / ("d" * 200)
    s = WindowsScheduler(launcher_dir=deep)
    with pytest.raises(winsched_mod.SchedulerError) as caught:
        _install(kind, s, tmp_path)
    assert "261" in str(caught.value), str(caught.value)
    assert not [c for c in ok_cmd if "/Create" in c], "a task was created anyway"
    assert not deep.exists(), "a launcher was written for a task never created"


def test_winsched_task_command_fits_the_cap_on_a_long_real_path():
    """R7 by computation; the cap itself is for a live schtasks leg to measure.

    A venv under an account name of 20 characters, the longest a local
    account's logon name can be, and a firm id of 64 characters: 197
    characters, under the 261 schtasks takes."""
    user = "u" * 20
    pythonw = Path(f"C:\\Users\\{user}\\cadre-win\\.venv\\Scripts\\pythonw.exe")
    stub = Path(f"C:\\Users\\{user}\\.cadre\\sched\\"
                f"cadre-heartbeat-{'f' * 64}.pyw")
    tr = winsched_mod._task_command(pythonw, stub)
    assert tr == f'"{pythonw}" "{stub}"'
    assert len(tr) == 197 and len(tr) <= winsched_mod._TR_CAP, len(tr)


@pytest.mark.parametrize("kind", ["timer", "service"])
def test_winsched_refuses_to_install_when_the_pythonw_self_test_fails(
        tmp_path, ok_cmd, monkeypatch, kind):
    why = (f"{_PYTHONW} returned 0, expected 7; the test command never ran; "
           "the launcher's log: no log was written")
    monkeypatch.setattr(winsched_mod, "_pythonw_self_test",
                        lambda pythonw: (False, why))
    s = WindowsScheduler(launcher_dir=tmp_path / "sched")
    with pytest.raises(winsched_mod.SchedulerError) as caught:
        _install(kind, s, tmp_path)
    assert why in str(caught.value), str(caught.value)
    assert not [c for c in ok_cmd if "/Create" in c], "a task was created anyway"
    assert not (tmp_path / "sched").exists(), "files were written anyway"


def _a_python_install(tmp_path, *names):
    scripts = tmp_path / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    for name in names:
        (scripts / name).write_bytes(b"MZ")
    return scripts


@pytest.mark.parametrize("kind", ["timer", "service"])
def test_winsched_refuses_to_install_without_pythonw_beside_the_installing_python(
        tmp_path, monkeypatch, kind):
    scripts = _a_python_install(tmp_path, "python.exe")
    monkeypatch.setattr(sys, "executable", str(scripts / "python.exe"))
    commands, self_tests = [], []
    monkeypatch.setattr(winsched_mod, "run_cmd",
                        lambda argv, timeout=30: commands.append(argv) or (0, ""))
    monkeypatch.setattr(winsched_mod, "_pythonw_self_test",
                        lambda pythonw: self_tests.append(pythonw) or (True, ""),
                        raising=False)
    s = WindowsScheduler(launcher_dir=tmp_path / "sched")
    with pytest.raises(winsched_mod.SchedulerError) as caught:
        _install(kind, s, tmp_path)
    assert str(scripts / "pythonw.exe") in str(caught.value), str(caught.value)
    assert self_tests == [], "the self-test ran with no pythonw.exe to run"
    assert commands == [], f"scheduler commands ran anyway: {commands}"
    assert not (tmp_path / "sched").exists(), "files were written anyway"


def test_resolve_pythonw_is_the_one_beside_the_installing_python(tmp_path,
                                                                 monkeypatch):
    scripts = _a_python_install(tmp_path, "python.exe", "pythonw.exe")
    monkeypatch.setattr(sys, "executable", str(scripts / "python.exe"))
    assert winsched_mod._resolve_pythonw() == scripts / "pythonw.exe"


def test_winsched_remove_takes_every_launcher_file(tmp_path, ok_cmd):
    launchers = tmp_path / "sched"
    launchers.mkdir()
    mine = ["cadre-heartbeat-lab.pyw", "cadre-heartbeat-lab.json",
            "cadre-heartbeat-lab.log",
            # What cadre wrote before #119, still there on an upgraded install.
            "cadre-heartbeat-lab.cmd",
            # #141's containment record. This leg's NAME says every launcher
            # file, and its input did not include this one, so it could not
            # fail for the reason it claims (avocet, #146 FINDING 4).
            "cadre-heartbeat-lab.containment.json"]
    theirs = ["cadre-heartbeat-other.json", "cadre-heartbeat-other.pyw"]
    for name in mine + theirs:
        (launchers / name).write_text("x", encoding="utf-8")

    out = WindowsScheduler(launcher_dir=launchers).remove("cadre-heartbeat-lab")

    for name in mine:
        assert not (launchers / name).exists(), f"{name} was left behind"
        assert name in out["removed"], out["removed"]
    assert sorted(p.name for p in launchers.iterdir()) == theirs


class _FakeLauncher:
    """pythonw.exe running the self-test's stub, faked.

    It does what the stub's command would do, as far as the self-test can see:
    it carries out the batch file's own marker line in the command's working
    directory, then exits with the code it was given. What it saw is kept,
    including the directory, so a test can check the self-test cleans up.
    """

    def __init__(self, returncode=7, marker=True, raises=None):
        self.returncode, self.marker, self.raises = returncode, marker, raises
        self.calls = []

    def __call__(self, argv, **kwargs):
        stub = Path(argv[1])
        spec = json.loads(stub.with_suffix(".json").read_text(encoding="utf-8"))
        batch = Path(spec["argv"][-1]).read_bytes()
        self.calls.append({"argv": argv, "kwargs": kwargs, "spec": spec,
                           "batch": batch, "dir": stub.parent,
                           "stub_text": stub.read_text(encoding="utf-8")})
        if self.raises is not None:
            raise self.raises
        if self.marker:
            for line in batch.decode("ascii").splitlines():
                if line.startswith("echo ran> "):
                    target = Path(spec["cwd"]) / line[len("echo ran> "):]
                    target.write_text("ran", encoding="utf-8")
        return SimpleNamespace(returncode=self.returncode)


def test_pythonw_self_test_runs_the_launcher_hidden_and_needs_7_and_the_marker(
        tmp_path, monkeypatch):
    monkeypatch.setenv("SystemRoot", _ROOT)
    run = _FakeLauncher()
    pythonw = tmp_path / "pythonw.exe"

    assert winsched_mod._pythonw_self_test(pythonw, run=run) == (True, "")

    [call] = run.calls
    stub = Path(call["argv"][1])
    assert call["argv"] == [str(pythonw), str(stub)] and stub.suffix == ".pyw"
    # The launcher a task runs, on the command shape M-W1 measured (arm P1):
    # cmd.exe on a batch file that exits 7.
    assert call["stub_text"] == winlaunch.stub_text(stub.with_suffix(".json"),
                                                    stub.with_suffix(".log"))
    cmd = str(winsched_mod._system32() / "cmd.exe")
    assert call["spec"]["argv"][:3] == [cmd, "/d", "/c"], call["spec"]["argv"]
    assert call["spec"]["supervise"] is False
    # ASCII, and naming no path, so an account name outside ASCII cannot break
    # the batch file cmd.exe reads in the console code page.
    text = call["batch"].decode("ascii")
    assert text.splitlines()[-1] == "exit /b 7", text
    assert str(call["dir"]) not in text, text
    for stream in ("stdin", "stdout", "stderr"):
        assert call["kwargs"].get(stream) is subprocess.DEVNULL, stream
    assert call["kwargs"].get("timeout"), "a self-test that can hang the install"
    if hasattr(subprocess, "STARTUPINFO"):
        si = call["kwargs"].get("startupinfo")
        assert si is not None and si.dwFlags & subprocess.STARTF_USESHOWWINDOW \
            and si.wShowWindow == 0, "the self-test is not started hidden"
    assert not call["dir"].exists(), "the self-test left its files behind"


@pytest.mark.parametrize("run, words", [
    (_FakeLauncher(returncode=0), "returned 0, expected 7"),
    (_FakeLauncher(returncode=1), "returned 1, expected 7"),
    (_FakeLauncher(returncode=7, marker=False), "the test command never ran"),
    (_FakeLauncher(raises=OSError("planted: cannot start")), "could not start"),
    (_FakeLauncher(raises=subprocess.TimeoutExpired("pythonw", 60)),
     "did not finish"),
], ids=["exit 0", "exit 1", "exit 7 without the marker", "cannot start",
        "timeout"])
def test_pythonw_self_test_fails_on_anything_but_7_with_the_marker(
        tmp_path, monkeypatch, run, words):
    monkeypatch.setenv("SystemRoot", _ROOT)
    ok, why = winsched_mod._pythonw_self_test(tmp_path / "pythonw.exe", run=run)
    assert ok is False and words in why, why
    assert not run.calls[0]["dir"].exists(), "the self-test left its files behind"


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
# read off a live task in lane F arms B and E, or in the fork doc's M-W2 arms
# A4b-1 and A4b-2, then what status() must say.
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
    # M-W2 A4b-1: `schtasks /End` on a running task. The run was ended by hand,
    # not failed by the pulse, so it gets a state of its own and keeps its code.
    pytest.param("Ready", "9/14/2026 6:29:45 PM", "267014",
                 {"failed": False, "state": "terminated", "never_run": False,
                  "dropped_tick": False, "last_result": 0x41306,
                  "last_fire": "9/14/2026 6:29:45 PM"},
                 None, id="ended-by-hand-is-not-a-failure"),
    # M-W2 A4b-2: a disabled task that never ran. It never reported 0x41302 as
    # its Last Result; disabled is read from Status, the task's own state.
    pytest.param("Disabled", "11/30/1999 12:00:00 AM", "267011",
                 {"failed": False, "state": "disabled", "never_run": True,
                  "dropped_tick": False, "last_result": 0x41303},
                 "last_fire", id="disabled-comes-from-the-task-state"),
    # The control for the row above that stopped reading as failed: a process
    # that really fails still does. Copied out of the R9 record (a command that
    # exited 7 through the launcher, fired by Task Scheduler), never retyped.
    pytest.param("Ready", "9/14/2026 5:29:24 PM", "7",
                 {"failed": True, "state": "ready", "never_run": False,
                  "dropped_tick": False, "last_result": 7,
                  "last_fire": "9/14/2026 5:29:24 PM"},
                 None, id="real-exit-7-is-a-failure"),
    # Synthetic on purpose: no live task has recorded Last Result 1 yet. status()
    # gives 1 no meaning of its own, so it must read exactly as the real 7 does.
    pytest.param("Ready", "9/14/2026 5:29:24 PM", "1",
                 {"failed": True, "state": "ready", "last_result": 1},
                 None, id="exit-1-is-a-failure"),
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


def test_winsched_status_reads_workdir_and_interval_from_the_launcher_spec(
        tmp_path, monkeypatch):
    """`heartbeat disable` reads the firm's workspace back from here before it
    removes the task, and `heartbeat status` lists it."""
    block = _WINSCHED_V_BLOCK.format(status="Ready",
                                     last_run="9/14/2026 12:51:00 PM",
                                     last_result="0")
    monkeypatch.setattr(winsched_mod, "run_cmd",
                        lambda argv, timeout=30: (0, block))
    winlaunch.write_launcher(tmp_path, "cadre-heartbeat-lab", argv=["py"],
                             env={}, cwd="C:\\firms\\lab", supervise=False,
                             interval="30m")
    st = WindowsScheduler(launcher_dir=tmp_path).status("cadre-heartbeat-lab")
    assert st.get("workdir") == "C:\\firms\\lab", st
    assert st.get("interval") == "30m", st


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
    # ".containment.json" is #141's record, and it belongs in this fixture
    # because THIS is the leg that says the directory goes when its last
    # launcher does -- the behaviour #141 took away by leaving a file behind
    # (avocet, #146 FINDING 4).
    for suffix in (".pyw", ".json", ".containment.json"):
        (launchers / f"cadre-heartbeat-lab{suffix}").write_text(
            "x", encoding="utf-8")

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
    for stem in ("cadre-heartbeat-lab", "cadre-heartbeat-other"):
        for suffix in (".pyw", ".json"):
            (launchers / f"{stem}{suffix}").write_text("x", encoding="utf-8")

    out = WindowsScheduler(launcher_dir=launchers).remove("cadre-heartbeat-lab")

    assert out["folder"] == {"path": _CADRE_FOLDER, "action": "kept",
                             "tasks_remaining": 1}
    assert (launchers / "cadre-heartbeat-other.pyw").exists()
    assert (launchers / "cadre-heartbeat-other.json").exists()
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


def test_winsched_remove_takes_the_containment_record_too(tmp_path, ok_cmd):
    """DoD D5: remove leaves nothing. #141 added a file it did not know about.

    avocet's FINDING 4, and it is a REGRESSION rather than a gap: before #141
    the launcher directory held the stub, the spec, the log and, on an upgraded
    install, the old .cmd -- remove took all four, the directory was then empty
    and remove deleted the directory too. #141 writes `<stem>.containment.json`
    beside them, remove does not name it, so the file survives, the directory
    is no longer empty, and THE DIRECTORY IS NO LONGER REMOVED. A behaviour
    that used to hold stopped holding, and nothing said so.

    The assertion is on the DIRECTORY, not only on the file. Asserting the file
    is gone would pass the day someone deletes it by a second spelling of its
    name; the directory being gone is the property D5 actually states, and it
    can only be true when every file this scheduler wrote has been taken.

    The name comes from `winlaunch.containment_path`, the one producer of it,
    so this leg cannot drift from the writer the way remove's own list did.
    """
    launchers = tmp_path / "sched"
    launchers.mkdir()
    stem = "cadre-heartbeat-lab"
    record = winlaunch.containment_path(launchers, stem)
    mine = [f"{stem}.pyw", f"{stem}.json", f"{stem}.log", f"{stem}.cmd",
            record.name]
    for name in mine:
        (launchers / name).write_text("x", encoding="utf-8")

    out = WindowsScheduler(launcher_dir=launchers).remove(stem)

    assert record.name in out["removed"], (
        f"the containment record is not in what remove reports it took: "
        f"{out['removed']}")
    assert not launchers.exists(), (
        f"the launcher directory survived remove because "
        f"{sorted(p.name for p in launchers.iterdir())} was left in it; before "
        f"#141 this directory was deleted and D5 says remove leaves nothing")
