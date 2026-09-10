"""The write-back debt, and the Stop gate that collects it.

Deliverable 2's remainder and deliverable 5 are one piece. A gate with nothing
writing its marker blocks every Member forever; commands with no gate reading
their marker are advice again. So the test that matters most in this file is
``test_the_gate_and_the_commands_are_actually_connected`` — it drives the real
rendered gate as a subprocess through close, block, learn, allow. Everything
above it checks one half in isolation, and a file of only those would pass with
the two halves wired to nothing.

Both directions on every claim. "The gate blocks" is worthless without "and
lets an unindebted Member go", because a gate that blocks unconditionally
passes the first check and makes the framework unusable.

No test here runs the real ``base``. ``which_base`` is monkeypatched in every
case, so the operator's own graph is never written to.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from firm.cli import install_hooks
from firm.services import writeback

FIRM = "zqfirm"
ME = "MEM-001"
COLLEAGUE = "MEM-002"


# ---------------------------------------------------------------------------
# base is never really called
# ---------------------------------------------------------------------------

class _FakeBase:
    """Stands in for the base binary. Records calls, answers with a code."""

    def __init__(self, *codes: int) -> None:
        self.codes = list(codes)
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        code = self.codes.pop(0) if self.codes else 0

        class R:
            returncode = code
            stdout = "" if code else "note recorded"
            stderr = "base: domain not found" if code else ""
        return R()


@pytest.fixture
def base_ok(monkeypatch):
    """base is installed and says yes."""
    fake = _FakeBase()
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: "/fake/base")
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


@pytest.fixture
def base_absent(monkeypatch):
    """base is not on this machine. A firm without it is degraded, not broken."""
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)


# ---------------------------------------------------------------------------
# The debt itself
# ---------------------------------------------------------------------------

def test_closing_a_unit_opens_a_debt(tmp_path):
    result = writeback.record_closure(tmp_path, FIRM, ME, "U-1")
    assert result["debt"] is True
    owed = writeback.open_debts(tmp_path, ME)
    assert [d["unit_id"] for d in owed] == ["U-1"]
    assert owed[0]["member_id"] == ME


def test_closing_a_unit_already_written_back_opens_no_debt(tmp_path, base_ok):
    """Learning first and closing second is the better habit, not a violation."""
    writeback.record_learning(tmp_path, FIRM, ME, "what it taught", unit_id="U-1")
    result = writeback.record_closure(tmp_path, FIRM, ME, "U-1")
    assert result["debt"] is False
    assert writeback.open_debts(tmp_path, ME) == []


def test_learning_about_a_unit_clears_its_debt(tmp_path, base_ok):
    writeback.record_closure(tmp_path, FIRM, ME, "U-1")
    assert writeback.open_debts(tmp_path, ME)
    result = writeback.record_learning(tmp_path, FIRM, ME, "what it taught",
                                       unit_id="U-1")
    assert result["ok"] and result["cleared"] is True
    assert writeback.open_debts(tmp_path, ME) == []


def test_learning_with_no_unit_clears_nothing(tmp_path, base_ok):
    """The debt is per Unit, so a lesson attached to no Unit cannot settle one.

    Recording it still succeeds — a Member noticing something general should
    never be turned away — but the gate's question is about a specific Unit and
    stays unanswered.
    """
    writeback.record_closure(tmp_path, FIRM, ME, "U-1")
    result = writeback.record_learning(tmp_path, FIRM, ME, "something general")
    assert result["ok"] and result["cleared"] is False
    assert [d["unit_id"] for d in writeback.open_debts(tmp_path, ME)] == ["U-1"]


def test_a_failed_base_learn_never_clears_the_debt(tmp_path, monkeypatch):
    """The worst possible outcome is a cleared debt and an empty graph.

    The Member walks away certain it wrote back, the gate agrees, and nothing
    was recorded anywhere. So the receipt is written only after base returns 0.
    """
    fake = _FakeBase(1)
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: "/fake/base")
    monkeypatch.setattr(subprocess, "run", fake)

    writeback.record_closure(tmp_path, FIRM, ME, "U-1")
    result = writeback.record_learning(tmp_path, FIRM, ME, "lesson", unit_id="U-1")
    assert result["ok"] is False
    assert "domain not found" in result["reason"]
    assert [d["unit_id"] for d in writeback.open_debts(tmp_path, ME)] == ["U-1"]


def test_base_absent_records_the_receipt_and_says_so(tmp_path, base_absent):
    """A licensee without BASE still closes Units. Degraded, never blocked."""
    writeback.record_closure(tmp_path, FIRM, ME, "U-1")
    result = writeback.record_learning(tmp_path, FIRM, ME, "lesson", unit_id="U-1")
    assert result["ok"] and result["graph"] == "skipped"
    assert writeback.open_debts(tmp_path, ME) == []


def test_a_colleagues_debt_is_not_mine(tmp_path):
    writeback.record_closure(tmp_path, FIRM, COLLEAGUE, "U-9")
    assert writeback.open_debts(tmp_path, ME) == []
    assert [d["unit_id"] for d in writeback.open_debts(tmp_path, COLLEAGUE)] == ["U-9"]
    # No member filter is the report view, and sees both.
    writeback.record_closure(tmp_path, FIRM, ME, "U-1")
    assert len(writeback.open_debts(tmp_path)) == 2


def test_no_writeback_directory_is_no_debt(tmp_path):
    """Absent is not a crash. Every firm predating this feature has no directory."""
    assert writeback.open_debts(tmp_path, ME) == []


@pytest.mark.parametrize("nasty", [
    "../../../etc/passwd", "..\\..\\windows\\system32", "C:evil", "with/slash",
])
def test_a_unit_id_cannot_escape_the_writeback_directory(tmp_path, nasty):
    """Unit ids arrive on a command line, so they are untrusted filenames."""
    writeback.record_closure(tmp_path, FIRM, ME, nasty)
    directory = writeback.writeback_dir(tmp_path)
    written = list(directory.glob("*"))
    assert written, "nothing was written at all"
    for path in written:
        assert path.parent == directory, f"{path} escaped to {path.parent}"
        assert ".." not in path.name


def test_an_unknown_note_type_is_refused_before_base_is_called(tmp_path, base_ok):
    """base rejects the whole write on a bad --type, and a Stop gate is the
    worst place to discover that."""
    result = writeback.record_learning(tmp_path, FIRM, ME, "lesson",
                                       note_type="ruminations")
    assert result["ok"] is False and "unknown --type" in result["reason"]
    assert base_ok.calls == []


def test_an_empty_lesson_is_refused(tmp_path, base_ok):
    result = writeback.record_learning(tmp_path, FIRM, ME, "   ")
    assert result["ok"] is False
    assert base_ok.calls == []


def test_the_lesson_reaches_base_with_the_firm_domain_and_the_member(tmp_path, base_ok):
    writeback.record_learning(tmp_path, FIRM, ME, "the lesson",
                              unit_id="U-1", note_type="correction")
    assert len(base_ok.calls) == 1
    cmd = base_ok.calls[0]
    assert cmd[:2] == ["/fake/base", "learn"]
    assert cmd[cmd.index("--domain") + 1] == FIRM
    assert cmd[cmd.index("--entity") + 1] == ME
    assert cmd[cmd.index("--type") + 1] == "correction"
    assert cmd[cmd.index("--text") + 1] == "the lesson"


# ---------------------------------------------------------------------------
# The gate, driven as the real subprocess it is
# ---------------------------------------------------------------------------

def _gate_at(tmp_path: Path) -> Path:
    script = tmp_path / "cadre-writeback-gate.py"
    script.write_text(install_hooks.render_writeback_hook(), encoding="utf-8")
    return script


def _run_gate(script: Path, workspace: Path, *, member: str | None = ME,
              stop_hook_active: bool = False, payload: str | None = None):
    env = dict(os.environ)
    env.pop("CADRE_MEMBER_ID", None)
    if member:
        env["CADRE_MEMBER_ID"] = member
    if payload is None:
        payload = json.dumps({"cwd": str(workspace),
                              "stop_hook_active": stop_hook_active})
    return subprocess.run([sys.executable, str(script)], input=payload,
                          capture_output=True, text=True, env=env, timeout=60)


def test_the_gate_blocks_a_member_who_owes_a_writeback(tmp_path):
    workspace = tmp_path / "firm"
    workspace.mkdir()
    writeback.record_closure(workspace, FIRM, ME, "U-1")
    done = _run_gate(_gate_at(tmp_path), workspace)
    assert done.returncode == 2, done.stderr
    assert "U-1" in done.stderr
    # Block AND teach. A bare refusal gets retried; one carrying the route gets
    # obeyed, which is the whole reason agent-intercept.py works.
    assert "base cadre learn --unit U-1" in done.stderr


def test_the_gate_lets_a_member_who_owes_nothing_finish(tmp_path):
    """The other direction. Without this the file passes with a gate that
    blocks unconditionally, which is not a gate, it is a wall."""
    workspace = tmp_path / "firm"
    workspace.mkdir()
    done = _run_gate(_gate_at(tmp_path), workspace)
    assert done.returncode == 0, done.stderr


def test_the_gate_does_not_block_twice_on_the_same_stop(tmp_path):
    """stop_hook_active means this gate already blocked once and the Member is
    re-running. Blocking again traps the session in a loop it cannot leave,
    which is how a guardrail earns being switched off."""
    workspace = tmp_path / "firm"
    workspace.mkdir()
    writeback.record_closure(workspace, FIRM, ME, "U-1")
    script = _gate_at(tmp_path)
    assert _run_gate(script, workspace).returncode == 2          # armed
    assert _run_gate(script, workspace, stop_hook_active=True).returncode == 0


def test_the_gate_ignores_a_session_that_is_not_a_member(tmp_path):
    """A Board session has no CADRE_MEMBER_ID and owes no Unit write-back."""
    workspace = tmp_path / "firm"
    workspace.mkdir()
    writeback.record_closure(workspace, FIRM, ME, "U-1")
    done = _run_gate(_gate_at(tmp_path), workspace, member=None)
    assert done.returncode == 0, done.stderr


def test_a_board_session_is_not_blocked_by_a_marker_with_no_member(tmp_path):
    """The Board guard is load-bearing, and this is the case that proves it.

    Deleting `if not member: return 0` looked equivalent at first: with no
    CADRE_MEMBER_ID the member is "" and the per-marker filter skips every
    normal debt anyway, so the mutant stayed green through the whole file. It
    is not equivalent. A marker whose member_id is missing or empty compares
    "" != "" as False, so it is NOT skipped — and without the guard the Board
    gets blocked by a malformed file it has nothing to do with. A malformed
    marker is exactly what an interrupted write leaves behind.
    """
    workspace = tmp_path / "firm"
    workspace.mkdir()
    directory = writeback.writeback_dir(workspace)
    directory.mkdir(parents=True)
    (directory / ("U-3" + writeback.OPEN_SUFFIX)).write_text(
        json.dumps({"unit_id": "U-3"}), encoding="utf-8")   # no member_id

    done = _run_gate(_gate_at(tmp_path), workspace, member=None)
    assert done.returncode == 0, (
        "a Board session was blocked by a marker naming no Member: " + done.stderr)


def test_the_gate_never_blocks_a_member_for_a_colleagues_debt(tmp_path):
    workspace = tmp_path / "firm"
    workspace.mkdir()
    writeback.record_closure(workspace, FIRM, COLLEAGUE, "U-9")
    done = _run_gate(_gate_at(tmp_path), workspace, member=ME)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("payload", ["", "not json at all", "[]", "null"])
def test_the_gate_fails_open_on_a_payload_it_cannot_read(tmp_path, payload):
    """A Member unable to end its session because this script has a bug is a
    worse outcome than a lesson going unrecorded."""
    workspace = tmp_path / "firm"
    workspace.mkdir()
    writeback.record_closure(workspace, FIRM, ME, "U-1")
    done = _run_gate(_gate_at(tmp_path), workspace, payload=payload)
    assert done.returncode == 0, done.stderr


def test_the_gate_survives_a_corrupt_marker(tmp_path):
    workspace = tmp_path / "firm"
    workspace.mkdir()
    writeback.record_closure(workspace, FIRM, ME, "U-1")
    bad = writeback.writeback_dir(workspace) / ("U-2" + writeback.OPEN_SUFFIX)
    bad.write_text("{{{ not json", encoding="utf-8")
    done = _run_gate(_gate_at(tmp_path), workspace)
    # The readable debt still blocks; the unreadable one is skipped, not fatal.
    assert done.returncode == 2
    assert "U-1" in done.stderr


# ---------------------------------------------------------------------------
# The two halves, connected. This is the deliverable.
# ---------------------------------------------------------------------------

def test_the_gate_and_the_commands_are_actually_connected(tmp_path, base_absent):
    """Close, blocked, learn, released — the real gate as a real subprocess.

    Every other test in this file checks one half against a marker it wrote
    itself. This is the only one that fails if the two halves are wired to
    different directories, different filename conventions, or different member
    keys, and any of those would ship a gate that never fires or never clears.

    It takes ``base_absent`` and NOT ``base_ok`` on purpose, and the reason is
    worth keeping. ``base_ok`` monkeypatches ``subprocess.run`` for the whole
    process, so it also swallows the call that launches the gate: the first
    version of this test ran no gate at all and read the fake's exit code as
    the gate's. It failed only because the assertion is ``== 2`` rather than
    ``!= 0``. With ``base_absent`` nothing is patched but ``which_base``, so
    ``record_learning`` needs no subprocess and the gate really runs.
    """
    workspace = tmp_path / "firm"
    workspace.mkdir()
    script = _gate_at(tmp_path)

    # 1. Nothing owed: the session may end.
    assert _run_gate(script, workspace).returncode == 0

    # 2. A Unit closes with nothing recorded.
    writeback.record_closure(workspace, FIRM, ME, "U-77")
    blocked = _run_gate(script, workspace)
    assert blocked.returncode == 2, "the gate did not fire on a real debt"
    assert "U-77" in blocked.stderr

    # 3. The Member does what the message told it to do, verbatim.
    assert "base cadre learn --unit U-77" in blocked.stderr
    result = writeback.record_learning(workspace, FIRM, ME,
                                       "closing U-77 taught us the thing",
                                       unit_id="U-77")
    assert result["ok"] and result["cleared"] is True

    # 4. Released.
    assert _run_gate(script, workspace).returncode == 0, \
        "the debt was cleared but the gate still blocks"


# ---------------------------------------------------------------------------
# Installing it
# ---------------------------------------------------------------------------

def test_installing_the_gate_writes_it_and_registers_it_under_stop(tmp_path):
    code, messages = install_hooks.install_writeback_hook(tmp_path)
    assert code == 0
    script = tmp_path / ".claude" / "hooks" / install_hooks.WRITEBACK_HOOK_SCRIPT_NAME
    assert script.exists()
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    commands = [h["command"] for entry in settings["hooks"]["Stop"]
                for h in entry["hooks"]]
    assert commands == [install_hooks.writeback_hook_command()]


def test_installing_the_gate_twice_registers_it_once(tmp_path):
    install_hooks.install_writeback_hook(tmp_path)
    install_hooks.install_writeback_hook(tmp_path)
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    commands = [h["command"] for entry in settings["hooks"]["Stop"]
                for h in entry["hooks"]]
    assert len(commands) == 1, f"registered {len(commands)} times: {commands}"


def test_a_moved_interpreter_repoints_the_gate_rather_than_duplicating_it(tmp_path):
    """Matching on the whole command line would register a second copy of the
    same gate every time the operator rebuilt a virtual environment, and the
    Member would then be blocked twice for one debt."""
    install_hooks.install_writeback_hook(tmp_path)
    settings_path = tmp_path / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text())
    settings["hooks"]["Stop"][0]["hooks"][0]["command"] = (
        "/some/old/python "
        f"$CLAUDE_PROJECT_DIR/.claude/hooks/{install_hooks.WRITEBACK_HOOK_SCRIPT_NAME}")
    settings_path.write_text(json.dumps(settings, indent=2))

    install_hooks.install_writeback_hook(tmp_path)
    settings = json.loads(settings_path.read_text())
    commands = [h["command"] for entry in settings["hooks"]["Stop"]
                for h in entry["hooks"]]
    assert commands == [install_hooks.writeback_hook_command()]


def test_the_gate_leaves_other_stop_hooks_alone(tmp_path):
    """An operator's own Stop hooks are not ours to remove."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps(
        {"hooks": {"Stop": [{"hooks": [{"type": "command",
                                        "command": "base hook stop"}]}]}}))
    install_hooks.install_writeback_hook(tmp_path)
    settings = json.loads(settings_path.read_text())
    commands = [h["command"] for entry in settings["hooks"]["Stop"]
                for h in entry["hooks"]]
    assert "base hook stop" in commands
    assert install_hooks.writeback_hook_command() in commands


