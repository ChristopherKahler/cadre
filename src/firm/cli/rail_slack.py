"""``cadre slack`` — the Slack rail's operator surface.

The licensee path, end to end::

    cadre slack manifest   # print the app manifest → paste at api.slack.com/apps
    cadre slack setup      # tokens → vault, pick channel, pair, pick mode
    cadre slack test       # round-trip a hello into the channel
    cadre slack enable     # own systemd user unit (NOT hub-embedded), Restart=on-failure
    cadre slack status     # unit state + config + thread map + last activity

Setup is deliberately conversational — it is the licensee's first touch of
the rail, and every failure names the exact remediation. Tokens are stored
via the secrets provider at the GLOBAL tier (operator-level, spans firms);
the systemd unit carries no secret, only ``CADRE_CLAUDE_BIN`` captured at
enable time (the heartbeat idiom — systemd's PATH can't resolve nvm).
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from firm.rail import load_config, load_threads, provider_dir, save_config, slack_call
from firm.rail.slack import (
    APP_TOKEN_KEY,
    BOT_TOKEN_KEY,
    PROVIDER,
    find_claude,
    resolve_tokens,
)

_UNIT_NAME = "cadre-rail-slack"
_MANIFEST = Path(__file__).resolve().parent.parent / "rail" / "slack-app-manifest.json"


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
# manifest
# ---------------------------------------------------------------------------

def run_manifest() -> int:
    """Print the Slack app manifest — the one-paste app creation."""
    print(_MANIFEST.read_text(encoding="utf-8"))
    print(
        "\n# Create the app: https://api.slack.com/apps → Create New App → "
        "From a manifest → paste the JSON above.\n"
        "# Then: Settings → Socket Mode → Enable → create an app-level token "
        "with connections:write (xapp-…),\n"
        "# and Install App to workspace → copy the Bot User OAuth Token "
        "(xoxb-…). Feed both to: cadre slack setup",
        file=sys.stderr,
    )
    return 0


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def _prompt_token(label: str, prefix: str) -> str | None:
    for _ in range(3):
        value = getpass.getpass(f"{label} ({prefix}…): ").strip()
        if value.startswith(prefix):
            return value
        print(f"  that doesn't look like a {prefix} token — try again")
    return None


def _pick_channel(bot_token: str, bot_name: str) -> tuple[str, str] | None:
    result = slack_call("conversations.list", bot_token,
                        types="public_channel,private_channel",
                        exclude_archived=True, limit=200)
    if not result.get("ok"):
        print(f"could not list channels: {result.get('error')}")
        return None
    channels = sorted(result.get("channels", []), key=lambda c: c.get("name", ""))
    for i, ch in enumerate(channels, 1):
        vis = "private" if ch.get("is_private") else "public"
        print(f"  {i:3d}. #{ch.get('name')}  ({vis})")
    # Slack only reveals private channels the bot is already a member of —
    # the recommended private board channel is invisible until invited.
    print(f"\n  Missing your channel? Private channels appear only AFTER you "
          f"type `/invite @{bot_name}` in them.\n"
          "  Invite the bot and type `r` to refresh, or paste the channel ID "
          "(Slack: channel name → scroll down → Channel ID).")
    while True:
        raw = input("Board channel number (or ID / r): ").strip()
        if raw.lower() == "r":
            return _pick_channel(bot_token, bot_name)
        if raw.isdigit() and channels and 1 <= int(raw) <= len(channels):
            ch = channels[int(raw) - 1]
            return str(ch["id"]), str(ch.get("name", ""))
        if raw[:1].upper() in ("C", "G") and raw.isalnum() and len(raw) > 8:
            info = slack_call("conversations.info", bot_token, channel=raw)
            if info.get("ok"):
                ch = info.get("channel", {})
                return str(ch["id"]), str(ch.get("name", raw))
            print(f"  Slack can't see {raw} from this bot "
                  f"({info.get('error')}) — /invite @{bot_name} there first, "
                  "then retry")
            continue
        hint = f"pick 1-{len(channels)}, " if channels else ""
        print(f"  {hint}paste a channel ID, or r to refresh")


def _ensure_membership(bot_token: str, channel_id: str, channel_name: str,
                       bot_name: str) -> bool:
    info = slack_call("conversations.info", bot_token, channel=channel_id)
    channel = info.get("channel", {}) if info.get("ok") else {}
    if channel.get("is_member"):
        return True
    if not channel.get("is_private"):
        joined = slack_call("conversations.join", bot_token, channel=channel_id)
        if joined.get("ok"):
            return True
    input(f"Invite the bot: type `/invite @{bot_name}` in #{channel_name}, "
          "then press Enter here… ")
    info = slack_call("conversations.info", bot_token, channel=channel_id)
    return bool(info.get("ok") and info.get("channel", {}).get("is_member"))


def _pair_operator(bot_token: str, channel_id: str, *, timeout_sec: int = 120) -> str | None:
    """Capture the operator's user id from the next message in the channel —
    the same pairing move as the Telegram plugin, no member-ID spelunking."""
    print("Pairing: post any message in the board channel now "
          f"(watching for {timeout_sec}s)…")
    started = time.time()
    oldest = f"{started:.6f}"
    while time.time() - started < timeout_sec:
        result = slack_call("conversations.history", bot_token,
                            channel=channel_id, oldest=oldest, limit=5)
        for msg in result.get("messages", []) if result.get("ok") else []:
            user = msg.get("user")
            if user and not msg.get("bot_id"):
                text = (msg.get("text") or "")[:40]
                confirm = input(f'Pair user {user} ("{text}")? [Y/n] ').strip().lower()
                if confirm in ("", "y", "yes"):
                    return str(user)
                oldest = msg["ts"]
        time.sleep(2)
    print("no message seen — pairing timed out")
    return None


def run_setup(firms_root: Path) -> int:
    firms_root = firms_root.expanduser().resolve()
    print("Cadre Slack rail setup — the board channel that runs your firms.\n"
          "No app yet? `cadre slack manifest` prints the one-paste manifest.\n")

    # A re-run (new channel, rotated pairing, changed mode) shouldn't cost
    # the operator another trip to api.slack.com — reuse vaulted tokens.
    app_token: str | None = None
    bot_token: str | None = None
    stored_bot, stored_app = resolve_tokens(firms_root)
    if stored_bot and stored_app:
        reuse = input("Found stored tokens in the vault — reuse them? [Y/n] ").strip().lower()
        if reuse in ("", "y", "yes"):
            bot_token, app_token = stored_bot, stored_app
    if not app_token or not bot_token:
        app_token = _prompt_token("App-level token", "xapp-")
        bot_token = _prompt_token("Bot user OAuth token", "xoxb-") if app_token else None
    if not app_token or not bot_token:
        print("setup aborted — tokens are required")
        return 1

    auth = slack_call("auth.test", bot_token)
    if not auth.get("ok"):
        print(f"bot token rejected by Slack: {auth.get('error')}")
        return 1
    print(f"✓ bot verified — {auth.get('user')} in {auth.get('team')}")
    socket = slack_call("apps.connections.open", app_token)
    if not socket.get("ok"):
        print(f"app token rejected ({socket.get('error')}) — it needs the "
              "connections:write scope from Socket Mode setup")
        return 1
    print("✓ socket mode token verified")

    from firm.secrets.provider import GLOBAL_TIER, resolve_provider
    provider = resolve_provider()
    provider.set(firms_root, BOT_TOKEN_KEY, bot_token, GLOBAL_TIER)
    provider.set(firms_root, APP_TOKEN_KEY, app_token, GLOBAL_TIER)
    print(f"✓ tokens stored in the {provider.name} vault (global tier)")

    bot_name = str(auth.get("user") or "cadre-board")
    picked = _pick_channel(bot_token, bot_name)
    if not picked:
        return 1
    channel_id, channel_name = picked
    if not _ensure_membership(bot_token, channel_id, channel_name, bot_name):
        print("bot still isn't in the channel — invite it and re-run setup")
        return 1
    print(f"✓ bot is in #{channel_name}")

    operator = _pair_operator(bot_token, channel_id)
    if not operator:
        return 1
    print(f"✓ paired — only {operator} can drive the rail or approve actions")

    mode = ""
    while mode not in ("approve", "skip"):
        mode = (input("Permission mode — approve (👍/👎 every action, default) "
                      "or skip (full trust)? [approve/skip] ").strip().lower()
                or "approve")

    config = load_config(PROVIDER)
    config.update({
        "channel_id": channel_id,
        "allowlist": [operator],
        "mode": mode,
        "firms_root": str(firms_root),
    })
    path = save_config(PROVIDER, config)
    print(f"✓ config written — {path}\n\nNext:\n"
          "  cadre slack test     # round-trip check\n"
          "  cadre slack enable   # run it as a service\n"
          f"Then message #{channel_name}. `@<firm-id> …` scopes a new thread "
          "to one firm.")
    return 0


# ---------------------------------------------------------------------------
# serve / test
# ---------------------------------------------------------------------------

def run_serve() -> int:
    from firm.rail.slack import run_serve as serve
    return serve()


def run_test() -> int:
    config = load_config(PROVIDER)
    if not config.get("channel_id"):
        _emit({"ok": False, "reason": "not configured — run: cadre slack setup"})
        return 1
    bot_token, _ = resolve_tokens(config.get("firms_root") or Path.cwd())
    if not bot_token:
        _emit({"ok": False, "reason": f"{BOT_TOKEN_KEY} not resolvable from vault/env"})
        return 1
    result = slack_call("chat.postMessage", bot_token,
                        channel=config["channel_id"],
                        text=":white_check_mark: cadre slack rail — wiring test")
    if not result.get("ok"):
        _emit({"ok": False, "reason": f"postMessage failed: {result.get('error')}"})
        return 1
    _emit({"ok": True, "channel": config["channel_id"],
           "mode": config.get("mode"), "claude_bin": find_claude()})
    return 0


# ---------------------------------------------------------------------------
# enable / disable / status — the heartbeat systemd idiom, service not timer
# ---------------------------------------------------------------------------

def run_enable(*, unit_dir: Path | None = None) -> int:
    config = load_config(PROVIDER)
    missing = [k for k in ("channel_id", "allowlist", "firms_root") if not config.get(k)]
    if missing:
        _emit({"ok": False, "reason": f"not configured ({', '.join(missing)} "
                                      "missing) — run: cadre slack setup"})
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
            description="Cadre Slack rail — the Co-Board board channel daemon",
            workdir=Path(config["firms_root"]),
            env={"CADRE_CLAUDE_BIN": claude_bin},
            argv=[sys.executable, "-m", "firm", "slack", "serve"],
        )
    except SchedulerError as exc:
        _emit({"ok": False, "reason": str(exc)})
        return 1
    _emit({"ok": True, "unit": installed.get("unit", _UNIT_NAME),
           "scheduler": sched.name, "unit_dir": installed.get("unit_dir", ""),
           "claude_bin": claude_bin, "mode": config.get("mode")})
    return 0


def run_disable(*, unit_dir: Path | None = None) -> int:
    sched = _sched(unit_dir)
    if not sched.status(_UNIT_NAME).get("installed"):
        _emit({"ok": False, "reason": "rail service is not installed"})
        return 1
    out = sched.remove(_UNIT_NAME)
    _emit({"ok": True, "removed": out.get("removed") or _UNIT_NAME})
    return 0


def run_mode(mode: str | None) -> int:
    """Show or flip the permission posture. A flip restarts the service if
    it's running — the daemon reads config at start, never mid-flight."""
    config = load_config(PROVIDER)
    if not config.get("channel_id"):
        _emit({"ok": False, "reason": "not configured — run: cadre slack setup"})
        return 1
    if mode is None:
        _emit({"ok": True, "mode": config.get("mode", "approve")})
        return 0
    config["mode"] = mode
    save_config(PROVIDER, config)
    result: dict[str, Any] = {"ok": True, "mode": mode}
    _restart_if_active(result)
    _emit(result)
    return 0


