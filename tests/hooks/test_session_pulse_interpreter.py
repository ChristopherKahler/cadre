"""The hook must work under the interpreter Claude Code actually launches.

Claude Code runs the registered command, which is a bare ``python3``. That is
not the interpreter Cadre is installed into. Measured on Windows 10 / Python
3.12.6 against the installed wheel, same firm, same payload:

    python3 (as registered)   rc 0, stdout 0 bytes,   firm: ImportError
    the venv that has cadre   rc 0, stdout 394 bytes, prints the roster

The hook exited 0 and printed nothing, and its contract reads silence as "no
firm here", so nothing anywhere reported the failure. It reproduces on Linux
too: this only ever looked green on machines carrying a developer editable
install in user site-packages, and the same interpreter run with ``-s`` gives
ModuleNotFoundError and 0 bytes.

So every test below drives the hook as a subprocess under an interpreter that
does NOT have cadre importable, which is the configuration that broke. A test
that runs it under the suite's own interpreter cannot see this defect at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from firm.cli.install_hooks import HOOK_SCRIPT_NAME, install_hooks
from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations
from firm.core.repo import create


@pytest.fixture(scope="module")
def bare_python(tmp_path_factory) -> str:
    """An interpreter with no cadre in it. The whole point of these tests."""
    root = tmp_path_factory.mktemp("bare-venv")
    try:
        venv.create(root, with_pip=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"could not build a bare venv here: {exc}")
    exe = (root / "Scripts" / "python.exe") if os.name == "nt" \
        else (root / "bin" / "python")
    if not exe.exists():
        pytest.skip(f"bare venv has no interpreter at {exe}")
    # -s: no user site-packages. Without it a developer box smuggles cadre in
    # through a stray __editable__.firm-0.1.0.pth in ~/.local, which is exactly
    # why this defect looked green on Linux for months.
    probe = subprocess.run([str(exe), "-s", "-c", "import firm"],
                           capture_output=True, timeout=60, env=_scrubbed_env())
    if probe.returncode == 0:
        pytest.skip("this interpreter can already import cadre; no red arm here")
    return str(exe)


def _scrubbed_env() -> dict:
    """The environment a real Claude Code hook launch has, minus our helpers.

    The suite runs with PYTHONPATH=src, which a child inherits, so without
    this the "bare" interpreter imports cadre straight out of the worktree and
    every test below skips itself as having no red arm.
    """
    env = dict(os.environ)
    for name in ("PYTHONPATH", "FIRM_SRC", "PYTHONIOENCODING"):
        env.pop(name, None)
    return env


def _seed(workspace: Path) -> None:
    db_path = get_db_path(workspace)
    conn = connect(db_path)
    try:
        apply_migrations(conn)
        create(conn, "firm", {"id": "chrisai", "name": "ChrisAI",
                              "operator": {"name": "Chris", "role": "Board"}})
        create(conn, "member", {"id": "MEM-001", "firm_id": "chrisai",
                                "name": "Quill", "role": "Blog Author"})
    finally:
        conn.commit()
        conn.close()


def _run_hook(interpreter: str, workspace: Path):
    hook = workspace / ".claude" / "hooks" / HOOK_SCRIPT_NAME
    return subprocess.run(
        [interpreter, "-s", str(hook)],
        input=json.dumps({"session_id": "t", "cwd": str(workspace)}),
        capture_output=True, text=True, encoding="utf-8", timeout=120, env=_scrubbed_env(),
    )


def test_the_hook_prints_the_roster_under_an_interpreter_without_cadre(
        bare_python, tmp_path):
    """The fix. install_hooks records where cadre lives; the hook reads it."""
    (tmp_path / ".firm").mkdir(parents=True)
    _seed(tmp_path)
    rc, _ = install_hooks(tmp_path)
    assert rc == 0

    result = _run_hook(bare_python, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), (
        "the hook printed nothing under the interpreter Claude Code launches\n"
        f"stderr: {result.stderr}")
    assert "active-roster" in result.stdout


def test_without_the_recorded_path_it_is_loud_not_silent(bare_python, tmp_path):
    """The red arm, and the half that matters more than the path itself.

    Silence used to mean two different things: no firm here, and this hook
    cannot run. Those must never be the same signal again.
    """
    (tmp_path / ".firm").mkdir(parents=True)
    _seed(tmp_path)
    install_hooks(tmp_path)
    (tmp_path / ".firm" / "python-path").unlink()

    result = _run_hook(bare_python, tmp_path)

    assert result.returncode == 0, "the hook must never block session start"
    assert not result.stdout.strip()
    assert "could not be imported" in result.stderr, (
        f"a firm is present and the hook cannot serve it - that must be "
        f"loud, not silent. stderr was: {result.stderr!r}")
    assert str(tmp_path) in result.stderr


def test_a_stale_recorded_path_is_loud_too(bare_python, tmp_path):
    """The venv gets deleted, moved or rebuilt. That is state three, not one."""
    (tmp_path / ".firm").mkdir(parents=True)
    _seed(tmp_path)
    install_hooks(tmp_path)
    (tmp_path / ".firm" / "python-path").write_text(
        str(tmp_path / "gone" / "site-packages") + "\n", encoding="utf-8")

    result = _run_hook(bare_python, tmp_path)

    assert result.returncode == 0
    assert "could not be imported" in result.stderr
    assert "gone" in result.stderr, "the reported paths must name what was tried"


def test_no_firm_here_stays_silent(bare_python, tmp_path):
    """State one is unchanged: an empty directory says nothing at all."""
    (tmp_path / ".firm").mkdir(parents=True)
    install_hooks(tmp_path)          # hook installed, but no firm.db seeded
    result = _run_hook(bare_python, tmp_path)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == "", (
        "an empty workspace must not warn; that is the one case where "
        f"silence is correct. stderr: {result.stderr!r}")
