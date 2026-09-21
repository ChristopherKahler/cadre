"""The launcher a Cadre scheduled task runs on Windows (#119, design D1a as ruled).

A task runs the firm's ``pythonw.exe`` on a generated stub, the stub hands over
to ``firm.sched.winlaunch.main``, and ``main`` starts the task's command through
``firm.core.proc``. It replaced ``conhost.exe --headless``, which hid the window
but was measured throwing every exit code away.

WHAT THIS FILE PINS, one condition of the orchestrator's ruling at a time:

  R1  the command starts through firm.core.proc, never raw subprocess, and its
      stdout and stderr are the log file itself, not a pipe;
  R2  the stub points sys.stdout and sys.stderr at the log BEFORE it imports
      any Cadre module; an import that raises leaves its traceback in the log
      and a non-zero exit; a log that cannot be opened exits non-zero before
      Cadre is imported;
  R5  a service runs its command again 5 seconds after every exit;
  R6  200,000 bytes on stdout and on stderr, interleaved, all reach the log,
      the exit code comes back, and nothing stalls.
  And the exit code itself: Task Scheduler's Last Result is the command's own,
  including a Windows status code too large for sys.exit to take unsigned.

HOW. The unit arms fake the spawn and the sleep. The stub arms run the stub the
product writes, with a real interpreter: python on Linux, pythonw.exe on
Windows, which is what a task runs.

On Windows the command a stub arm starts is ALSO pythonw.exe, on purpose.
pythonw has no console, so it cannot draw a console window even if the launcher
under test lost the no-window rule, and the operator's rule is that no test run
draws a window, a failing one included. Whether a console program started by
the launcher draws a window is not asserted here. The window instrument
measures it, on a private desktop first.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

STEM = "cadre-heartbeat-lab"


@pytest.fixture
def winlaunch():
    """Imported per test, so a tree without the module fails each arm alone."""
    from firm.sched import winlaunch as module
    return module


def _write(winlaunch, tmp_path, **overrides):
    fields = dict(argv=["pulse", "--firm-id", "lab"], env={"FIRM_ID": "lab"},
                  cwd=tmp_path, supervise=False, interval="30m")
    fields.update(overrides)
    return winlaunch.write_launcher(tmp_path / "sched", STEM, **fields)


class _Stop(Exception):
    """Raised by a fake sleep to end a supervise loop, which never returns."""


def _no_sleep(seconds):
    """A timer's sleep. A timer that sleeps was about to run its command again,
    and failing here keeps that mistake from hanging the run."""
    raise AssertionError("a timer slept, so it was about to run again")


# ---------------------------------------------------------------------------
# unit arms: the spawn and the sleep are faked
# ---------------------------------------------------------------------------


def test_the_command_starts_through_firm_core_proc(winlaunch):
    from firm.core import proc

    run = inspect.signature(winlaunch.main).parameters["run"].default
    assert run is proc.run_utf8, (
        f"main starts the command with {run!r}. Under pythonw only "
        "firm.core.proc adds CREATE_NO_WINDOW; a raw subprocess call from "
        "pythonw was measured opening a console window (M-W1, arm P2).")


def test_write_launcher_records_what_the_task_needs(winlaunch, tmp_path):
    stub = _write(winlaunch, tmp_path)
    assert stub == tmp_path / "sched" / f"{STEM}.pyw"
    spec = json.loads((tmp_path / "sched" / f"{STEM}.json")
                      .read_text(encoding="utf-8"))
    assert spec == {"stem": STEM, "argv": ["pulse", "--firm-id", "lab"],
                    "env": {"FIRM_ID": "lab"}, "cwd": str(tmp_path),
                    "supervise": False, "interval": "30m"}
    assert not (tmp_path / "sched" / f"{STEM}.log").exists(), (
        "the log belongs to the task's run, not to the install")


def test_a_timer_runs_its_command_once_with_its_output_on_the_log_itself(
        winlaunch, tmp_path, monkeypatch):
    monkeypatch.setenv("CADRE_WINLAUNCH_AMBIENT", "from-the-user")
    monkeypatch.setenv("FIRM_ID", "ambient")
    stub = _write(winlaunch, tmp_path)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=7)

    with open(tmp_path / "t.log", "w", encoding="utf-8") as log:
        code = winlaunch.main(stub.with_suffix(".json"), log, run=run,
                              sleep=_no_sleep)

    assert code == 7
    assert len(calls) == 1, f"{len(calls)} runs of a timer's command"
    argv, kw = calls[0]
    assert argv == ["pulse", "--firm-id", "lab"]
    assert kw["cwd"] == str(tmp_path)
    # Added over the user's own environment, as the .cmd launcher's `set` did.
    assert kw["env"]["CADRE_WINLAUNCH_AMBIENT"] == "from-the-user"
    assert kw["env"]["FIRM_ID"] == "lab", "the task's value must win"
    assert kw["stdout"] is log and kw["stderr"] is log, (
        "the command's output is not on the log's own handle; a pipe can "
        "fill and stall the run")
    assert kw["stdin"] is subprocess.DEVNULL


def test_a_timer_whose_command_cannot_start_raises_to_the_stub(winlaunch,
                                                              tmp_path):
    stub = _write(winlaunch, tmp_path)

    def run(argv, **kwargs):
        raise FileNotFoundError(2, "planted: no such program", argv[0])

    with open(tmp_path / "t.log", "w", encoding="utf-8") as log:
        with pytest.raises(FileNotFoundError):
            winlaunch.main(stub.with_suffix(".json"), log, run=run,
                           sleep=_no_sleep)


def test_a_service_runs_its_command_again_five_seconds_after_every_exit(
        winlaunch, tmp_path):
    stub = _write(winlaunch, tmp_path, supervise=True, interval=None)
    log_path = tmp_path / "s.log"
    codes = [1, 0, 9]
    runs, slept, log_at_sleep = [], [], []

    def run(argv, **kwargs):
        runs.append(argv)
        kwargs["stdout"].write(f"output of run {len(runs)}\n")
        return SimpleNamespace(returncode=codes[len(runs) - 1])

    def sleep(seconds):
        slept.append(seconds)
        log_at_sleep.append(log_path.read_text(encoding="utf-8"))
        if len(slept) == 3:
            raise _Stop

    with open(log_path, "w", encoding="utf-8") as log:
        with pytest.raises(_Stop):
            winlaunch.main(stub.with_suffix(".json"), log, run=run,
                           sleep=sleep)

    # A clean exit is restarted too: that is what the .cmd loop did.
    assert len(runs) == 3, runs
    assert slept == [5, 5, 5], slept
    for n, (text, code) in enumerate(zip(log_at_sleep, codes), start=1):
        # From the first byte: emptying the log without moving back to its
        # start leaves the next run writing after a hole the size of the last.
        assert text.startswith(f"output of run {n}"), repr(text[:80])
        assert f"exited {code}" in text, text
        others = [f"output of run {m}" for m in (1, 2, 3) if m != n]
        assert not any(o in text for o in others), (
            f"the log during backoff {n} still holds another run: {text!r}")


def test_a_service_whose_command_cannot_start_says_why_and_tries_again(
        winlaunch, tmp_path):
    stub = _write(winlaunch, tmp_path, supervise=True, interval=None)
    log_path = tmp_path / "s.log"
    attempts, log_at_sleep = [], []

    def run(argv, **kwargs):
        attempts.append(argv)
        if len(attempts) == 1:
            raise FileNotFoundError(2, "planted: no such program", argv[0])
        return SimpleNamespace(returncode=0)

    def sleep(seconds):
        log_at_sleep.append(log_path.read_text(encoding="utf-8"))
        if len(log_at_sleep) == 2:
            raise _Stop

    with open(log_path, "w", encoding="utf-8") as log:
        with pytest.raises(_Stop):
            winlaunch.main(stub.with_suffix(".json"), log, run=run,
                           sleep=sleep)

    assert len(attempts) == 2, "a command that could not start was not retried"
    assert "planted: no such program" in log_at_sleep[0], log_at_sleep[0]
    assert "could not start" in log_at_sleep[0], log_at_sleep[0]


@pytest.mark.parametrize("returncode, exit_with", [
    (0, 0), (7, 7), (0xC000013A, -1073741510), (0xFFFFFFFF, -1)],
    ids=["zero", "seven", "STATUS_CONTROL_C_EXIT", "all bits"])
def test_the_exit_code_comes_back_in_the_form_sys_exit_carries_whole(
        winlaunch, tmp_path, returncode, exit_with):
    """Measured on Windows, Python 3.12.6: sys.exit(3221225786) raised
    OverflowError and exited 0xFFFFFFFF; sys.exit(-1073741510) exited
    0xC000013A. The launcher hands sys.exit the second form."""
    stub = _write(winlaunch, tmp_path)
    with open(tmp_path / "t.log", "w", encoding="utf-8") as log:
        code = winlaunch.main(
            stub.with_suffix(".json"), log,
            run=lambda argv, **kw: SimpleNamespace(returncode=returncode),
            sleep=_no_sleep)
    assert code == exit_with


# ---------------------------------------------------------------------------
# stub arms: the stub the product writes, run by a real interpreter
# ---------------------------------------------------------------------------


def _pythonw_or_python() -> str:
    """What a task runs on Windows, and its stand-in everywhere else."""
    if sys.platform == "win32":
        return str(Path(sys.executable).with_name("pythonw.exe"))
    return sys.executable


def _command(code: str) -> list[str]:
    """A command for the launcher to start. pythonw on Windows: see the top."""
    return [_pythonw_or_python(), "-c", code]


def _run_stub(stub: Path, *pythonpath: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in (*pythonpath, SRC))
    return subprocess.run([_pythonw_or_python(), str(stub)], env=env,
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=120)


def _plant_firm(tmp_path: Path, winlaunch_body: str,
                package_body: str = "") -> Path:
    """A ``firm`` package that shadows the real one on PYTHONPATH."""
    root = tmp_path / "planted"
    (root / "firm" / "sched").mkdir(parents=True)
    (root / "firm" / "__init__.py").write_text(package_body, encoding="utf-8")
    (root / "firm" / "sched" / "__init__.py").write_text("", encoding="utf-8")
    (root / "firm" / "sched" / "winlaunch.py").write_text(winlaunch_body,
                                                          encoding="utf-8")
    return root


def test_the_stub_brings_the_commands_exit_code_back(winlaunch, tmp_path):
    marker = tmp_path / "command-ran"
    stub = _write(winlaunch, tmp_path, env={},
                  argv=_command(f"import sys; open({str(marker)!r}, 'w')"
                                ".write('ran'); sys.exit(7)"))
    done = _run_stub(stub)
    log = stub.with_suffix(".log")
    assert marker.exists(), (
        f"the command never ran (rc {done.returncode}); log: "
        f"{log.read_text(encoding='utf-8') if log.exists() else 'absent'}")
    assert done.returncode == 7, (
        f"rc {done.returncode}, the command exited 7; log: "
        f"{log.read_text(encoding='utf-8')}")


def test_a_command_that_raises_leaves_its_traceback_in_the_log(winlaunch,
                                                              tmp_path):
    stub = _write(winlaunch, tmp_path, env={},
                  argv=_command("import planted_missing_module_119"))
    done = _run_stub(stub)
    text = stub.with_suffix(".log").read_text(encoding="utf-8")
    assert done.returncode == 1, (done.returncode, text)
    assert "ModuleNotFoundError" in text and "planted_missing_module_119" in text, text


def test_an_import_that_raises_in_the_launcher_leaves_its_traceback(winlaunch,
                                                                   tmp_path):
    planted = _plant_firm(tmp_path, (
        "print('PLANTED: printed while firm.sched.winlaunch was importing')\n"
        "raise RuntimeError('PLANTED import failure')\n"))
    stub = _write(winlaunch, tmp_path, env={}, argv=_command("pass"))
    done = _run_stub(stub, planted)
    log = stub.with_suffix(".log")
    assert log.exists(), f"no log at all (rc {done.returncode})"
    text = log.read_text(encoding="utf-8")
    # Printed from inside the import, so it reaches the log only if stdout was
    # pointed at the log before the first Cadre import.
    assert "PLANTED: printed while firm.sched.winlaunch was importing" in text, text
    assert "Traceback" in text and "RuntimeError: PLANTED import failure" in text, text
    assert done.returncode == 3, (done.returncode, text)


def test_a_log_that_cannot_be_opened_exits_before_cadre_is_imported(winlaunch,
                                                                   tmp_path):
    imported = tmp_path / "firm-was-imported"
    planted = _plant_firm(
        tmp_path, "",
        package_body=f"open({str(imported)!r}, 'w').write('imported')\n")
    stub = _write(winlaunch, tmp_path, env={}, argv=_command("pass"))
    # A directory where the log file goes: opening it for writing fails on
    # every platform.
    stub.with_suffix(".log").mkdir()
    done = _run_stub(stub, planted)
    assert done.returncode == 2, done.returncode
    assert not imported.exists(), (
        "the stub imported firm before it had a log to write a failure to")

    # Control: with a log it can open, the same stub does import the planted
    # package, so the marker's absence above was a real reading. The planted
    # winlaunch has no main, so the import fails, into the log, with rc 3.
    stub.with_suffix(".log").rmdir()
    control = _run_stub(stub, planted)
    assert imported.exists(), (
        f"the planted firm package was never imported (rc {control.returncode})"
        ", so its marker cannot show whether the stub imports before the log")
    assert control.returncode == 3, control.returncode


def test_200_kb_on_stdout_and_stderr_interleaved_all_reach_the_log(winlaunch,
                                                                  tmp_path):
    chunk, rounds = 4096, 50                        # 204,800 bytes a stream
    stub = _write(winlaunch, tmp_path, env={}, argv=_command(
        "import sys\n"
        f"for _ in range({rounds}):\n"
        f"    sys.stdout.write('O' * {chunk}); sys.stdout.flush()\n"
        f"    sys.stderr.write('E' * {chunk}); sys.stderr.flush()\n"
        "sys.exit(7)\n"))
    done = _run_stub(stub)                          # a stall hits the timeout
    text = stub.with_suffix(".log").read_text(encoding="utf-8")
    assert done.returncode == 7, (done.returncode, text[-500:])
    assert text.count("O") == chunk * rounds, text.count("O")
    assert text.count("E") == chunk * rounds, text.count("E")


@pytest.mark.skipif(sys.platform != "win32",
                    reason="a Windows status code is a Windows exit code")
def test_a_windows_status_code_reaches_task_scheduler_whole(winlaunch,
                                                          tmp_path):
    stub = _write(winlaunch, tmp_path, env={},
                  argv=_command("import os; os._exit(-1073741510)"))
    done = _run_stub(stub)
    assert done.returncode & 0xFFFFFFFF == 0xC000013A, (
        f"{done.returncode & 0xFFFFFFFF:#010x}; log: "
        f"{stub.with_suffix('.log').read_text(encoding='utf-8')}")


def test_a_reinstall_drops_the_previous_launchers_containment_answer(
        winlaunch, tmp_path):
    """#141: `write_launcher` unlinks the old containment record. NO leg held it.

    avocet's FINDING 2, and it is a missing leg rather than a defect: the
    behaviour is correct and commented, and removing the `unlink` turned
    nothing red. A rule nothing can fail is a rule that leaves the day someone
    tidies the line away.

    WHY THE BEHAVIOUR IS RIGHT, which is what this pins. The record answers
    "is THIS launcher's tree contained". A record left by the launcher a
    reinstall just replaced is an answer about a process that no longer
    exists, and `status()` would report a freshly installed task as contained
    before any launcher of this install had run -- a true-LOOKING reading,
    which is worse than no reading at all. Absent until answered is the honest
    state.

    The record is built with `record_containment`, the function that writes it
    in production, so this cannot pass against a file shaped differently from
    the real one.
    """
    from firm.sched import winjob

    _write(winlaunch, tmp_path)
    sched = tmp_path / "sched"
    record = winlaunch.record_containment(
        sched, STEM, winjob.Containment(True, "", 0x00002000))
    assert record.exists(), "precondition: the arm never wrote a record"

    _write(winlaunch, tmp_path)

    assert not record.exists(), (
        "a reinstall kept the PREVIOUS launcher's containment answer, so "
        "`heartbeat status` reports a task that has never run as contained, "
        "on the word of a process that no longer exists")
