"""What the operator already has — the survey behind Equip and Train.

``sysconfig.inventory`` answers *what does this firm have*. This answers the
question that comes first: *what does this operator have on their machine, that
their firm could be given?* MCP servers, CLI tools, skills, commands — read from
the places they actually live, offered to the Board, never auto-copied.

Read-only and jailed. Nothing here writes; the Board decides, and
``sysconfig.mcp_set`` / ``vars_set`` / ``tool_install`` do the writing under
audit. Secret VALUES are masked on the way out — a survey is not a key dump.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

# BASE is the engine. It is surveyed first, reported loudest, and its extension
# list IS the CLI-tool registry — `tool_install` can only install what base can.

# Probed, not guessed: CLIs a firm plausibly needs, so the gap audit has
# ground truth to diff a roster against. Two tiers: bare presence for build
# tools, and a VERIFY probe — a cheap read-only identity call — for
# account-connected CLIs, because presence is not capability. gws taught us
# why (2026-07-14): it sat installed and OAuth'd — gmail, calendar, drive —
# while a founding declared "the firm has no email" and raised four false
# escalations about tools that worked. Every probe here must be read-only,
# fast, and print an identity, never a secret.
_CLI_PROBE = (
    # (binary, what it is, verify argv after the binary — None = presence only)
    ("git", "Version control", None),
    ("gh", "GitHub — issues, PRs, releases", ("auth", "status")),
    ("gws", "Google Workspace — gmail, calendar, drive, docs, sheets, tasks, "
            "people, chat, meet",
     ("gmail", "users", "getProfile", "--params", '{"userId":"me"}')),
    ("gcloud", "Google Cloud", ("auth", "list", "--format=value(account)")),
    ("m365", "Microsoft 365 — outlook, onedrive, teams, sharepoint", ("status",)),
    ("aws", "AWS", ("sts", "get-caller-identity")),
    ("railway", "Railway deploys", ("whoami",)),
    ("vercel", "Vercel deploys", ("whoami",)),
    ("netlify", "Netlify deploys", ("status",)),
    ("flyctl", "Fly.io deploys", ("auth", "whoami")),
    ("wrangler", "Cloudflare Workers", ("whoami",)),
    ("stripe", "Stripe payments", None),   # its config dump prints keys — never probe it
    ("ffmpeg", "Video and audio encoding", None),
    ("yt-dlp", "Media download", None),
    ("node", "JavaScript runtime", None),
    ("python3", "Python runtime", None),
    ("docker", "Containers", None),
    ("rg", "Fast search", None),
    ("jq", "JSON on the command line", None),
    ("pandoc", "Document conversion", None),
    ("imagemagick", "Image manipulation", None),
    ("convert", "ImageMagick convert", None),
)

_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "target",
              ".cache", ".npm", ".cargo", "dist", "build"}

_SECRETISH = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I)


def _mask(v: str) -> str:
    v = str(v)
    return f"{v[:3]}…{v[-2:]}" if len(v) > 8 else "•••"


def _run(argv: list[str], timeout: int = 10) -> tuple[int, str]:
    # stdin is CLOSED and prompts are disabled: a survey command that waits on
    # input is a hang, not a question. Measured on snap gh 2026-07-14 — `auth
    # status` in a subprocess sat on its prompt until the timeout and read as
    # "not signed in" when it was.
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1",
               NO_COLOR="1")
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return p.returncode, (p.stdout + p.stderr)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# BASE — the trophy
# ---------------------------------------------------------------------------

def base_survey() -> dict[str, Any]:
    """BASE and its extensions. This is the engine, and the Board should see it.

    A firm without BASE still runs; a firm with it inherits the whole extension
    fleet as CLI tools its Members can be handed. That difference is the single
    biggest capability jump in the product, so it gets its own card.
    """
    from firm.sysconfig.service import which_base
    binary = which_base()
    if not binary:
        return {"present": False, "extensions": [], "ext_capable": False}

    _, ver_out = _run([binary, "--version"], timeout=8)
    version = (ver_out.strip().splitlines() or [""])[0].strip()

    rc, help_out = _run([binary, "ext", "--help"], timeout=8)
    ext_capable = rc == 0 and "unknown command" not in help_out.lower()

    extensions: list[dict[str, str]] = []
    if ext_capable:
        rc, out = _run([binary, "ext", "list"], timeout=15)
        if rc == 0:
            for line in out.splitlines():
                line = line.rstrip()
                if not line.strip() or set(line.strip()) <= {"─", "-"}:
                    continue
                if re.match(r"^(NAME|PLUGIN COMMAND)\b", line):
                    continue
                if re.match(r"^\d+ (extension|plugin)", line.strip()):
                    continue
                if line.startswith("base "):     # plugin-command table, not extensions
                    continue
                cols = re.split(r"\s{2,}", line.strip())
                if len(cols) >= 2 and re.fullmatch(r"[a-z0-9][a-z0-9._-]*", cols[0]):
                    extensions.append({
                        "name": cols[0],
                        "version": cols[1],
                        "description": cols[-1] if len(cols) > 2 else "",
                    })

    return {
        "present": True,
        "path": binary,
        "version": version,
        "ext_capable": ext_capable,
        "extensions": extensions,
    }


# ---------------------------------------------------------------------------
# MCP — offered, never auto-copied
# ---------------------------------------------------------------------------

def _env_keys(path: Path) -> list[str]:
    try:
        return [ln.split("=", 1)[0].strip()
                for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()
                if "=" in ln and not ln.lstrip().startswith("#")]
    except OSError:
        return []


def notify_presets(root: Path) -> list[dict[str, Any]]:
    """Notification setups the operator already owns — offered one-click.

    Sources, most complete first (values NEVER leave the server; a preset
    carries names and references only):
      1. firms with a live notify_config — full channel + target + token,
      2. firm .env files carrying a CADRE_*_TOKEN — token wired, target needed,
      3. ~/.claude/channels/<channel>/.env — the operator's channel store.
    """
    import sqlite3 as _sq

    presets: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(channel: str, label: str, target: str, token_env: str,
            kind: str, ref: str) -> None:
        sig = (channel, target, token_env)
        if sig in seen:
            return
        seen.add(sig)
        presets.append({"channel": channel, "label": label, "target": target,
                        "token_env": token_env, "source": {"kind": kind, "ref": ref}})

    for db in sorted(root.glob("*/.firm/firm.db")):
        ws = db.parent.parent
        try:
            conn = _sq.connect(str(db))
            row = conn.execute("SELECT notify_config FROM firm").fetchone()
            conn.close()
        except _sq.Error:
            continue
        try:
            cfg = json.loads(row[0]) if row and row[0] else {}
        except (TypeError, ValueError):
            cfg = {}
        provider = str(cfg.get("provider") or "")
        if provider == "slack" and cfg.get("slack_user_id"):
            add("slack", f"{ws.name}'s Slack", str(cfg["slack_user_id"]),
                str(cfg.get("slack_token_env") or "CADRE_SLACK_TOKEN"),
                "firm", ws.name)
        elif provider == "telegram" and cfg.get("telegram_chat_id"):
            add("telegram", f"{ws.name}'s Telegram", str(cfg["telegram_chat_id"]),
                str(cfg.get("telegram_token_env") or "CADRE_TELEGRAM_TOKEN"),
                "firm", ws.name)

    for ws in sorted(p.parent.parent for p in root.glob("*/.firm/firm.db")):
        for key in _env_keys(ws / ".env"):
            for ch in ("slack", "telegram"):
                if ch in key.lower() and "token" in key.lower():
                    add(ch, f"token from {ws.name}", "", key, "firm-env", ws.name)

    channels = Path.home() / ".claude" / "channels"
    for ch in ("slack", "telegram"):
        for key in _env_keys(channels / ch / ".env"):
            add(ch, f"your {ch} channel token", "", key, "channel-env", ch)

    return presets


def plugin_mcp_specs() -> dict[str, dict[str, Any]]:
    """MCP servers declared by enabled plugins, with runnable specs.

    ``enabledPlugins`` only carries names; the runnable spec lives in the
    plugin's install dir. ``${CLAUDE_PLUGIN_ROOT}`` expands to that dir so the
    spec survives being copied into a firm's .mcp.json; a ``${user_config.*}``
    env value can't resolve outside the plugin runtime, so it becomes a vault
    placeholder for its own env key.
    """
    home = Path.home()
    enabled = _read_json(home / ".claude" / "settings.json").get("enabledPlugins") or {}
    installed = (_read_json(home / ".claude" / "plugins" / "installed_plugins.json")
                 .get("plugins") or {})
    out: dict[str, dict[str, Any]] = {}
    for key in enabled:
        entries = installed.get(str(key)) or []
        entry = entries[0] if isinstance(entries, list) and entries else None
        root = entry.get("installPath") if isinstance(entry, dict) else None
        if not root:
            continue
        # Declared in either home: .mcp.json (telegram, monster-meta) or
        # .claude-plugin/plugin.json (context-mode) — where mcpServers may be
        # the dict itself or a path reference to a json file that has it.
        declared: dict[str, Any] = dict(
            _read_json(Path(root) / ".mcp.json").get("mcpServers") or {})
        pj_mcp = (_read_json(Path(root) / ".claude-plugin" / "plugin.json")
                  .get("mcpServers"))
        if isinstance(pj_mcp, str):
            ref = pj_mcp.replace("${CLAUDE_PLUGIN_ROOT}", root).lstrip("./")
            candidate = Path(ref) if ref.startswith("/") else Path(root) / ref
            pj_mcp = _read_json(candidate).get("mcpServers")
        if isinstance(pj_mcp, dict):
            for n, s in pj_mcp.items():
                declared.setdefault(n, s)
        for name, spec in declared.items():
            if not isinstance(spec, dict) or name in out:
                continue
            fixed = json.loads(json.dumps(spec).replace("${CLAUDE_PLUGIN_ROOT}", root))
            env = fixed.get("env")
            if isinstance(env, dict):
                fixed["env"] = {
                    k: (f"${{{k}}}" if isinstance(v, str) and "${user_config." in v else v)
                    for k, v in env.items()
                }
            out[name] = fixed
    return out


def mcp_survey(workspace: Path) -> dict[str, Any]:
    """Every MCP server this operator has, and where it came from.

    Sources, in the order a Board would trust them:
      user     — ~/.claude.json mcpServers (their personal fleet)
      project  — ~/.claude.json projects.<path>.mcpServers (per-repo servers)
      plugin   — enabled plugins from ~/.claude/settings.json
      firm     — the firm's own .mcp.json (already equipped)

    Env values are MASKED. Adopting a server into a firm must be an explicit act
    that re-supplies the credential into the vault — a survey never carries a
    secret across the boundary from the operator's config into a firm.
    """
    home = Path.home()
    firm_mcp = _read_json(workspace / ".mcp.json").get("mcpServers") or {}
    equipped = set(firm_mcp)

    found: dict[str, dict[str, Any]] = {}

    def offer(name: str, spec: Any, source: str) -> None:
        if not isinstance(spec, dict) or name in found:
            return
        env = spec.get("env") or {}
        needs = [k for k in env if _SECRETISH.search(k)] if isinstance(env, dict) else []
        found[name] = {
            "name": name,
            "source": source,
            "command": spec.get("command") or spec.get("url") or spec.get("type") or "",
            "needs_keys": needs,
            "env_preview": {k: _mask(v) for k, v in (env.items() if isinstance(env, dict) else [])},
            "equipped": name in equipped,
            "available": True,
            "why_not": "",
        }

    claude_json = _read_json(home / ".claude.json")
    for name, spec in (claude_json.get("mcpServers") or {}).items():
        offer(name, spec, "user")
    for cfg in (claude_json.get("projects") or {}).values():
        if not isinstance(cfg, dict):
            continue
        for name, spec in (cfg.get("mcpServers") or {}).items():
            offer(name, spec, "project")

    # Plugin-provided servers are equippable like anything else (restriction
    # lifted 2026-07-13): their specs are materialized from the plugin's own
    # .mcp.json into the firm's — which is exactly what --strict-mcp-config
    # wants loaded. Only plugins that actually DECLARE MCP servers appear; a
    # skills-only plugin was never an MCP server and no longer shows as one.
    settings = _read_json(home / ".claude" / "settings.json")
    for name, spec in plugin_mcp_specs().items():
        offer(name, spec, "plugin")

    for name, spec in firm_mcp.items():
        offer(name, spec, "firm")
        found[name]["equipped"] = True

    # Equipped first, then by how much the Board is likely to care: their own
    # fleet, then per-project servers, then plugin-provided ones (mostly LSPs —
    # real, but rarely what a firm is hired to do).
    rank = {"firm": 0, "user": 1, "project": 2, "plugin": 3}
    servers = sorted(found.values(),
                     key=lambda s: (not s["equipped"], rank.get(s["source"], 9), s["name"]))
    return {
        "servers": servers,
        "equipped": sorted(equipped),
        "marketplaces": sorted(settings.get("extraKnownMarketplaces") or {}),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def raw_specs(names: list[str]) -> dict[str, dict[str, Any]]:
    """The real specs for *names*, with every secret replaced by a placeholder.

    This is the one function allowed to read the operator's actual MCP config for
    the purpose of writing a firm's ``.mcp.json`` — and it deliberately cannot
    carry a credential across. A secret-looking env value becomes ``${KEY}``.
    Claude Code expands ``${VAR}`` in ``.mcp.json`` from the process env, and the
    Member spawn injects the firm vault into that env — so the key travels from
    the vault to the server at run time and never lands in a file.
    """
    home = Path.home()
    claude_json = _read_json(home / ".claude.json")

    pool: dict[str, Any] = dict(claude_json.get("mcpServers") or {})
    for cfg in (claude_json.get("projects") or {}).values():
        if isinstance(cfg, dict):
            for n, spec in (cfg.get("mcpServers") or {}).items():
                pool.setdefault(n, spec)
    for n, spec in plugin_mcp_specs().items():
        pool.setdefault(n, spec)

    out: dict[str, dict[str, Any]] = {}
    for name in names:
        spec = pool.get(name)
        if not isinstance(spec, dict):
            continue
        clean = {k: v for k, v in spec.items() if k != "env"}
        env = spec.get("env")
        if isinstance(env, dict) and env:
            clean["env"] = {
                k: (f"${{{k}}}" if _SECRETISH.search(k) else v)
                for k, v in env.items()
            }
        out[name] = clean
    return out


def keys_needed(names: list[str]) -> list[str]:
    """Env keys the chosen servers need supplied into the vault."""
    needed: list[str] = []
    for spec in raw_specs(names).values():
        for k, v in (spec.get("env") or {}).items():
            if isinstance(v, str) and v.startswith("${") and k not in needed:
                needed.append(k)
    return needed


def _identity_line(out: str) -> str:
    """The line that proves WHO a tool is signed in as — never a secret.

    Preference order: an email address, then a "logged in / account /
    connected" line, then the first non-empty line that doesn't look like a
    credential. Truncated hard; this rides into prompts and charters.
    """
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", out)
    if m:
        return m.group(0)
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    for ln in lines:
        if _SECRETISH.search(ln):
            continue
        if re.search(r"logged in|signed in|account|connected|as\b", ln, re.I):
            return ln[:120]
    for ln in lines:
        if not _SECRETISH.search(ln):
            return ln[:120]
    return ""


def cli_prompt_line(c: dict[str, Any]) -> str:
    """One prompt-ready line of ground truth about a host CLI.

    Shared by the founding arsenal and the wiring prompt so the two agents
    never disagree about what this machine can do.
    """
    if c.get("live") is True:
        who = f": {c['detail']}" if c.get("detail") else ""
        return f"{c['name']} — {c['what']} — LIVE, probed just now{who}"
    if c.get("live") is False:
        return (f"{c['name']} — {c['what']} — installed but NOT signed in; "
                "auth is the only gap")
    return f"{c['name']} — {c['what']}"


_cli_cache: tuple[float, list[dict[str, Any]]] | None = None
_CLI_CACHE_TTL = 180.0   # verify probes hit real APIs; Equip reloads shouldn't


def cli_survey() -> list[dict[str, Any]]:
    """What's actually on PATH — and, for account-connected CLIs, whether the
    account is LIVE right now.

    ``live`` is True/False when the tool defines a verify probe (read-only
    identity call, ran just now), None when presence is all we can know.
    ``detail`` is the identity the probe printed — the survey's proof that
    "this machine can send email" is a fact, not an inference.
    """
    global _cli_cache
    import time
    if _cli_cache and time.monotonic() - _cli_cache[0] < _CLI_CACHE_TTL:
        return _cli_cache[1]

    seen: dict[str, dict[str, Any]] = {}
    for name, what, verify in _CLI_PROBE:
        if name in seen:
            continue
        path = shutil.which(name)
        seen[name] = {"name": name, "what": what,
                      "present": path is not None, "path": path or "",
                      "live": None, "detail": "",
                      "_verify": verify if path else None}

    from concurrent.futures import ThreadPoolExecutor

    def probe(entry: dict[str, Any]) -> None:
        # 25s, not 10: snap-wrapped binaries cold-start slowly, and a probe
        # that times out reads as "not signed in" — the exact lie this
        # survey exists to prevent. They run concurrently; wall time is fine.
        rc, out = _run([entry["path"], *entry["_verify"]], timeout=25)
        entry["live"] = rc == 0
        entry["detail"] = _identity_line(out) if rc == 0 else ""

    to_probe = [e for e in seen.values() if e["_verify"]]
    if to_probe:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(probe, to_probe))
    for e in seen.values():
        e.pop("_verify", None)

    result = list(seen.values())
    _cli_cache = (time.monotonic(), result)
    return result


# ---------------------------------------------------------------------------
# Knowledge — skills and commands
# ---------------------------------------------------------------------------

# Board-seat commands. A Member carrying one of these is a category error —
# /boardroom IS the Board's chair and /cadre IS the architect's — so they are
# invisible to every survey, every founding arsenal, and every loadout. Not
# an exclusion (those are the operator's to reverse); a constitutional line.
_BOARD_ONLY_COMMANDS = {"/boardroom", "/cadre"}

# Operator-ritual command families. Members get BASE as a CLI through its own
# card; the /base:* slash commands (status, weekly, groom, pulse…) run in the
# OPERATOR'S sessions — a Member never carries one.
_OPERATOR_COMMAND_PREFIXES = ("/base:",)


def _front_matter_desc(path: Path) -> str:
    """First `description:` in a skill's YAML front matter, if it has one."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            head = [next(fh, "") for _ in range(20)]
    except OSError:
        return ""
    for line in head:
        if line.lower().startswith("description:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")[:160]
    return ""


