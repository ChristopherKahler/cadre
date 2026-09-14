"""A child cadre starts never opens a window on the operator's screen (#119).

THE DEFECT. Every scheduled pulse opened a console window on the operator's
desktop, and closing that window killed the pulse (0xC000013A). The rule this
file holds, from the operator: no pulse, Member run, heartbeat, hook, install
or background task may open a console window, or any window.

WHERE A WINDOW COMES FROM. A console program started on Windows by a process
that has no console gets a brand-new console, and a new console has a window.
A console program started by a process that HAS a console inherits it and
opens nothing. Measured on Windows 10 19045 with a probe that could not open a
window (issue #119, lane F, "D1b BUILD NOTE" in the fork doc):

    started with CREATE_NO_WINDOW    its own console, only itself on it
    started with no flag             its parent's console
    started with DETACHED_PROCESS    no console; GetConsoleProcessList fails
    Ctrl+C on the parent's console   reaches the flagless child, not the
                                     flagged one
    GetConsoleWindow                 0 in every ATTACHED process, so it cannot
                                     tell attached from detached

THE RULE firm.core.proc keeps, as the orchestrator ruled on that table:

  - a process with NO console adds CREATE_NO_WINDOW to every child, because
    that child would otherwise get a window of its own;
  - a process WITH a console adds nothing, so its children inherit it: no new
    window, and Ctrl+C in a terminal still reaches a Member run;
  - the question is asked at every spawn and never cached, because a process
    can gain or lose a console while it runs;
  - when the question cannot be answered the flag is added: a lost Ctrl+C is
    the lesser harm, a window is the one the operator ruled out;
  - CREATE_NEW_CONSOLE and DETACHED_PROCESS are refused before anything
    starts, on every platform, because Win32 ignores CREATE_NO_WINDOW beside
    either one;
  - the caller's other flags are kept. ``spawn_detached`` passes its own
    CREATE_NO_WINDOW and CREATE_NEW_PROCESS_GROUP, so a Board-fired pulse gets
    a hidden console of its own even when the hub runs in a terminal.

HOW THIS RUNS ON EVERY HOST. The spawn is faked. proc.py's own ``sys`` name is
replaced, never the interpreter's ``sys.platform``. The Windows call is faked
at ``ctypes.WinDLL``, one layer UNDER the probe, so the probe's own code runs
in every arm. One leg needs the real Windows call; it is marked for Windows
and reads the answer in two real processes, one with a console and one
without, against an independent reader.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from firm.core import proc

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008

WRAPPERS = ("run_utf8", "popen_utf8")


def _call(wrapper: str, **kwargs):
    return getattr(proc, wrapper)(["child"], **kwargs)


@pytest.fixture
def spawned(monkeypatch):
    """The keywords firm.core.proc hands to subprocess, one dict per spawn."""
    calls: list[dict] = []

    def fake(argv, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(stdout="out", stderr="", returncode=0, pid=4242)

    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(subprocess, "Popen", fake)
    return calls


def _on(monkeypatch, platform: str) -> None:
    """What proc.py believes the platform is -- proc.py's own name only.

    ``raising=False`` because a proc.py that never reads the platform has no
    ``sys`` to replace, and that is exactly the tree these arms must go red on:
    measured, the strict form failed all 43 arms in this helper before a single
    assertion ran, including the 10 that hold on that tree.
    """
    monkeypatch.setattr(proc, "sys",
                        SimpleNamespace(platform=platform, stdin=sys.stdin,
                                        stdout=sys.stdout, stderr=sys.stderr),
                        raising=False)


class _Kernel32:
    """``ctypes.WinDLL("kernel32")``, answering the console question for us.

    One answer is used per call, so a test can change the answer between two
    spawns. An exception among the answers is raised from the call itself.
    """

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = 0

        def get_console_process_list(buffer, size):
            self.asked += 1
            answer = self.answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        self.GetConsoleProcessList = get_console_process_list


def _console(monkeypatch, *answers) -> _Kernel32:
    kernel = _Kernel32(*answers)
    monkeypatch.setattr(ctypes, "WinDLL",
                        lambda name, use_last_error=False: kernel,
                        raising=False)
    return kernel


def _flagged(kwargs: dict) -> bool:
    return bool((kwargs.get("creationflags") or 0) & CREATE_NO_WINDOW)


# ---------------------------------------------------------------------------
# a process with no console: every child is started without a window
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_a_process_with_no_console_starts_every_child_without_a_window(
        monkeypatch, spawned, wrapper):
    """The hub under pythonw, a detached spawn: a child here would get a window."""
    _on(monkeypatch, "win32")
    kernel = _console(monkeypatch, 0)
    _call(wrapper)
    assert kernel.asked == 1, "the console was never asked about"
    assert _flagged(spawned[0]), spawned[0]


# ---------------------------------------------------------------------------
# a process with a console: children inherit it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_a_process_with_a_console_lets_its_children_inherit_it(
        monkeypatch, spawned, wrapper):
    """No flag, so Ctrl+C on `firm pulse` in a terminal still reaches claude."""
    _on(monkeypatch, "win32")
    kernel = _console(monkeypatch, 3)
    _call(wrapper)
    assert kernel.asked == 1, "the console was never asked about"
    assert not _flagged(spawned[0]), spawned[0]


# ---------------------------------------------------------------------------
# a console question that cannot be answered: the flag goes on
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("failure", ["ctypes has no WinDLL",
                                     "kernel32 will not load",
                                     "the call itself raises"])
def test_a_console_question_that_cannot_be_answered_adds_the_flag(
        monkeypatch, spawned, wrapper, failure):
    _on(monkeypatch, "win32")
    if failure == "ctypes has no WinDLL":
        monkeypatch.delattr(ctypes, "WinDLL", raising=False)
    elif failure == "kernel32 will not load":
        def refuse(name, use_last_error=False):
            raise OSError("kernel32 would not load")
        monkeypatch.setattr(ctypes, "WinDLL", refuse, raising=False)
    else:
        _console(monkeypatch, OSError("GetConsoleProcessList failed"))
    _call(wrapper)
    assert _flagged(spawned[0]), (
        f"{failure}: the child was started without CREATE_NO_WINDOW, which on "
        "a process with no console is a window")


# ---------------------------------------------------------------------------
# asked at every spawn, never once
# ---------------------------------------------------------------------------

def test_the_console_is_asked_at_every_spawn(monkeypatch, spawned):
    """A process can gain or lose a console while it runs. An answer cached at
    import would keep describing the console it used to have."""
    _on(monkeypatch, "win32")
    kernel = _console(monkeypatch, 0, 3, 0)
    for wrapper in ("run_utf8", "popen_utf8", "run_utf8"):
        _call(wrapper)
    assert kernel.asked == 3, f"asked {kernel.asked} times for 3 spawns"
    assert [_flagged(c) for c in spawned] == [True, False, True], spawned


# ---------------------------------------------------------------------------
# the caller's own flags are kept
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("console, caller, expected", [
    (0, CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP),
    (3, CREATE_NEW_PROCESS_GROUP, CREATE_NEW_PROCESS_GROUP),
    # spawn_detached asks for a hidden console of its own even from a terminal
    (3, CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
     CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP),
])
def test_the_callers_own_flags_are_kept(monkeypatch, spawned, wrapper,
                                        console, caller, expected):
    _on(monkeypatch, "win32")
    _console(monkeypatch, console)
    _call(wrapper, creationflags=caller)
    got = spawned[0].get("creationflags")
    assert got == expected, f"creationflags {got!r}, expected {expected:#x}"


# ---------------------------------------------------------------------------
# a flag that opens a window is refused before anything starts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("flags, refused", [
    (CREATE_NEW_CONSOLE, ["CREATE_NEW_CONSOLE"]),
    (DETACHED_PROCESS, ["DETACHED_PROCESS"]),
    (CREATE_NEW_CONSOLE | DETACHED_PROCESS,
     ["CREATE_NEW_CONSOLE", "DETACHED_PROCESS"]),
    # what spawn_detached passed before D1c
    (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, ["DETACHED_PROCESS"]),
    # the no-window flag does not rescue it: Win32 ignores it beside either
    (CREATE_NO_WINDOW | CREATE_NEW_CONSOLE, ["CREATE_NEW_CONSOLE"]),
])
def test_a_flag_that_opens_a_window_is_refused_before_anything_starts(
        monkeypatch, spawned, wrapper, platform, flags, refused):
    """Raised, never stripped. A caller that asked for a new console expected
    one; quietly handing it a hidden console turns a design mistake into a
    mystery. Refused off Windows too, so the mistake fails in CI on Linux."""
    _on(monkeypatch, platform)
    _console(monkeypatch, 0)
    with pytest.raises(proc.WindowFlagRefused) as caught:
        _call(wrapper, creationflags=flags)
    assert spawned == [], "refused AND started: the child ran anyway"
    assert caught.value.refused == refused
    for name in refused:
        assert name in str(caught.value), str(caught.value)


# ---------------------------------------------------------------------------
# off Windows nothing is added
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("platform", ["linux", "darwin", "cygwin"])
def test_off_windows_a_child_gets_no_creation_flags(monkeypatch, spawned,
                                                    wrapper, platform):
    """``subprocess`` raises ValueError for non-zero creationflags off Windows."""
    _on(monkeypatch, platform)
    _console(monkeypatch, 0)
    _call(wrapper)
    assert not spawned[0].get("creationflags"), spawned[0]


# ---------------------------------------------------------------------------
# the real Windows call, in a process with a console and in one without
# ---------------------------------------------------------------------------
#
# WHY NEITHER CHILD CAN OPEN A WINDOW, because the operator's rule covers test
# controls too. Each child starts nothing. CREATE_NO_WINDOW gives it a console
# that has no window; DETACHED_PROCESS gives it no console at all.
#
# THE ONE WAY THIS LEG COULD STILL DRAW ONE, and the guard for it. A venv's
# python.exe on Windows is a launcher that starts the real interpreter as its
# own child. A launcher started DETACHED has no console to hand down, so that
# child would get a new console and a visible window. So the console child
# goes first and reports every process on its console; the detached child runs
# only if that list is exactly the child itself, which is what no launcher in
# the chain looks like.
#
# THE INDEPENDENT READER is CreateFileW("CONOUT$"): it opens the console's
# output buffer, which exists only for a process attached to a console. It
# shares no code with GetConsoleProcessList.

_PROBE_CHILD = '''
import ctypes, json, os
from firm.core import proc

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = ctypes.c_void_p
k32.CreateFileW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                            ctypes.c_void_p)
k32.CloseHandle.argtypes = (ctypes.c_void_p,)
k32.GetConsoleProcessList.argtypes = (ctypes.POINTER(ctypes.c_uint32),
                                      ctypes.c_uint32)
k32.GetConsoleProcessList.restype = ctypes.c_uint32
handle = k32.CreateFileW("CONOUT$", 0x80000000 | 0x40000000, 0x2, None, 3, 0,
                         None)
conout = handle not in (None, ctypes.c_void_p(-1).value)
if conout:
    k32.CloseHandle(handle)
pids = (ctypes.c_uint32 * 64)()
n = k32.GetConsoleProcessList(pids, 64)
print(json.dumps({
    "pid": os.getpid(),
    "console_pids": [int(pids[i]) for i in range(min(int(n), 64))],
    "probe": proc._has_console(),
    "conout": conout,
    "proc_file": proc.__file__,
}))
'''


def _interpreter() -> str:
    """The real interpreter, not a venv launcher, where Python can name it."""
    return getattr(sys, "_base_executable", None) or sys.executable


def _probe(flag: int) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    out = subprocess.run(
        [_interpreter(), "-c", _PROBE_CHILD], stdin=subprocess.DEVNULL,
        capture_output=True, encoding="utf-8", errors="replace", env=env,
        timeout=60, creationflags=flag)
    assert out.returncode == 0, (
        f"probe child (flags {flag:#x}) exited {out.returncode}:\n"
        f"{out.stdout[-1500:]}\n{out.stderr[-2500:]}")
    lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
    assert lines, f"probe child printed no result:\n{out.stdout[-1500:]}"
    return json.loads(lines[-1])


@pytest.mark.skipif(sys.platform != "win32",
                    reason="reads the real Windows console API")
def test_the_console_probe_reads_both_states_on_real_windows():
    attached = _probe(CREATE_NO_WINDOW)
    assert Path(attached["proc_file"]).resolve().is_relative_to(SRC.resolve()), (
        f"the child imported firm.core.proc from {attached['proc_file']}, "
        f"not from the tree under test at {SRC}")
    assert attached["console_pids"] == [attached["pid"]], (
        "the console child shares its console with "
        f"{attached['console_pids']}: a launcher sits between the test and "
        "the interpreter, and a DETACHED launcher would give the interpreter "
        "a visible window. The detached leg is not run.")
    assert attached["conout"] is True, (
        f"CONOUT$ did not open in a process with a console: {attached}")
    assert attached["probe"] is True, (
        f"the probe said no console where CONOUT$ opened: {attached}")

    detached = _probe(DETACHED_PROCESS)
    assert detached["console_pids"] == [], (
        f"a DETACHED_PROCESS child reported a console: {detached}")
    assert detached["conout"] is False, (
        f"CONOUT$ opened in a process with no console: {detached}")
    assert detached["probe"] is False, (
        f"the probe said console where there is none: {detached}")


# ---------------------------------------------------------------------------
# PART TWO -- spawn_detached, the Board-fired pulse (design D1c)
# ---------------------------------------------------------------------------
#
# The hub fires a Board pulse through WindowsScheduler.spawn_detached and
# returns. It used to pass DETACHED_PROCESS, which gives the pulse wrapper no
# console at all, so every console program the wrapper started -- the pulse's
# python, then each claude.exe -- got a window of its own. It now goes through
# firm.core.proc with CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP: a hidden
# console of its own that the pulse and its Members inherit, and that is not
# the hub's console, so Ctrl+C in the hub's terminal does not reach the pulse.
#
# The flag values are set on the subprocess module for the arm, because the
# names exist there only on Windows and spawn_detached reads them by name.

_WIN32_FLAG_NAMES = {
    "CREATE_NO_WINDOW": CREATE_NO_WINDOW,
    "CREATE_NEW_PROCESS_GROUP": CREATE_NEW_PROCESS_GROUP,
    "CREATE_NEW_CONSOLE": CREATE_NEW_CONSOLE,
    "DETACHED_PROCESS": DETACHED_PROCESS,
}


@pytest.mark.parametrize("hub_console", [3, 0, OSError("no answer")],
                         ids=["hub in a terminal", "hub with no console",
                              "console question fails"])
def test_a_board_fired_pulse_gets_a_hidden_console_of_its_own(
        monkeypatch, spawned, tmp_path, hub_console):
    from firm.sched.winsched import WindowsScheduler

    _on(monkeypatch, "win32")
    _console(monkeypatch, hub_console)
    for name, value in _WIN32_FLAG_NAMES.items():
        monkeypatch.setattr(subprocess, name, value, raising=False)

    out = WindowsScheduler(launcher_dir=tmp_path).spawn_detached(
        ["pulse-wrapper"], workdir=tmp_path, env={"CADRE_PROBE": "d1c"})

    assert len(spawned) == 1, f"{len(spawned)} spawns for one pulse"
    kw = spawned[0]
    flags = kw.get("creationflags") or 0
    assert not flags & DETACHED_PROCESS, (
        f"creationflags {flags:#x} carries DETACHED_PROCESS: the pulse has no "
        "console, so every console program it starts opens a window")
    assert flags == CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, f"{flags:#x}"
    assert kw.get("encoding") == "utf-8", (
        "spawned outside firm.core.proc, so neither the window rule nor the "
        "refusal applies to it")
    for stream in ("stdin", "stdout", "stderr"):
        assert kw.get(stream) is subprocess.DEVNULL, (
            f"{stream} is {kw.get(stream)!r}; a detached pulse holds no handle "
            "to the hub's console")
    assert kw.get("cwd") == str(tmp_path)
    assert kw.get("env", {}).get("CADRE_PROBE") == "d1c"
    assert out == {"via": "detached-popen", "pid": 4242}


# ---------------------------------------------------------------------------
# PART THREE -- exec_in_place: `cadre env exec`, the MCP server wrapper
# ---------------------------------------------------------------------------
#
# `cadre env exec -- <cmd>` is how a firm's .mcp.json starts an MCP server
# with the vault injected, so claude.exe runs it with the server's stdio
# pipes as its standard handles, and it replaces itself with the command.
# On POSIX that is a true exec. On Windows the C runtime starts the command
# with cadre's console and handles and exits, which opens a window exactly
# when cadre has no console. In that one case the command runs as a child
# through run_utf8, where the window rule applies, with cadre's own standard
# streams handed down and the child's exit code returned. Everywhere else it
# stays an exec, because MCP's stdio rides on the handles an exec keeps.

_EXEC_ENV = {"PATH": "/usr/bin", "VAULT_TOKEN": "from-the-vault"}


class _Execd(Exception):
    """Raised by the fake exec: a real one never returns."""


@pytest.fixture
def execs(monkeypatch):
    calls: list[tuple] = []

    def fake_execvpe(file, args, env):
        calls.append((file, list(args), dict(env)))
        raise _Execd(file)

    monkeypatch.setattr(os, "execvpe", fake_execvpe)
    return calls


@pytest.mark.parametrize("platform, console", [
    ("linux", 0), ("darwin", 0), ("win32", 3)],
    ids=["linux", "darwin", "windows with a console"])
def test_env_exec_stays_an_exec_where_that_opens_no_window(
        monkeypatch, spawned, execs, platform, console):
    _on(monkeypatch, platform)
    _console(monkeypatch, console, console)
    with pytest.raises(_Execd):
        proc.exec_in_place(["mcp-server", "--stdio"], _EXEC_ENV)
    assert execs == [("mcp-server", ["mcp-server", "--stdio"], _EXEC_ENV)]
    assert spawned == [], "started a child where an exec keeps the handles"


@pytest.mark.parametrize("console", [0, OSError("no answer")],
                         ids=["no console", "console question fails"])
def test_env_exec_with_no_console_runs_a_windowless_child_on_cadres_streams(
        monkeypatch, execs, console):
    _on(monkeypatch, "win32")
    _console(monkeypatch, console, console)
    started: list[dict] = []

    def fake_run(argv, **kwargs):
        started.append({"argv": argv, **kwargs})
        return SimpleNamespace(stdout=None, stderr=None, returncode=7)

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = proc.exec_in_place(["mcp-server", "--stdio"], _EXEC_ENV)

    assert execs == [], "exec'd with no console: the command gets a window"
    assert len(started) == 1, started
    kw = started[0]
    assert kw["argv"] == ["mcp-server", "--stdio"]
    assert _flagged(kw), kw
    assert kw.get("env") == _EXEC_ENV
    for stream in ("stdin", "stdout", "stderr"):
        assert kw.get(stream) is getattr(sys, stream), (
            f"{stream} was not handed down; an MCP server's stdio is on it")
    assert rc == 7, "the child's exit code did not come back"


def test_cadre_env_exec_goes_through_exec_in_place(monkeypatch, tmp_path):
    """The verb itself, so a future edit cannot quietly call os.execvpe again."""
    from firm.cli import env as env_cli

    class _Provider:
        def resolve(self, workspace):
            return {"VAULT_TOKEN": "from-the-vault"}

    seen: dict = {}

    def fake_exec_in_place(argv, env):
        seen["argv"], seen["env"] = argv, env
        return 5

    def no_raw_exec(*args):
        raise AssertionError("cadre env exec called os.execvpe itself")

    monkeypatch.setattr(env_cli, "resolve_provider", lambda: _Provider())
    monkeypatch.setattr(env_cli, "exec_in_place", fake_exec_in_place,
                        raising=False)
    monkeypatch.setattr(os, "execvpe", no_raw_exec)
    monkeypatch.delenv("VAULT_TOKEN", raising=False)

    rc = env_cli.run_env_exec(tmp_path, ["--", "mcp-server", "--stdio"])

    assert seen.get("argv") == ["mcp-server", "--stdio"], seen
    assert seen["env"]["VAULT_TOKEN"] == "from-the-vault"
    assert rc == 5
