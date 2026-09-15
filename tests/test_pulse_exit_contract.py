"""``firm pulse`` exits 0 if and only if its result says ``ok: true`` (#128).

A scheduler that starts the pulse sees nothing but the exit code: Task
Scheduler's Last Result, systemd's failed state, launchd's last exit status.
Before this change, in each of these situations the exit code and the printed
result said different things (measured at efa0f6d8):

    no firm database                        ok: false   exit 0
    a Member run failed or timed out        ok: false   exit 0
    one Member failed while another worked  ok: true    exit 0
    a notify rail that does not resolve     ok: false   exit 0
    --drain-queue with a request abandoned  ok: false   exit 0
    --abort with no firm database           ok: true    exit 0
    --abort with no resolvable firm id      ok: true    exit 1
    an error outside the one try            a traceback and no result line

TWO DETECTORS, WATCHING DIFFERENT THINGS (law 39).

* THE GUARD reads ``cli/pulse.py`` with ``ast``. In the four functions a pulse
  can end in, every ``return`` hands its result to ``_exit_with``, which prints
  the line and takes the exit code from it, or hands off to another of the
  four. None of them prints on its own, calls ``sys.exit`` or can run off its
  end, ``run_pulse`` is one ``try`` that catches everything, and each
  function's count of returns is pinned, so a new way out cannot arrive without
  someone adding a leg for it below.
* THE LEGS run ``python -m firm pulse`` as a child process, one per row of the
  contract, and assert the child's real exit code beside its real last stdout
  line. That is the channel a scheduler and the hub read (law 42). A call to
  ``run_pulse`` in this process would skip argparse, ``main()`` and
  ``sys.exit``.

A return that skips ``_exit_with`` while printing the same line reddens only the
guard. A result whose ``ok`` is wrong while ``_exit_with`` still prints it
reddens only a leg. Each detector sees a failure the other cannot.

THE STAND-IN MEMBER IS NEVER CLAUDE. A pulse that dispatches spawns whatever
``CADRE_CLAUDE_BIN`` names, and conftest points that at a path that does not
exist, so by default nothing resolves. Legs that need a Member run point it at
a stand-in: this interpreter, which rejects claude's flags and exits non-zero
(a real failed run, on every platform), or a ``#!/bin/sh`` script that prints a
completed run's stream-json, where the kernel runs such scripts. Legs that must
not dispatch name this interpreter too, so a leg that dispatched by mistake
shows a failed run rather than spawning anything else. Every other fence is
conftest's and reaches the child through its environment: ``BASE_HOME`` under
the test's temp directory, ``CADRE_NO_BASE``, and the local secrets provider.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import linecache
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

import firm.cli.pulse as pulse_cli
from firm.core.db import connect
from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.pulse import dblock
from firm.services import base_export, pulse_queue
from tests.platform_marks import (
    host_cannot_survive_sigterm,
    spawn_layer_rejects_this_platforms_binaries,
    stand_in_member_needs_a_shebang_host,
)

FIRM = "exitco"
OTHER_FIRM = "otherco"

#: The four functions a pulse can end in, and the one every ending goes through.
ENDINGS = ("run_pulse", "_run_resolved", "_drain_queue", "_handle_abort")
EXIT_FUNCTION = "_exit_with"

#: How many ``return`` statements each ending has. A new way out of the pulse
#: changes one of these numbers: add a leg below that drives it, then the count.
RETURNS = {"run_pulse": 5, "_run_resolved": 5, "_drain_queue": 1, "_handle_abort": 3}

#: This interpreter, standing in for claude. It rejects ``--print`` and exits
#: non-zero, so a pulse that dispatches a Member onto it records a real failed
#: run on every platform. Legs that must not dispatch name it as well: it
#: resolves, so the pulse passes its runtime preflight, and a leg that
#: dispatched by mistake shows a failed run instead of spawning anything else.
REFUSING_MEMBER = sys.executable

#: Every Member's Contract when a run has to COMPLETE: a validator in force, so
#: the runner does not ask for close-out evidence a stand-in cannot produce, and
#: one the stand-in's one-word result passes.
COMPLETING_CONTRACT = {
    "validation_config": {"validators": [{"name": "min_word_count", "threshold": 1}]},
}

#: A notify variable nothing sets, so a firm whose rail reads it cannot resolve.
UNSET_RAIL_ENV = "CADRE_T128_WEBHOOK_NEVER_SET"


# ---------------------------------------------------------------------------
# A firm to pulse
# ---------------------------------------------------------------------------

def _firm(ws: Path, *, members=(("MEM-001", "Lead", "active"),),
          contract: dict | None = None, notify_config: dict | None = None,
          other_firm: bool = False) -> Path:
    """A firm database at *ws* with nothing queued: the firm, its Members, and an
    operation and a project to hang Units on. *contract*, when given, is every
    Member's Contract. *other_firm* adds a second firm with one active Member,
    which leaves the database without a single firm to resolve to."""
    conn = connect(ws / ".firm" / "firm.db")
    try:
        apply_migrations(conn)
        firm = {"id": FIRM, "name": "Exit Co"}
        if notify_config is not None:
            firm["notify_config"] = notify_config
        create(conn, "firm", firm)
        if contract is not None:
            create(conn, "contract", {"id": "CON-001", "firm_id": FIRM, "name": "Stand-in",
                                      "runtime_type": "claude_code", **contract})
        for member_id, name, status in members:
            create(conn, "member", {
                "id": member_id, "firm_id": FIRM, "name": name, "role": "worker",
                "status": status,
                "contract_id": "CON-001" if contract is not None else None,
            })
        create(conn, "operation", {"id": "OPS-001", "firm_id": FIRM, "name": "Ops"})
        create(conn, "project", {"id": "PROJ-001", "firm_id": FIRM,
                                 "operation_id": "OPS-001", "name": "Work",
                                 "status": "in_progress", "due_date": "2099-12-31"})
        if other_firm:
            create(conn, "firm", {"id": OTHER_FIRM, "name": "Other Co"})
            create(conn, "member", {"id": "MEM-900", "firm_id": OTHER_FIRM,
                                    "name": "Elsewhere", "role": "worker",
                                    "status": "active"})
        conn.commit()
    finally:
        conn.close()
    return ws


def _units(ws: Path, *rows: dict) -> None:
    conn = connect(ws / ".firm" / "firm.db")
    try:
        for row in rows:
            create(conn, "unit", {"firm_id": FIRM, "project_id": "PROJ-001",
                                  "name": f"Unit {row['id']}", **row})
        conn.commit()
    finally:
        conn.close()


def _hold_lock(ws: Path, holder: str) -> None:
    """Write the pulse lock's row as *holder*, with a fresh heartbeat."""
    conn = connect(ws / ".firm" / "firm.db")
    try:
        assert dblock.acquire(conn, FIRM, holder)
    finally:
        conn.close()