def knowledge_survey(extra_dirs: list[str] | None = None) -> dict[str, Any]:
    """Skills and commands the operator has — the raw material of a loadout.

    ``~/.claude/skills/<name>/SKILL.md`` and ``~/.claude/commands/**/*.md`` are
    the canonical homes. *extra_dirs* are folders the Board attached by hand —
    a framework living anywhere on disk (Chris's Salvatore style pack, say)
    becomes loadout material by pointing at it.
    """
    home = Path.home()
    skills: list[dict[str, str]] = []
    commands: list[dict[str, str]] = []

    skills_root = home / ".claude" / "skills"
    if skills_root.is_dir():
        for entry in sorted(skills_root.iterdir(), key=lambda p: p.name.lower()):
            spec = entry / "SKILL.md"
            if entry.is_dir() and spec.is_file():
                skills.append({
                    "name": entry.name, "scope": "user", "path": str(entry),
                    "description": _front_matter_desc(spec),
                })

    cmds_root = home / ".claude" / "commands"
    if cmds_root.is_dir():
        for md in sorted(cmds_root.rglob("*.md")):
            if any(part in _SKIP_DIRS for part in md.parts):
                continue
            rel = md.relative_to(cmds_root).with_suffix("")
            name = "/" + ":".join(rel.parts)
            if name in _BOARD_ONLY_COMMANDS or name.startswith(_OPERATOR_COMMAND_PREFIXES):
                continue
            commands.append({
                "name": name,
                "scope": "user", "path": str(md),
            })

    attached: list[dict[str, Any]] = []
    for raw in extra_dirs or []:
        d = Path(raw).expanduser()
        if d.is_file():
            # a single attached document — a voice profile, a spec, a style guide
            attached.append({"path": str(d), "name": d.name, "files": 1,
                             "sample": [d.name]})
            continue
        if not d.is_dir():
            continue
        try:
            files = [p for p in d.rglob("*")
                     if p.is_file() and p.suffix in (".md", ".json", ".toml", ".yaml", ".yml")
                     and not any(s in p.parts for s in _SKIP_DIRS)]
        except OSError:
            files = []
        attached.append({
            "path": str(d), "name": d.name, "files": len(files),
            "sample": [f.name for f in files[:6]],
        })

    return {"skills": skills, "commands": commands, "attached": attached}


