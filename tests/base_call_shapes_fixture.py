"""One uncontrolled base call of each shape this codebase actually writes.

Issue #136, verdict amendment G-a. This file exists to be SCANNED, never run or
imported for its behaviour. It is the answer to law 31: a guard audited against
its own test cases proves it fires, and proves nothing about what it reaches.
Each shape below was read out of the tree at main `9848c444` by gadwall's G0-2
enumeration, so this is a list of what the codebase does, not a list of what the
scanner's author thought of.

Every call is on ONE line, and `test_deleting_a_fixture_line_drops_the_count_by_one`
deletes each in turn and requires the guard's count to fall by exactly one. A
shape the guard cannot see keeps the count up and fails that row -- which is the
only thing that can tell a guard with a blind axis from a guard that works.

The shapes, and where each one lives in the real tree:

1. a name bound from `which_base()`            -- `base_extension.install` (C2, C3, C4)
2. a name bound from `shutil.which("base")`    -- `secrets/provider.py:145` (C14)
3. a bare `"base"` argv[0]                     -- `sysconfig/service.py:425`, `:553`
4. a wrapper two hops from the real runner     -- `secrets/provider._run`, `firm_relay._run`
5. `popen_utf8` rather than `run_utf8`         -- the spawn wrappers
6. `cwd=` built from the PROCESS's directory   -- `base_extension.py:535`, `cwd=str(Path.cwd())`
7. a MODULE-LOCAL resolver wrapper             -- `rail/turns.py:75` `find_base()` (C20-C23)
8. a resolver that returns `(path, reason)`    -- `firm_relay.py:76` `_base()` (C15)

Shape 6 is the one the guard as first drafted would have missed on the `cwd`
axis: it passes a `cwd=`, so a check for "no cwd at all" reads it as controlled
while it is the uncontrolled directory written out longhand.

SHAPE 7 WAS MISSED FOR REAL, and it is why this file is worth more than its
length. The scanner's first version knew two ways to resolve base and the tree
has three: `rail/turns.py` wraps `shutil.which("base")` in its own `find_base()`
ladder, and all four relay functions bind through it. The scanner saw none of
those four calls, and what caught it was not review -- it was
`test_the_allow_list_has_no_stale_entries` reporting their allow-list entries as
stale, i.e. the allow-list being right and the reader being blind. Law 31
exactly: the guard fired on every case its author had thought of.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from firm.core.proc import popen_utf8, run_utf8
from firm.sysconfig.service import which_base


def _run(argv: list[str]):
    """A module-local wrapper, the way three real modules write one.

    The runner fixpoint has to reach through this: it passes its own parameter
    as argv, so it becomes a runner itself and a call to IT counts as a base
    call. gadwall's first #136 scan missed exactly this and the cross-check
    caught it.
    """
    return run_utf8(argv, capture_output=True, timeout=60)


def shape_1_resolver_bound_name():
    base = which_base()
    return run_utf8([base, "rule", "list", "--domain", "acme"], capture_output=True)


def shape_2_shutil_which_name():
    binary = shutil.which("base")
    return run_utf8([binary, "extension", "validate", "m.toml"], capture_output=True)


def shape_3_bare_string():
    return run_utf8(["base", "ext", "list"], capture_output=True)


def shape_4_wrapper_two_hops():
    base = which_base()
    return _run([base, "scaffold", "/tmp/ws"])


def shape_5_popen():
    base = which_base()
    return popen_utf8([base, "relay", "sessions"], capture_output=True)


def shape_6_cwd_from_the_process():
    base = which_base()
    return run_utf8([base, "cadre", "--help"], capture_output=True, cwd=str(Path.cwd()))


def _find_base_locally() -> str | None:
    """A module-local resolver ladder, the way `rail/turns.py:75` writes one.

    PATH first, then base's canonical install home. The scanner has to treat a
    function like this as a resolver, or every call that binds through it is
    invisible -- which is what happened to C20-C23.
    """
    found = shutil.which("base")
    if found:
        return found
    cand = Path.home() / ".local" / "bin" / "base"
    return str(cand) if cand.exists() else None


def shape_7_module_local_resolver():
    base = _find_base_locally()
    return run_utf8([base, "relay", "tasks"], capture_output=True)


def _base_and_why_not() -> tuple[str | None, str]:
    """A resolver that hands the path back INSIDE A TUPLE, as `firm_relay._base` does.

    Shape 8, and the one that was missed for real at G2. The path is element 0
    and element 1 is the sentence explaining why there is none, so a scanner
    that binds the whole unpack reads `absent` as base and a scanner that
    insists on a bare resolver call or a bare name sees no resolver at all.
    Both readings are wrong in different directions, which is why the index
    itself has to be recorded.
    """
    found = which_base()
    if found:
        return found, ""
    return None, "base is not on this machine"


def shape_8_tuple_returning_resolver():
    binary, absent = _base_and_why_not()
    return run_utf8([binary, "relay", "board"], capture_output=True)


def control_a_controlled_call_is_not_named(workspace: str):
    """The control: this one names its directory and must NOT be reported.

    Without it, a guard that reported every base call it found would pass every
    row above and still be useless. This is the benign input beside the hostile
    ones (law 49's second rule).
    """
    base = which_base()
    return run_utf8([base, "rule", "list", "--domain", "acme"],
                    capture_output=True, cwd=workspace)


def control_a_non_base_call_is_not_named():
    """The second control: a subprocess that is not base at all."""
    return run_utf8(["git", "rev-parse", "HEAD"], capture_output=True)
