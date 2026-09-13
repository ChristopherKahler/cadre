"""The pulse makes its own environment whole: a full PATH and the Board
notify token.

A pulse starts one of two ways, and neither handed it a whole environment:

* A timer pulse is a systemd user unit. It inherits the user manager's bare
  PATH, and its unit carries only what ``firm heartbeat enable`` captured
  (``firm.cli.heartbeat.capture_env``): never a PATH, never the vault, and --
  since #107 -- no notify credential either, because a baked copy sat in plain
  text on disk and outlived a rotation in the vault.
  The live unit on the operator's machine held FIRM_ID and CADRE_CLAUDE_BIN
  and nothing else (measured 2026-09-12, issue #107).
* Pulse now is dispatched by the hub, which carried a PATH but forwarded only
  the Slack token, so a Telegram firm's rail was dark there as well.

The notify rail reads its token from this process's environment only
(``firm.notify.rail_health``), so every such pulse raised "Board notify rail
is unresolvable" and counted a pulse error, and preflight held back every
Member whose tool lives in ``~/.local/bin``.

So the pulse fixes its own environment when it starts, whichever way it was
started: the one PATH the hub dispatches with (``pulse_path``), then the token
the firm's rail reads, taken from the firm's own stores when the environment
does not already hold it. An explicitly set value always wins. Nothing here
writes a unit file or hands a token to the scheduler: the vault exists so a
token does not sit in plain text on disk or on a command line. Because the
pulse does this itself, timer units already on disk get it without anyone
re-enabling them.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def pulse_path(workspace: Path, firm_id: str | None = None) -> str:
    """A full PATH for a pulse -- the ONE PATH floor, used by the hub when it
    dispatches Pulse now and by the pulse process itself when it starts.

    systemd ``--user`` starts with a BARE PATH (no ``~/.local/bin``, no
    ``.firm/bin``). After a host/WSL restart the detached pulse then can't
    resolve firm tools and every member skips (ESC-008/009/010/015), with the
    only unblock being a manual ``systemctl --user import-environment PATH``.
    Carry a real PATH so neither the preflight nor the member spawns ever run
    bare -- firm-local dirs first, then the inherited PATH, then a system floor
    in case the inherited PATH was thin.

    With *firm_id*, the directories where the hub found the firm's equipped
    tools come right after the firm-local dirs (#111). The hub's PATH carries
    nvm's bin and a timer's does not, so a timer pulse held back every Member
    equipped with gws or railway. Ahead of the inherited PATH, a
    ``#!/usr/bin/env node`` tool also runs under the node installed beside it,
    as it does for the hub, not under /usr/bin/node.
    """
    home = Path.home()
    lead = [str(home / ".local" / "bin"), str(workspace / ".firm" / "bin")]
    base_bin = shutil.which("base")
    if base_bin:
        lead.insert(0, str(Path(base_bin).parent))
    if firm_id:
        lead += _recorded_tool_dirs(workspace, firm_id)
    floor = ["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin",
             "/sbin", "/bin"]
    ordered: list[str] = []
    seen: set[str] = set()
    for chunk in lead + [os.environ.get("PATH") or ""] + floor:
        for seg in chunk.split(os.pathsep):
            if seg and seg not in seen:
                seen.add(seg)
                ordered.append(seg)
    return os.pathsep.join(ordered)


def _recorded_tool_dirs(workspace: Path, firm_id: str) -> list[str]:
    """The directories the hub found the firm's equipped tools in, that still
    exist (``firm.pulse.preflight.firm_cli_paths``). A tool whose directory is
    gone stays off the PATH, and preflight names the missing path."""
    try:
        from firm.core.db import connect, db_is_remote, get_db_path
        from firm.pulse.preflight import firm_cli_paths

        db_path = get_db_path(workspace)
        if not db_is_remote() and not db_path.exists():
            return []   # connect() would create an empty database
        conn = connect(db_path)
        try:
            recorded = firm_cli_paths(conn, firm_id)
        finally:
            conn.close()
    except Exception:
        return []   # a PATH for the pulse must never fail on the record
    dirs = [os.path.dirname(path) for _, path in sorted(recorded.items())]
    return [d for d in dirs if d and os.path.isdir(d)]


def read_env_file(workspace: Path) -> dict[str, str]:
    """KEY=VALUE pairs from the workspace .env — for reading, not for
    mutating this process (contrast dashboard._load_firm_env)."""
    env_path = workspace / ".env"
    out: dict[str, str] = {}
    if not env_path.exists():
        return out
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _firm_vault(workspace: Path) -> dict[str, str]:
    """The firm's merged vault, read the same way a Member run copies it into
    its environment (``firm.pulse.spawn.spawn_member_run``)."""
    try:
        from firm.secrets.provider import resolve_provider
        return resolve_provider().resolve(workspace)
    except Exception:
        return {}   # the vault is additive -- it must never block a pulse


def slack_token_from_workspace(
    workspace: Path, vault: dict[str, str] | None = None,
) -> str | None:
    """Best-effort Slack bot token for Board notifications — the vault is the
    home for it now; the .mcp.json regex remains as the legacy fallback for
    firms that predate the vault and still carry the token inline.

    The vault's canonical key is ``CADRE_SLACK_BOT_TOKEN`` (the ``xoxb`` bot
    token ``chat.postMessage`` needs); ``CADRE_SLACK_TOKEN`` is the legacy
    inline name. Resolving only the legacy key left chief-of-staff's Board
    notify pipe dark across 20 escalations — the token was in the vault the
    whole time, under the name this never looked for. Try both."""
    env = _firm_vault(workspace) if vault is None else vault
    token = env.get("CADRE_SLACK_BOT_TOKEN") or env.get("CADRE_SLACK_TOKEN")
    if token:
        return token
    mcp = workspace / ".mcp.json"
    if not mcp.exists():
        return None
    m = re.search(
        r"CADRE_SLACK_(?:BOT_)?TOKEN=([^\s\"']+)",
        mcp.read_text(encoding="utf-8", errors="replace"),
    )
    return m.group(1) if m else None


def notify_token(
    workspace: Path, cfg: dict[str, Any] | None,
) -> tuple[str, str] | None:
    """``(variable, token)`` for the firm's notify rail, or None.

    The variable is the one the rail reads (``firm.notify.token_env_name``), so
    a firm configured with ``slack_token_env: CADRE_SLACK_BOT_TOKEN`` gets its
    token under that name rather than under a default the rail never looks at.
    For a webhook the token is the URL.

    The value comes from every source the old paths reached, vault first: the
    vault's value for that name; for Slack, both vault names and then a token
    still inline in .mcp.json, which is where the hub's Pulse now dispatch used
    to forward from; last, the workspace .env, which is where ``firm heartbeat
    enable`` used to capture a timer unit's token from. Never raises.
    """
    try:
        from firm.notify import token_env_name

        name = token_env_name(cfg) if cfg else None
        if not name:
            return None
        vault = _firm_vault(workspace)
        token = vault.get(name)
        if not token and cfg.get("provider", "slack") == "slack":
            token = slack_token_from_workspace(workspace, vault)
        if not token:
            token = read_env_file(workspace).get(name)
        return (name, token) if token else None
    except Exception:
        return None


def _notify_config(db_path: Path, firm_id: str) -> dict[str, Any] | None:
    try:
        from firm.core.db import connect
        from firm.notify import get_notify_config

        conn = connect(db_path)
        try:
            return get_notify_config(conn, firm_id)
        finally:
            conn.close()
    except Exception:
        return None


@contextlib.contextmanager
def pulse_environment(
    workspace: Path, db_path: Path, firm_id: str,
) -> Iterator[None]:
    """Run the block with this process's environment made whole for a pulse.

    PATH first, because the base-backed vault shells out to ``base`` and a
    bare PATH cannot find it. Then the firm's notify token, only when the
    environment does not already hold one.

    What it replaced is put back when the block exits. The CLI process exits
    right after anyway; this is for a caller that runs a pulse in-process --
    the test suite -- which must not carry one pulse's PATH into the next test.
    """
    replaced: dict[str, str | None] = {}

    def put(name: str, value: str) -> None:
        replaced.setdefault(name, os.environ.get(name))
        os.environ[name] = value

    put("PATH", pulse_path(workspace, firm_id))
    try:
        found = notify_token(workspace, _notify_config(db_path, firm_id))
        if found and not os.environ.get(found[0]):
            put(*found)
        yield
    finally:
        for name, value in replaced.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
