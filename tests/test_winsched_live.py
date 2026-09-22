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

        # FAIL FIRST, PASS LAST. Setting PASS before the asserts printed PASS
        # as this leg's last line while pytest said FAILED -- two readers of
        # one run getting opposite answers, which is the same shape as VOID
        # counting as a pass. The verdict is only PASS once every assertion
        # below it has held.
        verdict = "FAIL"
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
        verdict = "PASS"
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


# ---------------------------------------------------------------------------
# #148 -- the hub's dispatch seam, contained, and --abort proving it (tiers B and C)
# ---------------------------------------------------------------------------

#: The program the stub Member runs. A FILE compiled before anything starts,
#: for the reason #147 paid for: a generated program that does not compile
#: leaves exactly the same empty marker as a Member that never started, and the
#: leg would then find nothing, print VOID, and be counted as PASSED.
#:
#: It records ITS OWN pid and creation time. Reading the tree with the module
#: under test would make the leg agree with whatever that module does, which is
#: the one thing it must not do.
_MEMBER_RECORDER = '''\
import os, subprocess, sys, time

out = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').CreationDate"
     % os.getpid()],
    capture_output=True, text=True).stdout.strip()

with open(sys.argv[1], "w", encoding="utf-8") as fh:
    fh.write("%d,%s\\n" % (os.getpid(), out))
    fh.flush()
    os.fsync(fh.fileno())

time.sleep(900)
'''


def _firm_148(ws: Path) -> None:
    """A scratch firm with one active Member and one Unit for it to claim."""
    from firm.core.db import connect
    from firm.core.migrate import apply_migrations
    from firm.core.repo import create

    conn = connect(ws / ".firm" / "firm.db")
    try:
        apply_migrations(conn)
        create(conn, "firm", {"id": "live148", "name": "Live 148"})
        create(conn, "member", {"id": "MEM-001", "firm_id": "live148",
                                "name": "Lead", "role": "worker",
                                "status": "active"})
        create(conn, "operation", {"id": "OPS-001", "firm_id": "live148",
                                   "name": "Ops"})
        create(conn, "project", {"id": "PROJ-001", "firm_id": "live148",
                                 "operation_id": "OPS-001", "name": "Work",
                                 "status": "in_progress",
                                 "due_date": "2099-12-31"})
        create(conn, "unit", {"id": "UNIT-001", "firm_id": "live148",
                              "project_id": "PROJ-001", "name": "Unit 1",
                              "assignee_member_id": "MEM-001"})
        conn.commit()
    finally:
        conn.close()


def _run_row_status(ws: Path) -> list[str]:
    from firm.core.db import connect

    conn = connect(ws / ".firm" / "firm.db")
    try:
        return [str(r[0]) for r in
                conn.execute("SELECT status FROM member_run").fetchall()]
    finally:
        conn.close()