def _rows(ws: Path, sql: str) -> list[tuple]:
    conn = connect(ws / ".firm" / "firm.db")
    try:
        return [tuple(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def _stand_in(tmp_path: Path) -> str:
    """A Member binary that is not claude, for runs that must complete or time out.

    It reads ``CADRE_MEMBER_ID``, which the spawn layer exports to every Member
    run: an id starting ``MEM-FAIL`` exits 3, one starting ``MEM-SLOW`` sleeps
    past any timeout, and any other prints what a completed ``claude --print``
    prints. ``exec`` puts the timeout's kill on the sleeping process itself
    rather than on a shell whose child would keep the pipe open.
    """
    path = tmp_path / "stand-in-member"
    path.write_text(textwrap.dedent("""\
        #!/bin/sh
        case "$CADRE_MEMBER_ID" in
          MEM-FAIL*) echo "stand-in member failing on purpose" >&2; exit 3 ;;
          MEM-SLOW*) exec sleep 60 ;;
        esac
        printf '%s\\n' '{"type":"assistant","message":{"content":[{"type":"text","text":"done"}]}}'
        printf '%s\\n' '{"type":"result","subtype":"success","is_error":false,"usage":{"input_tokens":1,"output_tokens":1}}'
        """), encoding="utf-8")
    path.chmod(0o755)
    return str(path)


# ---------------------------------------------------------------------------
# The channel a scheduler reads
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class _Exit:
    rc: int
    result: dict | None     # the last stdout line, parsed; None when it is not an object
    output: str             # the exit code, stdout and stderr, for failure messages


def _child_env(claude: str | None = None) -> dict[str, str]:
    env = dict(os.environ)                  # conftest's fences travel in here
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    env.pop("FIRM_ID", None)                # the database names the firm, or fails to
    env.pop("CADRE_DB_URL", None)           # the leg's own file, never a shared database
    env.pop(UNSET_RAIL_ENV, None)
    if claude is not None:
        env["CADRE_CLAUDE_BIN"] = claude
    return env


def _pulse(ws: Path, *args: str, claude: str | None = None, timeout: int = 120) -> _Exit:
    """``python -m firm pulse --workspace <ws> <args>``, as a child process."""
    proc = subprocess.run(
        [sys.executable, "-m", "firm", "pulse", "--workspace", str(ws), *args],
        capture_output=True, env=_child_env(claude), timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    lines = out.strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else None
    except ValueError:
        result = None
    return _Exit(proc.returncode, result if isinstance(result, dict) else None,
                 f"rc {proc.returncode}\nstdout:\n{out}\nstderr:\n{err}")


def _assert_exit(run: _Exit, *, rc: int, ok: bool, **fields) -> dict:
    """One row of the contract: the last line is the result, its ``ok`` is
    *ok*, the exit code is *rc*, and the fields that say which row it is."""
    assert run.result is not None, f"the last stdout line is not a JSON object\n{run.output}"
    assert run.result.get("ok") is ok, run.output
    assert run.rc == rc, run.output
    for key, value in fields.items():
        assert run.result.get(key) == value, f"{key}\n{run.output}"
    return run.result


def test_the_child_imports_the_pulse_module_this_file_reads():
    """Every leg is about the tree the guard reads. A child that imported firm
    from another checkout would grade that one (see conftest's first block)."""
    proc = subprocess.run(
        [sys.executable, "-c", "import firm.cli.pulse as p; print(p.__file__)"],
        capture_output=True, env=_child_env(), timeout=120, stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    child = Path(proc.stdout.decode("utf-8").strip()).resolve()
    assert child == Path(pulse_cli.__file__).resolve()


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------

_BYPASSES = {"print", "sys.exit", "exit", "quit", "os._exit", "sys.stdout.write"}


def _own_nodes(fn: ast.FunctionDef):
    """Every node in *fn*'s body, without entering a nested function or class:
    a ``return`` in there ends that function, not the pulse."""
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        yield node
        stack.extend(child for child in ast.iter_child_nodes(node)
                     if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                               ast.Lambda, ast.ClassDef)))


def _cannot_run_off(body: list[ast.stmt]) -> bool:
    """True when control cannot reach the end of *body*.

    A function that runs off its end returns None, and ``sys.exit(None)`` is
    exit 0: a way out that prints nothing and reports success.
    """
    if not body:
        return False
    last = body[-1]
    if isinstance(last, (ast.Return, ast.Raise)):
        return True
    if isinstance(last, ast.If):
        return _cannot_run_off(last.body) and _cannot_run_off(last.orelse)
    if isinstance(last, ast.With):
        return _cannot_run_off(last.body)
    if isinstance(last, ast.Try):
        if _cannot_run_off(last.finalbody):
            return True
        return (_cannot_run_off(last.orelse or last.body)
                and all(_cannot_run_off(h.body) for h in last.handlers))
    return False


def _exit_violations(source: str, endings=ENDINGS) -> tuple[list[str], dict[str, int]]:
    """Every way out of *endings* in *source* that does not go through the exit
    function, one line each, and each ending's count of returns."""
    functions = {node.name: node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef)}
    problems: list[str] = []
    counts: dict[str, int] = {}
    for name in endings:
        fn = functions.get(name)
        if fn is None:
            problems.append(f"{name} is not a module-level function")
            continue
        counts[name] = 0
        for node in _own_nodes(fn):
            if isinstance(node, ast.Return):
                counts[name] += 1
                value = node.value
                callee = (value.func.id if isinstance(value, ast.Call)
                          and isinstance(value.func, ast.Name) else None)
                if callee != EXIT_FUNCTION and callee not in endings:
                    shown = ast.unparse(value) if value is not None else ""
                    problems.append(
                        f"{name} line {node.lineno}: `return {shown}` neither goes "
                        f"through {EXIT_FUNCTION} nor hands off to one of {endings}")
            elif isinstance(node, ast.Call) and ast.unparse(node.func) in _BYPASSES:
                problems.append(f"{name} line {node.lineno}: {ast.unparse(node.func)}() "
                                f"outside {EXIT_FUNCTION}")
            elif (isinstance(node, ast.Raise) and node.exc is not None
                  and "SystemExit" in ast.unparse(node.exc)):
                problems.append(f"{name} line {node.lineno}: raise SystemExit")
        if not _cannot_run_off(fn.body):
            problems.append(f"{name} can run off its end and return None, which exits 0")
    if "run_pulse" in endings and "run_pulse" in functions:
        body = [s for s in functions["run_pulse"].body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        catches_all = (len(body) == 1 and isinstance(body[0], ast.Try)
                       and any(h.type is not None and ast.unparse(h.type) == "Exception"
                               for h in body[0].handlers))
        if not catches_all:
            problems.append("run_pulse is not one try with an `except Exception`, so an "
                            "error outside it leaves a traceback and no result line")
    return problems, counts


def test_every_way_out_of_the_pulse_goes_through_the_exit_function():
    problems, counts = _exit_violations(Path(pulse_cli.__file__).read_text(encoding="utf-8"))

    assert problems == [], "\n".join(problems)
    assert counts == RETURNS, (
        f"the pulse's ways out changed: {counts}, pinned {RETURNS}. Drive the new "
        "one through `python -m firm pulse` below, then update RETURNS.")


def _planted(inner: str, handler: str = "return _exit_with({'ok': False})") -> str:
    """A ``run_pulse`` shaped like the real one, with *inner* inside its try."""
    return ("def run_pulse():\n    try:\n" + textwrap.indent(inner, " " * 8)
            + "\n    except Exception as exc:\n" + textwrap.indent(handler, " " * 8) + "\n")


#: Every shape a way out of the pulse can take, enumerated from what Python
#: allows in these functions rather than from the guard's own cases (law 31),
#: each with the words only its own check prints (law 40).
PLANTED_WAYS_OUT = {
    "a bare exit code": (_planted("return 0"), "neither goes through"),
    "a code held in a name": (_planted("rc = 1\nreturn rc"), "neither goes through"),
    "a call to something else": (_planted("return int(_exit_with({}))"),
                                 "neither goes through"),
    "an exit code in the handler": (_planted("return _exit_with({})", "return 1"),
                                    "neither goes through"),
    "a print of its own": (_planted("print('{}')\nreturn _exit_with({})"),
                           "print() outside"),
    "sys.exit": (_planted("sys.exit(_exit_with({}))"), "sys.exit() outside"),
    "raise SystemExit": (_planted("raise SystemExit(0)"), "raise SystemExit"),
    "running off the end": (_planted("if flag:\n    return _exit_with({})"),
                            "can run off its end"),
    "no catch-all": ("def run_pulse():\n    return _exit_with({})\n", "not one try"),
}


@pytest.mark.parametrize("shape", sorted(PLANTED_WAYS_OUT))
def test_control_the_guard_fires_on_every_shape_of_a_way_out(shape):
    source, words = PLANTED_WAYS_OUT[shape]

    problems, _ = _exit_violations(source, endings=("run_pulse",))

    assert any(words in p for p in problems), (shape, problems)


def test_control_the_guard_is_silent_on_a_clean_ending():
    """The other half of the control above: a guard that fired on everything
    would pass every planted shape."""
    problems, counts = _exit_violations(_planted("return _exit_with({})"),
                                        endings=("run_pulse",))

    assert problems == []
    assert counts == {"run_pulse": 2}


# ---------------------------------------------------------------------------
# The legs: one per row of the contract, through the real CLI
# ---------------------------------------------------------------------------

@stand_in_member_needs_a_shebang_host
def test_every_member_run_completed_exits_0(tmp_path):
    ws = _firm(tmp_path / "ws", contract=COMPLETING_CONTRACT,
               members=(("MEM-001", "Lead", "active"), ("MEM-002", "Second", "active")))
    _units(ws, {"id": "UNIT-001", "assignee_member_id": "MEM-001"},
           {"id": "UNIT-002", "assignee_member_id": "MEM-002"})

    run = _pulse(ws, claude=_stand_in(tmp_path))

    _assert_exit(run, rc=0, ok=True, ran=2, errors=0)
    # The artifact, not only the line: both runs really completed.
    assert _rows(ws, "SELECT id, status FROM member_run ORDER BY id") == [
        ("RUN-001", "completed"), ("RUN-002", "completed")], run.output


@spawn_layer_rejects_this_platforms_binaries
def test_nothing_due_exits_0(tmp_path):
    ws = _firm(tmp_path / "ws")

    run = _pulse(ws, claude=REFUSING_MEMBER)

    result = _assert_exit(run, rc=0, ok=True, ran=0, errors=0)
    assert result.get("skip_reasons") == {"load=0 (no queued Units)": 1}, run.output


@spawn_layer_rejects_this_platforms_binaries
def test_a_failed_member_run_exits_1(tmp_path):
    ws = _firm(tmp_path / "ws")
    _units(ws, {"id": "UNIT-001", "assignee_member_id": "MEM-001"})

    run = _pulse(ws, claude=REFUSING_MEMBER)

    result = _assert_exit(run, rc=1, ok=False, ran=0, errors=1)
    [detail] = result["error_details"]
    # Which writer recorded it: orchestrator.py's failed-or-timed-out branch
    # stores the runner's result dict, the exception branch stores a string.
    assert detail["member"] == "MEM-001" and isinstance(detail["error"], dict), run.output
    assert detail["error"]["status"] == "failed" and detail["error"]["returncode"], run.output
    assert _rows(ws, "SELECT status FROM member_run") == [("failed",)], run.output


@stand_in_member_needs_a_shebang_host
def test_a_timed_out_member_run_exits_1(tmp_path):
    ws = _firm(tmp_path / "ws", members=(("MEM-SLOW-001", "Slow", "active"),),
               contract={"pulse_config": {"timeout_sec": 1}})
    _units(ws, {"id": "UNIT-001", "assignee_member_id": "MEM-SLOW-001"})

    run = _pulse(ws, claude=_stand_in(tmp_path))

    result = _assert_exit(run, rc=1, ok=False, ran=0, errors=1)
    assert result["error_details"][0]["error"]["status"] == "timed_out", run.output


@stand_in_member_needs_a_shebang_host
def test_one_failed_member_beside_one_that_worked_exits_1(tmp_path):
    """The partial failure. ``ok`` used to be ``not (errors and not ran)``, which
    is true here, so the scheduler recorded a clean run over a failed Member."""
    ws = _firm(tmp_path / "ws", contract=COMPLETING_CONTRACT,
               members=(("MEM-001", "Lead", "active"), ("MEM-FAIL-002", "Failing", "active")))
    _units(ws, {"id": "UNIT-001", "assignee_member_id": "MEM-001"},
           {"id": "UNIT-002", "assignee_member_id": "MEM-FAIL-002"})

    run = _pulse(ws, claude=_stand_in(tmp_path))

    result = _assert_exit(run, rc=1, ok=False, ran=1, errors=1)
    [detail] = result["error_details"]
    assert detail["member"] == "MEM-FAIL-002", run.output
    assert detail["error"]["status"] == "failed" and detail["error"]["returncode"] == 3, run.output


@spawn_layer_rejects_this_platforms_binaries
def test_every_member_run_failed_exits_1(tmp_path):
    """Both formulas for ``ok`` agree here, so the partial-failure mutation must
    leave this leg green: it is the control that the mutation hits only its row."""
    ws = _firm(tmp_path / "ws",
               members=(("MEM-001", "Lead", "active"), ("MEM-002", "Second", "active")))
    _units(ws, {"id": "UNIT-001", "assignee_member_id": "MEM-001"},
           {"id": "UNIT-002", "assignee_member_id": "MEM-002"})

    run = _pulse(ws, claude=REFUSING_MEMBER)

    _assert_exit(run, rc=1, ok=False, ran=0, errors=2)


@spawn_layer_rejects_this_platforms_binaries
def test_a_configured_notify_rail_that_does_not_resolve_exits_1(tmp_path):
    ws = _firm(tmp_path / "ws", notify_config={
        "provider": "webhook", "remind_hours": 24, "webhook_url_env": UNSET_RAIL_ENV})

    run = _pulse(ws, claude=REFUSING_MEMBER)

    result = _assert_exit(run, rc=1, ok=False, ran=0, errors=1)
    [detail] = result["error_details"]
    assert detail["member"] is None, run.output
    assert UNSET_RAIL_ENV in detail["error"]["notify_rail_unresolvable"], run.output


@pytest.mark.parametrize("dry_run", [False, True], ids=["live", "dry-run"])
def test_no_firm_database_exits_1(tmp_path, dry_run):
    ws = tmp_path / "ws"
    ws.mkdir()

    run = _pulse(ws, *(["--dry-run"] if dry_run else []))

    _assert_exit(run, rc=1, ok=False, reason="db-not-found", workspace=str(ws.resolve()))


def test_a_firm_id_that_does_not_resolve_exits_1(tmp_path):
    ws = _firm(tmp_path / "ws", other_firm=True)

    run = _pulse(ws)

    _assert_exit(run, rc=1, ok=False, reason="firm-id-unresolved")


@spawn_layer_rejects_this_platforms_binaries
def test_a_held_pulse_lock_exits_1(tmp_path):
    ws = _firm(tmp_path / "ws")
    _hold_lock(ws, "other-host-t128:1:aaaa")

    run = _pulse(ws, claude=REFUSING_MEMBER)

    _assert_exit(run, rc=1, ok=False, reason="pulse-already-running")


def test_a_member_runtime_that_is_not_wired_exits_1(tmp_path):
    """conftest's CADRE_CLAUDE_BIN names a path that does not exist."""
    ws = _firm(tmp_path / "ws")

    run = _pulse(ws)

    _assert_exit(run, rc=1, ok=False, reason="runtime-not-wired")


@spawn_layer_rejects_this_platforms_binaries
def test_an_error_inside_the_pulse_exits_1(tmp_path):
    """A firm with no Members makes ``pulse()`` refuse, inside the try that
    always printed ``reason: error``."""
    ws = _firm(tmp_path / "ws", members=())

    run = _pulse(ws, claude=REFUSING_MEMBER)

    result = _assert_exit(run, rc=1, ok=False, reason="error")
    assert "zero active members" in result["message"], run.output


@pytest.mark.parametrize("dry_run", [False, True], ids=["live", "dry-run"])
def test_an_error_outside_every_branch_still_prints_a_result_and_exits_1(tmp_path, dry_run):
    """The database path is a directory, so opening it raises before any branch
    that prints. That used to be a traceback and no result line (#128 U4)."""
    ws = tmp_path / "ws"
    (ws / ".firm" / "firm.db").mkdir(parents=True)

    run = _pulse(ws, *(["--dry-run"] if dry_run else []))

    result = _assert_exit(run, rc=1, ok=False, reason="error")
    assert result.get("message"), run.output


@spawn_layer_rejects_this_platforms_binaries
def test_exports_that_could_not_be_written_do_not_fail_the_pulse(tmp_path):
    """The control for the rule: ``base_export: false`` is a falsy field in a
    result whose ``ok`` is true, and the exit code follows ``ok`` alone
    (``cli/pulse.py``: an export failure must not turn a pulse that ran into one
    that errored)."""
    ws = _firm(tmp_path / "ws")
    export_dir = (ws / base_export.state_dir()
                  / Path(base_export.declared_exports()[0]["file"]).parent)
    export_dir.parent.mkdir(parents=True, exist_ok=True)
    export_dir.write_text("a file where the export directory goes", encoding="utf-8")

    run = _pulse(ws, claude=REFUSING_MEMBER)

    result = _assert_exit(run, rc=0, ok=True, base_export=False)
    assert result.get("base_export_reason"), run.output


def test_a_dry_run_with_nothing_due_exits_0(tmp_path):
    ws = _firm(tmp_path / "ws")

    run = _pulse(ws, "--dry-run")

    _assert_exit(run, rc=0, ok=True, dry_run=True, ran=0)


# --- --drain-queue ----------------------------------------------------------

def _request_pulse(ws: Path) -> None:
    conn = connect(ws / ".firm" / "firm.db")
    try:
        pulse_queue.request_pulse(conn, FIRM, requested_by="board")
        conn.commit()
    finally:
        conn.close()


@spawn_layer_rejects_this_platforms_binaries
def test_draining_an_empty_queue_exits_0(tmp_path):
    ws = _firm(tmp_path / "ws")

    run = _pulse(ws, "--drain-queue", claude=REFUSING_MEMBER)

    _assert_exit(run, rc=0, ok=True, drained=0)


@spawn_layer_rejects_this_platforms_binaries
def test_draining_a_request_whose_pulse_worked_exits_0(tmp_path):
    ws = _firm(tmp_path / "ws")
    _request_pulse(ws)

    run = _pulse(ws, "--drain-queue", claude=REFUSING_MEMBER)

    result = _assert_exit(run, rc=0, ok=True, drained=1)
    assert result["results"][0]["ok"] is True, run.output


@spawn_layer_rejects_this_platforms_binaries
def test_draining_a_request_whose_pulse_failed_exits_1(tmp_path):
    """#128 U1: the drain printed ``ok: all(results)`` and then returned 0."""
    ws = _firm(tmp_path / "ws")
    _units(ws, {"id": "UNIT-001", "assignee_member_id": "MEM-001"})
    _request_pulse(ws)

    run = _pulse(ws, "--drain-queue", claude=REFUSING_MEMBER)

    result = _assert_exit(run, rc=1, ok=False, drained=1)
    assert result["results"][0]["ok"] is False, run.output
    assert result["results"][0]["errors"] == 1, run.output


# --- a pulse connection that fails once the lock is held ----------------------

def _fail_the_pulse_connection(monkeypatch) -> tuple[list[str], list[threading.Event]]:
    """Make the connection a pulse opens for its own work fail, after the lock is taken.

    In this process, because only a monkeypatch can fail one connection and not
    the others: resolving the firm, taking the lock, claiming a request and
    releasing the lock all still connect, so the leg can read whether the lock
    was let go. The failing sites are the two ``conn = connect(`` lines in
    ``_run_resolved`` and ``_drain_queue`` (osprey's G1 ruling); the pulse's
    other connections are named ``rconn``, ``lconn`` and ``qconn``, and the
    same line in ``_handle_abort`` and the heartbeat is in another function.

    Returns the functions whose connection was failed, so a leg proves the
    fault fired, and the stop events handed to the lock's heartbeat thread, so
    a leg can see the heartbeat was told to stop.
    """
    import firm.pulse.spawn as spawn_mod

    real_connect, real_heartbeat = pulse_cli.connect, pulse_cli._start_heartbeat
    fired: list[str] = []
    stops: list[threading.Event] = []

    def connect(db_path):
        caller = sys._getframe(1)
        line = linecache.getline(caller.f_code.co_filename, caller.f_lineno).strip()
        if (caller.f_code.co_name in ("_run_resolved", "_drain_queue")
                and line.startswith("conn = connect(")):
            fired.append(caller.f_code.co_name)
            raise sqlite3.OperationalError("the pulse's own connection could not be opened")
        return real_connect(db_path)

    def start_heartbeat(db_path, firm_id, holder, stop):
        stops.append(stop)
        return real_heartbeat(db_path, firm_id, holder, stop)

    monkeypatch.setattr(pulse_cli, "connect", connect)
    monkeypatch.setattr(pulse_cli, "_start_heartbeat", start_heartbeat)
    monkeypatch.setattr(spawn_mod, "resolve_claude_bin", lambda: (REFUSING_MEMBER, "test"))
    return fired, stops


def test_a_pulse_connection_that_fails_after_the_lock_lets_the_lock_go(
        tmp_path, monkeypatch, capsys):
    """The connection was opened after the lock and its heartbeat, outside the
    try whose finally releases them, so a failure there left the lock held for
    its 10-minute TTL and the next pulse bounced off it."""
    ws = _firm(tmp_path / "ws")
    fired, stops = _fail_the_pulse_connection(monkeypatch)

    rc = pulse_cli.run_pulse(ws)

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert fired == ["_run_resolved"], fired
    assert result["ok"] is False and result["reason"] == "error", result
    assert rc == 1
    assert _rows(ws, "SELECT holder FROM pulse_lock") == []
    assert len(stops) == 1 and stops[0].is_set(), "the heartbeat was never told to stop"


def test_a_queued_pulse_whose_connection_fails_lets_the_lock_and_the_request_go(
        tmp_path, monkeypatch, capsys):
    """The drain had the same shape, and its claimed request was never
    completed either, so it sat claimed forever."""
    ws = _firm(tmp_path / "ws")
    _request_pulse(ws)
    fired, stops = _fail_the_pulse_connection(monkeypatch)

    rc = pulse_cli.run_pulse(ws, drain_queue=True)

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert fired == ["_drain_queue"], fired
    assert result["ok"] is False and result.get("drained") == 1, result
    assert result["results"][0]["ok"] is False and result["results"][0]["error"], result
    assert rc == 1
    assert _rows(ws, "SELECT holder FROM pulse_lock") == []
    # Completed, as the drain already completes a request whose pulse raised:
    # the request was processed, and its failure is in the result above.
    assert _rows(ws, "SELECT status FROM pulse_request") == [("done",)]
    assert len(stops) == 1 and stops[0].is_set(), "the heartbeat was never told to stop"


# --- --abort ----------------------------------------------------------------

def test_abort_with_no_firm_database_exits_1(tmp_path):
    """Nothing could be checked, which is not "nothing is running": a mistyped
    --workspace used to report a successful abort (osprey's G0 item 1)."""
    ws = tmp_path / "ws"
    ws.mkdir()

    run = _pulse(ws, "--abort")

    _assert_exit(run, rc=1, ok=False, reason="db-not-found", workspace=str(ws.resolve()),
                 lock="no-db", aborted=0)


def test_abort_with_a_firm_id_that_does_not_resolve_exits_1(tmp_path):
    """#128 U2: this printed ``ok: true`` and exited 1, the rule broken the other way."""
    ws = _firm(tmp_path / "ws", other_firm=True)

    run = _pulse(ws, "--abort")

    _assert_exit(run, rc=1, ok=False, reason="firm-id-unresolved", lock="firm-id-unresolved")


def test_abort_on_a_firm_with_no_pulse_running_exits_0(tmp_path):
    """The control the no-database change could mask (law 25): a real firm that
    abort checked and found idle still reports success."""
    ws = _firm(tmp_path / "ws")

    run = _pulse(ws, "--abort")

    _assert_exit(run, rc=0, ok=True, lock="none", aborted=0)


def test_abort_clears_a_lock_whose_local_holder_is_dead_and_exits_0(tmp_path):
    ws = _firm(tmp_path / "ws")
    gone = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
    gone.wait(timeout=60)   # reaped, so the pid is dead rather than a zombie
    assert not pulse_cli._pid_alive(gone.pid), "precondition: the holder's pid must be dead"
    _hold_lock(ws, f"{socket.gethostname()}:{gone.pid}:t128dead")

    run = _pulse(ws, "--abort")

    _assert_exit(run, rc=0, ok=True, lock="stale-cleared")
    assert _rows(ws, "SELECT holder FROM pulse_lock") == [], run.output


def test_abort_leaves_a_lock_held_from_another_machine_and_exits_0(tmp_path):
    ws = _firm(tmp_path / "ws")
    _hold_lock(ws, "other-host-t128:1:bbbb")

    run = _pulse(ws, "--abort")

    _assert_exit(run, rc=0, ok=True, lock="remote-holder")


def _live_holder(*code: str) -> subprocess.Popen:
    """A process to hold the lock, reaped the moment it dies.

    Abort asks whether the holder's pid is alive. A dead child nobody has
    waited on is a zombie, and a zombie's pid still answers, so a thread waits
    on it here: without that abort would see a killed holder as still exiting.
    """
    holder = subprocess.Popen(
        [sys.executable, "-c", "\n".join(code)], stdout=subprocess.PIPE,
        stdin=subprocess.DEVNULL, env=_child_env())
    threading.Thread(target=holder.wait, daemon=True).start()
    assert holder.stdout.readline().strip() == b"ready"
    return holder


def test_abort_stops_a_live_local_holder_and_exits_0(tmp_path):
    ws = _firm(tmp_path / "ws")
    holder = _live_holder("import time", "print('ready', flush=True)", "time.sleep(120)")
    try:
        _hold_lock(ws, f"{socket.gethostname()}:{holder.pid}:t128live")

        run = _pulse(ws, "--abort")

        _assert_exit(run, rc=0, ok=True, lock="cleared", aborted=1)
    finally:
        holder.kill()


@host_cannot_survive_sigterm
def test_abort_reports_a_holder_that_is_still_exiting_and_exits_0(tmp_path):
    ws = _firm(tmp_path / "ws")
    holder = _live_holder("import signal, time",
                          "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                          "print('ready', flush=True)", "time.sleep(120)")
    try:
        _hold_lock(ws, f"{socket.gethostname()}:{holder.pid}:t128stay")

        run = _pulse(ws, "--abort")

        _assert_exit(run, rc=0, ok=True, lock="signalled", aborted=1)
    finally:
        holder.send_signal(signal.SIGKILL)


# ---------------------------------------------------------------------------
# stranded_units: a pulse with nothing to do stops reading as a healthy idle firm
# ---------------------------------------------------------------------------

def test_stranded_units_names_every_unit_no_active_member_can_reach(tmp_path):
    """The definition is ``compute_load``'s, read from the other side: a pending
    or in-progress Unit is workable only when an active Member of the firm has
    claimed it, or has it assigned, unclaimed and pending. Each stranded shape is
    here once, beside Units that are reachable or finished and must not appear.
    A dry run, because the query is read-only and runs there too."""
    ws = _firm(tmp_path / "ws", other_firm=True, members=(
        ("MEM-001", "Lead", "active"), ("MEM-002", "Resting", "paused"),
        ("MEM-003", "Gone", "retired")))
    _units(
        ws,
        {"id": "UNIT-01", "status": "pending"},
        {"id": "UNIT-02", "status": "pending", "assignee_member_id": "MEM-002"},
        {"id": "UNIT-03", "status": "in_progress", "claimed_by": "MEM-003"},
        {"id": "UNIT-04", "status": "in_progress", "assignee_member_id": "MEM-001"},
        {"id": "UNIT-05", "status": "pending", "assignee_member_id": "MEM-001"},
        {"id": "UNIT-06", "status": "in_progress", "claimed_by": "MEM-001"},
        {"id": "UNIT-07", "status": "done"},
        {"id": "UNIT-08", "status": "blocked"},
        {"id": "UNIT-09", "status": "pending", "assignee_member_id": "MEM-001",
         "claimed_by": "MEM-002"},
        {"id": "UNIT-10", "status": "pending", "assignee_member_id": "MEM-900"},
    )

    run = _pulse(ws, "--dry-run", "--firm-id", FIRM)

    result = _assert_exit(run, rc=0, ok=True, dry_run=True)
    assert result.get("stranded_units") == [
        {"id": "UNIT-01", "status": "pending", "assignee_member_id": None,
         "claimed_by": None, "reason": "no assignee and no claim"},
        {"id": "UNIT-02", "status": "pending", "assignee_member_id": "MEM-002",
         "claimed_by": None, "reason": "assigned to MEM-002, who is paused"},
        {"id": "UNIT-03", "status": "in_progress", "assignee_member_id": None,
         "claimed_by": "MEM-003", "reason": "claimed by MEM-003, who is retired"},
        {"id": "UNIT-04", "status": "in_progress", "assignee_member_id": "MEM-001",
         "claimed_by": None, "reason": "in progress with no claim"},
        {"id": "UNIT-09", "status": "pending", "assignee_member_id": "MEM-001",
         "claimed_by": "MEM-002", "reason": "claimed by MEM-002, who is paused"},
        {"id": "UNIT-10", "status": "pending", "assignee_member_id": "MEM-900",
         "claimed_by": None, "reason": "assigned to MEM-900, who is not a Member of this firm"},
    ], run.output


def test_stranded_units_reads_an_empty_member_id_the_way_compute_load_does(tmp_path):
    """compute_load counts a claim only when claimed_by EQUALS an active Member's
    id, and assigned work only when claimed_by IS NULL. An empty string is
    neither, so a Unit carrying one is never dispatched, and stranded_units must
    list it rather than read '' as "no claim" (osprey's G1 note). The member
    foreign keys admit '' only while enforcement is off, which SQLite sets per
    connection, so these rows are written through a connection with it off."""
    ws = _firm(tmp_path / "ws")
    raw = sqlite3.connect(ws / ".firm" / "firm.db")
    try:
        raw.execute("PRAGMA foreign_keys = OFF")
        raw.executemany(
            "INSERT INTO unit (id, firm_id, project_id, name, status, assignee_member_id,"
            " claimed_by) VALUES (?, ?, 'PROJ-001', ?, 'pending', ?, ?)",
            [("UNIT-A", FIRM, "Claimed by an empty id", "MEM-001", ""),
             ("UNIT-B", FIRM, "Assigned to an empty id", "", None)])
        raw.commit()
    finally:
        raw.close()

    run = _pulse(ws, "--dry-run")

    result = _assert_exit(run, rc=0, ok=True, ran=0)
    # The pulse's own reading of the same rows: nothing is workable.
    assert result.get("skip_reasons") == {"load=0 (no queued Units)": 1}, run.output
    assert result.get("stranded_units") == [
        {"id": "UNIT-A", "status": "pending", "assignee_member_id": "MEM-001",
         "claimed_by": "", "reason": "claimed by '', who is not a Member of this firm"},
        {"id": "UNIT-B", "status": "pending", "assignee_member_id": "",
         "claimed_by": None, "reason": "assigned to '', who is not a Member of this firm"},
    ], run.output


def test_stranded_units_is_an_empty_list_when_every_open_unit_is_reachable(tmp_path):
    """``[]`` means the query ran and found nothing, which is not the same as
    the field being absent (law 7)."""
    ws = _firm(tmp_path / "ws")
    _units(ws, {"id": "UNIT-01", "status": "pending", "assignee_member_id": "MEM-001"},
           {"id": "UNIT-02", "status": "done"})

    run = _pulse(ws, "--dry-run")

    result = _assert_exit(run, rc=0, ok=True)
    assert result.get("stranded_units") == [], run.output


def test_stranded_units_is_absent_only_beside_the_reason_it_could_not_be_read(
        tmp_path, monkeypatch, capsys):
    """In this process, because only a monkeypatch can make the read fail on its
    own; the legs above already cover the channel. The pulse's own result and
    exit code do not depend on this side query."""
    ws = _firm(tmp_path / "ws")

    def unreadable(conn, firm_id):
        raise RuntimeError("the unit table could not be read")

    monkeypatch.setattr(pulse_cli, "_stranded_units", unreadable)

    rc = pulse_cli.run_pulse(ws, dry_run=True)

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "stranded_units" not in result, result
    assert result["stranded_units_error"] == "RuntimeError: the unit table could not be read"
    assert result["ok"] is True and rc == 0, result
