"""Tests for firm.secrets — vault primitives and provider tiering."""

from __future__ import annotations

import stat

import pytest

import firm.secrets.vault as vault_mod
from firm.secrets.provider import (
    FIRM_TIER,
    GLOBAL_TIER,
    BaseVaultProvider,
    LocalVaultProvider,
    validate_key,
)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Isolated CADRE_HOME + firm workspace."""
    cadre_home = tmp_path / "cadre-home"
    monkeypatch.setenv("CADRE_HOME", str(cadre_home))
    workspace = tmp_path / "firmws"
    (workspace / ".firm").mkdir(parents=True)
    return workspace


def test_master_key_created_0600(home):
    key = vault_mod.ensure_master_key()
    assert key
    path = vault_mod.master_key_path()
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # Stable across calls.
    assert vault_mod.ensure_master_key() == key


def test_vault_roundtrip_and_encryption_at_rest(home):
    path = vault_mod.cadre_home() / "vault.enc"
    vault_mod.write_vault(path, {"SLACK_TOKEN": "xoxb-secret-123456"})
    raw = path.read_bytes()
    assert b"xoxb-secret" not in raw          # never plaintext on disk
    assert vault_mod.read_vault(path) == {"SLACK_TOKEN": "xoxb-secret-123456"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_missing_vault_is_empty(home):
    assert vault_mod.read_vault(vault_mod.cadre_home() / "nope.enc") == {}


def test_wrong_key_fails_loud(home, tmp_path, monkeypatch):
    path = vault_mod.cadre_home() / "vault.enc"
    vault_mod.write_vault(path, {"K": "v"})
    # Rotate the master key out from under the vault.
    monkeypatch.setenv("CADRE_HOME", str(tmp_path / "other-home"))
    # Vault path is still the old one; key is new — must raise, not return {}.
    with pytest.raises(vault_mod.VaultError):
        vault_mod.read_vault(path)


def test_tier_merge_firm_overrides_global(home):
    p = LocalVaultProvider()
    p.set(home, "SHARED", "global-val", GLOBAL_TIER)
    p.set(home, "SHARED", "firm-val", FIRM_TIER)
    p.set(home, "ONLY_GLOBAL", "g", GLOBAL_TIER)
    p.set(home, "ONLY_FIRM", "f", FIRM_TIER)

    merged = p.resolve(home)
    assert merged == {"SHARED": "firm-val", "ONLY_GLOBAL": "g", "ONLY_FIRM": "f"}

    entries = p.entries(home)
    shared_global = next(e for e in entries if e.key == "SHARED" and e.tier == GLOBAL_TIER)
    assert shared_global.overridden is True
    only_global = next(e for e in entries if e.key == "ONLY_GLOBAL")
    assert only_global.overridden is False


def test_unset_and_missing_key(home):
    p = LocalVaultProvider()
    p.set(home, "K", "v", FIRM_TIER)
    p.unset(home, "K", FIRM_TIER)
    assert p.resolve(home) == {}
    with pytest.raises(ValueError):
        p.unset(home, "K", FIRM_TIER)


def test_validate_key_rejects_garbage():
    for bad in ("", "1LEADING", "has space", "dash-key", "a=b", "x;rm"):
        with pytest.raises(ValueError):
            validate_key(bad)
    assert validate_key("  GOOD_KEY2 ") == "GOOD_KEY2"


def test_firm_vault_lives_in_firm_dir(home):
    p = LocalVaultProvider()
    p.set(home, "K", "v", FIRM_TIER)
    assert (home / ".firm" / "vault.enc").exists()


def test_base_capability_rejects_unknown_command_exit_zero(monkeypatch):
    """base routes unknown subcommands to plugin dispatch with rc=0 —
    capability detection must read the marker, not the exit code."""
    class FakeProc:
        returncode = 0
        stdout = "base: unknown command 'env'\n"
        stderr = ""

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/base")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeProc())
    assert BaseVaultProvider.capable() is False

    class RealProc(FakeProc):
        stdout = "Manage environment vaults\n\nUsage: base env <COMMAND>"

    monkeypatch.setattr("subprocess.run", lambda *a, **k: RealProc())
    assert BaseVaultProvider.capable() is True
