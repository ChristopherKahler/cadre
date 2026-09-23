"""#168 GAP 2 · `cadre init` arms the NEVER gate, and the armed gate FIRES.

The red set, written against main `59f6aa89` before the fix (lane doc
`briefs/2026-09-22-dunlin-cadre-issue-168-gap2-policy-gate.md`: the G0 at
lines 90-362, osprey's verdict at 363-451).

WHAT EVERY LEG DRIVES. The founding legs go through `firm.__main__.main`,
the command a Claude Code session types. The doctor legs read the report
card's items BY KEY (`policy-gate`, `policy-fresh`) and never its exit code
or its top-level `ok`, which is `true` whenever the doctor ran at all
(`cli/doctor.py:738`) -- on main it is `true` while `policy-gate` fails.

WHY THE GATE IS RUN THROUGH A SHELL HERE. The existing gate tests
(`tests/hooks/test_policy_gate.py`, `tests/test_wiring_policy.py`) run the
hook FILE with the test's own interpreter. That can never see the command
the gate is REGISTERED under, and on main that command is `python3 <hook>`:
on a Windows host with no `python3` it exits 127, and Claude Code lets the
forbidden call through (measured on the operator's PC, G0 section 4). So
`_run_gate` reads the command out of `.claude/settings.json` and runs it the
way Claude Code 2.1.280 does -- Git Bash on Windows, `/bin/sh` elsewhere, the
PreToolUse JSON on stdin -- with PATH narrowed to one empty directory, so
`python3` cannot resolve on any host. It proves that blindness before it
believes a verdict (law 48): a verdict under a PATH that still finds
`python3` would prove nothing about the command.

WHAT THIS FILE IMPORTS. Only names that exist on main, so a leg that is red
on main is red for the reason it names and never for an ImportError (#166,
where a test imported a class by a name nobody had read). The new command
is asserted by what it does, not by how it is spelled.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from firm.cli.doctor import diagnose, fix
from firm.cli.install_hooks import POLICY_HOOK_SCRIPT_NAME, render_policy_hook
from firm.core import repo
from firm.core.db import connect, get_db_path
from firm.services import contract as contract_svc
from firm.services import policy as policy_svc
from tests.test_cadre_init_founds import (
    FIRM_ID,
    _chart_proposal,
    _FakeAgent,
    _fingerprint,
    _run,
    _write,
)

#: main's registration, byte for byte (`cli/install_hooks.py:41-43` at
#: `59f6aa89`). Every firm armed before #168 carries exactly this string.
MAIN_COMMAND = f"python3 $CLAUDE_PROJECT_DIR/.claude/hooks/{POLICY_HOOK_SCRIPT_NAME}"

#: The NEVER rule the legs fire on, verbatim from the shape the hub's Train
#: emits (`tests/test_wiring_policy.py`, TRAINED_PLAN). A firm founded by init
#: has no deny rule and no CLI verb adds one (`dashboard/wiring.py:747` is the
#: only writer), so `_add_rule` puts it on a Contract the way Train does.
RULE = {"match": "*slack_send_message*", "tool": "slack-desk",
        "reason": "The firm drafts everything and sends nothing."}

#: The call RULE denies, and a benign call it allows.
SEND = ("mcp__slack-desk__slack_send_message", {"channel_id": "D07", "text": "hi"})
UNREADS = ("mcp__slack-desk__slack_get_unreads", {})


@pytest.fixture
def root(tmp_path: Path) -> Path:
    firms = tmp_path / "firms"
    firms.mkdir()
    return firms


# ---------------------------------------------------------------------------
# Helpers. Each one reads production's own output; none builds an answer.
# ---------------------------------------------------------------------------

def _found(root: Path, tmp_path: Path, capsys) -> tuple[dict[str, Any], Path]:
    """Door A, as a session types it. Returns (the result object, workspace)."""
    rc, result, out = _run(["init", str(root), "--proposal",
                            str(_write(tmp_path, _chart_proposal()))], capsys)
    assert rc == 0 and result and result.get("ok") is True, out[-400:]
    return result, root / FIRM_ID


def _items(ws: Path, tmp_path: Path) -> dict[str, dict[str, Any]]:
    """The doctor's report card by key. `unit_dir` pins the scheduler to a
    temp directory (the tests' knob, `cli/heartbeat.py:54-60`), so no leg
    reads or writes the host's real timers."""
    return {c["key"]: c for c in diagnose(ws, FIRM_ID, unit_dir=tmp_path / "units")}


def _assert_ok(items: dict[str, dict[str, Any]], key: str) -> None:
    item = items[key]
    assert item["ok"] is True and item.get("state") == "ok", (
        f"{key} is {item.get('state')}: {item.get('detail')}")


def _settings(ws: Path) -> dict[str, Any]:
    path = ws / ".claude" / "settings.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _gate_commands(ws: Path) -> list[str]:
    """Every registered PreToolUse command that runs the gate script."""
    return [str(h.get("command") or "")
            for entry in (_settings(ws).get("hooks") or {}).get("PreToolUse") or []
            if isinstance(entry, dict)
            for h in entry.get("hooks") or []
            if isinstance(h, dict)
            and POLICY_HOOK_SCRIPT_NAME in str(h.get("command") or "")]


def _the_command(ws: Path) -> str:
    commands = _gate_commands(ws)
    assert len(commands) == 1, (
        f"expected exactly one registered gate, found {len(commands)}: {commands}")
    return commands[0]


def _add_rule(ws: Path) -> str:
    """Put RULE on the lead's Contract the way Train does
    (`dashboard/wiring.py:742-749`), materialize it with the function
    `doctor --fix` and Train call, and return the lead's member id."""
    conn = connect(get_db_path(ws))
    try:
        lead = next(m for m in repo.find(conn, "member", firm_id=FIRM_ID)
                    if not m.get("reports_to_member_id"))
        contract = repo.get(conn, "contract", lead["contract_id"]) or {}
        raw = contract.get("validation_config")
        vc = json.loads(raw) if isinstance(raw, str) and raw.strip() else dict(raw or {})
        vc["deny"] = [RULE]
        contract_svc.update_contract(conn, contract["id"], {"validation_config": vc})
        policy_svc.materialize(conn, ws, FIRM_ID)
        conn.commit()
    finally:
        conn.close()
    return str(lead["id"])


def _hook_shell() -> str:
    """The shell Claude Code 2.1.280 runs a hook command through.

    POSIX: `shell: true`, which is /bin/sh. Windows: Git Bash. With no Git
    Bash, Claude Code falls back to PowerShell ("Claude Code on Windows
    requires either Git for Windows (for bash) or PowerShell", its own
    message), where this command does not parse and the call proceeds.
    That host is boundary B3, and a Windows run FAILS here rather than
    skipping, so the one fail-open case is never recorded as a pass.
    """
    if os.name != "nt":
        return "/bin/sh"
    candidates = [os.environ.get("CLAUDE_CODE_GIT_BASH_PATH") or "",
                  r"C:\Program Files\Git\bin\bash.exe"]
    git = shutil.which("git")
    if git:
        candidates.append(str(Path(git).resolve().parent.parent / "bin" / "bash.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    pytest.fail("no Git Bash on this Windows host: Claude Code would run the gate "
                "under PowerShell, where it does not parse (G0 boundary B3)")


def _shell(command: str, env: dict[str, str], cwd: Path,
           stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [_hook_shell(), "-c", command], input=stdin, capture_output=True,
        text=True, encoding="utf-8", errors="replace", env=env, cwd=str(cwd),
        timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _run_gate(ws: Path, command: str, member: str | None,
              call: tuple[str, dict[str, Any]], tmp_path: Path,
              path_dir: Path | None = None) -> subprocess.CompletedProcess:
    """Run *command* as Claude Code would, for *member* (None = the Board).

    With no *path_dir*, PATH is one empty directory and `python3` must not
    resolve; that is checked through the same shell and env first.
    """
    blind = path_dir is None
    if path_dir is None:
        path_dir = tmp_path / "empty-bin"
        path_dir.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "CADRE_MEMBER_ID"}
    env["PATH"] = str(path_dir)
    # Claude Code hands a Windows project root to bash with `/` (G0 section 4).
    env["CLAUDE_PROJECT_DIR"] = (str(ws).replace("\\", "/") if os.name == "nt"
                                 else str(ws))
    if member:
        env["CADRE_MEMBER_ID"] = member
    if blind:
        seen = _shell("command -v python3", env, ws)
        assert seen.stdout.strip() == "", (
            f"python3 still resolves with PATH={path_dir}: {seen.stdout!r}; a "
            "verdict under this PATH would prove nothing about the command")
    tool, tool_input = call
    payload = json.dumps({"hook_event_name": "PreToolUse", "cwd": str(ws),
                          "tool_name": tool, "tool_input": tool_input})
    return _shell(command, env, ws, payload)


def _verdict(proc: subprocess.CompletedProcess) -> str:
    """What Claude Code 2.1.280 does with a command-type PreToolUse hook's
    result, read from its bundle (G0 section 4).

      denied    exit 0 with a deny JSON: the gate ran and refused the call
      blocked   exit 2: the call is refused (here: the gate did not run)
      allowed   exit 0 with no deny: the call goes ahead
      proceeds  any other exit: a non-blocking error, the call goes ahead
    """
    if proc.returncode == 2:
        return "blocked"
    if proc.returncode != 0:
        return "proceeds"
    try:
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except ValueError:
        return "allowed"
    decision = (out.get("hookSpecificOutput") or {}).get("permissionDecision")
    return "denied" if decision == "deny" else "allowed"


# ---------------------------------------------------------------------------
# R -- init leaves the gate armed, current and reported (orders items 5, 2)
# ---------------------------------------------------------------------------

def test_r1_init_proposal_leaves_the_gate_armed_and_current(root, tmp_path, capsys):
    """Read by key, through the service and through the CLI surface."""
    _, ws = _found(root, tmp_path, capsys)
    items = _items(ws, tmp_path)
    _assert_ok(items, "policy-gate")
    _assert_ok(items, "policy-fresh")

    _, card, out = _run(["doctor", "--workspace", str(ws), "--json"], capsys)
    assert card is not None and isinstance(card.get("checks"), list), out[-300:]
    by_key = {c["key"]: c for c in card["checks"]}
    _assert_ok(by_key, "policy-gate")
    _assert_ok(by_key, "policy-fresh")


def test_r1b_init_materializes_the_policy(root, tmp_path, capsys):
    """`policy-fresh` reads an ABSENT policy.json as fresh when the firm has
    no deny rules (`cli/doctor.py:181`), so the item passes on main and
    cannot tell. The file itself is read here."""
    _, ws = _found(root, tmp_path, capsys)
    path = ws / ".firm" / "policy.json"
    assert path.is_file(), "init returned without materializing .firm/policy.json"
    conn = connect(get_db_path(ws))
    try:
        want = policy_svc.member_denies(conn, FIRM_ID)
    finally:
        conn.close()
    assert json.loads(path.read_text(encoding="utf-8")) == want


def test_r2_init_brief_arms_the_gate(root, tmp_path, capsys, monkeypatch):
    """Door B, driven exactly as `test_r11_door_b_runs_the_agent...` drives it."""
    from firm.services import founding as svc

    agent = _FakeAgent(_chart_proposal())
    monkeypatch.setattr(svc, "popen_utf8", agent, raising=True)
    monkeypatch.setattr("firm.pulse.spawn.resolve_claude_bin",
                        lambda: ("/usr/bin/claude", "fake"))
    monkeypatch.setattr(svc, "_inventory", lambda: ("(no arsenal)", {}))
    spec = tmp_path / "spec.md"
    spec.write_text("# A two-person writing firm\n\nIt writes things.\n",
                    encoding="utf-8")

    rc, result, out = _run(["init", str(root), "--brief", str(spec)], capsys)
    assert rc == 0 and result and result.get("ok") is True, out[-400:]
    items = _items(root / FIRM_ID, tmp_path)
    _assert_ok(items, "policy-gate")
    _assert_ok(items, "policy-fresh")


def test_r3_the_result_says_the_gate_is_armed(root, tmp_path, capsys):
    """Absent is not armed (law 7): the result carries the gate's state."""
    result, _ = _found(root, tmp_path, capsys)
    gate = result.get("policy_gate")
    assert isinstance(gate, dict) and gate.get("armed") is True, (
        f"the founding result does not say the NEVER gate is armed: {gate!r}")


# ---------------------------------------------------------------------------
# F -- the gate init registered FIRES, run as Claude Code runs it (item 6)
# ---------------------------------------------------------------------------

def test_f1_the_registered_gate_fires_with_no_python3(root, tmp_path, capsys):
    """The send is DENIED by the gate itself (a deny JSON, not an exit 2 from
    a gate that did not run), the benign call passes, the Board passes."""
    _, ws = _found(root, tmp_path, capsys)
    lead = _add_rule(ws)
    command = _the_command(ws)
    assert not command.lstrip().startswith("python3"), (
        f"the registered gate still needs python3 on the PATH: {command}")

    send = _run_gate(ws, command, lead, SEND, tmp_path)
    assert _verdict(send) == "denied", (send.returncode, send.stdout, send.stderr)
    unreads = _run_gate(ws, command, lead, UNREADS, tmp_path)
    assert _verdict(unreads) == "allowed", (unreads.returncode, unreads.stderr)
    board = _run_gate(ws, command, None, SEND, tmp_path)
    assert _verdict(board) == "allowed", "the gate governs Members only"


def test_f2_control_mains_command_needs_python3_to_fire(tmp_path):
    """GREEN ON BOTH HEADS. The blindness control for F1 (law 39).

    main's command over main's gate and a real policy: with python3
    unresolvable it cannot start and the call PROCEEDS; with a python3 on
    the PATH (a shim that execs this interpreter) it DENIES. So the stripped
    PATH really blinds python3, and the old command differs from a working
    gate by that one lookup and nothing else.
    """
    ws = tmp_path / "armed-on-main"
    hooks = ws / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / POLICY_HOOK_SCRIPT_NAME).write_text(render_policy_hook(), encoding="utf-8")
    (ws / ".firm").mkdir()
    (ws / ".firm" / "policy.json").write_text(json.dumps({"MEM-001": [RULE]}),
                                              encoding="utf-8")

    blind = _run_gate(ws, MAIN_COMMAND, "MEM-001", SEND, tmp_path)
    assert _verdict(blind) == "proceeds", (blind.returncode, blind.stderr)

    shim_dir = tmp_path / "shim-bin"
    shim_dir.mkdir()
    shim = shim_dir / "python3"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
                    encoding="utf-8", newline="\n")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    seeing = _run_gate(ws, MAIN_COMMAND, "MEM-001", SEND, tmp_path, path_dir=shim_dir)
    assert _verdict(seeing) == "denied", (seeing.returncode, seeing.stderr)