def test_the_hub_seam_contains_its_pulse_and_abort_proves_the_tree_died(
        tmp_path, capsys):
    """TIERS B AND C for #148: Windows agreed, and the tree actually died.

    PASS, FAIL, or VOID -- and VOID IS A SKIP, NEVER A PASS. A CI runner that
    cannot produce a running Member leaves the same empty marker as a Member
    that started and vanished, and a bare `return` there counts as PASSED under
    `-ra` with its prints swallowed.

    THE PULSE IS STARTED THROUGH THE HUB'S SEAM, NOT THE RUNNING HUB. The
    operator's hub is never touched: this calls `resolve_scheduler()
    .spawn_detached` with `_fire_pulse`'s own wrapper shape, which is the code
    path the pulse button reaches and the one #148 flags as uncontained. That
    is the whole point of the leg -- the scheduled-task path was already
    contained by #141, and this is the path that was not.

    THE SNAPSHOT ASSERTION IS NOT OPTIONAL. `alive_after == []` is also what an
    empty walk returns, so a Windows walk that found nothing at all would pass
    the headline assertion while proving the opposite. The leg therefore
    asserts that `descendants_before` CONTAINED the Member's generation: the
    walk saw it, and then it was gone.
    """
    from firm.core.db import get_db_path
    from firm.pulse.environment import pulse_path
    from firm.sched import resolve_scheduler

    ws = tmp_path / "ws"
    (ws / ".firm").mkdir(parents=True)
    _firm_148(ws)

    marker = tmp_path / "member.txt"
    recorder = tmp_path / "member_recorder.py"
    # CONTROL 0: the program compiles, before anything is installed or started.
    compile(_MEMBER_RECORDER, str(recorder), "exec")
    recorder.write_text(_MEMBER_RECORDER, encoding="utf-8")

    stub = tmp_path / "stub-member.cmd"
    stub.write_text(
        "@echo off\r\n"
        f'"{sys.executable}" "{recorder}" "{marker}"\r\n',
        encoding="utf-8")

    env = {"FIRM_ID": "live148", "CADRE_CLAUDE_BIN": str(stub),
           "PATH": pulse_path(ws, "live148")}

    # --- TIER B GATE: can this host contain a pulse at all? -----------------
    # Read from a pulse that RUNS TO COMPLETION, because the pulse this leg
    # aborts never prints a result -- it is killed mid-run, which is the point.
    # A host that cannot make a job is not a failure of this change (R3), so it
    # is a VOID with its reading, never a FAIL.
    import subprocess as _sp

    probe_ws = tmp_path / "probe"
    (probe_ws / ".firm").mkdir(parents=True)
    _firm_148(probe_ws)
    probe_env = dict(os.environ)
    probe_env.update({"CADRE_CLAUDE_BIN": str(stub)})
    probe_env["PYTHONPATH"] = str(
        Path(__file__).resolve().parents[1] / "src")
    probe = _sp.run(
        [sys.executable, "-m", "firm", "pulse", "--workspace", str(probe_ws),
         "--firm-id", "live148", "--dry-run"],
        capture_output=True, text=True, env=probe_env, timeout=300)
    probe_lines = probe.stdout.strip().splitlines()
    probe_result = json.loads(probe_lines[-1]) if probe_lines else {}

    with capsys.disabled():
        print(f"[148] containment reading: "
              f"contained={probe_result.get('contained')!r} "
              f"supported={probe_result.get('containment_supported')!r} "
              f"flags={probe_result.get('containment_flags')!r} "
              f"reason={probe_result.get('containment_reason')!r}")

    if probe_result.get("contained") is not True:
        pytest.skip(
            f"[148] VOID: this host did not contain its pulse, so nothing "
            f"below can prove a tree died. reading={probe_result!r}")

    # --- TIER C: the hub's seam, a real Member, and the abort ---------------
    unit = f"pulse-live148-{int(time.time())}"
    log = ws / ".firm" / "pulse-logs" / f"{unit}.json"
    log.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, "-m", "firm", "pulse", "--workspace", str(ws),
            "--firm-id", "live148", "--source", "board"]
    wrapper = (
        "import subprocess, sys; "
        "from firm.core.proc import run_utf8; "
        f"rc = run_utf8({argv!r}, stdout=open({str(log)!r}, 'w'), "
        "stderr=subprocess.STDOUT).returncode; "
        "sys.exit(rc)")
    compile(wrapper, "<wrapper>", "exec")     # the same control, same reason

    dispatched = None
    try:
        dispatched = resolve_scheduler().spawn_detached(
            [sys.executable, "-c", wrapper], workdir=ws, env=env, unit=unit)

        # CONTROL 1: the Member actually started, and is alive, BEFORE the act.
        deadline = time.time() + 180
        gens: set[tuple[int, str]] = set()
        while time.time() < deadline:
            gens = _generations(marker)
            if gens and _alive(gens):
                break
            time.sleep(1.0)
        live_before = _alive(gens)
        with capsys.disabled():
            print(f"[148] dispatched via={dispatched.get('via')!r} "
                  f"pid={dispatched.get('pid')!r}")
            print(f"[148] member generations recorded: {sorted(gens)}")
            print(f"[148] alive before the act: {sorted(live_before)}")
        if not live_before:
            pytest.skip(
                f"[148] VOID: no Member was running before the act, so the "
                f"abort had nothing to prove. recorded={sorted(gens)} "
                f"log={log.read_text(encoding='utf-8')[-2000:]!r}")

        # --- THE ACT --------------------------------------------------------
        abort_env = dict(os.environ)
        abort_env["PYTHONPATH"] = str(
            Path(__file__).resolve().parents[1] / "src")
        abort = _sp.run(
            [sys.executable, "-m", "firm", "pulse", "--workspace", str(ws),
             "--firm-id", "live148", "--abort"],
            capture_output=True, text=True, env=abort_env, timeout=300)
        lines = abort.stdout.strip().splitlines()
        result = json.loads(lines[-1]) if lines else {}

        time.sleep(2.0)                       # let the job close its tree
        live_after = _alive(gens)
        rows = _run_row_status(ws)

        before_pids = {int(e[0]) for e in result.get("descendants_before", [])}
        member_pids = {pid for pid, _created in gens}

        with capsys.disabled():
            print(f"[148] abort rc={abort.returncode} result={result!r}")
            print(f"[148] descendants_before pids: {sorted(before_pids)}")
            print(f"[148] member pids: {sorted(member_pids)}")
            print(f"[148] alive after the act: {sorted(live_after)}")
            print(f"[148] member_run rows: {rows}")
            verdict = ("PASS" if (result.get("ok") is True
                                  and result.get("alive_after") == []
                                  and not live_after
                                  and member_pids & before_pids)
                       else "FAIL")
            print(f"[148] {verdict}")

        # THE SNAPSHOT SAW THE MEMBER. Asserted first, because `alive_after ==
        # []` is also what a walk that found nothing returns, and that walk
        # would pass every other assertion here while proving the reverse.
        assert member_pids & before_pids, (
            f"the pre-act snapshot did not contain the Member's generation: "
            f"snapshot={sorted(before_pids)} member={sorted(member_pids)}. "
            f"An empty walk passes `alive_after == []` for the wrong reason.")
        assert result.get("ok") is True, result
        assert abort.returncode == 0, abort.stdout + abort.stderr
        assert result.get("alive_after") == [], result
        assert not live_after, (
            f"the Member outlived the abort: {sorted(live_after)}")
        assert rows and all(r != "running" for r in rows), rows
    finally:
        # Survivors ended BY GENERATION, never by pattern.
        from firm.sched.base import run_cmd

        for pid, _created in _generations(marker):
            run_cmd(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=60)
        if dispatched and dispatched.get("pid"):
            run_cmd(["taskkill", "/F", "/T", "/PID",
                     str(dispatched["pid"])], timeout=60)
