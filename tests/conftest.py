"""Suite-wide hermeticity.

Secrets resolution must never depend on the machine running the tests:
no probing the operator's installed ``base``, no touching the real
``~/.cadre``. Individual tests override CADRE_HOME with their own tmp dir.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# THE TREE UNDER TEST MUST BE THIS ONE. Checked once, at collection, because a
# number measured against another checkout is worse than no number at all.
#
# The shared development venv carries an editable install whose `.pth` names ONE
# checkout. Nothing about running pytest from a git worktree overrides that, and
# `tests/` has an `__init__.py`, so pytest puts the worktree ROOT on `sys.path`
# and the root holds no `firm` package. The `.pth` therefore wins and every
# `import firm` resolves to the OTHER checkout, while tests that read files by
# path -- or spawn a child with PYTHONPATH set -- keep reading this one. One run,
# two trees under test.
#
# Measured 2026-09-12: bare `python -m pytest` in a worktree imported
# `firm` from the main clone; the same file with PYTHONPATH pinned imported it
# from the worktree, and the two runs disagreed about whether the claude fence
# held. The fix is to pin PYTHONPATH to this repo's `src`. This block only makes
# forgetting it loud.
#
# AN INSTALLED PACKAGE IS NOT THE HIJACK AND MUST NOT TRIP THIS. The
# `clean-install` CI job runs the suite against a wheel in a throwaway venv with
# no PYTHONPATH on purpose -- that job exists to catch a dependency that installs
# but cannot import, and requiring `firm` to come from this repo would defeat it.
# So the refusal is narrow: another checkout's `src/` tree, never site-packages.
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]


def _foreign_source_tree(used: Path, repo: Path) -> bool:
    """True only when `firm` came from a DIFFERENT checkout's source tree."""
    if repo in used.parents:
        return False
    return used.parents[1].name == "src"


def _assert_the_tree_under_test_is_this_one() -> None:
    spec = importlib.util.find_spec("firm")
    if spec is None or not spec.origin:
        return  # nothing imported it yet; the tests that need it will say so
    used = Path(spec.origin).resolve()
    if _foreign_source_tree(used, _REPO):
        raise RuntimeError(
            "REFUSING TO RUN: `firm` resolves to another checkout.\n"
            f"  imported from : {used}\n"
            f"  tests live in : {_REPO}\n"
            "Every import-based assertion in this run would grade that tree "
            "instead of this one, and file-based assertions would still read "
            "this one -- one run, two trees, and the numbers cannot be "
            "reconciled afterwards.\n"
            f"Fix: run with PYTHONPATH={_REPO / 'src'}")


_assert_the_tree_under_test_is_this_one()


@pytest.fixture(autouse=True)
def _base_home_is_never_the_operators(tmp_path, monkeypatch):
    """Point every `base` a CHILD PROCESS runs at a throwaway global tier.

    The stub below is an in-process monkeypatch, so it does not exist in a
    child interpreter. Several test files run the CLI for real --
    ``sys.executable -m firm`` -- and those children resolve the host's actual
    base binary and write the operator's machine with it. Measured 2026-09-10:
    ``test_doctor_survives_a_pipe`` runs ``cadre init --demo`` as a subprocess
    and put its pytest temp directory into the operator's global workspace
    registry, twice, after the in-process stub was already in place.

    BASE_HOME is inherited, so it reaches the child. It also ARMS base's own
    write tripwire (isolation_active() is true whenever BASE_HOME is set, in
    the shipped release as well as in debug), which is the point rather than a
    side effect: a child that resolves the real home now panics rc 101 instead
    of quietly appending to the operator's tier. Silent contamination becomes a
    loud crash.

    HOME is deliberately LEFT ALONE. Setting HOME equal to BASE_HOME makes the
    real tier and the throwaway tier indistinguishable to that tripwire, and
    base then dies rc 101 while blaming path resolution -- a false diagnosis
    that costs an afternoon. tmp_path sits under the temp directory on every
    platform this suite runs on, which is what keeps both panic branches happy.
    """
    monkeypatch.setenv("BASE_HOME", str(tmp_path / "base-home"))
    monkeypatch.setenv("CADRE_NO_BASE", "1")


@pytest.fixture(autouse=True)
def _no_ambient_base(monkeypatch):
    """No test reaches the machine's real ``base`` binary unless it asks to.

    ``run_init`` scaffolds the firm's BASE tier, so it shells out to whatever
    ``base`` the host has. On a developer machine that carries one, the suite
    created real ``.base/`` tiers inside pytest temp directories AND registered
    every one of them in the operator's global workspace registry. Measured
    2026-09-10: one run of tests/test_init.py put four entries into
    ~/.base-gbl/base.toml pointing at pytest temp paths that no longer exist.

    Worse, on WSL ``which_base()`` resolved to the WINDOWS binary across /mnt/c,
    so the writes landed in the Windows global tier while the Linux one sat
    untouched -- an isolation check that reads only one tier reports clean while
    the other fills up.

    "base is absent" is a first-class supported state throughout this codebase
    (degraded, never broken), so defaulting every test to it is honest as well
    as hermetic. A test that wants base stubs ``which_base`` itself, which is
    what tests/cli/test_base_wire_reporting.py and
    tests/services/test_base_extension.py already do; an autouse fixture runs
    first, so their own monkeypatch still wins.
    """
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)


