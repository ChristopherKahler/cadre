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

import json
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


# ---------------------------------------------------------------------------
# #147 -- enable over a RUNNING heartbeat, on a real task (tiers B and C)
# ---------------------------------------------------------------------------

def _generations(marker: Path) -> set[tuple[int, str]]:
    """The pids the launcher recorded, each with its creation time.

    IDENTITY IS A GENERATION, NOT A PID, and that is lifted from #147's own
    instrument rather than invented here: Windows hands a pid to another
    process soon after the first exits, so a pid that is "still there" can be
    a stranger. A generation cannot be mistaken for its successor.
    """
    if not marker.exists():
        return set()
    out = set()
    for line in marker.read_text(encoding="utf-8").splitlines():
        pid, _, created = line.partition(",")
        if pid.strip().isdigit():
            out.add((int(pid), created.strip()))
    return out


def _alive(gens: set[tuple[int, str]]) -> set[tuple[int, str]]:
    """Which of those generations are still running, by pid AND creation time."""
    from firm.sched.base import run_cmd
    live = set()
    for pid, created in gens:
        rc, said = run_cmd(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}')"
             ".CreationDate"], timeout=60)
        if rc == 0 and said.strip() and created[:14] and created[:14] in said:
            live.add((pid, created))
    return live


def test_enable_over_a_running_heartbeat_ends_its_tree(tmp_path, capsys):
    """TIER C: Windows complied AND the pulse tree died. PASS, FAIL or VOID.

    THIS LEG'S LAST PRINTED LINE IS ITS VERDICT, and VOID is never a pass. The
    CI runner may produce no running instance at all (an interactive-only task
    with no interactive session), and reporting that as either colour would be
    a claim nobody measured.

    THE CONTROL RUNS FIRST AND GATES THE ARM. Without it, a command that ended
    on its own is indistinguishable from one the re-create ended, and the
    answer is wrong in exactly the direction that matters. #147's own
    instrument did this and said so.

    THE CONTAINMENT RECORD IS READ BEFORE THE ACT, because `write_launcher`
    DELETES it on every reinstall (winlaunch.py:126). Read afterwards it is
    always absent, and the leg would call an uncontained host contained.
    """
    from firm.sched.base import run_cmd
    from firm.sched.winsched import WindowsScheduler
    from firm.sched import winlaunch

    stem = f"{PREFIX}147"
    launchers = tmp_path / "launchers"
    marker = tmp_path / "gens.txt"
    sched = WindowsScheduler(launcher_dir=launchers)

    # A pulse that records its own generation and a detached grandchild's, then
    # outlives this leg -- so anything still alive at the end really survived.
    recorder = (
        "import datetime,os,subprocess,sys,time\n"
        "def stamp(p):\n"
        "    import subprocess as s\n"
        "    o=s.run(['powershell','-NoProfile','-Command',"
        "f\"(Get-CimInstance Win32_Process -Filter 'ProcessId={p}').CreationDate\"],"
        "capture_output=True,text=True).stdout.strip()\n"
        "    return o\n"
        "g=subprocess.Popen([sys.executable,'-c','import time;time.sleep(900)'])\n"
        f"open(r'{marker}','w').write("
        "f'{os.getpid()},{stamp(os.getpid())}\n{g.pid},{stamp(g.pid)}\n')\n"
        "time.sleep(900)\n")

    verdict = "VOID"
    try:
        sched.install_timer(stem, description="147 live",
                            workdir=tmp_path, env={},
                            argv=[sys.executable, "-c", recorder],
                            interval="15m")
        run_cmd(["schtasks", "/Run", "/TN", sched._tn(stem)], timeout=60)

        deadline = time.time() + 30
        gens: set = set()
        while time.time() < deadline and len(gens) < 2:
            gens = _generations(marker)
            time.sleep(1)

        control = _alive(gens)
        print(f"[147] recorded generations: {sorted(gens)}")
        print(f"[147] alive before the act : {sorted(control)}")
        if len(control) < 2:
            print("[147] /Run produced no running tree on this runner; "
                  "nothing to end, so nothing is measured here")
            print(f"[147] status(): {sched.status(stem)}")
            print("[147] VOID: no running instance to act on")
            return

        record = winlaunch.containment_path(launchers, stem)
        contained = record.exists() and "true" in record.read_text(
            encoding="utf-8").lower()
        print(f"[147] containment record before the act: "
              f"{record.read_text(encoding='utf-8') if record.exists() else 'ABSENT'}")
        if not contained:
            print("[147] the launcher is not contained on this host, so ending "
                  "it is not expected to end its tree")
            print("[147] VOID: uncontained launcher")
            return

        # THE ACT: the product's own call, never raw schtasks.
        sched.install_timer(stem, description="147 live",
                            workdir=tmp_path, env={},
                            argv=[sys.executable, "-c", recorder],
                            interval="30m")

        end = time.time() + 10
        survivors = control
        while time.time() < end and survivors:
            survivors = _alive(control)
            time.sleep(1)

        print(f"[147] alive after the act  : {sorted(survivors)}")
        print(f"[147] task still installed : {_query(sched._tn(stem)) == 0}")
        verdict = "PASS" if not survivors else "FAIL"
        assert not survivors, (
            "enable re-created the task and left the old pulse tree running",
            sorted(survivors))
    finally:
        # Ended BY GENERATION only: a pid whose creation time differs is a
        # stranger and is never touched.
        for pid, _created in _alive(_generations(marker)):
            run_cmd(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=60)
        sched.remove(stem)
        print(f"[147] {verdict}")


