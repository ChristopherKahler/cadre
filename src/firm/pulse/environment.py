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

A Member must also run the `firm` of the install that launched its pulse
(#166). The install's scripts folder holds `firm` and `cadre` beside
`python`, `pip` and every dependency's command, so it never goes on a
Member's PATH whole: a Member's `python` would lose the system packages and
its `pip install` would land in Cadre's own install. Instead each pulse keeps
``<ws>/.firm/entry/<install key>/`` holding copies of only those two, and
``pulse_path`` names it first (``member_entry_dir``, ``ensure_member_entry``).
"""

from __future__ import annotations

import contextlib
import hashlib
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
    bare -- this install's entry folder first, then the firm-local dirs, then
    the inherited PATH, then a system floor in case the inherited PATH was thin.

    THE ENTRY FOLDER LEADS (#166): it holds only copies of this install's
    `firm` and `cadre`, so a Member runs the `firm` of the install that
    launched its pulse and every other name resolves exactly as before. It
    goes ahead of base's folder, `~/.local/bin` and `.firm/bin` because each of
    those can hold another install's `firm` (measured on WSL; `.firm/bin` held
    the copy the walk's workaround made). This function only NAMES it, and
    names it whether or not it exists yet, so the hub's dispatch and the pulse
    it starts hand Members the same PATH; the pulse writes the copies
    (``ensure_member_entry``).

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
    entry = member_entry_dir(workspace)
    if entry is not None:
        lead.insert(0, str(entry))
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


#: What the entry folder holds: the two commands a Member runs by name.
ENTRY_POINTS = ("firm", "cadre")


def _install_key(folder: str) -> str:
    """Twelve hex digits naming an install by its scripts folder. ``normcase``
    after ``realpath``, so on Windows one folder spelled in another case is
    one install."""
    canonical = os.path.normcase(os.path.realpath(folder))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def member_entry_dir(workspace: Path) -> Path | None:
    """``<ws>/.firm/entry/<key>``, the folder that leads a Member's PATH, or
    None when this install has no scripts folder holding `firm`
    (``firm.identity.own_scripts_dir``). Pure: it names the folder and writes
    nothing, so the hub and the pulse it starts name the same one.

    One folder per install, because two installs can pulse one firm (the hub
    runs a firm's own ``.venv``, a timer runs whatever enabled it, a hand
    pulse runs whatever was typed), and one shared folder would let one pulse
    swap `firm` under the other's running Members.
    """
    from firm.identity import own_scripts_dir

    scripts = own_scripts_dir()
    if not scripts:
        return None
    return workspace / ".firm" / "entry" / _install_key(scripts)


def ensure_member_entry(workspace: Path) -> dict[str, str]:
    """Make this install's entry folder hold exactly its `firm` and `cadre`.

    Returns what the pulse's result line carries about it, PRESENCE-KEYED
    like #141's containment record: nothing when all is well,
    ``member_entry_missing`` when there is no folder a Member can use,
    ``member_entry_note`` when it is usable but something in it is not as it
    should be. NEVER RAISES: a pulse whose Members cannot run `firm` still
    pulses, and says why.
    """
    try:
        return _ensure_member_entry(workspace)
    except Exception as exc:                        # noqa: BLE001
        return {"member_entry_missing":
                f"the entry folder could not be made: {exc}"}


def _ensure_member_entry(workspace: Path) -> dict[str, str]:
    from firm.identity import own_scripts_dir, scripts_folders_asked

    scripts = own_scripts_dir()
    if not scripts:
        asked = ", ".join(scripts_folders_asked()) or "none"
        return {"member_entry_missing": (
            "no folder this interpreter installs console scripts to holds "
            f"`firm` (asked: {asked}), so a Member finds `firm` only if its "
            "PATH already has one")}
    entry = workspace / ".firm" / "entry" / _install_key(scripts)
    entry.mkdir(parents=True, exist_ok=True)
    # The copies are never committed with a firm kept under git. Written only
    # when absent: an operator's own edit of it stands.
    ignore = entry.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")

    notes: list[str] = []
    on_disk = {os.path.normcase(n): n for n in os.listdir(scripts)}
    wanted: dict[str, Path] = {}
    for name in ENTRY_POINTS:
        found = shutil.which(name, path=scripts)
        if not found:
            notes.append(f"`{name}` is not in {scripts}")
            continue
        # The name as it is on disk: on Windows `which` answers in PATHEXT's
        # spelling (`firm.EXE`), and the copy keeps the install's own.
        real = on_disk.get(os.path.normcase(Path(found).name), Path(found).name)
        wanted[real] = Path(scripts) / real
        sibling = f"{name}-script.py"       # an old setuptools launcher reads it
        if os.path.normcase(sibling) in on_disk:
            wanted[on_disk[os.path.normcase(sibling)]] = Path(scripts) / sibling

    # ONLY WHAT THE PULSE PUT THERE (G0 verdict condition 13). This folder
    # leads the pulse's own PATH, `resolve_claude_bin` walks that PATH when
    # CADRE_CLAUDE_BIN is unset, and a Member can write inside the workspace:
    # anything else left here would shadow every name, `claude` included, from
    # the next pulse on. Other installs' folders and the .gitignore beside
    # them are never touched.
    for item in entry.iterdir():
        if item.name in wanted:
            continue
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
        except OSError as exc:
            notes.append(f"{item.name} could not be removed: {exc}")

    # BYTE COPIES, rewritten when the install's bytes differ. A pip or uv
    # launcher carries its interpreter's absolute path, so a copy runs the
    # install it came from (the walk's RUN-002 closed its Unit on one).
    # BOUNDARY: a launcher that finds its interpreter relative to its own
    # folder (uv's --relocatable venvs) cannot start from a copy, so such an
    # install's Members cannot run `firm`, exactly as on main when no other
    # `firm` is on the PATH. Not built for; not seen on any install here.
    for name, source in wanted.items():
        target = entry / name
        data = source.read_bytes()
        if target.is_file() and target.read_bytes() == data:
            continue
        temp = entry / f".{name}.{os.getpid()}.tmp"
        try:
            temp.write_bytes(data)
            shutil.copymode(source, temp)
            os.replace(temp, target)
        except OSError as exc:
            # Windows refuses to replace an image a still-running Member
            # holds. That copy names the same interpreter (same install, same
            # key), so it stays and the next pulse refreshes it.
            notes.append(f"{name} could not be refreshed; the copy there "
                         f"stays: {exc}")
            with contextlib.suppress(OSError):
                temp.unlink()

    record: dict[str, str] = {}
    if not shutil.which("firm", path=str(entry)):
        record["member_entry_missing"] = (
            f"the entry folder {entry} holds no runnable `firm`")
    if notes:
        record["member_entry_note"] = "; ".join(notes)
    return record


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
) -> Iterator[dict[str, str]]:
    """Run the block with this process's environment made whole for a pulse.

    The Member entry folder first (#166): its copies are made or refreshed
    before the PATH that names it is put in place, and what that found is
    what the block receives, for the pulse's result line. Then PATH, because
    the base-backed vault shells out to ``base`` and a bare PATH cannot find
    it. Then the firm's notify token, only when the environment does not
    already hold one.

    What it replaced is put back when the block exits. The CLI process exits
    right after anyway; this is for a caller that runs a pulse in-process --
    the test suite -- which must not carry one pulse's PATH into the next test.
    """
    replaced: dict[str, str | None] = {}

    def put(name: str, value: str) -> None:
        replaced.setdefault(name, os.environ.get(name))
        os.environ[name] = value

    entry = ensure_member_entry(workspace)
    put("PATH", pulse_path(workspace, firm_id))
    try:
        found = notify_token(workspace, _notify_config(db_path, firm_id))
        if found and not os.environ.get(found[0]):
            put(*found)
        yield entry
    finally:
        for name, value in replaced.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
