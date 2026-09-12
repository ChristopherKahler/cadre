"""Give CI a `claude` to find, then PROVE the suite's fence still hides it.

ISSUE #100. The claude fence from #81 has four dynamic arms.
`test_the_fence_reaches_a_child_interpreter` and
`test_every_known_resolver_refuses_a_set_but_unusable_hatch` both assert that
all three resolvers return None. **A host with no claude satisfies that whether
the fence works or has been deleted outright**, so on every CI runner those two
arms run, pass, and measure nothing. One of them is the arm written for the
exact defect that opened #81 -- consulting is not refusing.

Measured as a 2x2 before this script was written, reading what a CHILD
interpreter resolves, which is the boundary `_child_resolves` uses:

    host                          fence          arm 292/378
    no claude anywhere            ON             passes
    no claude anywhere            DELETED        passes     <- measures nothing
    decoy at ~/.local/bin/claude  ON             passes     <- the real measurement
    decoy at ~/.local/bin/claude  DELETED        FAILS      <- now catches a broken fence

So the fix is not to touch the arms. It is to give the runner something the
resolvers WOULD return if the fence failed. This script installs that and then
checks both halves, because a setup step that silently does nothing is the same
class of defect as the arms it exists to repair.

WHY `~/.local/bin/claude` AND NOT A PATH ENTRY. All three resolvers fall back to
`~/.local/bin/claude` after their PATH lookup -- `spawn.resolve_claude_bin`
appends it to the directories it walks, and `dashboard.launch._which_claude` and
`rail.turns.find_claude` both probe it by name. One file there is therefore
reachable by all three with no PATH edit at all. It also keeps
`test_shutil_which_alone_would_not_have_been_enough` honest, because that arm
sets `PATH=/nonexistent` and needs the resolver to still find one.

WHY A BARE NAME WITH NO EXTENSION, ON WINDOWS TOO. `_candidate_names()` returns
the PATHEXT spellings first and then plain `claude` as a last resort, so the
bare file is matched on every platform. Verified against the real
`_candidate_names` with `sys.platform` set to each of linux, darwin and win32.

WHY A SHEBANG AND THE EXECUTE BIT. `spawn._is_execable` sniffs the first bytes
and accepts an ELF image or a shebang on POSIX, and returns True unconditionally
on Windows, so a `/bin/sh` stub satisfies every platform. `os.access(X_OK)` needs
the mode bit on POSIX; on Windows it is True for any readable file.

THE DECOY REFUSES TO RUN. If something ever does exec it, exit 97 and a message
on stderr is far easier to trace than a real agent starting up. It is a decoy for
a resolver, never a runtime.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MARKER = "cadre-ci-decoy-claude-issue-100"
DECOY = (
    "#!/bin/sh\n"
    f"# {MARKER}\n"
    "echo 'cadre CI decoy claude: a resolver reached me, which means the "
    "suite fence did not hold' >&2\n"
    "exit 97\n"
)

# A path that must not exist, to stand in for the suite's own sentinel.
ABSENT = str(Path(__file__).resolve().parent / "_no_claude_here_and_never_created")

PROBE = (
    "from firm.pulse.spawn import resolve_claude_bin\n"
    "from firm.dashboard.launch import _which_claude\n"
    "from firm.rail.turns import find_claude\n"
    "print(resolve_claude_bin()[0])\n"
    "print(_which_claude())\n"
    "print(find_claude())\n"
)


def child(**overrides) -> list[str | None]:
    """What a CHILD interpreter resolves. A child, because that is the boundary
    the #81 breach crossed and the one an in-process patch cannot reach."""
    # PYTHONPATH is deliberately NOT defaulted here. The caller sets it, the
    # same way the suite legs do. Defaulting it to "src" would quietly defeat
    # the clean-install job, whose entire purpose is to resolve `firm` out of
    # site-packages rather than out of this checkout.
    env = dict(os.environ)
    for k, v in overrides.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    out = subprocess.run([sys.executable, "-c", PROBE], capture_output=True,
                         text=True, timeout=120, env=env)
    if out.returncode != 0:
        sys.exit(f"probe child failed: {out.stderr[-800:]}")
    lines = out.stdout.strip().splitlines()
    if len(lines) != 3:
        sys.exit(f"probe child printed {lines!r}, expected three lines")
    return [None if line == "None" else line for line in lines]


def main() -> int:
    target = Path.home() / ".local" / "bin" / "claude"

    # Never clobber a real one. This script is CI setup; on a developer box the
    # file it wants to write is the operator's actual claude, and overwriting
    # that would be a far worse bug than the one being fixed.
    if target.exists() and MARKER not in target.read_text(
            encoding="utf-8", errors="replace"):
        sys.exit(f"{target} already exists and is not this decoy -- refusing to "
                 "overwrite it. This script is for CI runners only.")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(DECOY, encoding="utf-8", newline="\n")
    target.chmod(0o755)
    print(f"installed decoy: {target}")

    # HALF ONE: with no fence, every resolver must find exactly this file. If it
    # does not, the two dynamic arms stay vacuous and this step has quietly
    # bought nothing -- which is precisely the failure #100 is about.
    unfenced = child(CADRE_CLAUDE_BIN=None, PATH=os.defpath)
    expected = str(target)
    wrong = [v for v in unfenced if v != expected]
    if wrong:
        sys.exit(
            "the decoy is installed but the resolvers do not return it with the "
            f"fence lifted, so the suite's dynamic arms would still measure "
            f"nothing.\n  expected all three: {expected}\n  got: {unfenced}")
    print(f"unfenced, all three resolvers return the decoy: {expected}")

    # HALF TWO: with a fence pointed at an absent path, every resolver must
    # still refuse. Installing a findable claude must make the arms REAL, not
    # make them fail -- and this is the assertion those arms themselves make.
    assert not Path(ABSENT).exists(), f"{ABSENT} exists; pick another"
    fenced = child(CADRE_CLAUDE_BIN=ABSENT, PATH=os.defpath)
    open_ones = [v for v in fenced if v is not None]
    if open_ones:
        sys.exit(
            "a resolver returned a binary while CADRE_CLAUDE_BIN pointed at an "
            f"absent path, so the fence does not hold on this runner: {fenced}")
    print("fenced, all three resolvers refuse: the arms now measure the fence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
