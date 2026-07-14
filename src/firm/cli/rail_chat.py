"""``cadre chat`` — the chat rail's operator surface.

The licensee path, end to end (no external app, no tokens)::

    cadre chat setup       # firms root + port + permission mode — 20 seconds
    cadre chat serve       # run the daemon in the foreground
    cadre chat open        # print (and try to launch) the UI URL
    cadre chat enable      # own systemd user unit, Restart=on-failure
    cadre chat status      # unit state + config + conversation map

Same verb set as ``cadre slack`` minus everything Slack made necessary —
manifest, tokens, channel pick, pairing. The daemon binds localhost; the OS
session is the allowlist.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from firm.rail import load_threads, provider_dir, save_config
from firm.rail.chat import (
    DEFAULT_PORT,
    PROVIDER,
    chat_config,
    daemon_url,
    local_call,
)
from firm.rail.turns import find_claude

_UNIT_NAME = "cadre-rail-chat"


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def _sched(unit_dir=None):
    """The platform scheduler; an explicit *unit_dir* pins the systemd
    backend (the tests' knob, meaningless on other platforms)."""
    if unit_dir is not None:
        from firm.sched.systemd import SystemdScheduler
        return SystemdScheduler(unit_dir=unit_dir)
    from firm.sched import resolve_scheduler
    return resolve_scheduler()


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def run_setup(firms_root: Path) -> int:
    firms_root = firms_root.expanduser().resolve()
    print("Cadre chat rail setup — the boardroom in your browser.\n")

    config = chat_config()
    default_port = int(config.get("port", DEFAULT_PORT))
    raw = input(f"Port [{default_port}]: ").strip()
    if raw:
        try:
            port = int(raw)
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError:
            print("port must be a number between 1024 and 65535")
            return 1
    else:
        port = default_port

    mode = ""
    while mode not in ("approve", "skip"):
        mode = (input("Permission mode — approve (Allow/Deny card per action, "
                      "default) or skip (full trust)? [approve/skip] ").strip().lower()
                or "approve")

    config.update({
        "firms_root": str(firms_root),
        "host": "127.0.0.1",   # safe default — `cadre chat host tailscale` opens the phone path
        "port": port,
        "mode": mode,
    })
    path = save_config(PROVIDER, config)
    print(f"✓ config written — {path}\n\nNext:\n"
          "  cadre chat serve    # run it (foreground)\n"
          "  cadre chat open     # open the UI\n"
          "  cadre chat enable   # run it as a service\n"
          "  cadre chat host tailscale   # reach it from your phone over the tailnet\n"
          f"UI: http://127.0.0.1:{port} — `@<firm-id> …` scopes a fresh "
          "conversation to one firm.\n"
          "Phone + friendly names: docs/TAILSCALE-SETUP.md and "
          "docs/HOSTNAMES-SETUP.md — hand either to Claude Code and it "
          "walks the machine through it.")
    return 0


# ---------------------------------------------------------------------------
# serve / open / test / say
# ---------------------------------------------------------------------------

def run_serve() -> int:
    from firm.rail.chat import run_serve as serve
    return serve()


def run_open() -> int:
    """Print the UI URL and best-effort launch a browser (WSL-aware)."""
    config = chat_config()
    url = daemon_url(config)
    state = local_call(f"{url}/api/state")
    print(url)
    if not state.get("ok"):
        print("(daemon not responding — start it: cadre chat serve, "
              "or cadre chat enable)", file=sys.stderr)
    for launcher in (["wslview", url],
                     ["cmd.exe", "/c", "start", url.replace("&", "^&")],
                     ["xdg-open", url]):
        try:
            subprocess.run(launcher, capture_output=True, timeout=10)
            break
        except (OSError, subprocess.TimeoutExpired):
            continue
    return 0


def run_test() -> int:
    config = chat_config()
    if not config.get("firms_root"):
        _emit({"ok": False, "reason": "not configured — run: cadre chat setup"})
        return 1
    state = local_call(f"{daemon_url(config)}/api/state")
    if not state.get("ok"):
        _emit({"ok": False, "reason": state.get("reason", "daemon unreachable"),
               "hint": "start it: cadre chat serve"})
        return 1
    _emit({"ok": True, "url": daemon_url(config),
           "conversations": len(state.get("conversations", [])),
           "mode": state.get("mode"), "claude_bin": find_claude()})
    return 0


def run_say(text: str, conversation: str | None) -> int:
    """Post *text* into a conversation — the voice a running boardroom
    session uses to answer mid-turn (its spawn env carries the routing)."""
    url = os.environ.get("CADRE_RAIL_CHAT_URL") or daemon_url(chat_config())
    conv_id = conversation or os.environ.get("CADRE_RAIL_THREAD_TS")
    if not conv_id:
        _emit({"ok": False, "reason": "no conversation — pass --conversation "
                                      "or set CADRE_RAIL_THREAD_TS"})
        return 1
    result = local_call(f"{url}/api/say",
                        {"conversation_id": conv_id, "text": text})
    _emit(result)
    return 0 if result.get("ok") else 1


# ---------------------------------------------------------------------------
# enable / disable / status — the heartbeat systemd idiom, service not timer
# ---------------------------------------------------------------------------

def run_enable(*, unit_dir: Path | None = None) -> int:
    config = chat_config()
    if not config.get("firms_root"):
        _emit({"ok": False, "reason": "not configured — run: cadre chat setup"})
        return 1
    claude_bin = find_claude()
    if not claude_bin:
        _emit({"ok": False, "reason": "claude binary not found — set CADRE_CLAUDE_BIN"})
        return 1
    sched = _sched(unit_dir)
    from firm.sched.base import SchedulerError
    try:
        installed = sched.install_service(
            _UNIT_NAME,
            description="Cadre chat rail — the Co-Board boardroom chat daemon",
            workdir=Path(config["firms_root"]),
            env={"CADRE_CLAUDE_BIN": claude_bin},
            argv=[sys.executable, "-m", "firm", "chat", "serve"],
        )
    except SchedulerError as exc:
        _emit({"ok": False, "reason": str(exc)})
        return 1
    _emit({"ok": True, "unit": installed.get("unit", _UNIT_NAME),
           "scheduler": sched.name, "unit_dir": installed.get("unit_dir", ""),
           "url": daemon_url(config), "claude_bin": claude_bin,
           "mode": config.get("mode")})
    return 0


def run_disable(*, unit_dir: Path | None = None) -> int:
    sched = _sched(unit_dir)
    if not sched.status(_UNIT_NAME).get("installed"):
        _emit({"ok": False, "reason": "rail service is not installed"})
        return 1
    out = sched.remove(_UNIT_NAME)
    _emit({"ok": True, "removed": out.get("removed") or _UNIT_NAME})
    return 0


def _restart_if_active(result: dict[str, Any]) -> None:
    """Config edits apply at daemon start — bounce the service when it runs."""
    sched = _sched()
    if sched.status(_UNIT_NAME).get("state") == "active":
        ok, out = sched.restart(_UNIT_NAME)
        result["service"] = "restarted" if ok else f"restart failed: {out}"
    else:
        result["note"] = ("takes effect next daemon start — restart your "
                          "foreground `cadre chat serve` if one is running")


def run_mode(mode: str | None) -> int:
    if mode is None:
        _emit({"ok": True, "mode": chat_config().get("mode", "approve")})
        return 0
    result = apply_setting("mode", mode)
    _emit(result)
    return 0 if result.get("ok") else 1


def run_model(model: str | None) -> int:
    if model is None:
        _emit({"ok": True, "model": chat_config().get("model") or "(account default)"})
        return 0
    value = "" if model.strip().lower() == "default" else model.strip()
    result = apply_setting("model", value)
    if result.get("ok"):
        result["model"] = result["model"] or "(account default)"
    _emit(result)
    return 0 if result.get("ok") else 1


def _tailscale_ip() -> str | None:
    """The machine's tailscale IPv4 — `tailscale ip` first, then a CGNAT
    (100.64/10) interface scan for setups where the CLI isn't on PATH."""
    try:
        proc = subprocess.run(["tailscale", "ip", "-4"],
                              capture_output=True, text=True, timeout=10)
        if proc.returncode == 0:
            for line in proc.stdout.strip().splitlines():
                if line.strip():
                    return line.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        proc = subprocess.run(["ip", "-4", "addr"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    import re
    match = re.search(
        r"inet (100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+)", proc.stdout)
    return match.group(1) if match else None


def run_host(value: str | None) -> int:
    """Show or set the bind address. ``local`` = 127.0.0.1 (default),
    ``tailscale`` = this machine's tailnet address (the phone path — the
    tailnet's device auth + encryption is the boundary). A raw IP is
    accepted for unusual setups; a public interface is on the operator."""
    config = chat_config()
    if value is None:
        _emit({"ok": True, "host": config.get("host", "127.0.0.1"),
               "url": daemon_url(config)})
        return 0
    value = value.strip().lower()
    if value == "local":
        host = "127.0.0.1"
    elif value == "tailscale":
        host = _tailscale_ip()
        if not host:
            _emit({"ok": False,
                   "reason": "no tailscale interface found — is tailscaled up? "
                             "(pass the 100.x address directly if you know it)"})
            return 1
    else:
        import re
        if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", value):
            _emit({"ok": False, "reason": "pass local, tailscale, or an IPv4 address"})
            return 1
        host = value
    result = apply_setting("host", host)
    if result.get("ok") and host != "127.0.0.1":
        result["url"] = daemon_url(chat_config())
        result["warning"] = (f"every device that can reach {host}:{chat_config().get('port')} "
                             "has full Board access — keep this on the tailnet, "
                             "never a public interface")
    _emit(result)
    return 0 if result.get("ok") else 1


def run_updates(value: str | None) -> int:
    if value is None:
        _emit({"ok": True,
               "updates": "on" if chat_config().get("updates", True) else "off"})
        return 0
    result = apply_setting("updates", value == "on")
    if result.get("ok"):
        result["note_scope"] = ("applies to new conversations — existing "
                                "sessions keep the rules they were born with")
    _emit(result)
    return 0 if result.get("ok") else 1


def apply_setting(key: str, value: Any) -> dict[str, Any]:
    """The dashboard's write seam — mutate ONE rail option and bounce the
    service if it's running. Pairs with :func:`status_payload` (the read
    seam), the same two-function contract as ``rail_slack``. Settable:
    mode (approve|skip), model (str, '' = account default), port (int),
    updates / ack_posts / midflight_relay (bool)."""
    config = chat_config()
    if not config.get("firms_root"):
        return {"ok": False, "reason": "rail not configured — run: cadre chat setup"}
    if key == "mode":
        if value not in ("approve", "skip"):
            return {"ok": False, "reason": "mode must be approve or skip"}
        config["mode"] = value
    elif key == "model":
        config["model"] = str(value or "").strip()
    elif key == "port":
        try:
            config["port"] = int(value)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "port must be a number"}
    elif key == "host":
        config["host"] = str(value).strip()
    elif key == "link_url":
        # what browsers should open (e.g. http://firm.chat behind a proxy);
        # empty = the daemon's own url
        config["link_url"] = str(value or "").strip()
    elif key == "dash_url":
        # where the boardroom dashboard lives (e.g. http://firm.dash);
        # firm badges and the switcher link into it
        config["dash_url"] = (str(value or "").strip()
                              or "http://127.0.0.1:8484")
    elif key in ("updates", "ack_posts", "midflight_relay"):
        config[key] = bool(value)
    else:
        return {"ok": False, "reason": f"unknown rail setting {key!r}"}
    save_config(PROVIDER, config)
    result: dict[str, Any] = {"ok": True, key: config[key]}
    _restart_if_active(result)
    return result


def status_payload() -> dict[str, Any]:
    """The rail's state as one dict — shared by `cadre chat status` and the
    boardroom dashboard's System panel (import this, don't re-derive)."""
    config = chat_config()
    state = _sched().status(_UNIT_NAME).get("state", "unknown")
    entry: dict[str, Any] = {
        "ok": True,
        "service": state,
        "configured": bool(config.get("firms_root")),
        "url": daemon_url(config),
        "link_url": str(config.get("link_url") or "") or daemon_url(config),
        "mode": config.get("mode", ""),
        "model": config.get("model") or "",
        "ack_posts": bool(config.get("ack_posts", True)),
        "updates": bool(config.get("updates", True)),
        "firms_root": config.get("firms_root", ""),
        "conversations": len(load_threads(PROVIDER)),
    }
    health = provider_dir(PROVIDER) / "health.json"
    if health.exists():
        try:
            last = json.loads(health.read_text()).get("last_activity")
            entry["last_activity_age_sec"] = int(time.time() - float(last))
        except (OSError, ValueError, TypeError):
            pass
    return entry


def run_status() -> int:
    _emit(status_payload())
    return 0
