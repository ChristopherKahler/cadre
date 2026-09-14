"""The register of tests that genuinely cannot run on a given platform.

One file, so the count is a number anyone can read rather than something
buried in a keyword filter in the CI workflow. Before 2026-09-10 the Windows
job ran

    pytest tests -k "sched or inventory or equip or floor or preflight or
    heartbeat or dashboard or launch or secrets or sysconfig or snapshot or
    seedkit or db or notify"

which deselected 1081 of 1321 tests. Nobody could say which ones, or why, or
whether the number was going up or down. It was also hiding real failures:
measured on Windows 10 / Python 3.12.6, the unfiltered suite failed 15 tests
while the filtered one reported 3.

Every mark here states the defect it is waiting on, and every condition tests
for that defect rather than for the platform, so the skip disappears by itself
when the defect is fixed instead of needing someone to remember.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

from firm.pulse import spawn as _spawn

#: The spawn layer decides whether a file can be exec'd by reading its first
#: bytes and accepting only an ELF image or a shebang
#: (firm/pulse/spawn.py::_is_execable). That is a Linux executable-format
#: check running on every platform. Windows executables are PE, magic MZ;
#: macOS ones are Mach-O. So off Linux resolve_claude_bin() finds nothing,
#: every spawn aborts before exec, and six tests fail - the same six that
#: fail on the macOS CI leg, which is what shows the cluster is not a macOS
#: quirk but everything-that-is-not-Linux.
#:
#: The condition below asks whether the check accepts the interpreter running
#: this suite. On Linux that is an ELF binary and nothing skips. Teach
#: _is_execable about PE and Mach-O and these six run everywhere with no edit
#: to this file.
spawn_layer_rejects_this_platforms_binaries = pytest.mark.skipif(
    not _spawn._is_execable(sys.executable),
    reason=(
        "spawn._is_execable accepts only ELF images and shebang scripts, so "
        "it rejects this platform's own interpreter and resolve_claude_bin "
        "returns nothing. Off-Linux spawn defect, owned separately; this skip "
        "clears itself once the format check is fixed."
    ),
)

#: Issue #111's tests reproduce the defect as measured on Linux: an nvm CLI is
#: a ``#!/usr/bin/env node`` script, symlinked into nvm's bin, that takes node
#: from PATH, and a systemd user timer starts the pulse with the manager's PATH.
#: The tests run that script. A Windows kernel does not read ``#!`` lines:
#: running the script by path fails with WinError 193 (measured 2026-09-12 on
#: Python 3.12.6 and 3.13.5). There npm installs a CLI as .cmd and .ps1 shims
#: and pulses are Task Scheduler tasks, so the reproduction has no counterpart.
#: No defect is waiting here; the mechanism does not exist on that host.
host_cannot_exec_a_shebang_script = pytest.mark.skipif(
    os.name != "posix",
    reason=(
        "runs a #!/usr/bin/env node script the way nvm installs a CLI, and "
        "this host's kernel does not execute #! scripts (Windows: WinError "
        "193; npm installs .cmd and .ps1 shims there and pulses are Task "
        "Scheduler tasks)."
    ),
)


def _chmod_can_make_a_directory_unlistable() -> bool:
    """Probe, rather than assume, that mode bits actually bite here.

    On Windows ``os.chmod`` only toggles the read-only attribute; it cannot
    remove read access, and an administrator bypasses directory ACLs in any
    case. Under a root uid on POSIX the mode is likewise ignored. In both
    cases a directory chmod'd to 000 stays perfectly listable, so an arm
    asserting that the hub refuses an unreadable root would be asserting a
    refusal that correctly never happens.

    Probed rather than keyed on ``os.name`` so that any host where the
    mechanism does exist runs the arms, and so the skip disappears by itself
    if the suite later runs somewhere it does.
    """
    if os.name != "posix":
        return False
    with tempfile.TemporaryDirectory() as d:
        probe = os.path.join(d, "probe")
        os.mkdir(probe)
        os.chmod(probe, 0o000)
        try:
            os.listdir(probe)
            return False
        except PermissionError:
            return True
        finally:
            os.chmod(probe, 0o755)


#: Issue #113's unreadable-root and unreadable-database arms need a directory
#: or file the process genuinely cannot read. No defect is waiting on this
#: skip; where it fires, the condition being tested cannot be produced at all.
skip_without_posix_permissions = pytest.mark.skipif(
    not _chmod_can_make_a_directory_unlistable(),
    reason=(
        "needs a directory this process genuinely cannot read, and chmod 000 "
        "does not produce one here (Windows chmod only toggles the read-only "
        "attribute; a root uid ignores the mode). The refusal under test "
        "cannot be provoked, so asserting it would prove nothing."
    ),
)
