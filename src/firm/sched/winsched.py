r"""Windows backend — Task Scheduler via ``schtasks`` + a pythonw launcher.

``schtasks /TR`` caps the command at 261 characters and cannot set
environment variables, so every install writes a launcher to
``~/.cadre/sched/``: ``<stem>.json`` (command, environment, working
directory) and ``<stem>.pyw``, the stub the task runs, which writes the
command's output to ``<stem>.log``. Debuggable by opening the files; removable
by deleting the task and the files.

Timers: ``/SC MINUTE|HOURLY|DAILY /MO n``. Services: Task Scheduler has no
restart-on-failure for interactive user tasks, so the launcher supervises —
the task starts at logon and the launcher runs the command again 5 seconds
after every exit.

Honesty notes: sub-minute intervals round up to 1 minute (Task Scheduler's
floor); ``status()`` parses ``schtasks /Query /V`` for state and run times.

No window (#119, design D1a as ruled). A task pointed at a console program
gets a console, and a console gets a window: every pulse drew one on the
operator's desktop, and closing it killed the pulse. The task command is
therefore ``"<pythonw.exe>" "<stem>.pyw"``, the pythonw.exe beside the Python
that installs the task. pythonw has no console, and the launcher
(:mod:`firm.sched.winlaunch`) starts the command through ``firm.core.proc``,
which gives it a console that has no window. ``conhost.exe --headless`` hid
the window too, but was measured returning exit code 0 for a command that
exited 5 or 7, so Task Scheduler would have read every failed pulse as a
success; it is not used. Before any file is written, an install refuses when
there is no pythonw.exe (never falling back to python.exe, which would draw a
window on every run), runs the launcher on this machine and requires the test
command's exit code and marker back, and refuses a task command past schtasks'
261-character cap rather than cutting it. Logon mode is unchanged: interactive
only, so a task runs while the user is logged on (a locked screen included)
and never while logged out.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from firm.core.proc import popen_utf8, run_utf8
from firm.sched import winlaunch
from firm.sched.base import SchedulerError, interval_to_seconds, run_cmd

_TASK_FOLDER = "Cadre"
# The folder as Task Scheduler names it. Its first character is the root.
_FOLDER_PATH = "\\" + _TASK_FOLDER

# Task Scheduler's `Last Result`, which schtasks prints as a SIGNED decimal, read
# here as the unsigned 32-bit code. Every value below was captured from a live
# task on Windows 10 19045 during issue #119 unless it says otherwise.
_LR_OK = 0x00000000
_LR_RUNNING = 0x00041301        # an instance is running right now
_LR_NEVER_RUN = 0x00041303      # the task has never run
# A trigger fired while an instance was still running and MultipleInstances=
# IgnoreNew dropped it. Measured: `Last Run Time` still advances to that
# trigger, and the NEXT finish overwrites this code with the finish's own, so
# a dropped tick is visible only between the two. It is not a task failure.
_LR_REFUSED = 0x800710E0
# Documented, NOT captured: an Interactive-only trigger while the user is logged
# out. Skipping then is the design (logged out means no pulse), not a failure.
_LR_NOT_LOGGED_ON = 0x800704DD
_NOT_FAILURES = frozenset({_LR_OK, _LR_RUNNING, _LR_NEVER_RUN, _LR_REFUSED,
                           _LR_NOT_LOGGED_ON})
# A never-run task reports `Last Run Time: 11/30/1999 12:00:00 AM` (en-US).
# Matched on the year so another date order still reads as "never", not as
# a pulse that fired in 1999.
_NEVER_RUN_TIME = re.compile("(?<![0-9])1999(?![0-9])")


def _folder_cleanup_script() -> str:
    """PowerShell that deletes the Cadre task folder only when it holds no task.

    Every firm's task lives in this one folder, so it may only go once the last
    task has. Hidden tasks count (GetTasks(1)). schtasks cannot delete a folder
    at all -- measured during #119, /Delete on a folder path returns rc 1 and
    leaves it -- so this goes through the Task Scheduler COM object. Output is
    one word the caller parses: absent, deleted, or kept <n>.
    """
    root = _FOLDER_PATH[0]
    return (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "$s=New-Object -ComObject Schedule.Service;$s.Connect();"
        f"try{{$f=$s.GetFolder('{_FOLDER_PATH}')}}catch{{'absent';exit 0}};"
        "$n=@($f.GetTasks(1)).Count;"
        "if($n -gt 0){'kept '+$n;exit 0};"
        f"$s.GetFolder('{root}').DeleteFolder('{_TASK_FOLDER}',0);"
        "'deleted'"
    )


def _folder_answer(rc: int, out: str) -> dict[str, Any]:
    for line in reversed(out.splitlines()):
        word = line.strip()
        if word in ("absent", "deleted"):
            return {"action": word}
        if word.startswith("kept "):
            try:
                return {"action": "kept", "tasks_remaining": int(word[5:])}
            except ValueError:
                break
    # Anything else is reported, never read as a clean removal.
    return {"action": "unknown", "detail": f"rc={rc}: {out[:300]}"}


def _last_result_code(val: str) -> int | None:
    try:
        return int(val) & 0xFFFFFFFF
    except ValueError:
        return None


# schtasks /Create takes a task command of at most this many characters.
_TR_CAP = 261

# The self-test's command exits with this code, and an install requires it back.
_SELF_TEST_EXIT = 7
_SELF_TEST_TIMEOUT = 60


def _system32() -> Path:
    """Where this Windows keeps cmd.exe."""
    root = (os.environ.get("SystemRoot") or os.environ.get("windir")
            or "C:\\Windows")
    return Path(root) / "System32"


def _resolve_pythonw() -> Path:
    """The pythonw.exe a task runs: the one beside the Python installing it.

    Never python.exe. A task started on python.exe gets a console, and the
    console gets a window on every run (ruling R4).
    """
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise SchedulerError(
            f"refusing to install a Windows task: {pythonw} does not exist. A "
            "task runs the pythonw.exe beside the Python that installs it, "
            "because python.exe would open a window on every run. Install "
            "from a Python that has pythonw.exe, such as a python.org install "
            "or a venv made from one.")
    return pythonw


def _task_command(pythonw: Path, stub: Path) -> str:
    """The task command: pythonw.exe on the launcher's stub."""
    return f'"{pythonw}" "{stub}"'


