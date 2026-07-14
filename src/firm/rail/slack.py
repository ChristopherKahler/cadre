"""The Slack rail — a Socket Mode daemon that turns a channel into the boardroom.

Flow per message (allowlisted user, configured channel):

1. React 👀, take the thread's turn lock (one turn in flight per thread).
2. Fresh thread → spawn ``claude -p "/boardroom …"`` headless at the firms
   root (the Co-Board's boot directory — never inside a firm, that's the
   member seat). Reply → ``--resume <session_id>`` from the thread map.
3. Post the session's answer into the thread; 👀 becomes ✅ (or ❌ + reason —
   never silent, never fake-green).

Permission posture is the Board's choice at setup (``mode`` in config):

* ``approve`` (default) — ``--permission-prompt-tool`` routes every
  permission ask to :mod:`firm.rail.approve_mcp`, which posts 👍/👎 into the
  thread and waits. Governance from a phone.
* ``skip`` — ``--dangerously-skip-permissions``, the same trust as a terminal
  the operator opened themself (the ``dashboard/launch.py`` summon posture).

The turn machinery itself (compose → spawn → stream-tap → steer) lives in
:mod:`firm.rail.turns`, shared with every other rail — this module owns only
the Slack transport. Socket Mode keeps every connection outbound (no public
URL, WSL2-safe); ``slack_sdk``'s builtin client is imported lazily inside
:func:`run_serve` so everything testable here stays stdlib-importable.
"""

from __future__ import annotations

import json
import os
import re
import shutil       # noqa: F401  (monkeypatch surface for tests — patches globally)
import subprocess   # noqa: F401  (monkeypatch surface for tests — patches globally)
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from firm.rail import (
    chunk_text,
    load_config,
    load_threads,
    prune_threads,
    provider_dir,
    save_threads,
    slack_call,
)
from firm.rail import turns
from firm.rail.turns import (   # noqa: F401  (re-exports — the rail's public seam)
    APPROVE_TOOL,
    TurnResult,
    build_cmd,
    find_base,
    find_claude,
    parse_scope,
    parse_stream,
)

PROVIDER = "slack"
BOT_TOKEN_KEY = "CADRE_SLACK_BOT_TOKEN"
APP_TOKEN_KEY = "CADRE_SLACK_APP_TOKEN"


def _log(message: str) -> None:
    """Narrate to stdout — the serve terminal and journald are the Board's
    window into a daemon that otherwise works in total silence."""
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def resolve_tokens(firms_root: str | Path) -> tuple[str | None, str | None]:
    """(bot xoxb, app xapp) — process env wins, then the global-tier vault."""
    env_bot = os.environ.get(BOT_TOKEN_KEY)
    env_app = os.environ.get(APP_TOKEN_KEY)
    if env_bot and env_app:
        return env_bot, env_app
    vault: dict[str, str] = {}
    try:
        from firm.secrets.provider import resolve_provider
        vault = resolve_provider().resolve(Path(firms_root))
    except Exception:
        pass   # vault is additive — an empty resolve reports honest None below
    return env_bot or vault.get(BOT_TOKEN_KEY), env_app or vault.get(APP_TOKEN_KEY)


# ---------------------------------------------------------------------------
# Turn composition — Slack's surface parameters over the shared core
# ---------------------------------------------------------------------------

def say_command() -> str:
    """The absolute ``cadre slack say`` invocation for THIS install — baked
    into steer messages so the receiving session needs zero PATH luck."""
    return f"{Path(sys.executable).parent / 'cadre'} slack say"


def rail_protocol() -> str:
    return turns.rail_protocol(surface="Slack", say_cmd=say_command())


def compose_prompt(text: str, *, resumed: bool, updates: bool = True) -> str:
    return turns.compose_prompt(text, resumed=resumed, updates=updates,
                                surface="Slack", say_cmd=say_command())


