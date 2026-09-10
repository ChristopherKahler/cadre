"""Suite-wide hermeticity.

Secrets resolution must never depend on the machine running the tests:
no probing the operator's installed ``base``, no touching the real
``~/.cadre``. Individual tests override CADRE_HOME with their own tmp dir.
"""

from __future__ import annotations

import pytest


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