def _pythonw_self_test(pythonw: Path, run: Any = run_utf8) -> tuple[bool, str]:
    """Does a task's launch path work here, and bring the exit code back?

    Runs what a task runs -- *pythonw* on a stub written by
    :func:`firm.sched.winlaunch.write_launcher` -- with a test command that
    writes a marker into its working directory and exits 7, and requires exit
    code 7 AND the marker (ruling R3): an exit code alone cannot show the
    command ran. Started hidden, every stream closed, with a timeout. All it
    writes is in a temporary directory, removed afterwards.

    The test command is cmd.exe on a batch file because that is the shape
    measured on a private desktop (fork doc, M-W1 arm P1: no window, exit code
    7 back). The batch file is ASCII and names no path, so an account name
    outside ASCII cannot break it.
    """
    with tempfile.TemporaryDirectory(prefix="cadre-selftest-",
                                     ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        batch = root / "exit7.cmd"
        batch.write_bytes(b"@echo off\r\necho ran> command-ran\r\n"
                          b"exit /b 7\r\n")
        stub = winlaunch.write_launcher(
            root, "self-test",
            argv=[str(_system32() / "cmd.exe"), "/d", "/c", str(batch)],
            env={}, cwd=root, supervise=False)
        kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL,
                                  "stdout": subprocess.DEVNULL,
                                  "stderr": subprocess.DEVNULL,
                                  "timeout": _SELF_TEST_TIMEOUT}
        if hasattr(subprocess, "STARTUPINFO"):
            hidden = subprocess.STARTUPINFO()
            hidden.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            hidden.wShowWindow = 0   # SW_HIDE
            kwargs["startupinfo"] = hidden
        try:
            proc = run([str(pythonw), str(stub)], **kwargs)
        except subprocess.TimeoutExpired:
            return False, (f"{pythonw} did not finish the self-test in "
                           f"{_SELF_TEST_TIMEOUT} seconds")
        except OSError as exc:
            return False, f"{pythonw} could not start: {exc}"
        wrong = []
        if proc.returncode != _SELF_TEST_EXIT:
            wrong.append(f"{pythonw} returned {proc.returncode}, expected "
                         f"{_SELF_TEST_EXIT}")
        if not (root / "command-ran").exists():
            wrong.append("the test command never ran")
        if not wrong:
            return True, ""
        log = stub.with_suffix(".log")
        said = (log.read_text(encoding="utf-8", errors="replace").strip()[-300:]
                if log.exists() else "no log was written")
        return False, "; ".join(wrong) + f"; the launcher's log: {said}"