def run_say(text: str, thread: str | None) -> int:
    """Post *text* into the board channel — the voice a running boardroom
    session uses to answer mid-turn (its spawn env carries the thread)."""
    config = load_config(PROVIDER)
    channel = os.environ.get("CADRE_RAIL_CHANNEL") or config.get("channel_id")
    if not channel:
        _emit({"ok": False, "reason": "no channel — run setup or set CADRE_RAIL_CHANNEL"})
        return 1
    thread_ts = thread or os.environ.get("CADRE_RAIL_THREAD_TS") or None
    bot_token, _ = resolve_tokens(config.get("firms_root") or Path.cwd())
    if not bot_token:
        _emit({"ok": False, "reason": f"{BOT_TOKEN_KEY} not resolvable from env/vault"})
        return 1
    result = slack_call("chat.postMessage", bot_token, channel=channel,
                        thread_ts=thread_ts, text=text)
    if not result.get("ok"):
        _emit({"ok": False, "reason": f"postMessage failed: {result.get('error')}"})
        return 1
    _emit({"ok": True, "channel": channel, "thread_ts": thread_ts or "(channel root)"})
    return 0


def _restart_if_active(result: dict[str, Any]) -> None:
    """Config edits apply at daemon start — bounce the service when it runs."""
    sched = _sched()
    if sched.status(_UNIT_NAME).get("state") == "active":
        ok, out = sched.restart(_UNIT_NAME)
        result["service"] = "restarted" if ok else f"restart failed: {out}"
    else:
        result["note"] = ("takes effect next daemon start — restart your "
                          "foreground `cadre slack serve` if one is running")