# ---------------------------------------------------------------------------
# FC -- a gate that cannot start: a Member is stopped, the Board never is
# ---------------------------------------------------------------------------

def _broken(ws: Path, tmp_path: Path) -> str:
    """The registered command with its interpreter replaced by a path that
    does not exist: the arming install deleted, as boundary B4 describes."""
    command = _the_command(ws)
    assert sys.executable in command, (
        "the registered gate does not name this install's interpreter, so "
        f"there is nothing here to break: {command}")
    return command.replace(sys.executable,
                           str(tmp_path / "gone" / Path(sys.executable).name))


def test_fc1_a_gate_that_cannot_start_blocks_a_member(root, tmp_path, capsys):
    _, ws = _found(root, tmp_path, capsys)
    lead = _add_rule(ws)
    broken = _broken(ws, tmp_path)
    for call in (SEND, UNREADS):
        proc = _run_gate(ws, broken, lead, call, tmp_path)
        assert _verdict(proc) == "blocked", (call[0], proc.returncode, proc.stderr)
        assert "cadre doctor --fix" in proc.stderr, proc.stderr


def test_fc2_a_gate_that_cannot_start_never_blocks_the_board(root, tmp_path, capsys):
    """The Board's own session in the firm folder is where `doctor --fix`
    runs, so it must never be locked out by the gate's own failure."""
    _, ws = _found(root, tmp_path, capsys)
    _add_rule(ws)
    proc = _run_gate(ws, _broken(ws, tmp_path), None, SEND, tmp_path)
    assert _verdict(proc) == "allowed", (proc.returncode, proc.stderr)


