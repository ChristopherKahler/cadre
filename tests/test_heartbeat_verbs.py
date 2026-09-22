"""The heartbeat verbs agree with each other, and every exit prints one object (#131).

Three defects, measured on main ``6c740786``:

* **The verbs disagree about how you name a firm.** ``enable`` takes ``--workspace``
  and ``--firm-id``; ``disable`` takes only ``--firm-id`` and otherwise reads the
  current directory (``cli/heartbeat.py:245``); ``status`` takes neither. So
  ``cadre heartbeat disable --workspace /some/firm`` is an ``unrecognized
  arguments`` error from the TOP-LEVEL parser, exit 2, **nothing on stdout**, and
  the timer keeps firing.
* **An argument error prints nothing a caller can parse.** Every verb prints one
  pretty-printed JSON object through ``_emit`` -- except when argparse rejects the
  arguments, which puts usage on stderr and leaves stdout empty.
  ``dashboard/founding.py::set_pulse`` already depends on exactly one object
  coming back.
* **``disable`` trusts a removal it never checked.** It calls ``sched.remove(stem)``
  and throws the answer away (``cli/heartbeat.py:270``), then prints ``ok: true``
  (``:312``). A removal that failed reports success and the timer keeps firing.

THE CONTRACT IS "STDOUT IS EXACTLY ONE JSON OBJECT", never "the last stdout line".
The output is pretty-printed (``cli/heartbeat.py:134``), so its last line is a
closing brace. #131's done-means says last line; the 09-14 verdict item 6 approved
the whole-object wording and this file measures that one.

THE CHANNEL. Every leg that drives a verb runs ``python -m firm heartbeat`` as a
CHILD PROCESS, because that is the channel a caller and the hub read: argparse,
``main()`` and ``sys.exit`` are the thing under test here and an in-process call
skips all three. The two legs that need a scheduler to REFUSE a removal are
in-process with a stand-in scheduler, and say so at the leg -- no real backend can
be made to fail on demand without touching the host's own timers.

WHAT THE CHILD IS FENCED WITH, and which tier each fence isolates (law 21). THE
HOME FENCE IS TWO VARIABLES, NOT ONE, because the two platforms read different
ones -- measured with ``ntpath.expanduser`` and ``posixpath.expanduser`` rather
than assumed. POSIX reads ``HOME``; Windows reads ``USERPROFILE``, then
``HOMEDRIVE``+``HOMEPATH``, and IGNORES ``HOME`` entirely. ``default_unit_dir()``
is ``Path.home() / ".config/systemd/user"``, so the fence must move
``Path.home()`` itself: it sets BOTH ``HOME`` and ``USERPROFILE`` at the test's
own temp directory and drops ``HOMEDRIVE``/``HOMEPATH`` so the fallback cannot
fire. Without it these legs would install units into the operator's real
systemd -- and on Windows, before this, ``disable`` was running ``remove()``
against the operator's real unit directory. CI measured that at ``b4a0628d``:
the four legs needing an installed timer failed on windows-latest and nowhere
else. ``test_the_child_really_lives_in_the_temp_home`` now ASKS the child what
``Path.home()`` is instead of trusting that the fence took, because a fence that
is merely SET is not a fence that WORKS and only the child can answer that.
``CADRE_SCHEDULER=systemd`` pins the backend, so the same legs measure the same
thing on every host.
``CADRE_DB_URL`` is dropped so each leg uses its own file. Every other fence is
conftest's and travels in the parent's environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import firm.cli.heartbeat as hb
from firm.core.db import connect
from firm.core.migrate import apply_migrations
from firm.core.repo import create

FIRM = "verbco"
OTHER = "otherco"

#: The prog name the CHILD prints. `_build_parser` sets it from
#: `Path(sys.argv[0]).name`, and anything ending `.py` becomes "cadre" -- which
#: `python -m firm` always does. Composed as a literal rather than read from a
#: parser built HERE, because this process's argv[0] is pytest's.
CHILD_PROG = "cadre"


def _usage_shapes() -> dict[str, tuple[list[str], str]]:
    """Every argument-error shape, READ FROM THE PARSER at this head.

    avocet's before-fact, and the reason this is a function rather than the
    table it used to be: **the shape SET moves, not only the raising level.**
    `heartbeat status --firm-id F` is a top-level argument error before D1 and
    a VALID command after it. A hand-written table is a record of one tree and
    a guess about every other, and the guess is silent -- a shape that stopped
    being an error just stops being tested.

    So the set is built from what the parser accepts right now: every verb
    mistyped, every accepted flag mistyped, and every flag that takes a value
    with the value omitted.

    The second element is the parser that raises it, asserted exactly. The
    rules, measured rather than assumed: argparse bubbles an unknown optional
    up to the TOP level as "unrecognized arguments"; a missing value is raised
    by the verb's own subparser; an unknown verb is raised by the heartbeat
    parser, which owns the choices.
    """
    from firm.__main__ import _build_parser

    parser = _build_parser()
    heartbeat = parser._subparsers._group_actions[0].choices["heartbeat"]
    verbs = heartbeat._subparsers._group_actions[0].choices

    shapes: dict[str, tuple[list[str], str]] = {
        "a mistyped verb": (["heartbeat", "statuss"], f"{CHILD_PROG} heartbeat"),
        "a flag before the verb": (["heartbeat", "--nope", "status"], CHILD_PROG),
    }
    for verb, verb_parser in sorted(verbs.items()):
        for action in verb_parser._actions:
            for opt in action.option_strings:
                if opt in ("-h", "--help"):
                    continue
                shapes[f"{verb} {opt} mistyped"] = (
                    ["heartbeat", verb, f"{opt}x"], CHILD_PROG)
                if action.nargs != 0:
                    shapes[f"{verb} {opt} with no value"] = (
                        ["heartbeat", verb, opt],
                        f"{CHILD_PROG} heartbeat {verb}")
    assert len(verbs) >= 3 and len(shapes) > len(verbs), (
        "read the parser and found almost nothing, which proves NOTHING "
        "(law 23)", sorted(verbs), sorted(shapes))
    return shapes


USAGE_SHAPES = _usage_shapes()


class _Run:
    def __init__(self, proc: subprocess.CompletedProcess) -> None:
        self.rc = proc.returncode
        self.out = proc.stdout.decode("utf-8", "replace")
        self.err = proc.stderr.decode("utf-8", "replace")

    @property
    def detail(self) -> str:
        return f"rc {self.rc}\nstdout:\n{self.out}\nstderr:\n{self.err}"

    def one_object(self) -> dict:
        """Stdout parsed WHOLE. Not the last line: the output is pretty-printed,
        so its last line is a closing brace and a last-line reader is wrong."""
        assert self.out.strip(), f"stdout is empty\n{self.detail}"
        try:
            value = json.loads(self.out)
        except ValueError as exc:
            raise AssertionError(
                f"stdout is not exactly one JSON object ({exc})\n{self.detail}"
            ) from None
        assert isinstance(value, dict), self.detail
        return value


def _child_env_for(home: Path) -> dict[str, str]:
    """The environment every child in this file runs with.

    One producer, so the fence the control below proves is the same fence the
    legs use. Two copies of a fence are two fences, and only one of them gets
    tested.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(hb.__file__).resolve().parents[2])
    # BOTH, because the two platforms read different variables -- measured with
    # `ntpath.expanduser` and `posixpath.expanduser` rather than assumed:
    # Windows reads USERPROFILE (then HOMEDRIVE+HOMEPATH) and IGNORES HOME;
    # POSIX reads HOME. `default_unit_dir()` is `Path.home()/.config/systemd/
    # user`, so a fence that sets only HOME leaves the child resolving the
    # operator's real profile on Windows. CI found that the hard way at
    # b4a0628d: the four legs needing an installed timer failed there and
    # nowhere else.
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env.pop("HOMEDRIVE", None)            # so the fallback cannot fire either
    env.pop("HOMEPATH", None)
    env["CADRE_SCHEDULER"] = "systemd"
    # `enable` refuses to install a timer whose pulse has no Member runtime to
    # spawn, and conftest points CADRE_CLAUDE_BIN at a path that does not
    # exist so nothing resolves by accident. These legs are about the VERBS,
    # not about the runtime, so the setup points it at this interpreter -- it
    # resolves, and nothing here ever runs a pulse.
    env["CADRE_CLAUDE_BIN"] = sys.executable
    env.pop("CADRE_DB_URL", None)
    env.pop("FIRM_ID", None)
    return env