def write_turn_mcp_config(
    path: Path,
    *,
    channel: str,
    thread_ts: str,
    approvers: list[str],
    approve_timeout_sec: int,
) -> str:
    """The approve-mode ``--mcp-config``. No token in the file — approve_mcp
    inherits ``CADRE_SLACK_BOT_TOKEN`` from the daemon's process env through
    claude. Only routing lives here."""
    return turns.write_turn_mcp_config(path, env={
        "CADRE_RAIL_PROVIDER": "slack",
        "CADRE_RAIL_CHANNEL": channel,
        "CADRE_RAIL_THREAD_TS": thread_ts,
        "CADRE_RAIL_APPROVERS": ",".join(approvers),
        "CADRE_RAIL_APPROVE_TIMEOUT": str(approve_timeout_sec),
    })


def relay_steer(session_id: str, text: str) -> bool:
    """Steer a live turn — see :func:`firm.rail.turns.relay_steer`."""
    return turns.relay_steer(session_id, text,
                             from_name="slack-rail",
                             slug_prefix="slack-steer",
                             say_cmd=say_command()) is not None


def run_turn(
    config: dict[str, Any],
    *,
    text: str,
    thread_ts: str,
    resume: str | None,
    bot_token: str,
    claude_bin: str,
    on_session: Callable[[str], None] | None = None,
) -> TurnResult:
    """One board turn: compose → spawn at the firms root → stream-parse.

    *on_session* fires the moment the child announces its session id (the
    init event, seconds in) — the daemon records it immediately so a reply
    arriving MID-turn can be steered into the live session instead of
    waiting out a 20-minute brief.
    """
    firms_root = config["firms_root"]
    prompt = compose_prompt(text, resumed=bool(resume),
                            updates=bool(config.get("updates", True)))
    mcp_config: str | None = None
    if config.get("mode") != "skip":
        mcp_config = write_turn_mcp_config(
            provider_dir(PROVIDER) / "turns" / f"{thread_ts}.mcp.json",
            channel=config["channel_id"],
            thread_ts=thread_ts,
            approvers=list(config.get("allowlist", [])),
            approve_timeout_sec=int(config.get("approve_timeout_sec", 300)),
        )
    cmd = build_cmd(
        claude_bin,
        mode=config.get("mode", "approve"),
        prompt=prompt,
        resume=resume,
        mcp_config=mcp_config,
        full_load=bool(config.get("full_load")),
        model=str(config.get("model") or "") or None,
    )
    env = dict(os.environ)
    env[BOT_TOKEN_KEY] = bot_token   # approve_mcp inherits this through claude
    # Thread routing for `cadre slack say` — the session's own voice into
    # its thread, mid-turn (progress notes, answers to steered messages).
    env["CADRE_RAIL_CHANNEL"] = str(config.get("channel_id", ""))
    env["CADRE_RAIL_THREAD_TS"] = thread_ts
    return turns.spawn_turn(
        cmd,
        cwd=firms_root,
        env=env,
        timeout_sec=int(config.get("turn_timeout_sec", 1800)),
        on_session=on_session,
    )