# ---------------------------------------------------------------------------
# P -- idempotent and polite to files that were already there (item 3)
# ---------------------------------------------------------------------------

def test_p1_an_existing_settings_file_is_merged_not_replaced(root, tmp_path, capsys):
    before = {
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {
            "PreToolUse": [{"matcher": "Bash",
                            "hooks": [{"type": "command", "command": "echo other-pre"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "echo other-stop"}]}],
        },
    }
    claude_dir = root / FIRM_ID / ".claude"
    claude_dir.mkdir(parents=True)
    (claude_dir / "settings.json").write_text(json.dumps(before), encoding="utf-8")

    _, ws = _found(root, tmp_path, capsys)
    after = _settings(ws)
    assert after.get("permissions") == before["permissions"]
    assert (after.get("hooks") or {}).get("Stop") == before["hooks"]["Stop"]
    assert before["hooks"]["PreToolUse"][0] in (after.get("hooks") or {}).get("PreToolUse", [])
    _the_command(ws)


def test_p2_doctor_fix_after_init_changes_nothing_on_the_gate(root, tmp_path, capsys):
    _, ws = _found(root, tmp_path, capsys)
    watched = [ws / ".claude" / "settings.json",
               ws / ".claude" / "hooks" / POLICY_HOOK_SCRIPT_NAME,
               ws / ".firm" / "policy.json"]
    before = [p.read_bytes() if p.is_file() else None for p in watched]

    units = tmp_path / "units"
    did = fix(ws, FIRM_ID, diagnose(ws, FIRM_ID, unit_dir=units), unit_dir=units)
    assert not [d for d in did if d.startswith("policy")], did
    assert [p.read_bytes() if p.is_file() else None for p in watched] == before


