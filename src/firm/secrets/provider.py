"""Secrets provider resolution — base-backed when available, self-contained
otherwise.

Cadre is independent of BASE; not every operator has it. The contract is
identical either way — two tiers, firm overrides global:

* ``LocalVaultProvider`` — cadre's own encrypted vaults
  (``~/.cadre/vault.enc`` global, ``<firm>/.firm/vault.enc`` firm).
* ``BaseVaultProvider`` — delegates to ``base env`` when the installed
  base binary ships that subsystem (``~/.base-gbl`` global,
  ``<firm>/.base`` firm). Detection is capability-based, not
  presence-based: a base without ``base env`` falls through to local.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

import firm.secrets.vault as vault_mod

GLOBAL_TIER = "global"
FIRM_TIER = "firm"

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_key(key: str) -> str:
    key = (key or "").strip()
    if not _KEY_RE.fullmatch(key):
        raise ValueError(
            f"invalid variable name {key!r} — letters, digits, underscore; "
            "must not start with a digit"
        )
    return key


@dataclasses.dataclass
class VarEntry:
    key: str
    value: str
    tier: str          # "global" | "firm"
    overridden: bool = False   # a global entry shadowed by a firm entry


class SecretsProvider(Protocol):
    name: str

    def resolve(self, workspace: Path) -> dict[str, str]:
        """Merged env for the firm — global layered under firm."""
        ...

    def entries(self, workspace: Path) -> list[VarEntry]:
        """Every entry across both tiers, with shadowing marked."""
        ...

    def set(self, workspace: Path, key: str, value: str, tier: str) -> None: ...

    def unset(self, workspace: Path, key: str, tier: str) -> None: ...


def _merge_entries(
    global_vars: dict[str, str], firm_vars: dict[str, str],
) -> list[VarEntry]:
    entries = [
        VarEntry(key=k, value=v, tier=GLOBAL_TIER, overridden=k in firm_vars)
        for k, v in sorted(global_vars.items())
    ]
    entries += [
        VarEntry(key=k, value=v, tier=FIRM_TIER)
        for k, v in sorted(firm_vars.items())
    ]
    return entries


class LocalVaultProvider:
    """Self-contained encrypted vaults — no external tooling required."""

    name = "local"

    def global_vault_path(self) -> Path:
        return vault_mod.cadre_home() / "vault.enc"

    def firm_vault_path(self, workspace: Path) -> Path:
        return workspace / ".firm" / "vault.enc"

    def _tier_path(self, workspace: Path, tier: str) -> Path:
        if tier == GLOBAL_TIER:
            return self.global_vault_path()
        if tier == FIRM_TIER:
            return self.firm_vault_path(workspace)
        raise ValueError(f"unknown tier {tier!r} — use 'global' or 'firm'")

    def resolve(self, workspace: Path) -> dict[str, str]:
        merged = dict(vault_mod.read_vault(self.global_vault_path()))
        merged.update(vault_mod.read_vault(self.firm_vault_path(workspace)))
        return merged

    def entries(self, workspace: Path) -> list[VarEntry]:
        return _merge_entries(
            vault_mod.read_vault(self.global_vault_path()),
            vault_mod.read_vault(self.firm_vault_path(workspace)),
        )

    def set(self, workspace: Path, key: str, value: str, tier: str) -> None:
        key = validate_key(key)
        path = self._tier_path(workspace, tier)
        data = vault_mod.read_vault(path)
        data[key] = value
        vault_mod.write_vault(path, data)

    def unset(self, workspace: Path, key: str, tier: str) -> None:
        key = validate_key(key)
        path = self._tier_path(workspace, tier)
        data = vault_mod.read_vault(path)
        if key not in data:
            raise ValueError(f"{key} is not set at the {tier} tier")
        del data[key]
        vault_mod.write_vault(path, data)


class BaseVaultProvider:
    """Delegate to the BASE CLI's env subsystem.

    Expected command surface (lands with base's vault work; see
    00-kit-base): ``base env list/set/unset/resolve`` with ``--json`` and
    workspace scoping via cwd. Every call is defensive — any failure
    downgrades to LocalVaultProvider at resolution time, never mid-flight.
    """

    name = "base"

    @staticmethod
    def capable() -> bool:
        # PATH first, then base's canonical install home — a systemd-spawned
        # hub has a minimal PATH (same fallback as sysconfig.service.which_base;
        # inlined because sysconfig imports this module).
        binary = shutil.which("base") or (
            str(p) if (p := Path.home() / ".local" / "bin" / "base").exists() else None)
        if not binary:
            return False
        try:
            probe = subprocess.run(
                [binary, "env", "--help"],
                capture_output=True, text=True, timeout=10,
                env=os.environ.copy(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        # base routes unknown subcommands to plugin dispatch and still exits 0
        # ("base: unknown command 'env'") — capability means the subcommand
        # actually exists, so the marker check is the real test.
        blob = (probe.stdout + probe.stderr).lower()
        return probe.returncode == 0 and "unknown command" not in blob

    def _run(self, workspace: Path, *args: str) -> str:
        proc = subprocess.run(
            ["base", "env", *args],
            capture_output=True, text=True, timeout=30, cwd=str(workspace),
            env=os.environ.copy(),
        )
        if proc.returncode != 0:
            raise ValueError(
                f"base env {' '.join(args[:2])} failed: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc.stdout

    def resolve(self, workspace: Path) -> dict[str, str]:
        out = self._run(workspace, "resolve", "--json")
        data = json.loads(out)
        return {str(k): str(v) for k, v in data.items()}

    def entries(self, workspace: Path) -> list[VarEntry]:
        out = self._run(workspace, "list", "--json", "--show")
        payload = json.loads(out)
        tiers = payload if isinstance(payload, dict) else {}
        return _merge_entries(
            {str(k): str(v) for k, v in (tiers.get("global") or {}).items()},
            {str(k): str(v) for k, v in (tiers.get("workspace") or {}).items()},
        )

    def set(self, workspace: Path, key: str, value: str, tier: str) -> None:
        key = validate_key(key)
        scope = ["--global"] if tier == GLOBAL_TIER else []
        self._run(workspace, "set", key, value, *scope)

    def unset(self, workspace: Path, key: str, tier: str) -> None:
        key = validate_key(key)
        scope = ["--global"] if tier == GLOBAL_TIER else []
        self._run(workspace, "unset", key, *scope)


_BASE_CAPABLE: bool | None = None   # memo — one probe per process, not per spawn


def resolve_provider() -> SecretsProvider:
    """Base when its env subsystem exists, cadre's own vault otherwise.

    ``CADRE_SECRETS_PROVIDER=local|base`` pins the choice (tests, debugging,
    or an operator who wants to force one store).
    """
    forced = (os.environ.get("CADRE_SECRETS_PROVIDER") or "").strip().lower()
    if forced == "local":
        return LocalVaultProvider()
    if forced == "base":
        return BaseVaultProvider()
    global _BASE_CAPABLE
    if _BASE_CAPABLE is None:
        _BASE_CAPABLE = BaseVaultProvider.capable()
    return BaseVaultProvider() if _BASE_CAPABLE else LocalVaultProvider()