def test_the_registered_command_names_a_real_interpreter(tmp_path):
    """The other two hooks hardcode python3, which does not exist on a default
    Windows install — and Windows is a real host for this framework now."""
    command = install_hooks.writeback_hook_command()
    assert "python3 $CLAUDE_PROJECT_DIR" not in command
    assert sys.executable in command


# ---------------------------------------------------------------------------
# The wire step arms it. Nobody has to remember to.
# ---------------------------------------------------------------------------

def test_wiring_a_firm_arms_the_writeback_gate(tmp_path):
    """Founding a firm installs the gate, and the installed gate really bites.

    Asserting the file landed would pass with a gate registered under the wrong
    event, pointed at a missing script, or unable to run at all. So this drives
    the wired workspace the rest of the way: close a Unit in it, run the file
    the wire step actually wrote, and require exit 2.
    """
    import sqlite3

    from firm.core.db import get_db_path
    from firm.core.migrate import apply_migrations
    from firm.core.repo import create
    from firm.dashboard import wiring

    firm_id = "acme"
    workspace = tmp_path / firm_id
    workspace.mkdir()
    db = get_db_path(workspace)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    create(conn, "firm", {"id": firm_id, "name": "Acme"})
    conn.commit()
    conn.close()

    result = wiring.commit(tmp_path, firm_id, {"members": [], "mcp": [], "gaps": []})
    assert result["ok"], result.get("error")

    script = (workspace / ".claude" / "hooks"
              / install_hooks.WRITEBACK_HOOK_SCRIPT_NAME)
    assert script.exists(), "the wire step did not write the gate"

    settings = json.loads((workspace / ".claude" / "settings.json").read_text())
    stop_commands = [h["command"] for entry in settings["hooks"]["Stop"]
                     for h in entry["hooks"]]
    assert any(install_hooks.WRITEBACK_HOOK_SCRIPT_NAME in c for c in stop_commands),         f"gate not registered under Stop: {settings['hooks'].keys()}"

    # The policy gate is a PreToolUse hook and must not have been displaced.
    pre = [h["command"] for entry in settings["hooks"]["PreToolUse"]
           for h in entry["hooks"]]
    assert any(install_hooks.POLICY_HOOK_SCRIPT_NAME in c for c in pre)

    # And it bites, using the file the wire step wrote rather than a re-render.
    writeback.record_closure(workspace, firm_id, ME, "U-1")
    blocked = _run_gate(script, workspace)
    assert blocked.returncode == 2, (
        "the wire step armed a gate that does not fire: " + blocked.stderr)
    assert "U-1" in blocked.stderr


