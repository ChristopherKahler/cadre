"""Suite-wide hermeticity.

Secrets resolution must never depend on the machine running the tests:
no probing the operator's installed ``base``, no touching the real
``~/.cadre``. Individual tests override CADRE_HOME with their own tmp dir.
"""

from __future__ import annotations

import pytest


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
