"""Encrypted vault file primitives.

A vault is one Fernet-encrypted JSON object (``{KEY: value}``) on disk.
The master key lives OUTSIDE every firm workspace (``~/.cadre/master.key``,
0600) so a firm folder — typically a git repo — can be committed, synced,
or shared without ever carrying a decryptable secret.

``cryptography`` is imported lazily: a cadre install that predates the
dependency keeps working everywhere except the vault surfaces, which
report an honest remediation instead of crashing the dashboard.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class VaultError(RuntimeError):
    """Vault could not be read or written — message carries the remediation."""


def crypto_available() -> bool:
    try:
        import cryptography  # noqa: F401
        return True
    except ImportError:
        return False


def _fernet(key: bytes):
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise VaultError(
            "the vault needs the 'cryptography' package — reinstall cadre "
            "(pip install -e . inside the firm venv) to pick up dependencies"
        )
    return Fernet(key)


def cadre_home() -> Path:
    """Operator-level cadre dir — ``$CADRE_HOME`` or ``~/.cadre``."""
    override = (os.environ.get("CADRE_HOME") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".cadre"


def master_key_path() -> Path:
    return cadre_home() / "master.key"


def ensure_master_key() -> bytes:
    """Return the master key, generating it on first use (dir 0700, key 0600)."""
    path = master_key_path()
    if path.exists():
        return path.read_bytes().strip()
    from cryptography.fernet import Fernet  # surface ImportError via _fernet path
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


def read_vault(path: Path) -> dict[str, str]:
    """Decrypt *path* into a dict. Absent vault = empty dict; a vault that
    exists but cannot be decrypted raises — silently dropping secrets a tool
    depends on is worse than failing loud."""
    if not path.exists():
        return {}
    try:
        key = ensure_master_key()
    except ImportError:
        raise VaultError(
            "vault exists but the 'cryptography' package is missing — "
            "reinstall cadre to read it"
        )
    token = path.read_bytes()
    try:
        raw = _fernet(key).decrypt(token)
        data = json.loads(raw)
    except VaultError:
        raise
    except Exception:
        raise VaultError(
            f"cannot decrypt {path} — the master key at {master_key_path()} "
            "does not match this vault (moved machines? restore the original "
            "master.key, or delete the vault and re-enter its variables)"
        )
    if not isinstance(data, dict):
        raise VaultError(f"{path} decrypted to a non-object payload")
    return {str(k): str(v) for k, v in data.items()}


def write_vault(path: Path, data: dict[str, str]) -> None:
    """Encrypt *data* to *path* atomically (tmp + replace, 0600)."""
    key = ensure_master_key()
    token = _fernet(key).encrypt(json.dumps(data, sort_keys=True).encode())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(token)
    os.replace(tmp, path)
