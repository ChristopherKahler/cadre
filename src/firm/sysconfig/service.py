"""System-config service layer — everything the /api/sysconfig routes do.

Same discipline as the entity services: all writes are audited (Records),
all file writes are backed up first, and secret VALUES never land in a
record, a log line, or an unmasked API listing.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

import firm.secrets.vault as vault_mod
from firm.secrets.provider import (
    FIRM_TIER,
    GLOBAL_TIER,
    resolve_provider,
    validate_key,
)
from firm.services._records import log_event
from firm.sysconfig.platforms import detect_platform

_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_BOARD = {"type": "board", "id": None}


def which_base() -> str | None:
    """Resolve the BASE binary — PATH first, then its canonical install home.

    A systemd-spawned hub carries a minimal PATH; ~/.local/bin (the installer's
    target) is not on it, and "BASE not detected" on a machine that has BASE
    is worse than no probe at all.
    """
    found = shutil.which("base")
    if found:
        return found
    cand = Path.home() / ".local" / "bin" / "base"
    return str(cand) if cand.exists() else None


# ---------------------------------------------------------------------------
# Describe
# ---------------------------------------------------------------------------

def describe(workspace: Path) -> dict[str, Any]:
    adapter = detect_platform(workspace)
    surfaces = []
    if adapter:
        for s in adapter.surfaces():
            target = workspace / s.path
            surfaces.append({
                "key": s.key, "label": s.label, "path": s.path,
                "kind": s.kind, "description": s.description,
                "exists": target.is_file(),
            })
    provider = resolve_provider()
    return {
        "platform": adapter.id if adapter else None,
        "platform_label": adapter.label if adapter else "Unknown platform",
        "surfaces": surfaces,
        "secrets_provider": provider.name,
        "crypto_available": vault_mod.crypto_available(),
        "base": {
            "present": which_base() is not None,
            "ext_capable": _base_ext_capable(),
            "workspace_graph": (workspace / ".base").is_dir(),
        },
        "env_file_keys": sorted(_parse_env_file(workspace / ".env")),
    }


# ---------------------------------------------------------------------------
# Config file surfaces
# ---------------------------------------------------------------------------

def _surface_path(workspace: Path, key: str) -> tuple[Any, Path]:
    adapter = detect_platform(workspace)
    if adapter is None:
        raise ValueError("no platform detected for this firm — no file surfaces")
    surface = adapter.surface(key)
    return surface, workspace / surface.path

def read_file(workspace: Path, key: str) -> dict[str, Any]:
    surface, target = _surface_path(workspace, key)
    content = ""
    if target.is_file():
        content = target.read_text(encoding="utf-8", errors="replace")
    return {
        "key": surface.key, "path": surface.path, "kind": surface.kind,
        "exists": target.is_file(), "content": content,
    }


def _backup(workspace: Path, target: Path) -> str | None:
    """Copy the current file into .firm/backups/sysconfig/ before a write."""
    if not target.is_file():
        return None
    backups = workspace / ".firm" / "backups" / "sysconfig"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = backups / f"{stamp}-{target.name}"
    shutil.copy2(target, dest)
    return str(dest.relative_to(workspace))


def write_file(
    conn: sqlite3.Connection,
    firm_id: str,
    workspace: Path,
    key: str,
    content: str,
) -> dict[str, Any]:
    surface, target = _surface_path(workspace, key)
    if surface.kind == "json":
        try:
            json.loads(content or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"not valid JSON: {exc}")
    backup = _backup(workspace, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)
    log_event(
        conn, firm_id=firm_id, event_type="sysconfig.file_updated",
        actor=_BOARD, target_ref={"type": "firm", "id": firm_id},
        details={"surface": surface.key, "path": surface.path,
                 "bytes": len(content.encode()), "backup": backup},
    )
    return {"key": surface.key, "path": surface.path, "backup": backup}


# ---------------------------------------------------------------------------
# MCP servers (.mcp.json structured editor)
# ---------------------------------------------------------------------------

def _read_mcp(workspace: Path) -> dict[str, Any]:
    path = workspace / ".mcp.json"
    if not path.is_file():
        return {"mcpServers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f".mcp.json is not parseable: {exc}")
    if not isinstance(data, dict):
        raise ValueError(".mcp.json root must be an object")
    data.setdefault("mcpServers", {})
    return data


def _write_mcp(
    conn: sqlite3.Connection, firm_id: str, workspace: Path,
    data: dict[str, Any], detail: dict[str, Any],
) -> None:
    target = workspace / ".mcp.json"
    backup = _backup(workspace, target)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    log_event(
        conn, firm_id=firm_id, event_type="sysconfig.mcp_updated",
        actor=_BOARD, target_ref={"type": "firm", "id": firm_id},
        details={**detail, "backup": backup},
    )


def mcp_list(workspace: Path) -> dict[str, Any]:
    """Server list for the structured editor. Env VALUES are masked —
    the raw file surface exists for operators who want the bytes."""
    data = _read_mcp(workspace)
    servers = []
    for name, spec in sorted(data.get("mcpServers", {}).items()):
        if not isinstance(spec, dict):
            continue
        env = spec.get("env")
        if not isinstance(env, dict):
            env = {}
        servers.append({
            "name": name,
            "transport": spec.get("type") or ("http" if spec.get("url") else "stdio"),
            "command": spec.get("command"),
            "args": spec.get("args") or [],
            "url": spec.get("url"),
            "env_keys": sorted(env),
        })
    return {"servers": servers}


def mcp_set(
    conn: sqlite3.Connection, firm_id: str, workspace: Path,
    name: str, spec: dict[str, Any],
) -> dict[str, Any]:
    name = (name or "").strip()
    if not _MCP_NAME_RE.fullmatch(name):
        raise ValueError(f"invalid server name {name!r}")
    if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
        raise ValueError("server spec needs a 'command' (stdio) or 'url' (http/sse)")
    data = _read_mcp(workspace)
    existed = name in data["mcpServers"]
    data["mcpServers"][name] = spec
    _write_mcp(conn, firm_id, workspace, data,
               {"op": "update" if existed else "add", "server": name})
    return {"server": name, "op": "update" if existed else "add"}


def mcp_remove(
    conn: sqlite3.Connection, firm_id: str, workspace: Path, name: str,
) -> dict[str, Any]:
    data = _read_mcp(workspace)
    if name not in data["mcpServers"]:
        raise ValueError(f"no server named {name!r} in .mcp.json")
    del data["mcpServers"][name]
    _write_mcp(conn, firm_id, workspace, data, {"op": "remove", "server": name})
    return {"server": name, "op": "remove"}


# ---------------------------------------------------------------------------
# Variables (encrypted vault)
# ---------------------------------------------------------------------------

def _mask(value: str) -> str:
    return "••••" + value[-4:] if len(value) >= 10 else "••••••"


def vars_list(workspace: Path) -> dict[str, Any]:
    provider = resolve_provider()
    entries = [
        {"key": e.key, "tier": e.tier, "masked": _mask(e.value),
         "overridden": e.overridden}
        for e in provider.entries(workspace)
    ]
    return {"provider": provider.name, "vars": entries}


def vars_set(
    conn: sqlite3.Connection, firm_id: str, workspace: Path,
    key: str, value: str, tier: str,
) -> dict[str, Any]:
    key = validate_key(key)
    if tier not in (GLOBAL_TIER, FIRM_TIER):
        raise ValueError(f"unknown tier {tier!r}")
    if not isinstance(value, str) or not value:
        raise ValueError("value required")
    resolve_provider().set(workspace, key, value, tier)
    log_event(
        conn, firm_id=firm_id, event_type="sysconfig.var_set",
        actor=_BOARD, target_ref={"type": "firm", "id": firm_id},
        details={"key": key, "tier": tier},   # never the value
    )
    return {"key": key, "tier": tier}


def vars_delete(
    conn: sqlite3.Connection, firm_id: str, workspace: Path,
    key: str, tier: str,
) -> dict[str, Any]:
    resolve_provider().unset(workspace, validate_key(key), tier)
    log_event(
        conn, firm_id=firm_id, event_type="sysconfig.var_unset",
        actor=_BOARD, target_ref={"type": "firm", "id": firm_id},
        details={"key": key, "tier": tier},
    )
    return {"key": key, "tier": tier}


def vars_reveal(workspace: Path, key: str) -> dict[str, Any]:
    """Plaintext value for the operator's eye toggle — local dashboard only;
    deliberately NOT recorded (the operator inspecting their own vault is
    not a firm event)."""
    key = validate_key(key)
    merged = resolve_provider().resolve(workspace)
    if key not in merged:
        raise ValueError(f"{key} is not set")
    return {"key": key, "value": merged[key]}


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k = k.strip()
                if _looks_like_key(k):
                    out[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _looks_like_key(k: str) -> bool:
    try:
        validate_key(k)
        return True
    except ValueError:
        return False


def vars_import(
    conn: sqlite3.Connection, firm_id: str, workspace: Path,
    scrub: bool = False,
) -> dict[str, Any]:
    """Import the firm's plaintext .env into the firm-tier vault.

    Verified before scrub: every imported key is read back from the vault;
    only then (and only with *scrub*) is the plaintext file deleted.
    """
    env_path = workspace / ".env"
    pairs = _parse_env_file(env_path)
    if not pairs:
        raise ValueError("no importable KEY=VALUE lines in .env")
    provider = resolve_provider()
    for k, v in pairs.items():
        provider.set(workspace, k, v, FIRM_TIER)
    merged = provider.resolve(workspace)
    missing = [k for k, v in pairs.items() if merged.get(k) != v]
    if missing:
        raise ValueError(f"vault verification failed for: {', '.join(missing)}")
    scrubbed = False
    if scrub:
        env_path.unlink()
        scrubbed = True
    log_event(
        conn, firm_id=firm_id, event_type="sysconfig.env_imported",
        actor=_BOARD, target_ref={"type": "firm", "id": firm_id},
        details={"keys": sorted(pairs), "count": len(pairs), "scrubbed": scrubbed},
    )
    return {"imported": sorted(pairs), "count": len(pairs), "scrubbed": scrubbed}


# ---------------------------------------------------------------------------
# Inventory + tool install (base ext)
# ---------------------------------------------------------------------------

def _base_ext_capable() -> bool:
    binary = which_base()
    if not binary:
        return False
    try:
        probe = subprocess.run(
            [binary, "ext", "--help"], capture_output=True, text=True,
            timeout=10, env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    blob = (probe.stdout + probe.stderr).lower()
    return probe.returncode == 0 and "unknown command" not in blob


def _base_ext_list() -> list[dict[str, str]]:
    """Parse `base ext list` extension rows — defensive; layout drift
    degrades to fewer rows, never an exception."""
    try:
        proc = subprocess.run(
            ["base", "ext", "list"], capture_output=True, text=True,
            timeout=15, env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    tools: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        if not line.strip() or set(line.strip()) <= {"─", "-"}:
            continue
        if re.match(r"^(NAME|PLUGIN COMMAND)\b", line):
            continue
        if re.match(r"^\d+ (extension|plugin)", line.strip()):
            continue
        if line.startswith("base "):        # plugin-command table rows
            continue
        cols = re.split(r"\s{2,}", line.strip())
        if len(cols) >= 2 and re.fullmatch(r"[a-z0-9][a-z0-9._-]*", cols[0]):
            tools.append({"name": cols[0], "version": cols[1],
                          "description": cols[-1] if len(cols) > 2 else ""})
    return tools


def inventory(workspace: Path) -> dict[str, Any]:
    adapter = detect_platform(workspace)
    inv: dict[str, Any] = {"skills": [], "commands": []}
    if adapter is not None:
        inv = adapter.inventory(workspace)
    inv["tools"] = _base_ext_list() if _base_ext_capable() else []
    inv["tools_source"] = "base ext" if _base_ext_capable() else None
    return inv


_FS_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "target",
                 ".cache", ".npm", ".cargo", "dist", "build"}


def fs_browse(
    path: str | None, q: str | None = None, root: Path | None = None,
) -> dict[str, Any]:
    """Directory listing / manifest search for the tool-install browser.

    Read-only, names only, jailed to *root* (the operator's home) — the
    dashboard may be reachable on the LAN, so no peeking above the jail.
    Without *q*: one directory's subdirs + ``.toml`` files. With *q*:
    bounded recursive filename search for matching ``.toml`` under *path*.
    """
    root = (root or Path.home()).resolve()
    current = Path(path).expanduser().resolve() if path else root
    if not (current == root or current.is_relative_to(root)):
        raise ValueError("path is outside the browsable area")
    if not current.is_dir():
        raise ValueError(f"not a directory: {current}")

    q = (q or "").strip().lower()
    dirs: list[str] = []
    files: list[dict[str, str]] = []
    if q:
        visited = 0
        for base_dir, subdirs, names in os.walk(current):
            visited += 1
            if visited > 4000 or len(files) >= 100:
                break
            depth = Path(base_dir).relative_to(current).parts
            if len(depth) >= 6:
                subdirs[:] = []
                continue
            subdirs[:] = [d for d in subdirs if d not in _FS_SKIP_DIRS]
            for n in names:
                if n.endswith(".toml") and q in n.lower():
                    files.append({"name": n, "path": str(Path(base_dir) / n)})
                    if len(files) >= 100:
                        break
        files.sort(key=lambda f: f["path"])
    else:
        try:
            for child in sorted(current.iterdir(), key=lambda p: p.name.lower()):
                if child.is_dir() and child.name not in _FS_SKIP_DIRS:
                    dirs.append(child.name)
                elif child.is_file() and child.suffix == ".toml":
                    files.append({"name": child.name, "path": str(child)})
        except PermissionError:
            raise ValueError(f"no permission to list {current}")
    return {
        "path": str(current),
        "parent": str(current.parent) if current != root else None,
        "root": str(root),
        "dirs": dirs,
        "files": files,
        "truncated": bool(q) and len(files) >= 100,
    }


def tool_install(
    conn: sqlite3.Connection, firm_id: str, workspace: Path, source: str,
) -> dict[str, Any]:
    """Install a CLI tool from a local extension manifest via base's
    plugin system.

    Both base verbs take a manifest PATH (no repo slugs). Verb selection
    reads the manifest: a ``[dist]`` block means prebuilt binaries to
    fetch (``base ext add``); otherwise it's a plain linked install
    (``base ext install``). Installs are global (~/.base-gbl) today;
    per-workspace installs land with the 00-kit-base workspace work.
    """
    if not _base_ext_capable():
        raise ValueError(
            "tool install needs the BASE CLI's extension system (base ext) — "
            "install BASE, or drop tools into the firm workspace manually"
        )
    source = (source or "").strip()
    if not source:
        raise ValueError("pick a manifest — Browse to a base-extension .toml")
    local = Path(source).expanduser()
    if not local.is_file() or local.suffix != ".toml":
        raise ValueError(
            f"{source} is not a manifest .toml — Browse to the extension's "
            "base-extension.toml (repo slugs/URLs aren't supported by base ext)"
        )
    import tomllib
    try:
        manifest = tomllib.loads(local.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"manifest is not valid TOML: {exc}")
    verb = "add" if manifest.get("dist") else "install"
    try:
        proc = subprocess.run(
            ["base", "ext", verb, str(local)],
            capture_output=True, text=True, timeout=180,
            cwd=str(workspace), env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        raise ValueError(f"base ext {verb} timed out after 180s")
    ok = proc.returncode == 0
    log_event(
        conn, firm_id=firm_id, event_type="sysconfig.tool_install",
        actor=_BOARD, target_ref={"type": "firm", "id": firm_id},
        details={"source": str(local), "verb": verb, "ok": ok},
    )
    if not ok:
        raise ValueError(
            f"base ext {verb} failed: "
            f"{(proc.stderr.strip() or proc.stdout.strip())[:500]}"
        )
    return {"source": str(local), "verb": verb,
            "output": proc.stdout.strip()[-1000:]}
