"""Platform schedulers — the third platform-adapter family in Cadre.

``sysconfig.platforms`` adapts *agent platforms*; ``secrets.provider`` adapts
*vault backends*; this package adapts the *host OS scheduler* so the autonomy
layer (pulse heartbeats, rails, detached pulse dispatch) runs on Linux, macOS,
and Windows without any consumer knowing which OS it is on.

Backends:
  - ``systemd``  — Linux/WSL2: user timers + services (the original mechanism)
  - ``launchd``  — macOS: LaunchAgents plists via ``launchctl``
  - ``winsched`` — Windows: Task Scheduler via ``schtasks`` + launcher scripts

``resolve_scheduler()`` picks by ``sys.platform`` (override with
``CADRE_SCHEDULER=systemd|launchd|winsched`` for testing). Every backend
answers the same small interface; anything a platform genuinely cannot do
(e.g. next-fire time on launchd) is omitted from ``status()``, never faked.
"""

from __future__ import annotations

import os
import sys

from firm.sched.base import Scheduler, interval_to_seconds  # noqa: F401


def resolve_scheduler() -> Scheduler:
    forced = (os.environ.get("CADRE_SCHEDULER") or "").strip().lower()
    if forced == "systemd" or (not forced and sys.platform.startswith("linux")):
        from firm.sched.systemd import SystemdScheduler
        return SystemdScheduler()
    if forced == "launchd" or (not forced and sys.platform == "darwin"):
        from firm.sched.launchd import LaunchdScheduler
        return LaunchdScheduler()
    if forced == "winsched" or (not forced and sys.platform.startswith("win")):
        from firm.sched.winsched import WindowsScheduler
        return WindowsScheduler()
    # Unknown platform: systemd is the least-surprising default on unixy
    # systems, and its errors say exactly what is missing.
    from firm.sched.systemd import SystemdScheduler
    return SystemdScheduler()