def to_mrkdwn(text: str) -> str:
    """Light markdown → Slack mrkdwn: bold markers and headings only.

    Deliberately minimal — a wrong conversion reads worse than none.
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    return re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)


# ---------------------------------------------------------------------------
# The daemon
# ---------------------------------------------------------------------------

class SlackRail:
    """Event filter, thread map, and turn dispatch. Transport-agnostic:
    :meth:`handle_event` takes a plain Slack event dict, so tests drive it
    without a socket and :func:`run_serve` wires the real one."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        bot_token: str,
        claude_bin: str,
        bot_user_id: str = "",
        turn_runner: Callable[..., TurnResult] = run_turn,
        max_workers: int = 4,
    ) -> None:
        self.config = config
        self.bot_token = bot_token
        self.claude_bin = claude_bin
        self.bot_user_id = bot_user_id
        self._turn_runner = turn_runner
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="rail-turn")
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._threads_guard = threading.Lock()
        self.threads = prune_threads(load_threads(PROVIDER))

    # -- filtering ---------------------------------------------------------

    def accepts(self, event: dict[str, Any]) -> bool:
        """The structural allowlist. Everything rejected here never spawns."""
        if event.get("type") != "message":
            return False
        if event.get("subtype"):          # edits, joins, bot_message, …
            return False
        if event.get("bot_id"):           # no self-loops, no other bots
            return False
        user = event.get("user", "")
        if self.bot_user_id and user == self.bot_user_id:
            return False
        if event.get("channel") != self.config.get("channel_id"):
            return False
        if user not in self.config.get("allowlist", []):
            return False
        return bool((event.get("text") or "").strip())

    def handle_event(self, event: dict[str, Any]) -> bool:
        """Dispatch one event to a worker. Returns whether it was accepted."""
        if not self.accepts(event):
            if (event.get("type") == "message"
                    and event.get("channel") == self.config.get("channel_id")
                    and not event.get("bot_id")):
                why = ("subtype " + event["subtype"] if event.get("subtype")
                       else "user not allowlisted" if event.get("user")
                       not in self.config.get("allowlist", []) else "empty text")
                _log(f"✗ ignored message in board channel ({why})")
            return False
        text = event["text"].strip()
        ts = event["ts"]
        thread_ts = event.get("thread_ts") or ts
        kind = "reply" if event.get("thread_ts") else "new thread"
        _log(f"▶ accepted {kind} ({len(text)} chars) — thread {thread_ts}")
        self._pool.submit(self._work, text, ts, thread_ts)
        return True

    # -- the turn ----------------------------------------------------------

    def _lock_for(self, thread_ts: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(thread_ts, threading.Lock())

    def _react(self, ts: str, add: str | None = None, remove: str | None = None) -> None:
        # Reactions are UX, not truth — failures (already_reacted, …) are ignored.
        channel = self.config["channel_id"]
        if remove:
            slack_call("reactions.remove", self.bot_token,
                       channel=channel, timestamp=ts, name=remove)
        if add:
            slack_call("reactions.add", self.bot_token,
                       channel=channel, timestamp=ts, name=add)

    def _post(self, thread_ts: str, text: str) -> None:
        for chunk in chunk_text(to_mrkdwn(text)) or ["(empty response)"]:
            slack_call("chat.postMessage", self.bot_token,
                       channel=self.config["channel_id"],
                       thread_ts=thread_ts, text=chunk)

    def _note_session(self, thread_ts: str, session_id: str) -> None:
        """Record a turn's session the moment it announces itself — this is
        what lets a mid-turn reply find the LIVE session to relay into."""
        with self._threads_guard:
            self.threads[thread_ts] = {"session_id": session_id,
                                       "status": "running",
                                       "last_turn": time.time()}
            save_threads(PROVIDER, self.threads)
        _log(f"  session {session_id[:8]}… announced — thread {thread_ts}")

    def _work(self, text: str, ts: str, thread_ts: str) -> None:
        lock = self._lock_for(thread_ts)
        if not lock.acquire(blocking=False):
            entry = self.threads.get(thread_ts) or {}
            live = (entry.get("session_id")
                    if entry.get("status") == "running" else None)
            if live and self.config.get("midflight_relay", True) \
                    and relay_steer(live, text):
                # Delivered INTO the live turn via its hooks — its answer
                # folds this in. 📨 = steered, not queued.
                _log(f"📨 relayed mid-flight into session {live[:8]}…")
                self._react(ts, add="incoming_envelope")
                return
            _log(f"🕐 queued behind the in-flight turn — thread {thread_ts}")
            self._react(ts, add="hourglass_flowing_sand")
            lock.acquire()   # queue behind the in-flight turn
            self._react(ts, remove="hourglass_flowing_sand")
        started = time.monotonic()
        try:
            self._react(ts, add="eyes")
            entry = self.threads.get(thread_ts) or {}
            resume = entry.get("session_id")
            if self.config.get("ack_posts", True):
                # The board deserves an instant receipt — a first brief can
                # take minutes and silence reads as failure.
                if resume:
                    ack = "_⚙️ resuming the session…_"
                else:
                    scope, _ = parse_scope(text)
                    seat = f"`{scope}` boardroom" if scope else "portfolio boardroom"
                    ack = (f"_⚙️ on it — opening a {seat} session. The first "
                           "brief can take a few minutes; the answer lands "
                           "in this thread._")
                self._post(thread_ts, ack)
            on_session = lambda sid: self._note_session(thread_ts, sid)  # noqa: E731
            result = self._turn_runner(
                self.config,
                text=text,
                thread_ts=thread_ts,
                resume=resume,
                bot_token=self.bot_token,
                claude_bin=self.claude_bin,
                on_session=on_session,
            )
            if resume and not result.ok and not result.session_id:
                # The resume target is gone (pruned elsewhere / foreign
                # machine) — open a fresh session and say so, don't fake it.
                result = self._turn_runner(
                    self.config, text=text, thread_ts=thread_ts, resume=None,
                    bot_token=self.bot_token, claude_bin=self.claude_bin,
                    on_session=on_session,
                )
                if result.ok:
                    result.text = ("_previous session was gone — this is a "
                                   "fresh one._\n\n" + result.text)
            with self._threads_guard:
                sid = result.session_id or (
                    (self.threads.get(thread_ts) or {}).get("session_id"))
                if sid:
                    self.threads[thread_ts] = {
                        "session_id": sid,
                        "status": "idle",
                        "last_turn": time.time(),
                    }
                    save_threads(PROVIDER, self.threads)
            elapsed = int(time.monotonic() - started)
            if result.ok:
                self._post(thread_ts, result.text)
                self._react(ts, add="white_check_mark", remove="eyes")
                _log(f"✓ turn done in {elapsed}s — {len(result.text)} chars "
                     f"posted to thread {thread_ts}")
            else:
                reason = result.detail or "turn failed"
                self._post(thread_ts, f":x: {reason}" +
                           (f"\n\n{result.text}" if result.text else ""))
                self._react(ts, add="x", remove="eyes")
                _log(f"✗ turn failed in {elapsed}s — {reason}")
        except Exception as exc:   # a broken turn must not kill the daemon
            self._post(thread_ts, f":x: rail error: {exc}")
            self._react(ts, add="x", remove="eyes")
            _log(f"✗ rail error: {exc}")
        finally:
            self._touch_health()
            lock.release()

    def _touch_health(self) -> None:
        try:
            (provider_dir(PROVIDER) / "health.json").write_text(
                json.dumps({"last_activity": time.time()}), encoding="utf-8")
        except OSError:
            pass


def run_serve() -> int:
    """Foreground daemon — config + vault + Socket Mode, blocks until killed."""
    config = load_config(PROVIDER)
    missing = [k for k in ("channel_id", "allowlist", "firms_root") if not config.get(k)]
    if missing:
        print(f"rail not configured (missing {', '.join(missing)}) — run: cadre slack setup",
              file=sys.stderr)
        return 1
    bot_token, app_token = resolve_tokens(config["firms_root"])
    if not bot_token or not app_token:
        print(f"tokens not found — vault keys {BOT_TOKEN_KEY} / {APP_TOKEN_KEY} "
              "(cadre slack setup stores them)", file=sys.stderr)
        return 1
    claude_bin = find_claude()
    if not claude_bin:
        print("claude binary not found — set CADRE_CLAUDE_BIN", file=sys.stderr)
        return 1

    auth = slack_call("auth.test", bot_token)
    if not auth.get("ok"):
        print(f"auth.test failed: {auth.get('error')}", file=sys.stderr)
        return 1

    # The firms root is the Co-Board's boot directory — make sure its shipped
    # CLAUDE.md is laid before the first session boots there.
    from firm.dashboard.launch import ensure_boardroom_claude
    ensure_boardroom_claude(config["firms_root"])

    rail = SlackRail(config, bot_token=bot_token, claude_bin=claude_bin,
                     bot_user_id=str(auth.get("user_id", "")))

    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse
    from slack_sdk.web import WebClient

    client = SocketModeClient(app_token=app_token,
                              web_client=WebClient(token=bot_token))

    def _listener(smc: SocketModeClient, req: SocketModeRequest) -> None:
        if req.type != "events_api":
            return
        # Ack inside Slack's 3s window BEFORE any work.
        smc.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        rail.handle_event(req.payload.get("event", {}) or {})

    client.socket_mode_request_listeners.append(_listener)
    client.connect()
    print(f"cadre slack rail up — channel {config['channel_id']}, "
          f"mode {config.get('mode')}, firms root {config['firms_root']}")
    threading.Event().wait()   # serve until systemd (or ^C) stops us
    return 0