def _cadre(home: Path, *args: str, cwd: Path | None = None) -> _Run:
    return _Run(subprocess.run(
        [sys.executable, "-m", "firm", *args], capture_output=True,
        env=_child_env_for(home), cwd=str(cwd) if cwd else None, timeout=180,
        stdin=subprocess.DEVNULL))


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".config" / "systemd" / "user").mkdir(parents=True)
    return home


def _install(home: Path, ws: Path, firm_id: str = FIRM,
             interval: str = "15m") -> None:
    """A firm with a timer installed, seeded rather than enabled.

    `heartbeat enable` runs `systemctl enable --now`, and `systemctl --user`
    talks to the REAL user manager whatever HOME says -- measured on this host:
    "Failed to enable unit: Unit file cadre-heartbeat-verbco.timer does not
    exist", exit 1. That is systemd's property, not this code's, and a leg that
    worked around it by pointing at the operator's own unit directory would
    install timers on their machine.

    So the state is produced the way the backend DEFINES it: on systemd,
    `installed` is the unit file (`sched/systemd.py::status`), which is exactly
    what the G0's Q5 ruling turns on.

    WHAT THE SEEDED TEXT IS NOT. `render_service` and `render_timer` have **no
    caller in the product** -- measured at this head, the only hits outside
    their own definitions are in this file. `enable` installs through
    `sched.install_timer`, which calls the backend's own `_write_service` with
    the argv, so it writes a DIFFERENT ExecStart: the render functions predate
    #128 and carry no `--source`. These units are therefore the right shape for
    every question this file asks -- which firm, which workspace, installed or
    not -- and the wrong shape for any question about the argv. **No leg here
    reads ExecStart**, and one that needs to must seed what `install_timer`
    builds instead.
    """
    unit_dir = home / ".config" / "systemd" / "user"
    stem = f"{hb._UNIT_PREFIX}{firm_id}"
    (unit_dir / f"{stem}.service").write_text(
        hb.render_service(ws, firm_id, sys.executable, {}), encoding="utf-8")
    (unit_dir / f"{stem}.timer").write_text(
        hb.render_timer(firm_id, interval), encoding="utf-8")