def test_p3_a_malformed_settings_file_is_reported_and_left_alone(root, tmp_path, capsys):
    """Ruling (c): the firm stays founded (`ok: true`), the result says the
    gate is NOT armed and names the file and the fix, and the operator's
    file is not touched."""
    claude_dir = root / FIRM_ID / ".claude"
    claude_dir.mkdir(parents=True)
    bad = b'{"hooks": {"PreToolUse": [ this is not json'
    (claude_dir / "settings.json").write_bytes(bad)

    result, _ = _found(root, tmp_path, capsys)
    gate = result.get("policy_gate")
    assert isinstance(gate, dict) and gate.get("armed") is False, gate
    detail = str(gate.get("detail") or "")
    assert "settings.json" in detail and "cadre doctor" in detail and "--fix" in detail, detail
    assert (claude_dir / "settings.json").read_bytes() == bad


# ---------------------------------------------------------------------------
# S -- scope: never the operator's own Claude settings (item 4, #167)
# ---------------------------------------------------------------------------

def test_s1_the_arm_never_writes_the_operators_claude_folder(root, tmp_path, capsys,
                                                             monkeypatch):
    home = tmp_path / "operator-home"
    (home / ".claude" / "hooks").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text('{"hooks": {}}', encoding="utf-8")
    (home / ".claude" / "hooks" / "keep.py").write_text("# the operator's own\n",
                                                        encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    before = _fingerprint(home / ".claude")

    _found(root, tmp_path, capsys)
    assert _fingerprint(home / ".claude") == before, (
        "founding a firm changed the operator's own ~/.claude")


# ---------------------------------------------------------------------------
# U -- the upgrade path (law 22, orders item 7)
# ---------------------------------------------------------------------------

def test_u1_a_firm_armed_with_mains_command_is_repointed_by_fix(root, tmp_path, capsys):
    _, ws = _found(root, tmp_path, capsys)
    other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo other-pre"}]}
    hooks_dir = ws / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / POLICY_HOOK_SCRIPT_NAME).write_text(render_policy_hook(), encoding="utf-8")
    (ws / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        other,
        {"matcher": "*", "hooks": [{"type": "command", "command": MAIN_COMMAND}]},
    ]}}, indent=2) + "\n", encoding="utf-8")

    assert _items(ws, tmp_path)["policy-gate"]["ok"] is False, (
        "a gate registered as python3 reads as current, so --fix never re-points it")

    units = tmp_path / "units"
    fix(ws, FIRM_ID, diagnose(ws, FIRM_ID, unit_dir=units), unit_dir=units)
    command = _the_command(ws)
    assert not command.lstrip().startswith("python3"), command
    assert other in _settings(ws)["hooks"]["PreToolUse"]
    _assert_ok(_items(ws, tmp_path), "policy-gate")