@pytest.fixture(autouse=True)
def _hermetic_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("CADRE_SECRETS_PROVIDER", "local")
    monkeypatch.setenv("CADRE_HOME", str(tmp_path / "cadre-home-default"))


@pytest.fixture(autouse=True)
def _no_test_can_spawn_a_claude_agent(tmp_path, monkeypatch):
    """No test boots a real Claude agent -- in this process OR in a child.

    THE DEFECT (issue #81). The suite spawned a real ``claude`` carrying
    ``--dangerously-skip-permissions``, parented by pytest, running out of a
    pytest temp directory. It took a relay title and answered a ping addressed
    to another session. It also spends the operator's tokens on every run of
    this suite.

    WHY AN ENV VAR AND NOT A ``PATH`` SCRUB, and this is the half that is easy
    to get wrong. Measured at a489230:

        PATH=/nonexistent python -c "...resolve_claude_bin()"
        -> ('/home/<user>/.local/bin/claude', 'PATH resolution: ...')

    ``resolve_claude_bin`` appends ``~/.local/bin`` UNCONDITIONALLY, *after* the
    PATH walk (spawn.py). So a fixture that sanitises PATH looks exactly like a
    fence and is not one, and it fails SILENTLY -- which is the failure mode
    this whole issue is about. Pointing ``CADRE_CLAUDE_BIN`` at a path the
    resolvers refuse short-circuits them BEFORE the PATH walk and before that
    append, and #81 made ``dashboard.launch._which_claude`` honour the same
    variable so the resolvers stop at the same gate.

    THE SENTINEL IS A PATH THAT DOES NOT EXIST, AND THAT IS MEASURED. It used
    to be a real file with mode 0o644, which fences Linux and DOES NOT FENCE
    WINDOWS::

        linux    os.access(<plain file>, os.X_OK)  ->  False
        windows  os.access(<plain file>, os.X_OK)  ->  True

    Windows has no execute bit, so ``os.access(X_OK)`` is True for any readable
    file and every resolver handed the sentinel back as a real claude binary.
    Windows CI caught it: 3 failed, 1862 passed, all three in the fence's own
    file. A directory is no better -- ``X_OK`` is True for a directory on BOTH
    platforms. An absent path is the only target where ``os.access(X_OK)``,
    ``Path.is_file()`` and ``shutil.which()`` all agree on both platforms, so it
    is the only one that fences by the same mechanism everywhere.

    AND IT IS AN ENV VAR BECAUSE ENV REACHES CHILDREN. The agent that was caught
    was a CHILD of pytest. ``_no_ambient_base`` above says it plainly: an
    in-process monkeypatch does not exist in a child interpreter. An env var is
    inherited, so this fence crosses the process boundary the breach crossed.

    NOT AN EXECUTABLE STUB, deliberately. ``_is_execable`` accepts anything with
    a shebang, so a "refusing" stub script would be RETURNED as the binary and
    then actually spawned -- louder than silence, but still a spawn. Unfindable
    beats loud.

    HOME IS NOT TOUCHED, for the reason ``_base_home_is_never_the_operators``
    gives above. That one already cost an afternoon.

    A test that WANTS a resolvable claude sets ``CADRE_CLAUDE_BIN`` itself or
    stubs the resolver, exactly as the base fence intends -- an autouse fixture
    runs first, so the test's own monkeypatch still wins.
    """
    unusable = tmp_path / "no-claude-here-and-never-created"
    assert not unusable.exists(), (
        f"{unusable} exists, so the fence would be pointing at a real file and "
        "on Windows every resolver would accept it -- see the docstring above")
    monkeypatch.setenv("CADRE_CLAUDE_BIN", str(unusable))


# NO IN-PROCESS STUB OF `_which_claude`, AND THAT IS MEASURED, NOT PREFERRED.
#
# A second layer that monkeypatched `firm.dashboard.launch._which_claude` to
# None was written, tried, and removed. It SHADOWS the function that
# tests/test_launch.py stubs and asserts on, so those tests stop exercising the
# real resolver and start exercising the fence. Bisected on tests/test_launch.py
# at a489230, one file, four conditions:
#
#     product fix only, no fixture     9 passed
#     this fixture only, no product fix    4 failed, 5 passed
#     both                             4 failed, 5 passed
#     pristine main                    9 passed
#
# The in-process stub alone accounted for all four failures and the product fix
# alone accounted for none. It was also redundant: since #81 made
# `_which_claude` honour CADRE_CLAUDE_BIN, the env var above closes BOTH
# resolvers, in this process and in every child -- which the in-process stub
# never could. A redundant lock that breaks the tests of the door it locks is
# not belt and braces, it is a second failure mode.
