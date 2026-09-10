"""The session-pulse hook has exactly one copy, and the installer ships it verbatim.

This is the guard, not a style rule. On 2026-09-10 there were two copies: this
hook under ``install/`` and a ``_HOOK_TEMPLATE`` string literal inside
``firm/cli/install_hooks.py``. The end-to-end golden test ran the first and
``cadre init --install-hooks`` wrote the second, and they had drifted 123 diff
lines apart with the suite green throughout.

The cost was not theoretical. Commit 0d9965a fixed a Windows encoding defect
that made the hook exit silently on any character cp1252 cannot carry. It
landed in the copy the test ran. It never reached a single user.

A rule nobody can violate beats a rule everybody remembers, so this fails the
build if a second copy comes back.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "src" / "firm" / "hooks" / "session_pulse_entry.py"

needs_checkout = pytest.mark.skipif(
    not ENTRYPOINT.exists(),
    reason=f"needs the repo checkout: {ENTRYPOINT}",
)

#: A line unique to the hook. Any file carrying it is a copy of the hook.
FINGERPRINT = "SessionStart:startup entrypoint for Cadre session-pulse"


@needs_checkout
def test_only_one_file_carries_the_hook():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    if tracked.returncode != 0:
        pytest.skip("not a git checkout")

    carriers = []
    for rel in tracked.stdout.splitlines():
        p = REPO_ROOT / rel
        if not p.is_file() or p.suffix not in (".py", ".txt", ".md"):
            continue
        if p == Path(__file__):
            continue  # this test names the fingerprint on purpose
        try:
            if FINGERPRINT in p.read_text(encoding="utf-8"):
                carriers.append(rel)
        except (UnicodeDecodeError, OSError):
            continue

    assert carriers == [str(ENTRYPOINT.relative_to(REPO_ROOT)).replace("\\", "/")], (
        "the session-pulse hook must exist in exactly one file. Found: "
        + ", ".join(carriers))


@needs_checkout
def test_the_installer_ships_that_file_and_nothing_else():
    """No embedded template, and the write is byte-for-byte."""
    src = (REPO_ROOT / "src" / "firm" / "cli" / "install_hooks.py").read_text(
        encoding="utf-8")

    # A bare _HOOK_TEMPLATE assignment is the shape that caused this.
    assert not re.search(r"^_HOOK_TEMPLATE\s*=", src, re.M), (
        "install_hooks.py has an embedded session-pulse template again")

    assert "dest.write_bytes(_HOOK_SOURCE.read_bytes())" in src, (
        "the hook must be installed byte-for-byte, not re-encoded through "
        "the platform locale")


@needs_checkout
def test_the_installed_hook_is_byte_identical_to_the_source(tmp_path):
    """The end of the chain: what lands in a workspace equals what ships."""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from firm.cli.install_hooks import HOOK_SCRIPT_NAME, install_hooks

    (tmp_path / ".firm").mkdir(parents=True)
    rc, _messages = install_hooks(tmp_path)
    assert rc == 0

    installed = tmp_path / ".claude" / "hooks" / HOOK_SCRIPT_NAME
    assert installed.read_bytes() == ENTRYPOINT.read_bytes()
