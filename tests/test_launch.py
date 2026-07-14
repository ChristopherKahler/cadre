"""The launch module — prompt composition and the wt.exe spawn line.

The spawn itself needs a Windows host, so Popen is captured; what CAN be
proven headlessly is the part that breaks in practice — the launcher script's
quoting — so every script is also run through ``bash -n``.
"""

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from firm.dashboard import launch


@pytest.fixture(autouse=True)
def _linux_branch(monkeypatch):
    """summon() dispatches by OS — pin the Linux/WSL branch these tests were
    written against; the macOS branch has its own test at the bottom."""
    monkeypatch.setattr(sys, "platform", "linux")

# launch shares the global subprocess module — patching its Popen patches
# everyone's, including the bash -n check below. Keep the real one.
_REAL_POPEN = subprocess.Popen


@pytest.fixture
def spawn(monkeypatch):
    calls = {}
    monkeypatch.setattr(launch.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(launch.subprocess, "Popen",
                        lambda argv, **kw: calls.setdefault("argv", argv))
    return calls


def _script(calls) -> tuple[Path, str]:
    path = Path(calls["argv"][-1])
    assert _REAL_POPEN(["bash", "-n", str(path)]).wait() == 0
    return path, path.read_text()


def test_no_claude_is_a_clean_refusal(monkeypatch, tmp_path):
    # which() empty is not enough — the resolver also probes ~/.local/bin and
    # nvm, which exist on a dev machine. Pin the resolver itself.
    monkeypatch.setattr(launch, "_which_claude", lambda: None)
    r = launch.summon(str(tmp_path), "chrisai")
    assert r["ok"] is False
    assert "claude not found" in r["error"]


def test_no_terminal_is_a_clean_refusal(monkeypatch, tmp_path):
    monkeypatch.setattr(launch.shutil, "which",
                        lambda name: "/fake/claude" if name == "claude" else None)
    monkeypatch.setattr(launch.glob, "glob", lambda pattern: [])
    r = launch.summon(str(tmp_path), "chrisai")
    assert r["ok"] is False
    assert "wt.exe" in r["error"]           # names everything it tried


def test_summon_scopes_boardroom_to_the_firm(spawn, tmp_path):
    r = launch.summon(str(tmp_path), "chrisai")
    assert r == {"ok": True, "firm_id": "chrisai", "mode": "summon"}
    argv = spawn["argv"]
    assert argv[:5] == ["/fake/wt.exe", "-w", "0", "nt", "--title"]
    assert argv[6:9] == ["wsl.exe", "--", "bash"]
    _, script = _script(spawn)
    assert f"cd {shlex.quote(str(tmp_path))}" in script
    assert "/boardroom chrisai" in script
    assert "--dangerously-skip-permissions" in script
    assert "Agenda" not in script


def test_summon_lays_the_boardroom_claude_md(spawn, tmp_path):
    launch.summon(str(tmp_path), "chrisai")
    doc = (tmp_path / "CLAUDE.md").read_text()
    assert "Co-Board" in doc and "firms root" in doc


def test_boardroom_claude_md_is_never_overwritten(spawn, tmp_path):
    (tmp_path / "CLAUDE.md").write_text("the operator's own words")
    launch.summon(str(tmp_path), "chrisai")
    assert (tmp_path / "CLAUDE.md").read_text() == "the operator's own words"


def test_deploy_rides_the_agenda_in_shell_safely(spawn, tmp_path):
    agenda = ("- Address gate GATE-3 on chrisai — Vale asks: \"ship it's v2\".\n"
              "- What broke last night?")
    r = launch.summon(str(tmp_path), "chrisai", agenda)
    assert r["mode"] == "deploy"
    _, script = _script(spawn)
    # Round-trip the exec line through the shell's own parser: the prompt must
    # come out byte-identical — newlines, quotes, apostrophes and all.
    cmd, flag, prompt = shlex.split(script.partition("exec ")[2])
    assert cmd == "/fake/claude"
    assert flag == "--dangerously-skip-permissions"
    assert prompt == f"/boardroom chrisai\n\nAgenda:\n{agenda}"


def test_macos_branch_opens_a_command_file(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(launch, "_which_claude", lambda: "/fake/claude")
    calls = {}
    monkeypatch.setattr(launch.subprocess, "Popen",
                        lambda argv, **kw: calls.setdefault("argv", argv))
    r = launch.summon(str(tmp_path), "chrisai")
    assert r["ok"] is True
    assert calls["argv"][0] == "open"
    assert calls["argv"][1].endswith(".command")
