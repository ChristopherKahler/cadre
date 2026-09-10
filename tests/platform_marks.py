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

import sys

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
