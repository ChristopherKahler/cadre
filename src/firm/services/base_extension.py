"""Install Cadre's base extension manifest, and refuse to install a bad one.

The manifest source of truth ships in the repo at ``firm/base_ext/cadre.toml``
so ``pip install cadre`` carries it. Only one value in it is machine-specific —
``framework_dir`` — so installing is: render that one placeholder, write the
result to a temporary file, ask base to validate it, and only then ask base to
install it. Validate before install, every time, with no path that skips it.

Two things this module refuses to do, both because they have already gone wrong
once on this machine:

*It never installs a manifest it did not validate.* ``base extension install``
does not re-check anything the author got wrong, and a manifest can be accepted
while carrying a key that does nothing at all.

*It never claims an install it did not read back.* ``base extension install``
exiting 0 is the writer's own opinion of its own work. The installed file is
read from disk afterwards and its name and version compared, which is the only
thing that can say the manifest actually landed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

PLACEHOLDER = "{{framework_dir}}"


def manifest_source() -> Path:
    """The shipped manifest, inside the installed package."""
    return Path(__file__).resolve().parent.parent / "base_ext" / "cadre.toml"


def framework_root() -> Path:
    """The repo or install root the handler is resolved against.

    ``bin/cadre`` sits beside ``src/``, so the root is two levels above the
    ``firm`` package in a source checkout and the package parent otherwise.
    """
    firm_pkg = Path(__file__).resolve().parent.parent
    candidate = firm_pkg.parent.parent          # <root>/src/firm -> <root>
    if (candidate / "bin" / "cadre").exists():
        return candidate
    return firm_pkg.parent


def render(framework_dir: Path | str | None = None) -> str:
    """The manifest with its one machine-specific value filled in."""
    root = Path(framework_dir) if framework_dir else framework_root()
    text = manifest_source().read_text(encoding="utf-8")
    if PLACEHOLDER not in text:
        raise ValueError(
            f"{manifest_source()} carries no {PLACEHOLDER}; refusing to install a "
            "manifest whose framework_dir was hard-coded for another machine")
    return text.replace(PLACEHOLDER, root.as_posix())


def _base_env() -> dict[str, str]:
    env = {"HOME": str(Path.home()),
           "PATH": os.environ.get("PATH") or "/usr/bin:/bin"}
    for passthrough in ("BASE_HOME", "XDG_CONFIG_HOME"):
        value = os.environ.get(passthrough)
        if value:
            env[passthrough] = value
    return env


def _installed_path() -> Path:
    home = os.environ.get("BASE_HOME")
    root = Path(home) if home else Path.home()
    return root / ".base-gbl" / "extensions" / "cadre.toml"


def install(framework_dir: Path | str | None = None) -> dict[str, Any]:
    """Render, validate, install, read back. Never raises.

    ``ok`` False with ``reason`` "base is not installed" is not an error — a
    licensee may not carry base, and a firm without it is degraded, never
    broken. That is the same contract ``base_domain.sync`` keeps.
    """
    from firm.sysconfig.service import which_base

    result: dict[str, Any] = {"ok": False, "validated": False, "installed": False,
                              "read_back": False, "path": "", "reason": ""}
    base = which_base()
    if not base:
        result["reason"] = "base is not installed — the extension is skipped, not failed"
        return result

    try:
        rendered = render(framework_dir)
    except (OSError, ValueError) as exc:
        result["reason"] = str(exc)
        return result

    tmpdir = tempfile.mkdtemp(prefix="cadre-ext-")
    staged = Path(tmpdir) / "cadre.toml"
    try:
        staged.write_text(rendered, encoding="utf-8")
        env = _base_env()

        checked = subprocess.run(
            [base, "extension", "validate", str(staged)],
            capture_output=True, text=True, timeout=60,
            env=env, stdin=subprocess.DEVNULL)
        if checked.returncode != 0:
            result["reason"] = ("the manifest did not validate, so nothing was "
                                f"installed: {(checked.stderr or checked.stdout).strip()[:300]}")
            return result
        result["validated"] = True

        placed = subprocess.run(
            [base, "extension", "install", str(staged)],
            capture_output=True, text=True, timeout=60,
            env=env, stdin=subprocess.DEVNULL)
        if placed.returncode != 0:
            result["reason"] = ("validated but the install failed: "
                                f"{(placed.stderr or placed.stdout).strip()[:300]}")
            return result
        result["installed"] = True

        landed = _installed_path()
        result["path"] = str(landed)
        if not landed.exists():
            result["reason"] = (f"base reported success but {landed} does not exist")
            return result
        on_disk = landed.read_text(encoding="utf-8")
        if 'name = "cadre"' not in on_disk:
            result["reason"] = f"{landed} exists but is not Cadre's manifest"
            return result
        if PLACEHOLDER in on_disk:
            result["reason"] = (f"{landed} still carries {PLACEHOLDER} — the "
                                "framework directory never got filled in")
            return result
        result["read_back"] = True
        result["ok"] = True
        result["reason"] = f"installed and read back from {landed}"
        return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"the install did not run: {exc}"
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
