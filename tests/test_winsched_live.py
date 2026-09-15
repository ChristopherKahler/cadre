"""The Windows scheduler against the real Task Scheduler (#119, design D7).

Every other scheduler test fakes schtasks. This one installs a real task, reads
it back, removes it, and proves it is gone, so it runs only where that is
wanted: on Windows with CADRE_WINSCHED_LIVE=1. The CI Windows job sets it; a
developer's machine does not unless they choose to, because a real task
appears in that machine's Task Scheduler for the length of the test.

WHY IT CANNOT LEAVE ANYTHING BEHIND
  - The stem is unique to this run (pid and time), under the product's own
    \\Cadre folder, and the launcher files go to a new temporary directory.
  - It refuses to start if any \\Cadre\\cadre-livetest-* task already exists. A
    task it did not create is somebody's evidence, and it never deletes one.
  - The interval is one day, so the task cannot fire while the test runs.
  - Removal is the product's remove(), then a query by the exact name must
    answer rc 1. A finally block deletes by that exact name if anything went
    wrong first. Nothing is ever deleted by pattern.

WHAT IT DOES NOT PROVE. It never waits for the task to fire. Whether an
interactive-only task fires on a CI runner with no interactive logon is not
measured (M-CI in the fork doc); firing, the exit code reaching Task Scheduler
and the absence of any window were measured on a real desktop (fork doc, "R9 ·
REAL DESKTOP · REAL TASK SCHEDULER TRIGGER · PASS").
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

LIVE = sys.platform == "win32" and os.environ.get("CADRE_WINSCHED_LIVE") == "1"
PREFIX = "cadre-livetest-"

pytestmark = pytest.mark.skipif(
    not LIVE, reason="writes a real Windows scheduled task; set CADRE_WINSCHED_LIVE=1 "
                     "on Windows to run it")


def _query(name: str) -> int:
    from firm.sched.base import run_cmd
    return run_cmd(["schtasks", "/Query", "/TN", name])[0]


def test_a_real_timer_installs_reports_and_removes_without_a_trace():
    from firm.sched.base import run_cmd
    from firm.sched.winsched import WindowsScheduler

    rc, listing = run_cmd(["schtasks", "/Query", "/FO", "CSV", "/NH"], timeout=60)
    assert rc == 0, f"schtasks could not list tasks: {listing[:300]}"
    leftovers = [ln for ln in listing.splitlines() if f"\\Cadre\\{PREFIX}" in ln]
    if leftovers:
        pytest.fail("refusing to run: a live-test task from another run exists and is "
                    f"not this test's to delete: {leftovers[:3]}")

    stem = f"{PREFIX}{os.getpid()}-{int(time.time())}"
    name = f"\\Cadre\\{stem}"
    # A short directory on purpose: the task command must fit schtasks' 261
    # characters, and pytest's own tmp_path can be long enough not to.
    launchers = Path(tempfile.mkdtemp(prefix="cl"))
    workdir = launchers / "work"
    workdir.mkdir()
    scheduler = WindowsScheduler(launcher_dir=launchers)
    try:
        installed = scheduler.install_timer(
            stem, description="cadre live test", workdir=workdir,
            env={"CADRE_LIVETEST": "1"},
            argv=[sys.executable, "-c", "raise SystemExit(0)"], interval="1d")
        assert installed["unit"] == name, installed
        assert _query(name) == 0, f"{name} is not in Task Scheduler after install"
        assert stem in scheduler.list_installed(PREFIX)

        st = scheduler.status(stem)
        assert st["installed"] is True, st
        assert st.get("never_run") is True, st
        assert st["failed"] is False, st
        assert st.get("workdir") == str(workdir), st
        assert st.get("interval") == "1d", st

        out = scheduler.remove(stem)
        assert name in out["removed"], out
        assert _query(name) == 1, f"{name} is still in Task Scheduler after remove()"
        for suffix in (".pyw", ".json", ".log"):
            assert not (launchers / f"{stem}{suffix}").exists(), suffix
        folder = out["folder"]
        assert folder["action"] in ("deleted", "kept"), folder
        if folder["action"] == "deleted":
            assert folder.get("verified_gone") is True, folder
        else:
            assert folder.get("tasks_remaining", 0) >= 1, folder
    finally:
        if _query(name) == 0:
            run_cmd(["schtasks", "/Delete", "/TN", name, "/F"])
        shutil.rmtree(launchers, ignore_errors=True)
    assert _query(name) == 1, f"{name} survived the test"