def test_the_child_really_lives_in_the_temp_home(tmp_path):
    """The fence, proved rather than trusted.

    Every leg that needs an installed timer depends on the child resolving
    `Path.home()` to the test's own directory, because that is where
    `default_unit_dir()` looks and where the fixture writes. A fence that is
    merely SET is not a fence that WORKS: setting HOME alone did nothing on
    Windows, and four legs failed in CI for a reason no Linux run could show.

    This asks the child what it thinks its home is, which is the one question
    that would have caught that here instead of twenty minutes into a Windows
    suite.
    """
    home = _home(tmp_path)
    env = _child_env_for(home)

    proc = subprocess.run(
        [sys.executable, "-c",
         "from pathlib import Path; print(Path.home())"],
        capture_output=True, env=env, timeout=120, stdin=subprocess.DEVNULL)

    answered = proc.stdout.decode("utf-8", "replace").strip()
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert Path(answered) == home, (
        "the child resolved a different home, so every timer leg would look "
        "somewhere the fixture never wrote", answered, str(home))


def _firm_at(root: Path, firm_id: str = FIRM) -> Path:
    ws = root
    conn = connect(ws / ".firm" / "firm.db")
    try:
        apply_migrations(conn)
        create(conn, "firm", {"id": firm_id, "name": f"Firm {firm_id}"})
        conn.commit()
    finally:
        conn.close()
    return ws


