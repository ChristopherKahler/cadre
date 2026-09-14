r"""Windows backend — Task Scheduler via ``schtasks`` + launcher scripts.

``schtasks /TR`` caps the command at ~261 characters and cannot set
environment variables, so every install writes a launcher ``.cmd`` to
``~/.cadre/sched/<stem>.cmd`` (env + cd + command) and points the task at
that. Debuggable by opening the file; removable by deleting the task and
the file.

Timers: ``/SC MINUTE|HOURLY|DAILY /MO n``. Services: Task Scheduler has no
restart-on-failure for interactive user tasks, so the launcher wraps the
command in a restart loop — the task starts at logon and the loop supervises.

Honesty notes: sub-minute intervals round up to 1 minute (Task Scheduler's
floor); ``status()`` parses ``schtasks /Query /V`` for state and run times.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from firm.sched.base import SchedulerError, interval_to_seconds, run_cmd

_TASK_FOLDER = "Cadre"

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


def _last_result_code(val: str) -> int | None:
    try:
        return int(val) & 0xFFFFFFFF
    except ValueError:
        return None


def _cmd_quote(s: str) -> str:
    return f'"{s}"' if (" " in s or "&" in s) else s


class WindowsScheduler:
    name = "winsched"

    def __init__(self, launcher_dir: Path | None = None):
        self.launcher_dir = launcher_dir or Path.home() / ".cadre" / "sched"

    # -- internals ----------------------------------------------------------

    def _tn(self, stem: str) -> str:
        return f"\\{_TASK_FOLDER}\\{stem}"

    def _launcher(self, stem: str) -> Path:
        return self.launcher_dir / f"{stem}.cmd"

    def _write_launcher(self, stem: str, workdir: Path, env: dict[str, str],
                        argv: list[str], *, supervise: bool) -> Path:
        self.launcher_dir.mkdir(parents=True, exist_ok=True)
        lines = ["@echo off", f"rem stem={stem}"]
        for k, v in sorted(env.items()):
            lines.append(f'set "{k}={v}"')
        lines.append(f'cd /d "{workdir}"')
        cmd = " ".join(_cmd_quote(a) for a in argv)
        if supervise:
            # Task Scheduler can't restart interactive user tasks on failure —
            # the launcher supervises instead (5s backoff, exits with logoff).
            lines += [":loop", cmd, "timeout /t 5 /nobreak >nul", "goto loop"]
        else:
            lines.append(cmd)
        path = self._launcher(stem)
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        return path

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
        launcher = self._write_launcher(stem, workdir, env, argv, supervise=False)
        launcher.write_text(
            launcher.read_text(encoding="utf-8").replace(
                "@echo off", f"@echo off\r\nrem interval={interval}", 1),
            encoding="utf-8")
        cmd = ["schtasks", "/Create", "/TN", self._tn(stem),
               "/TR", f'"{launcher}"', "/F",
               *self._schedule_flags(interval)]
        rc, out = run_cmd(cmd)
        if rc != 0:
            raise SchedulerError(f"schtasks /Create: {out}")
        return {"unit": self._tn(stem), "unit_dir": str(self.launcher_dir)}

    def install_service(self, stem: str, *, description: str, workdir: Path,
                        env: dict[str, str], argv: list[str]) -> dict[str, Any]:
        launcher = self._write_launcher(stem, workdir, env, argv, supervise=True)
        cmd = ["schtasks", "/Create", "/TN", self._tn(stem),
               "/TR", f'"{launcher}"', "/F", "/SC", "ONLOGON"]
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
        launcher = self._launcher(stem)
        if launcher.exists():
            launcher.unlink()
            removed.append(launcher.name)
        return {"removed": removed}

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
        launcher = self._launcher(stem)
        if launcher.exists():
            for line in launcher.read_text(encoding="utf-8").splitlines():
                if line.startswith("cd /d "):
                    out["workdir"] = line[6:].strip().strip('"')
                elif line.startswith("rem interval="):
                    out["interval"] = line.partition("=")[2].strip()
        return out

    def list_installed(self, prefix: str) -> list[str]:
        if not self.launcher_dir.is_dir():
            return []
        return sorted(p.stem for p in self.launcher_dir.glob(f"{prefix}*.cmd"))

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
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = subprocess.Popen(
            argv, cwd=str(workdir), env=full_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags, close_fds=True,
        )
        return {"via": "detached-popen", "pid": proc.pid}