# ---------------------------------------------------------------------------
# The deadlock this pair could create, and the guard against it
# ---------------------------------------------------------------------------

#: The briefing exactly as it read BEFORE the gate existed. It is the control:
#: back then it was a harmless wrong string, because nothing checked whether a
#: Member wrote anything back. The moment the gate shipped it became a deadlock
#: - the Member does precisely what its briefing says, the gate blocks at exit
#: 2, and the briefing never names the command that clears it. A guard that
#: cannot fail on THIS text is not guarding anything.
BRIEFING_BEFORE_THE_GATE = (
    "Closing one: `firm unit complete <id> --member MEM-001 --outputs <file>`. "
    "Queue follow-up with `firm unit create`, and record what you learned with "
    "`base learn --domain <project> --entity MEM-001 --text \"...\"` before "
    "you finish.")


def _verb_the_gate_demands(stderr: str, unit_id: str) -> str:
    """The command the gate really tells a Member to run, out of its own stderr.

    Read off the rendered message rather than imported from a constant. Two
    surfaces reading one constant still drift when only one of them is wired to
    it, and that is the failure being guarded - so the guard compares what the
    two surfaces actually SAY.
    """
    found = re.search(r"Record it now:\s+(.+?)\s+" + re.escape(unit_id), stderr)
    assert found, (
        "the gate's message no longer contains a command in the shape this "
        f"guard reads, so it checked nothing. stderr was: {stderr!r}")
    return found.group(1).strip()


