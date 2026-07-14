"""systemd backend — Linux/WSL2 user units. The original mechanism, extracted.

Everything Cadre shipped on before the scheduler seam existed lives here
unchanged: unit files in ``~/.config/systemd/user``, ``systemctl --user``
lifecycle, ``systemd-run --user --collect`` for detached dispatch, and the
reset-failed ghost hygiene fork 005 taught us.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from firm.sched.base import SchedulerError, run_cmd


class SystemdScheduler:
    name = "systemd"

    def __init__(self, unit_dir: Path | None = None):
        self.unit_dir = unit_dir or Path.home() / ".config" / "systemd" / "user"

    # -- internals ----------------------------------------------------------

    def _ctl(self, *args: str) -> tuple[int, str]:
        return run_cmd(["systemctl", "--user", *args])

    def _write_service(self, stem: str, description: str, workdir: Path,
                       env: dict[str, str], argv: list[str],
                       extra: str = "") -> None:
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        env_lines = "\n".join(f'Environment="{k}={v}"' for k, v in sorted(env.items()))
        exec_line = " ".join(argv)
        (self.unit_dir / f"{stem}.service").write_text(f"""[Unit]
Description={description}

[Service]
{extra}WorkingDirectory={workdir}
{env_lines}
ExecStart={exec_line}
""")

    # -- interface ----------------------------------------------------------

    def available(self) -> tuple[bool, str]:
        rc, out = self._ctl("is-system-running")
        # degraded/running both mean the user manager answers; only a missing
        # binary or no user session is a real no.
        if "unavailable" in out or "Failed to connect" in out:
            return False, out
        return True, ""

    def install_timer(self, stem: str, *, description: str, workdir: Path,
                      env: dict[str, str], argv: list[str],
                      interval: str) -> dict[str, Any]:
        self._write_service(stem, description, workdir, env, argv,
                            extra="Type=oneshot\n")
        (self.unit_dir / f"{stem}.timer").write_text(f"""[Unit]
Description={description} (every {interval})

[Timer]
OnBootSec=2m
OnUnitActiveSec={interval}
RandomizedDelaySec=30
Persistent=false

[Install]
WantedBy=timers.target
""")
        for step in (["daemon-reload"], ["enable", "--now", f"{stem}.timer"]):
            rc, out = self._ctl(*step)
            if rc != 0:
                raise SchedulerError(f"systemctl {' '.join(step)}: {out}")
        return {"unit": f"{stem}.timer", "unit_dir": str(self.unit_dir)}

    def install_service(self, stem: str, *, description: str, workdir: Path,
                        env: dict[str, str], argv: list[str]) -> dict[str, Any]:
        self._write_service(stem, description, workdir, env, argv,
                            extra="Restart=on-failure\nRestartSec=5\n")
        service = f"{stem}.service"
        for step in (["daemon-reload"], ["enable", "--now", service]):
            rc, out = self._ctl(*step)
            if rc != 0:
                raise SchedulerError(f"systemctl {' '.join(step)}: {out}")
        return {"unit": service, "unit_dir": str(self.unit_dir)}

    def remove(self, stem: str) -> dict[str, Any]:
        removed = []
        for suffix in (".timer", ".service"):
            unit = f"{stem}{suffix}"
            if (self.unit_dir / unit).exists():
                self._ctl("disable", "--now", unit)
                (self.unit_dir / unit).unlink(missing_ok=True)
                removed.append(unit)
        self._ctl("daemon-reload")
        # A unit that ever failed stays in the runtime as `not-found failed`
        # after its file is gone — permanent ghost noise (fork 005).
        self.clear_failed(stem)
        return {"removed": removed}

    def status(self, stem: str) -> dict[str, Any]:
        timer = self.unit_dir / f"{stem}.timer"
        service = self.unit_dir / f"{stem}.service"
        installed = timer.exists() or service.exists()
        out: dict[str, Any] = {"installed": installed, "state": "absent",
                               "failed": False}
        if not installed:
            return out
        probe = f"{stem}.timer" if timer.exists() else f"{stem}.service"
        rc, state = self._ctl("is-active", probe)
        out["state"] = state or "unknown"
        rc_f, _ = self._ctl("is-failed", probe)
        out["failed"] = rc_f == 0
        if service.exists():
            for line in service.read_text().splitlines():
                if line.startswith("WorkingDirectory="):
                    out["workdir"] = line.partition("=")[2]
        if timer.exists():
            for line in timer.read_text().splitlines():
                if line.startswith("OnUnitActiveSec="):
                    out["interval"] = line.split("=", 1)[1].strip()
            rc, show = self._ctl(
                "show", f"{stem}.timer",
                "--property=NextElapseUSecRealtime,LastTriggerUSec")
            if rc == 0:
                for line in show.splitlines():
                    key, _, val = line.partition("=")
                    if key == "NextElapseUSecRealtime" and val:
                        out["next_fire"] = val
                    if key == "LastTriggerUSec" and val:
                        out["last_fire"] = val
        return out

    def list_installed(self, prefix: str) -> list[str]:
        stems = {p.stem for p in self.unit_dir.glob(f"{prefix}*.timer")}
        stems |= {p.stem for p in self.unit_dir.glob(f"{prefix}*.service")}
        return sorted(stems)

    def clear_failed(self, stem: str) -> None:
        self._ctl("reset-failed", f"{stem}.service", f"{stem}.timer")

    def restart(self, stem: str) -> tuple[bool, str]:
        rc, out = self._ctl("restart", f"{stem}.service")
        return rc == 0, out

    def spawn_detached(self, argv: list[str], *, workdir: Path,
                       env: dict[str, str],
                       unit: str | None = None) -> dict[str, Any]:
        if unit and run_cmd(["systemd-run", "--version"], timeout=10)[0] == 0:
            cmd = ["systemd-run", "--user", "--collect", "--unit", unit,
                   "--working-directory", str(workdir)]
            cmd += [f"--setenv={k}={v}" for k, v in sorted(env.items())]
            cmd += argv
            rc, out = run_cmd(cmd, timeout=30)
            if rc == 0:
                return {"via": "systemd-run", "unit": unit}
            # fall through — a broken user manager must not eat the pulse
        full_env = dict(os.environ)
        full_env.update(env)
        proc = subprocess.Popen(
            argv, cwd=str(workdir), env=full_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"via": "detached-popen", "pid": proc.pid}
