"""launchd backend — macOS LaunchAgents.

Timers ride ``StartInterval`` (seconds); services ride ``KeepAlive`` with
``SuccessfulExit: false`` (restart on failure). Plists land in
``~/Library/LaunchAgents/<stem>.plist`` and load through ``launchctl
bootstrap gui/$UID`` with a ``load -w`` fallback for older macOS.

Honesty notes: launchd does not report a next-fire time, so ``status()``
omits it; ``clear_failed`` is a no-op because launchd keeps no ghost state
the way systemd does.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path
from typing import Any

from firm.sched.base import SchedulerError, interval_to_seconds, run_cmd


class LaunchdScheduler:
    name = "launchd"

    def __init__(self, agent_dir: Path | None = None):
        self.agent_dir = agent_dir or Path.home() / "Library" / "LaunchAgents"

    # -- internals ----------------------------------------------------------

    def _domain(self) -> str:
        uid = os.getuid() if hasattr(os, "getuid") else 0   # non-POSIX: tests only
        return f"gui/{uid}"

    def _plist(self, stem: str) -> Path:
        return self.agent_dir / f"{stem}.plist"

    def _write_plist(self, stem: str, payload: dict[str, Any]) -> Path:
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        path = self._plist(stem)
        path.write_bytes(plistlib.dumps(payload))
        return path

    def _bootstrap(self, path: Path) -> None:
        rc, out = run_cmd(["launchctl", "bootstrap", self._domain(), str(path)])
        if rc != 0 and "already bootstrapped" not in out.lower():
            # older macOS or odd session: the legacy verb still works
            rc2, out2 = run_cmd(["launchctl", "load", "-w", str(path)])
            if rc2 != 0:
                raise SchedulerError(f"launchctl bootstrap: {out} / load: {out2}")

    def _payload(self, stem: str, description: str, workdir: Path,
                 env: dict[str, str], argv: list[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "Label": stem,
            "ProgramArguments": list(argv),
            "WorkingDirectory": str(workdir),
            "StandardOutPath": str(workdir / ".firm" / f"{stem}.log"),
            "StandardErrorPath": str(workdir / ".firm" / f"{stem}.log"),
        }
        if env:
            payload["EnvironmentVariables"] = {k: str(v) for k, v in env.items()}
        return payload

    # -- interface ----------------------------------------------------------

    def available(self) -> tuple[bool, str]:
        rc, out = run_cmd(["launchctl", "version"], timeout=10)
        return (rc == 0), ("" if rc == 0 else out)

    def install_timer(self, stem: str, *, description: str, workdir: Path,
                      env: dict[str, str], argv: list[str],
                      interval: str) -> dict[str, Any]:
        self.remove(stem)   # re-enable = replace; bootstrap rejects doubles
        payload = self._payload(stem, description, workdir, env, argv)
        payload["StartInterval"] = interval_to_seconds(interval)
        path = self._write_plist(stem, payload)
        self._bootstrap(path)
        return {"unit": path.name, "unit_dir": str(self.agent_dir)}

    def install_service(self, stem: str, *, description: str, workdir: Path,
                        env: dict[str, str], argv: list[str]) -> dict[str, Any]:
        self.remove(stem)
        payload = self._payload(stem, description, workdir, env, argv)
        payload["RunAtLoad"] = True
        payload["KeepAlive"] = {"SuccessfulExit": False}
        payload["ThrottleInterval"] = 5
        path = self._write_plist(stem, payload)
        self._bootstrap(path)
        return {"unit": path.name, "unit_dir": str(self.agent_dir)}

    def remove(self, stem: str) -> dict[str, Any]:
        removed = []
        path = self._plist(stem)
        rc, out = run_cmd(["launchctl", "bootout", f"{self._domain()}/{stem}"])
        if rc != 0:
            run_cmd(["launchctl", "remove", stem])   # legacy fallback
        if path.exists():
            path.unlink()
            removed.append(path.name)
        return {"removed": removed}

    def status(self, stem: str) -> dict[str, Any]:
        path = self._plist(stem)
        out: dict[str, Any] = {"installed": path.exists(), "state": "absent",
                               "failed": False}
        if not path.exists():
            return out
        try:
            payload = plistlib.loads(path.read_bytes())
            if payload.get("WorkingDirectory"):
                out["workdir"] = payload["WorkingDirectory"]
            if payload.get("StartInterval"):
                out["interval"] = f"{int(payload['StartInterval'])}s"
        except Exception:
            pass
        rc, printed = run_cmd(["launchctl", "print", f"{self._domain()}/{stem}"])
        if rc != 0:
            out["state"] = "loaded-not-running" if "could not find" not in printed.lower() else "not-loaded"
            return out
        out["state"] = "active"
        for line in printed.splitlines():
            line = line.strip()
            if line.startswith("state ="):
                out["state"] = line.partition("=")[2].strip()
            if line.startswith("last exit code =") and line.partition("=")[2].strip() not in ("0", "(never exited)"):
                out["failed"] = True
        return out

    def list_installed(self, prefix: str) -> list[str]:
        if not self.agent_dir.is_dir():
            return []
        return sorted(p.stem for p in self.agent_dir.glob(f"{prefix}*.plist"))

    def clear_failed(self, stem: str) -> None:
        return None   # launchd keeps no systemd-style ghost state

    def restart(self, stem: str) -> tuple[bool, str]:
        rc, out = run_cmd(["launchctl", "kickstart", "-k",
                           f"{self._domain()}/{stem}"])
        return rc == 0, out

    def spawn_detached(self, argv: list[str], *, workdir: Path,
                       env: dict[str, str],
                       unit: str | None = None) -> dict[str, Any]:
        full_env = dict(os.environ)
        full_env.update(env)
        proc = subprocess.Popen(
            argv, cwd=str(workdir), env=full_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"via": "detached-popen", "pid": proc.pid}
