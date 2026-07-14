"""Suite-wide hermeticity.

Secrets resolution must never depend on the machine running the tests:
no probing the operator's installed ``base``, no touching the real
``~/.cadre``. Individual tests override CADRE_HOME with their own tmp dir.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _hermetic_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("CADRE_SECRETS_PROVIDER", "local")
    monkeypatch.setenv("CADRE_HOME", str(tmp_path / "cadre-home-default"))