# ---------------------------------------------------------------------------
# #147 -- enable over a RUNNING heartbeat, on a real task (tiers B and C)
# ---------------------------------------------------------------------------

#: The program the task runs. A FILE, not a ``-c`` string, and that is a fix
#: rather than a preference: the first draft built this as a nested f-string
#: inside a test literal and DID NOT COMPILE -- ``SyntaxError: unterminated
#: f-string``. On CI the task would have exited 1, written no marker, and the
#: leg would have found nothing to measure, printed VOID, returned, and been
#: COUNTED AS PASSED. The one leg that is the whole proof of this PR would have
#: been green on a program that never ran.
_RECORDER = '''\
import os, subprocess, sys, time


def stamp(pid):
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').CreationDate"
         % pid],
        capture_output=True, text=True).stdout.strip()
    return out


grandchild = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(900)"])

# `with`, because this process then sleeps for 900 s: an unclosed write may
# never flush, and a marker that arrives after the leg has finished is a
# marker that was never there.
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    fh.write("%d,%s\\n" % (os.getpid(), stamp(os.getpid())))
    fh.write("%d,%s\\n" % (grandchild.pid, stamp(grandchild.pid)))
    fh.flush()
    os.fsync(fh.fileno())

time.sleep(900)
'''


def _generations(marker: Path) -> set[tuple[int, str]]:
    """The pids the launcher recorded, each with its creation time.

    IDENTITY IS A GENERATION, NOT A PID, lifted from #147's own instrument:
    Windows hands a pid to another process soon after the first exits, so a pid
    that is "still there" can be a stranger.
    """
    if not marker.exists():
        return set()
    out = set()
    for line in marker.read_text(encoding="utf-8").splitlines():
        pid, _, created = line.partition(",")
        if pid.strip().isdigit() and created.strip():
            out.add((int(pid), created.strip()))
    return out


def _alive(gens: set[tuple[int, str]]) -> set[tuple[int, str]]:
    """Which generations are still running, by pid AND creation time."""
    from firm.sched.base import run_cmd
    live = set()
    for pid, created in gens:
        rc, said = run_cmd(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}')"
             ".CreationDate"], timeout=60)
        if rc == 0 and created and created[:14] in said:
            live.add((pid, created))
    return live