# ---------------------------------------------------------------------------
# Folder attach
# ---------------------------------------------------------------------------

def browse(path: str | None) -> dict[str, Any]:
    """Read-only directory walk, jailed to the operator's home.

    ``sysconfig.fs_browse`` exists but only surfaces ``.toml`` manifests — it was
    built for the tool-install picker. Train needs to attach a *folder* of
    knowledge, so this reports directories and what's inside them.
    """
    root = Path.home().resolve()
    current = Path(path).expanduser().resolve() if path else root
    if not (current == root or current.is_relative_to(root)):
        raise ValueError("that folder is outside your home directory")
    if not current.is_dir():
        raise ValueError(f"not a folder: {current}")

    dirs: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    try:
        for child in sorted(current.iterdir(), key=lambda p: p.name.lower()):
            if child.is_file() and child.suffix == ".md":
                # single documents are attachable too — a voice.md is knowledge
                files.append({"name": child.name, "path": str(child)})
                continue
            if not child.is_dir() or child.name in _SKIP_DIRS:
                continue
            if child.name.startswith(".") and child.name != ".claude":
                continue
            try:
                n = sum(1 for p in child.iterdir()
                        if p.is_file() and p.suffix in (".md", ".json", ".toml"))
            except OSError:
                n = 0
            dirs.append({"name": child.name, "path": str(child), "docs": n})
    except PermissionError:
        raise ValueError(f"no permission to read {current}")

    here = sum(1 for p in current.iterdir()
               if p.is_file() and p.suffix in (".md", ".json", ".toml"))
    return {
        "path": str(current),
        "parent": str(current.parent) if current != root else None,
        "root": str(root),
        "dirs": dirs,
        "files": files,
        "docs_here": here,
    }


# ---------------------------------------------------------------------------

def survey(workspace: Path, extra_dirs: list[str] | None = None) -> dict[str, Any]:
    """Everything the Board could hand their firm, in one payload."""
    return {
        "ok": True,
        # A charter on disk means the wiring agent has already run once. Coming back
        # to Train after that is a reroll, not a step back — the UI has to know.
        "wired": (workspace / "CLAUDE.md").is_file(),
        "base": base_survey(),
        "mcp": mcp_survey(workspace),
        "cli": cli_survey(),
        "knowledge": knowledge_survey(extra_dirs),
    }