def run_model(model: str | None) -> int:
    """Show or set the board-turn model (e.g. opus, opus[1m], sonnet, a full
    model id). 'default' clears the override back to the account default."""
    config = load_config(PROVIDER)
    if not config.get("channel_id"):
        _emit({"ok": False, "reason": "not configured — run: cadre slack setup"})
        return 1
    if model is None:
        _emit({"ok": True, "model": config.get("model") or "(account default)"})
        return 0
    config["model"] = "" if model.strip().lower() == "default" else model.strip()
    save_config(PROVIDER, config)
    result: dict[str, Any] = {"ok": True,
                              "model": config["model"] or "(account default)"}
    _restart_if_active(result)
    _emit(result)
    return 0


def run_updates(value: str | None) -> int:
    """Show or toggle the in-turn proactive updates (the rail protocol).

    On = the session narrates into the thread at the load-bearing moments
    (turn accepted, plan change, before spend, still-working backstop).
    Off = quiet — one answer per turn, nothing between.
    """
    config = load_config(PROVIDER)
    if not config.get("channel_id"):
        _emit({"ok": False, "reason": "not configured — run: cadre slack setup"})
        return 1
    if value is None:
        _emit({"ok": True, "updates": "on" if config.get("updates", True) else "off"})
        return 0
    config["updates"] = value == "on"
    save_config(PROVIDER, config)
    result: dict[str, Any] = {"ok": True, "updates": value,
                              "note_scope": "applies to new threads — existing "
                                            "sessions keep the rules they were born with"}
    _restart_if_active(result)
    _emit(result)
    return 0