class WindowsScheduler:
    name = "winsched"

    def __init__(self, launcher_dir: Path | None = None):
        self.launcher_dir = launcher_dir or Path.home() / ".cadre" / "sched"

    # -- internals ----------------------------------------------------------

    def _tn(self, stem: str) -> str:
        return f"\\{_TASK_FOLDER}\\{stem}"

    def _stub(self, stem: str) -> Path:
        return self.launcher_dir / f"{stem}.pyw"

    def _spec(self, stem: str) -> Path:
        return self.launcher_dir / f"{stem}.json"

    def _log(self, stem: str) -> Path:
        return self.launcher_dir / f"{stem}.log"

    def _prepare_task(self, stem: str) -> str:
        """The task command for *stem*, or SchedulerError before any write."""
        pythonw = _resolve_pythonw()
        ok, why = _pythonw_self_test(pythonw)
        if not ok:
            raise SchedulerError(
                "refusing to install a Windows task: the launcher failed its "
                f"self-test on this machine: {why}")
        tr = _task_command(pythonw, self._stub(stem))
        if len(tr) > _TR_CAP:
            raise SchedulerError(
                f"the task command is {len(tr)} characters and schtasks takes "
                f"at most {_TR_CAP}; it is refused rather than cut. Use a "
                f"shorter launcher directory than {self.launcher_dir}, or a "
                f"Python installed at a shorter path than {pythonw}.")
        return tr

    @staticmethod
    def _schedule_flags(interval: str) -> list[str]:
        seconds = interval_to_seconds(interval)
        if seconds < 3600:
            return ["/SC", "MINUTE", "/MO", str(max(1, round(seconds / 60)))]
        if seconds < 86400:
            return ["/SC", "HOURLY", "/MO", str(max(1, round(seconds / 3600)))]
        return ["/SC", "DAILY", "/MO", str(max(1, round(seconds / 86400)))]

    # -- interface ----------------------------------------------------------

    def available(self) -> tuple[bool, str]:
        rc, out = run_cmd(["schtasks", "/Query", "/FO", "LIST"], timeout=20)
        return (rc == 0), ("" if rc == 0 else out)

    def install_timer(self, stem: str, *, description: str, workdir: Path,
                      env: dict[str, str], argv: list[str],
                      interval: str) -> dict[str, Any]:
        tr = self._prepare_task(stem)
        winlaunch.write_launcher(self.launcher_dir, stem, argv=argv, env=env,
                                 cwd=workdir, supervise=False,
                                 interval=interval)
        cmd = ["schtasks", "/Create", "/TN", self._tn(stem),
               "/TR", tr, "/F",
               *self._schedule_flags(interval)]
        rc, out = run_cmd(cmd)
        if rc != 0:
            raise SchedulerError(f"schtasks /Create: {out}")
        return {"unit": self._tn(stem), "unit_dir": str(self.launcher_dir)}

    def install_service(self, stem: str, *, description: str, workdir: Path,
                        env: dict[str, str], argv: list[str]) -> dict[str, Any]:
        tr = self._prepare_task(stem)
        winlaunch.write_launcher(self.launcher_dir, stem, argv=argv, env=env,
                                 cwd=workdir, supervise=True)
        cmd = ["schtasks", "/Create", "/TN", self._tn(stem),
               "/TR", tr, "/F", "/SC", "ONLOGON"]
        rc, out = run_cmd(cmd)
        if rc != 0:
            raise SchedulerError(f"schtasks /Create: {out}")
        # start it now — ONLOGON alone would wait for the next login
        run_cmd(["schtasks", "/Run", "/TN", self._tn(stem)])
        return {"unit": self._tn(stem), "unit_dir": str(self.launcher_dir)}

    def remove(self, stem: str) -> dict[str, Any]:
        removed = []
        run_cmd(["schtasks", "/End", "/TN", self._tn(stem)])
        rc, out = run_cmd(["schtasks", "/Delete", "/TN", self._tn(stem), "/F"])
        if rc == 0:
            removed.append(self._tn(stem))
        # A .cmd launcher is what cadre wrote before #119; an upgraded install
        # can still hold one, and removing the heartbeat must take it too.
        for leftover in (self._stub(stem), self._spec(stem), self._log(stem),
                         self.launcher_dir / f"{stem}.cmd"):
            if leftover.exists():
                leftover.unlink()
                removed.append(leftover.name)
        result: dict[str, Any] = {"removed": removed,
                                  "folder": self._remove_folder_if_empty()}
        # The launcher directory goes the same way: only once it is empty, so
        # another firm's launcher is never touched.
        if self.launcher_dir.is_dir() and not any(self.launcher_dir.iterdir()):
            self.launcher_dir.rmdir()
            removed.append(str(self.launcher_dir))
        return result

    def _remove_folder_if_empty(self) -> dict[str, Any]:
        rc, out = run_cmd(["powershell.exe", "-NoProfile", "-NonInteractive",
                           "-Command", _folder_cleanup_script()], timeout=60)
        answer: dict[str, Any] = {"path": _FOLDER_PATH, **_folder_answer(rc, out)}
        if answer["action"] == "deleted":
            # Read it back. A folder query answers rc 0 with no rows while an
            # EMPTY folder still exists; only rc 1 means it is gone.
            qrc, _ = run_cmd(["schtasks", "/Query", "/TN",
                              _FOLDER_PATH + _FOLDER_PATH[0]])
            answer["verified_gone"] = qrc == 1
        return answer

    def status(self, stem: str) -> dict[str, Any]:
        out: dict[str, Any] = {"installed": False, "state": "absent",
                               "failed": False}
        rc, q = run_cmd(["schtasks", "/Query", "/TN", self._tn(stem),
                         "/FO", "LIST", "/V"])
        if rc != 0:
            return out
        out["installed"] = True
        out["state"] = "unknown"
        # `state` is task liveness only. Neither `last_fire` nor `failed` can say
        # whether a PULSE did work: a dropped tick advances Last Run Time, and
        # its refusal code is overwritten by the next finish. The firm database
        # is the record of work.
        for line in q.splitlines():
            key, _, val = (x.strip() for x in line.partition(":"))
            if key == "Status" and val:
                out["state"] = val.lower()
            elif key == "Next Run Time" and val and val != "N/A":
                out["next_fire"] = val
            elif key == "Last Run Time" and val and val != "N/A":
                if _NEVER_RUN_TIME.search(val):
                    out["never_run"] = True      # absent, not a 1999 timestamp
                else:
                    out["never_run"] = False
                    out["last_fire"] = val
            elif key == "Last Result" and val:
                code = _last_result_code(val)
                if code is None:
                    # Output nobody has seen yet must never read as healthy.
                    out["last_result"] = val
                    out["failed"] = True
                    continue
                out["last_result"] = code
                out["dropped_tick"] = code == _LR_REFUSED
                if code == _LR_NEVER_RUN:
                    out["never_run"] = True
                if code not in _NOT_FAILURES:
                    out["failed"] = True
        spec = self._spec(stem)
        if spec.exists():
            recorded = json.loads(spec.read_text(encoding="utf-8"))
            if recorded.get("cwd"):
                out["workdir"] = recorded["cwd"]
            if recorded.get("interval"):
                out["interval"] = recorded["interval"]
        return out

    def list_installed(self, prefix: str) -> list[str]:
        if not self.launcher_dir.is_dir():
            return []
        return sorted(p.stem for p in self.launcher_dir.glob(f"{prefix}*.pyw"))

    def clear_failed(self, stem: str) -> None:
        return None   # Task Scheduler keeps no ghost units

    def restart(self, stem: str) -> tuple[bool, str]:
        run_cmd(["schtasks", "/End", "/TN", self._tn(stem)])
        rc, out = run_cmd(["schtasks", "/Run", "/TN", self._tn(stem)])
        return rc == 0, out

    def spawn_detached(self, argv: list[str], *, workdir: Path,
                       env: dict[str, str],
                       unit: str | None = None) -> dict[str, Any]:
        full_env = dict(os.environ)
        full_env.update(env)
        # A hidden console of its own (#119). The pulse and the Members it
        # starts inherit that console, so none of them opens a window, and it is
        # not the hub's console, so Ctrl+C in the hub's terminal does not reach
        # the pulse. DETACHED_PROCESS used to give the pulse no console at all,
        # and then every console program it started got a window of its own.
        # stdin is closed rather than inherited: the pulse reads nothing, and a
        # handle to the hub's console input is not something it should hold.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | \
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = popen_utf8(
            argv, cwd=str(workdir), env=full_env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True,
        )
        return {"via": "detached-popen", "pid": proc.pid}