def test_u2_a_firm_with_no_gate_is_armed_by_fix_as_before(root, tmp_path, capsys):
    """GREEN ON BOTH HEADS: the path every firm founded before #168 takes."""
    _, ws = _found(root, tmp_path, capsys)
    # What main's init leaves behind: no gate file and no settings.json.
    (ws / ".claude" / "hooks" / POLICY_HOOK_SCRIPT_NAME).unlink(missing_ok=True)
    (ws / ".claude" / "settings.json").unlink(missing_ok=True)
    assert _items(ws, tmp_path)["policy-gate"]["ok"] is False

    units = tmp_path / "units"
    fix(ws, FIRM_ID, diagnose(ws, FIRM_ID, unit_dir=units), unit_dir=units)
    _assert_ok(_items(ws, tmp_path), "policy-gate")
    _the_command(ws)


def test_u3_init_over_a_folder_that_already_has_a_gate(root, tmp_path, capsys):
    claude_dir = root / FIRM_ID / ".claude"
    (claude_dir / "hooks").mkdir(parents=True)
    (claude_dir / "hooks" / POLICY_HOOK_SCRIPT_NAME).write_text("# a stale gate\n",
                                                                encoding="utf-8")
    (claude_dir / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "*", "hooks": [{"type": "command", "command": MAIN_COMMAND}]},
    ]}}), encoding="utf-8")

    _, ws = _found(root, tmp_path, capsys)
    command = _the_command(ws)
    assert not command.lstrip().startswith("python3"), command
    assert ((claude_dir / "hooks" / POLICY_HOOK_SCRIPT_NAME).read_text(encoding="utf-8")
            == render_policy_hook())