def apply_setting(key: str, value: Any) -> dict[str, Any]:
    """The dashboard's write seam — mutate ONE rail option and bounce the
    service if it's running. Pairs with :func:`status_payload` (the read
    seam) so the System page needs exactly two imports and zero rail
    knowledge. Settable: mode (approve|skip), model (str, '' = account
    default), updates / ack_posts / midflight_relay (bool)."""
    config = load_config(PROVIDER)
    if not config.get("channel_id"):
        return {"ok": False, "reason": "rail not configured — run: cadre slack setup"}
    if key == "mode":
        if value not in ("approve", "skip"):
            return {"ok": False, "reason": "mode must be approve or skip"}
        config["mode"] = value
    elif key == "model":
        config["model"] = str(value or "").strip()
    elif key in ("updates", "ack_posts", "midflight_relay"):
        config[key] = bool(value)
    else:
        return {"ok": False, "reason": f"unknown rail setting {key!r}"}
    save_config(PROVIDER, config)
    result: dict[str, Any] = {"ok": True, key: config[key]}
    _restart_if_active(result)
    return result


def status_payload() -> dict[str, Any]:
    """The rail's state as one dict — shared by `cadre slack status` and the
    boardroom dashboard's System panel (import this, don't re-derive)."""
    config = load_config(PROVIDER)
    state = _sched().status(_UNIT_NAME).get("state", "unknown")
    entry: dict[str, Any] = {
        "ok": True,
        "service": state,
        "configured": bool(config.get("channel_id")),
        "channel_id": config.get("channel_id", ""),
        "mode": config.get("mode", ""),
        "model": config.get("model") or "",
        "allowlist": config.get("allowlist", []),
        "ack_posts": bool(config.get("ack_posts", True)),
        "updates": bool(config.get("updates", True)),
        "firms_root": config.get("firms_root", ""),
        "threads": len(load_threads(PROVIDER)),
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
