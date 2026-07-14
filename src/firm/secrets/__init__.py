"""Firm secrets — encrypted variable vault (Railway-style, two tiers).

Values live encrypted at rest and are injected into firm processes at
spawn time. The Firm Secret entity (``KEY-*``) stays reference-only
metadata; this package is the value store it points at.
"""

from firm.secrets.vault import VaultError, crypto_available
from firm.secrets.provider import (
    FIRM_TIER,
    GLOBAL_TIER,
    LocalVaultProvider,
    SecretsProvider,
    VarEntry,
    resolve_provider,
)

__all__ = [
    "FIRM_TIER",
    "GLOBAL_TIER",
    "LocalVaultProvider",
    "SecretsProvider",
    "VarEntry",
    "VaultError",
    "crypto_available",
    "resolve_provider",
]