def _a_briefing_for(member_id: str, tmp_path: Path) -> str:
    from firm.core import repo
    from firm.core.db import get_db_path
    from firm.core.migrate import apply_migrations
    from firm.services import brief

    db = get_db_path(tmp_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    repo.create(conn, "firm", {"id": FIRM, "name": "Test Firm"})
    repo.create(conn, "member", {"id": member_id, "firm_id": FIRM,
                                 "name": "Pen", "role": "Writer"})
    repo.create(conn, "operation", {"id": "OPS-1", "firm_id": FIRM, "name": "Ops"})
    repo.create(conn, "project", {"id": "PRJ-1", "firm_id": FIRM,
                                  "operation_id": "OPS-1", "name": "Alpha",
                                  "status": "in_progress", "due_date": "2026-12-31"})
    repo.create(conn, "unit", {"id": "UNT-1", "firm_id": FIRM, "name": "Work",
                               "project_id": "PRJ-1",
                               "assignee_member_id": member_id,
                               "status": "pending"})
    try:
        return brief.render(conn, FIRM, member_id)
    finally:
        conn.close()


def test_the_briefing_names_the_verb_the_gate_demands(tmp_path):
    """A Member that obeys its own briefing must not be trapped by the gate.

    This is the whole reason the briefing and the gate render from one
    constant. Before the fix the briefing said `base learn --domain <project>
    --entity MEM-001`, which records a note and writes NO marker - so a Member
    following its instructions to the letter closed a Unit, wrote back exactly
    as told, and was still blocked, with nothing on screen naming the command
    that would release it.

    The guard deliberately does not compare two constants. It runs the real
    gate, reads the command out of the stderr a Member would actually see, and
    requires the rendered briefing to contain that same command.
    """
    workspace = tmp_path / "firm"
    workspace.mkdir()
    writeback.record_closure(workspace, FIRM, ME, "U-1")
    blocked = _run_gate(_gate_at(tmp_path), workspace)
    assert blocked.returncode == 2, "the gate did not block, so it demanded nothing"

    demanded = _verb_the_gate_demands(blocked.stderr, "U-1")
    briefing = _a_briefing_for(ME, tmp_path / "ws")

    assert demanded in briefing, (
        f"the gate demands {demanded!r} but the briefing never names it. A "
        f"Member that does what its briefing says is then blocked with no way "
        f"out on screen. Briefing was:\n{briefing}")

    # The control. Without it this is a substring search that would pass just
    # as happily over a briefing that names nothing at all.
    assert demanded not in BRIEFING_BEFORE_THE_GATE, (
        "the control text no longer fails this guard, so the guard cannot tell "
        "a briefing that names the right verb from one that does not")


def test_the_briefing_does_not_send_a_member_to_a_verb_that_writes_no_marker(tmp_path):
    """The specific wrong turn, named so it cannot come back quietly.

    `base learn --domain ... --entity ...` is a real command and it does record
    a note. That is exactly why it is dangerous here: it succeeds, so the
    Member has no reason to think anything is wrong, and it leaves no marker,
    so the gate keeps blocking.
    """
    briefing = _a_briefing_for(ME, tmp_path / "ws")
    assert "base learn --domain" not in briefing, (
        "the briefing sends the Member to a command that records a note and "
        "writes no marker, which reads as success and still deadlocks")


@pytest.mark.parametrize("verb", ["CLOSE_VERB", "WRITE_BACK_VERB"])
def test_the_verb_a_member_is_told_to_run_actually_resolves(verb):
    """One constant feeding both surfaces makes them agree; it does not make
    them right.

    Change the constant to nonsense and the gate and the briefing agree
    perfectly on a command that does not exist. Every guard above stays green,
    because they all compare the two surfaces to each other. This is the only
    one that asks the parser.

    `base cadre <x>` reaches the same argparse as `firm <x>` — the extension's
    command handler forwards to it — so the tail is what has to resolve.
    """
    value = getattr(writeback, verb)
    words = value.split()
    assert words[:2] == ["base", "cadre"], (
        f"{verb} is {value!r}, which is not a `base cadre ...` invocation, so "
        f"this guard cannot tell what to resolve")
    tail = words[2]
    done = subprocess.run([sys.executable, "-m", "firm", tail, "--help"],
                          capture_output=True, text=True, timeout=60,
                          cwd=str(Path(__file__).resolve().parents[2]))
    assert done.returncode == 0, (
        f"{verb} tells a Member to run `{value}`, but `firm {tail} --help` "
        f"exits {done.returncode} - the command does not exist. stderr: "
        + (done.stderr or "").strip())


def test_that_resolve_guard_can_actually_fail():
    """Must-fail canary. The guard above is a subprocess check, so prove the
    parser really does reject a verb that is not there — otherwise a broken
    invocation would read as a pass."""
    done = subprocess.run([sys.executable, "-m", "firm", "notaverb", "--help"],
                          capture_output=True, text=True, timeout=60,
                          cwd=str(Path(__file__).resolve().parents[2]))
    assert done.returncode != 0, (
        "the firm parser accepted a verb that does not exist, so the resolve "
        "guard above cannot discriminate")


def test_the_gate_message_survives_a_legacy_code_page(tmp_path):
    """The Windows CI leg reads this stream through a cp1252 pipe.

    The gate is not run through the Cadre CLI, so `_force_utf8_streams` does
    not cover it - it is launched directly by Claude Code and pins its own
    stderr to UTF-8. That protects the WRITE. The READ is the other half: the
    Windows job runs the whole suite with PYTHONIOENCODING deliberately unset,
    so anything capturing this message decodes it with the platform locale.

    A curly quote or an em dash in the message would therefore pass here on
    Linux and fail on Windows only - the exact shape of defect that green
    board was just fixed to catch. Keeping the message ASCII costs nothing;
    the message is a shell command and a sentence about it.
    """
    workspace = tmp_path / "firm"
    workspace.mkdir()
    writeback.record_closure(workspace, FIRM, ME, "U-1")
    done = subprocess.run(
        [sys.executable, str(_gate_at(tmp_path))],
        cwd=str(workspace),
        env={**os.environ, "CADRE_MEMBER_ID": ME},
        input=json.dumps({"cwd": str(workspace), "stop_hook_active": False}).encode(),
        capture_output=True, timeout=60)
    assert done.returncode == 2, "the gate did not block, so it printed nothing to check"

    offending = sorted({chr(b) for b in done.stderr if b > 127})
    assert not offending, (
        f"the gate's message carries non-ASCII {offending}, which a cp1252 "
        f"pipe on the Windows leg cannot round-trip")
    # And the positive half: it really does decode on the narrow code page.
    assert "U-1" in done.stderr.decode("cp1252")


# ---------------------------------------------------------------------------
# Authority: a Board decision, not a crash
# ---------------------------------------------------------------------------

def _firm_with_one_unit(root: Path) -> None:
    from firm.core import repo
    from firm.core.db import get_db_path
    from firm.core.migrate import apply_migrations

    db = get_db_path(root)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    repo.create(conn, "firm", {"id": FIRM, "name": "Test Firm"})
    repo.create(conn, "member", {"id": ME, "firm_id": FIRM, "name": "Pen",
                                 "role": "Writer", "status": "active"})
    repo.create(conn, "operation", {"id": "OPS-1", "firm_id": FIRM, "name": "Ops"})
    repo.create(conn, "project", {"id": "PRJ-1", "firm_id": FIRM,
                                  "operation_id": "OPS-1", "name": "Alpha",
                                  "status": "in_progress", "due_date": "2026-12-31"})
    repo.create(conn, "unit", {"id": "UNT-1", "firm_id": FIRM, "name": "Work",
                               "project_id": "PRJ-1", "assignee_member_id": ME,
                               "status": "pending"})
    conn.commit()
    conn.close()


def test_closing_without_the_board_s_grant_explains_itself(tmp_path, capsys,
                                                           monkeypatch):
    """A Member without authority gets the escalation route, not a traceback.

    `complete_unit` raises AuthorityError, and nothing caught it — so the
    Member saw a Python stack ending in `AuthorityError: authority_required`.
    That reads as Cadre crashing rather than as a Board decision, and a Member
    that believes the framework is broken stops using the verb instead of
    raising the escalation the error is telling it to raise.

    Fixed at `run_unit_complete`, which is where it escaped, so `firm unit
    complete` gets it too and not only `base cadre complete`.
    """
    from firm.cli.unit import run_unit_complete

    _firm_with_one_unit(tmp_path)
    monkeypatch.setenv("CADRE_MEMBER_ID", ME)

    code = run_unit_complete(tmp_path, "UNT-1", ME, firm_id=FIRM)
    captured = capsys.readouterr()

    assert code == 1, "an ungranted close must fail, not succeed quietly"
    assert "authority_required" in captured.err
    assert "escalation raise" in captured.err, (
        "the denial does not carry the route out, so the Member is stuck: "
        + captured.err)
    assert "Traceback" not in captured.err


def test_closing_with_the_grant_still_works(tmp_path, capsys, monkeypatch):
    """The other direction. Without it the test above passes just as happily
    over a `complete` that refuses everybody."""
    from firm.cli.unit import run_unit_complete
    from firm.core.db import get_db_path
    from firm.services.authority import grant_authority

    _firm_with_one_unit(tmp_path)
    conn = sqlite3.connect(get_db_path(tmp_path))
    conn.row_factory = sqlite3.Row
    grant_authority(conn, ME)
    conn.commit()
    conn.close()

    monkeypatch.setenv("CADRE_MEMBER_ID", ME)
    code = run_unit_complete(tmp_path, "UNT-1", ME, firm_id=FIRM)
    captured = capsys.readouterr()

    assert code == 0, "a granted Member could not close its own Unit: " + captured.err
    assert "completed UNT-1" in captured.out