def test_enable_over_a_running_heartbeat_ends_its_tree(tmp_path, capsys):
    """TIER C: Windows complied AND the pulse tree died. PASS, FAIL or VOID.

    VOID IS A SKIP, NOT A PASS. A CI runner may produce no running instance at
    all (an interactive-only task with no interactive session), and a ``return``
    there counts as PASSED under ``-ra`` while its prints are swallowed -- so
    the claim would read as proved and the table would never reach the step log.
    ``pytest.skip`` is distinct from a pass and visible in the summary.

    THE CONTROL RUNS FIRST AND GATES THE ARM, twice over. ``compile()`` on the
    recorder before anything is installed, because a program that does not
    compile produces exactly the same empty marker as a pulse that was never
    started -- and the first draft of this leg shipped with one that did not.
    Then the liveness control: without it, a command that ended on its own is
    indistinguishable from one the re-create ended.

    THE CONTAINMENT RECORD IS READ BEFORE THE ACT, because ``write_launcher``
    deletes it on every reinstall (winlaunch.py:126), and it is PARSED rather
    than substring-matched: the record also carries ``supported: true``, so a
    text test for "true" passes an UNCONTAINED host straight into a FAIL where
    the honest answer is VOID.
    """
    from firm.sched.base import run_cmd
    from firm.sched.winsched import WindowsScheduler
    from firm.sched import winlaunch

    # CONTROL 0: the program must compile before anything is installed.
    compile(_RECORDER, "<recorder>", "exec")

    # A UNIQUE STEM, this file's own rule. `/Create /F` over a stale livetest
    # task would delete evidence this run did not create.
    stem = f"{PREFIX}147-{os.getpid()}-{int(time.time())}"
    rc, listing = run_cmd(["schtasks", "/Query", "/FO", "CSV", "/NH"], timeout=60)
    assert rc == 0, f"schtasks could not list tasks: {listing[:300]}"
    leftovers = [ln for ln in listing.splitlines() if f"\\Cadre\\{PREFIX}" in ln]
    if leftovers:
        pytest.fail("refusing to run: a live-test task from another run exists "
                    f"and is not this test's to delete: {leftovers[:3]}")

    launchers = tmp_path / "launchers"
    marker = tmp_path / "gens.txt"
    script = tmp_path / "recorder.py"
    script.write_text(_RECORDER, encoding="utf-8")
    sched = WindowsScheduler(launcher_dir=launchers)
    readings: list[str] = []
    verdict = "VOID"

    def say(line: str) -> None:
        readings.append(f"[147] {line}")

    try:
        sched.install_timer(stem, description="147 live", workdir=tmp_path,
                            env={},
                            argv=[sys.executable, str(script), str(marker)],
                            interval="15m")
        run_cmd(["schtasks", "/Run", "/TN", sched._tn(stem)], timeout=60)

        deadline = time.time() + 30
        gens: set = set()
        while time.time() < deadline and len(gens) < 2:
            gens = _generations(marker)
            time.sleep(1)

        control = _alive(gens)
        say(f"recorded generations: {sorted(gens)}")
        say(f"alive before the act : {sorted(control)}")
        say(f"status() before      : {sched.status(stem)}")

        if len(control) < 2:
            say("/Run produced no running tree on this runner, so there was "
                "nothing to end and nothing is measured here")
            pytest.skip("[147] VOID: no running instance to act on -- "
                        + " | ".join(readings))

        record_path = winlaunch.containment_path(launchers, stem)
        record = (json.loads(record_path.read_text(encoding="utf-8"))
                  if record_path.exists() else {})
        say(f"containment record before the act: {record or 'ABSENT'}")
        if record.get("contained") is not True:
            say("the launcher is not contained on this host, so ending it is "
                "not expected to end its tree")
            pytest.skip("[147] VOID: uncontained launcher -- "
                        + " | ".join(readings))

        # THE ACT: the product's own call, never raw schtasks, and a DIFFERENT
        # interval so the spec can be read back as proof the re-create landed.
        sched.install_timer(stem, description="147 live", workdir=tmp_path,
                            env={},
                            argv=[sys.executable, str(script), str(marker)],
                            interval="30m")

        end = time.time() + 10
        survivors = control
        while time.time() < end and survivors:
            survivors = _alive(control)
            time.sleep(1)

        after = sched.status(stem)
        say(f"alive after the act  : {sorted(survivors)}")
        say(f"task still installed : {_query(sched._tn(stem)) == 0}")
        say(f"status() after       : {after}")
        say("containment record after: "
            f"{'PRESENT' if record_path.exists() else 'ABSENT'}")

        verdict = "PASS" if not survivors else "FAIL"
        assert not survivors, (
            "enable re-created the task and left the old pulse tree running",
            sorted(survivors))
        # Condition 3's post-act readings, ASSERTED rather than only printed.
        assert _query(sched._tn(stem)) == 0, (
            "the task did not survive its own re-create")
        assert after.get("interval") == "30m", (
            "the spec still names the old interval", after)
        assert not record_path.exists(), (
            "write_launcher deletes the containment record on reinstall; it is "
            "still there, so the re-create did not write a new launcher")
    finally:
        # The verdict and the table go out BEFORE the teardown, so a failure in
        # `remove()` cannot swallow the reading this leg exists to produce.
        with capsys.disabled():
            for line in readings:
                print(line)
            print(f"[147] {verdict}")
        # Ended BY GENERATION only: a pid whose creation time differs is a
        # stranger and is never touched.
        for pid, _created in _alive(_generations(marker)):
            run_cmd(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=60)
        sched.remove(stem)