def _units(home: Path) -> list[str]:
    return sorted(p.name for p in
                  (home / ".config" / "systemd" / "user").glob("cadre-heartbeat-*"))


def _interval(ws: Path, firm_id: str = FIRM):
    conn = connect(ws / ".firm" / "firm.db")
    try:
        row = conn.execute("SELECT pulse_interval FROM firm WHERE id = ?",
                           (firm_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# R1-R3 · the three verbs name a firm the same way
# ═══════════════════════════════════════════════════════════════════════════

def test_R1_disable_takes_a_workspace_and_disables_that_firm(tmp_path):
    """From a directory that is not the firm's. Today `--workspace` is an
    unrecognized argument and the timer keeps firing."""
    home = _home(tmp_path)
    ws = _firm_at(tmp_path / "ws")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    _install(home, ws)
    assert _units(home), "precondition: the timer is installed"

    run = _cadre(home, "heartbeat", "disable", "--workspace", str(ws),
                 cwd=elsewhere)

    result = run.one_object()
    assert run.rc == 0, run.detail
    assert result["ok"] is True, run.detail
    assert result["firm_id"] == FIRM, run.detail
    assert _units(home) == [], ("the units are gone", run.detail)


def test_R2_status_reports_one_firm_when_given_one_and_all_when_not(tmp_path):
    home = _home(tmp_path)
    one = _firm_at(tmp_path / "one", FIRM)
    two = _firm_at(tmp_path / "two", OTHER)
    _install(home, one, FIRM)
    _install(home, two, OTHER)

    every = _cadre(home, "heartbeat", "status")
    assert {e["firm_id"] for e in every.one_object()["heartbeats"]} == {FIRM, OTHER}, \
        every.detail

    run = _cadre(home, "heartbeat", "status", "--firm-id", FIRM)

    result = run.one_object()
    assert run.rc == 0, run.detail
    assert [e["firm_id"] for e in result["heartbeats"]] == [FIRM], run.detail


def test_R2_control_status_on_a_firm_with_no_timer_is_an_answer(tmp_path):
    """osprey's Q1: the query ran and found none. An empty list is an answer,
    not a failure -- `disable` on the same firm stays a failure, because
    disable is an instruction that did not happen."""
    home = _home(tmp_path)
    _firm_at(tmp_path / "ws")

    run = _cadre(home, "heartbeat", "status", "--firm-id", FIRM)

    result = run.one_object()
    assert run.rc == 0, run.detail
    assert result["ok"] is True, run.detail
    assert result["heartbeats"] == [], run.detail
    assert result["firm_id"] == FIRM, ("the card names what was asked about",
                                       run.detail)


def test_R3b_status_with_a_flag_that_resolves_no_firm_does_not_list_them_all(
        tmp_path):
    """osprey's pre-PR finding, folded in before the PR.

    A workspace whose database holds no firm resolves to nothing. Listing every
    installed heartbeat there would answer a question about ONE firm with
    somebody else's timers, which is the silent fall-through condition 3
    forbids -- one branch over from the missing-database case. `disable`
    already failed here because it needs the id to build a stem; `status` did
    not, because a None filter reads as no filter.
    """
    home = _home(tmp_path)
    other = _firm_at(tmp_path / "other", OTHER)
    _install(home, other, OTHER)
    empty = tmp_path / "firmless"
    (empty / ".firm").mkdir(parents=True)
    conn = connect(empty / ".firm" / "firm.db")
    try:
        apply_migrations(conn)          # a database with no firm row in it
        conn.commit()
    finally:
        conn.close()

    run = _cadre(home, "heartbeat", "status", "--workspace", str(empty))

    result = run.one_object()
    assert run.rc == 1, run.detail
    assert result["ok"] is False, run.detail
    assert result["reason"] == "firm-id-unresolved", run.detail
    assert result["workspace"] == str(empty), run.detail
    assert "heartbeats" not in result, (
        "the other firm's timer is not an answer to this question", run.detail)


def test_R3_status_takes_a_workspace(tmp_path):
    home = _home(tmp_path)
    ws = _firm_at(tmp_path / "ws")
    _install(home, ws)

    run = _cadre(home, "heartbeat", "status", "--workspace", str(ws))

    result = run.one_object()
    assert run.rc == 0, run.detail
    assert [e["firm_id"] for e in result["heartbeats"]] == [FIRM], run.detail


# ═══════════════════════════════════════════════════════════════════════════
# R4-R7 · one JSON object on stdout for every exit
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("shape", sorted(USAGE_SHAPES))
def test_R4_every_argument_error_prints_one_object_on_stdout(tmp_path, shape):
    """Three different parsers raise these, so an override that reaches only one
    level passes some shapes and not others. That is the false PASS the
    verdict's condition 1 exists for, and why each shape is its own leg."""
    home = _home(tmp_path)

    argv, raised_by = USAGE_SHAPES[shape]

    run = _cadre(home, *argv)

    result = run.one_object()
    assert run.rc == 2, run.detail
    assert result["ok"] is False, run.detail
    assert result["reason"] == "usage", run.detail
    assert result["message"].strip(), ("argparse's own words, verbatim",
                                       run.detail)
    assert result["parser"] == raised_by, (
        "WHICH level raised it is part of the assertion, and it is an exact "
        "field rather than a substring of a usage blob: the raising level "
        "MOVES as flags are added, and a leg that did not pin it would pass "
        "on an override that reaches only some levels", raised_by, run.detail)


def test_R4_control_the_message_names_the_offending_flag(tmp_path):
    """The message is argparse's, word for word (osprey's Q2), so the wording
    cannot drift from what the parser actually enforces."""
    home = _home(tmp_path)

    run = _cadre(home, "heartbeat", "status", "--nope")

    assert "--nope" in run.one_object()["message"], run.detail


def test_R5_bare_heartbeat_prints_the_usage_object_and_exits_2(tmp_path):
    """Exit 0 tells a calling script it worked -- for example one whose verb
    variable came out empty. Today it prints help and exits 0."""
    home = _home(tmp_path)

    run = _cadre(home, "heartbeat")

    result = run.one_object()
    assert run.rc == 2, run.detail
    assert result["ok"] is False and result["reason"] == "usage", run.detail


@pytest.mark.parametrize("argv", [
    ["heartbeat", "--help"],
    ["heartbeat", "enable", "--help"],
    ["heartbeat", "disable", "--help"],
    ["heartbeat", "status", "--help"],
])
def test_R6_control_explicit_help_still_prints_help_and_exits_0(tmp_path, argv):
    """Help is what was asked for. The control that keeps R5 from swallowing
    the one case where exit 0 is right."""
    home = _home(tmp_path)

    run = _cadre(home, *argv)

    assert run.rc == 0, run.detail
    assert "usage:" in run.out, run.detail
    with pytest.raises(ValueError):
        json.loads(run.out)


def test_R5_R6_control_bare_and_help_must_not_be_the_same_answer(tmp_path):
    """The pair, and neither leg means anything without it.

    avocet measured that today `cadre heartbeat` and `cadre heartbeat --help`
    print BYTE-IDENTICAL output, 436 characters, both exit 0. So a fix that
    changed only one of them, or a leg that checked only one, could leave the
    two answering the same thing to two different questions. This asserts they
    differ, which is the one claim neither R5 nor R6 makes alone.
    """
    home = _home(tmp_path)

    bare = _cadre(home, "heartbeat")
    helped = _cadre(home, "heartbeat", "--help")

    assert (bare.rc, helped.rc) == (2, 0), (bare.detail, helped.detail)
    assert bare.out != helped.out, (
        "they were byte-identical before this change and must not be after",
        bare.detail)
    assert bare.one_object()["reason"] == "usage", bare.detail
    assert "usage:" in helped.out, helped.detail


def test_R7_control_other_commands_keep_argparse_usage_on_stderr(tmp_path):
    """The override is heartbeat's alone. Applied to every command it would
    change stdout for the whole CLI, which nothing asked for."""
    home = _home(tmp_path)

    run = _cadre(home, "pulse", "--nope")

    assert run.rc == 2, run.detail
    assert run.out.strip() == "", ("stdout stays empty for other commands",
                                   run.detail)
    assert "usage:" in run.err, run.detail


def test_R11_every_verb_prints_exactly_one_object_parsed_whole(tmp_path):
    """Not "the last stdout line": the output is pretty-printed, so the last
    line is a closing brace (`cli/heartbeat.py:134`). #131's done-means says
    last line; the 09-14 verdict item 6 superseded it."""
    home = _home(tmp_path)
    ws = _firm_at(tmp_path / "ws")

    _install(home, ws)
    for argv in (["heartbeat", "status", "--workspace", str(ws)],
                 ["heartbeat", "disable", "--workspace", str(ws)],
                 ["heartbeat", "disable", "--workspace", str(ws)],
                 ["heartbeat", "bogus"]):
        run = _cadre(home, *argv)
        body = run.one_object()
        assert "ok" in body, (argv, run.detail)
        assert run.out.rstrip().endswith("}"), (
            "pretty-printed, which is exactly why the whole object is parsed "
            "rather than the last line", argv, run.detail)


# ═══════════════════════════════════════════════════════════════════════════
# R8-R10 · disable proves the removal instead of trusting it
# ═══════════════════════════════════════════════════════════════════════════

class _RefusingScheduler:
    """A scheduler whose `remove()` does nothing and whose query keeps saying
    installed. IN-PROCESS, and here is why: no real backend can be made to
    refuse a removal on demand without touching the host's own timers, and a
    leg that chmod'ed a unit directory would measure the filesystem rather
    than the contract."""

    name = "refusing"

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.removed_calls = 0

    def status(self, stem: str) -> dict:
        return {"installed": True, "state": "active", "failed": False,
                "workdir": str(self.workdir), "interval": "15m"}

    def remove(self, stem: str) -> dict:
        self.removed_calls += 1
        return {"removed": []}


def test_R8_a_removal_that_did_not_happen_exits_1_and_changes_nothing(
        tmp_path, capsys, monkeypatch):
    """The verdict's condition 2: the timer still fires, so the interval is
    still true and the lock is still that pulse's. `disable` must not clear
    `firm.pulse_interval` and must not run `release_and_finalize`."""
    ws = _firm_at(tmp_path / "ws")
    conn = connect(ws / ".firm" / "firm.db")
    try:
        conn.execute("UPDATE firm SET pulse_interval = '15m' WHERE id = ?", (FIRM,))
        conn.commit()
    finally:
        conn.close()
    sched = _RefusingScheduler(ws)
    monkeypatch.setattr(hb, "_sched", lambda unit_dir=None: sched)

    called: list[str] = []
    import firm.pulse.cleanup as cleanup_mod
    monkeypatch.setattr(cleanup_mod, "release_and_finalize",
                        lambda *a, **k: called.append("ran") or {})

    rc = hb.run_disable(FIRM, workspace=ws)

    result = json.loads(capsys.readouterr().out)
    assert rc == 1, result
    assert result["ok"] is False, result
    assert result["reason"] == "not-removed", result
    assert sched.removed_calls == 1, ("it did try", result)
    assert _interval(ws) == "15m", (
        "a timer that still fires still has its interval", result)
    assert called == [], (
        "the lock still belongs to that pulse; nothing to finalize", result)


def test_R9_control_a_removal_that_worked_still_exits_0(tmp_path):
    """The control law 25 asks for: the leg that separates *repaired* from
    *silenced*. Fixing R8 by always exiting 1 would pass R8 and fail this."""
    home = _home(tmp_path)
    ws = _firm_at(tmp_path / "ws")
    _install(home, ws)

    run = _cadre(home, "heartbeat", "disable", "--workspace", str(ws))

    result = run.one_object()
    assert run.rc == 0, run.detail
    assert result["ok"] is True, run.detail
    assert _units(home) == [], run.detail

    after = _cadre(home, "heartbeat", "status", "--workspace", str(ws))
    assert after.one_object()["heartbeats"] == [], (
        "the scheduler's own query says not found", after.detail)


class _QuietlyRemovingScheduler(_RefusingScheduler):
    """Removes the task and reports nothing removed.

    Real on Windows: `schtasks /Delete` returns non-zero for a task that is
    already gone, so `remove()` appends nothing to its list while the task is
    genuinely absent (`sched/winsched.py`).
    """

    def status(self, stem: str) -> dict:
        if self.removed_calls:
            return {"installed": False, "state": "absent", "failed": False}
        return super().status(stem)


def test_R14_a_removal_the_scheduler_reported_as_nothing_still_exits_0(
        tmp_path, capsys, monkeypatch):
    """The state that tells the fix from the lazy wrong fix.

    Exiting 1 whenever `remove()`'s list is empty passes R8 and R9 both, so
    mutation Q8 measured INERT until this leg existed. Here the list is empty
    and the task is GONE, and the only thing that can tell the difference is
    asking the scheduler again -- which is D3's whole point.
    """
    ws = _firm_at(tmp_path / "ws")
    sched = _QuietlyRemovingScheduler(ws)
    monkeypatch.setattr(hb, "_sched", lambda unit_dir=None: sched)
    import firm.pulse.cleanup as cleanup_mod
    monkeypatch.setattr(cleanup_mod, "release_and_finalize", lambda *a, **k: {})

    rc = hb.run_disable(FIRM, workspace=ws)

    result = json.loads(capsys.readouterr().out)
    assert rc == 0, result
    assert result["ok"] is True, result
    assert result["scheduler_removed"] == {"removed": []}, (
        "reported honestly, and not what the exit code rests on", result)


def test_R10_the_payload_carries_the_schedulers_own_answer(tmp_path, capsys,
                                                           monkeypatch):
    """`remove()`'s answer is reported for the record even when the exit code
    does not rest on it."""
    ws = _firm_at(tmp_path / "ws")
    sched = _RefusingScheduler(ws)
    monkeypatch.setattr(hb, "_sched", lambda unit_dir=None: sched)
    import firm.pulse.cleanup as cleanup_mod
    monkeypatch.setattr(cleanup_mod, "release_and_finalize", lambda *a, **k: {})

    hb.run_disable(FIRM, workspace=ws)

    result = json.loads(capsys.readouterr().out)
    assert result["scheduler_removed"] == {"removed": []}, (
        "what the scheduler ITSELF said it removed, reported rather than "
        "trusted. A separate key from `removed`, which is the unit this verb "
        "set out to remove: two different claims do not share one name",
        result)


# ═══════════════════════════════════════════════════════════════════════════
# R12-R13 · a named workspace with no database is a JSON failure
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("verb", ["disable", "status"])
def test_R12_a_named_workspace_with_no_database_is_a_json_failure(tmp_path, verb):
    """The verdict's condition 3: not a traceback, and not a quiet fall-through
    to the current directory. The same words `run_pulse` uses for the same
    missing database, so the verbs and the pulse say one thing."""
    home = _home(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()

    run = _cadre(home, "heartbeat", verb, "--workspace", str(empty))

    result = run.one_object()
    assert run.rc == 1, run.detail
    assert result["ok"] is False, run.detail
    assert result["reason"] == "db-not-found", run.detail
    assert result["workspace"] == str(empty), run.detail
    assert "Traceback" not in run.err, run.detail
