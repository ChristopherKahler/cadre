"""The chat rail — cadre's own boardroom chat, no third-party surface.

A localhost HTTP daemon serving a single-page chat UI (``chat.html``) and a
small JSON API. Same backend flow as the Slack rail, provided by
:mod:`firm.rail.turns`: every conversation is a headless ``/boardroom``
session at the firms root, replies ``--resume`` it, a reply landing mid-turn
is steered into the live session via ``base relay task``, and approve mode
gates every action — here as an Allow/Deny card instead of a 👍/👎 reaction.

What this surface adds over Slack: the live turn is visible. The stream tap
that announces the session id also feeds an activity ticker (current tool /
latest reasoning line) over SSE, so a long brief never looks like a dead
daemon. Ticker lines are ephemeral by design — pushed, never persisted; the
conversation file holds only real messages.

Security boundary: the daemon binds ``127.0.0.1`` by default — the OS
session is the allowlist, the same trust as the operator's own terminal. No
token, no pairing. ``cadre chat host tailscale`` rebinds to the machine's
tailscale interface (100.64/10) so the operator's phone reaches the board
over the tailnet's own device auth + encryption; binding a public interface
is deliberately unsupported.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from firm.rail import (
    _write_private_json,
    load_config,
    load_threads,
    provider_dir,
    prune_threads,
    save_threads,
)
from firm.rail import turns
from firm.rail.turns import TurnResult, build_cmd, parse_scope

PROVIDER = "chat"
SURFACE = "cadre chat"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7787
_UI_FILE = Path(__file__).resolve().parent / "chat.html"


def _log(message: str) -> None:
    """Narrate to stdout — the serve terminal and journald are the Board's
    window into the daemon."""
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def chat_config() -> dict[str, Any]:
    """Provider config with the chat-only keys defaulted in."""
    config = load_config(PROVIDER)
    config.setdefault("host", DEFAULT_HOST)
    config.setdefault("port", DEFAULT_PORT)
    # where the boardroom dashboard lives — firm badges and the firm
    # switcher link into it (override to a friendly name, e.g. http://firm.dash)
    config.setdefault("dash_url", "http://127.0.0.1:8484")
    return config


def daemon_url(config: dict[str, Any]) -> str:
    return f"http://{config.get('host', DEFAULT_HOST)}:{config.get('port', DEFAULT_PORT)}"


def say_command() -> str:
    """The absolute ``cadre chat say`` invocation for THIS install — baked
    into steer messages so the receiving session needs zero PATH luck."""
    import sys
    return f"{Path(sys.executable).parent / 'cadre'} chat say"


def compose_prompt(text: str, *, resumed: bool, updates: bool = True) -> str:
    return turns.compose_prompt(text, resumed=resumed, updates=updates,
                                surface=SURFACE, say_cmd=say_command())


def relay_steer(session_id: str, text: str) -> str | None:
    """Steer a live turn; returns the relay task slug (the receipt handle)."""
    return turns.relay_steer(session_id, text,
                             from_name="chat-rail",
                             slug_prefix="chat-steer",
                             say_cmd=say_command())


# ---------------------------------------------------------------------------
# Firm awareness + context telemetry (derived from the turn's event stream)
# ---------------------------------------------------------------------------

_FIRM_NAME_CACHE: dict[str, str] = {}
_FIRM_PATH_RE = re.compile(r"(?:^|[/\s\"'`=])firms/([a-z0-9][a-z0-9_-]*)")


def firm_display_name(firms_root: str, slug: str) -> str:
    """The firm's display-friendly name from its own db (`firm.name`);
    falls back to the slug so a badge never blocks on a broken db."""
    if slug in _FIRM_NAME_CACHE:
        return _FIRM_NAME_CACHE[slug]
    name = slug
    db = Path(firms_root) / slug / ".firm" / "firm.db"
    if db.exists():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            row = con.execute("SELECT name FROM firm LIMIT 1").fetchone()
            con.close()
            if row and row[0]:
                name = str(row[0])
        except sqlite3.Error:
            pass
    _FIRM_NAME_CACHE[slug] = name
    return name


def firms_in_event(obj: dict[str, Any], firms_root: str) -> list[str]:
    """Firm slugs whose paths appear in one assistant event — the honest
    'what is this session actually touching' signal (real file paths in
    tool calls, not the session's claims). Only dirs that exist as firms
    count, so prose mentions never mint badges."""
    if obj.get("type") != "assistant":
        return []
    found: list[str] = []
    for slug in _FIRM_PATH_RE.findall(json.dumps(obj)):
        if slug not in found and (Path(firms_root) / slug / ".firm").exists():
            found.append(slug)
    return found


def context_window_for(model: str) -> int:
    """Token window the configured model runs with — [1m] models get the
    1M window, everything else the standard 200k."""
    return 1_000_000 if "[1m]" in (model or "") else 200_000


def list_firms(firms_root: str) -> list[dict[str, str]]:
    """Every firm under the root (a dir holding ``.firm/``), display-named —
    the chat UI's firm switcher and badge links ride this."""
    root = Path(firms_root)
    if not firms_root or not root.is_dir():
        return []
    firms = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / ".firm").is_dir():
            firms.append({"slug": child.name,
                          "name": firm_display_name(firms_root, child.name)})
    return firms


# ---------------------------------------------------------------------------
# Conversation store — the chat provider owns its history (Slack stored it
# for free). One JSON file per conversation, atomic 0600 writes.
# ---------------------------------------------------------------------------

def conversations_dir() -> Path:
    path = provider_dir(PROVIDER) / "conversations"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def new_conversation_id() -> str:
    return f"c{int(time.time() * 1000):x}{os.urandom(2).hex()}"


def load_conversation(conv_id: str) -> dict[str, Any] | None:
    path = conversations_dir() / f"{conv_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_conversation(conv: dict[str, Any]) -> None:
    _write_private_json(conversations_dir() / f"{conv['id']}.json", conv)


def list_conversations() -> list[dict[str, Any]]:
    """Newest-first metadata for the sidebar — never the full message bodies."""
    entries: list[dict[str, Any]] = []
    for path in conversations_dir().glob("c*.json"):
        try:
            conv = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        messages = conv.get("messages", [])
        entries.append({
            "id": conv.get("id", path.stem),
            "title": conv.get("title", ""),
            "created": conv.get("created", 0),
            "last_ts": messages[-1]["ts"] if messages else conv.get("created", 0),
            "message_count": len(messages),
        })
    return sorted(entries, key=lambda e: e["last_ts"], reverse=True)


# ---------------------------------------------------------------------------
# Event bus — one process-wide sequence the SSE clients follow
# ---------------------------------------------------------------------------

class EventBus:
    def __init__(self, maxlen: int = 500) -> None:
        self._cond = threading.Condition()
        self._events: deque[tuple[int, str, dict[str, Any]]] = deque(maxlen=maxlen)
        self.seq = 0

    def publish(self, kind: str, data: dict[str, Any]) -> None:
        with self._cond:
            self.seq += 1
            self._events.append((self.seq, kind, data))
            self._cond.notify_all()

    def wait_since(self, since: int, timeout: float = 20.0) -> list[tuple[int, str, dict[str, Any]]]:
        """Events newer than *since*; blocks up to *timeout* when none yet."""
        with self._cond:
            fresh = [e for e in self._events if e[0] > since]
            if fresh:
                return fresh
            self._cond.wait(timeout)
            return [e for e in self._events if e[0] > since]


# ---------------------------------------------------------------------------
# The turn — chat's surface parameters over the shared core
# ---------------------------------------------------------------------------

def run_turn(
    config: dict[str, Any],
    *,
    text: str,
    conv_id: str,
    resume: str | None,
    claude_bin: str,
    on_session: Callable[[str], None] | None = None,
    on_activity: Callable[[str], None] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> TurnResult:
    """One board turn: compose → spawn at the firms root → stream-tap."""
    firms_root = config["firms_root"]
    prompt = compose_prompt(text, resumed=bool(resume),
                            updates=bool(config.get("updates", True)))
    mcp_config: str | None = None
    if config.get("mode") != "skip":
        mcp_config = turns.write_turn_mcp_config(
            provider_dir(PROVIDER) / "turns" / f"{conv_id}.mcp.json",
            env={
                "CADRE_RAIL_PROVIDER": "chat",
                "CADRE_RAIL_CHAT_URL": daemon_url(config),
                "CADRE_RAIL_THREAD_TS": conv_id,
                "CADRE_RAIL_APPROVE_TIMEOUT": str(int(config.get("approve_timeout_sec", 300))),
            },
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
    # Explicit env for the child: inherit the daemon's, add the thread routing
    # `cadre chat say` needs for the session's mid-turn voice.
    env = dict(os.environ)
    env["CADRE_RAIL_PROVIDER"] = "chat"
    env["CADRE_RAIL_CHAT_URL"] = daemon_url(config)
    env["CADRE_RAIL_THREAD_TS"] = conv_id
    return turns.spawn_turn(
        cmd,
        cwd=firms_root,
        env=env,
        timeout_sec=int(config.get("turn_timeout_sec", 1800)),
        on_session=on_session,
        on_activity=on_activity,
        on_event=on_event,
    )


# ---------------------------------------------------------------------------
# The rail — store + dispatch + approvals, transport-agnostic (the HTTP
# handler calls these methods; tests drive them directly)
# ---------------------------------------------------------------------------

class ChatRail:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        claude_bin: str,
        turn_runner: Callable[..., TurnResult] = run_turn,
        max_workers: int = 4,
    ) -> None:
        self.config = config
        self.claude_bin = claude_bin
        self.bus = EventBus()
        self._turn_runner = turn_runner
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="rail-turn")
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._threads_guard = threading.Lock()
        self._store_guard = threading.Lock()
        self.threads = prune_threads(load_threads(PROVIDER))
        # Live turn state, authoritative for the UI: the threads map only
        # says "running" once the session announces itself (seconds in) —
        # this covers the whole turn span, dispatch to release.
        self.live: dict[str, str] = {}
        self.approvals: dict[str, dict[str, Any]] = {}
        self._approvals_guard = threading.Lock()

    # -- store -------------------------------------------------------------

    def _append(self, conv_id: str, role: str, text: str,
                kind: str = "chat", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._store_guard:
            conv = load_conversation(conv_id)
            if conv is None:
                raise KeyError(f"unknown conversation {conv_id}")
            message = {
                "id": len(conv["messages"]) + 1,
                "role": role,
                "kind": kind,
                "text": text,
                "ts": time.time(),
            }
            if extra:
                message.update(extra)
            conv["messages"].append(message)
            save_conversation(conv)
        self.bus.publish("message", {"conversation_id": conv_id, "message": message})
        return message

    def _update_message(self, conv_id: str, message_id: int,
                        patch: dict[str, Any]) -> None:
        with self._store_guard:
            conv = load_conversation(conv_id)
            if conv is None:
                return
            for message in conv["messages"]:
                if message["id"] == message_id:
                    message.update(patch)
                    break
            save_conversation(conv)

    def _set_status(self, conv_id: str, status: str) -> None:
        self.live[conv_id] = status
        self.bus.publish("status", {"conversation_id": conv_id, "status": status})

    def status_of(self, conv_id: str) -> str:
        return self.live.get(conv_id, "idle")

    def firm_badges(self, conv: dict[str, Any]) -> list[dict[str, str]]:
        firms_root = str(self.config.get("firms_root", ""))
        return [{"slug": slug, "name": firm_display_name(firms_root, slug)}
                for slug in conv.get("firms", [])]

    def _note_firms(self, conv_id: str, slugs: list[str]) -> None:
        """Append newly-touched firms to the conversation's working context —
        badges persist and only ever grow within a conversation."""
        with self._store_guard:
            conv = load_conversation(conv_id)
            if conv is None:
                return
            known = list(conv.get("firms", []))
            fresh = [s for s in slugs if s not in known]
            if not fresh:
                return
            conv["firms"] = known + fresh
            save_conversation(conv)
            badges = self.firm_badges(conv)
        self.bus.publish("firms", {"conversation_id": conv_id, "firms": badges})
        _log(f"🏛 firm context now {', '.join(b['slug'] for b in badges)} "
             f"— conversation {conv_id}")

    def _note_context(self, conv_id: str, usage: dict[str, Any]) -> None:
        """Record the turn's context footprint (prompt-side tokens: fresh +
        cache reads + cache writes) so the Board can see resumes closing in
        on the window."""
        tokens = 0
        for key in ("input_tokens", "cache_read_input_tokens",
                    "cache_creation_input_tokens"):
            try:
                tokens += int(usage.get(key) or 0)
            except (TypeError, ValueError):
                pass
        if tokens <= 0:
            return
        window = context_window_for(str(self.config.get("model") or ""))
        with self._store_guard:
            conv = load_conversation(conv_id)
            if conv is None:
                return
            conv["context_tokens"] = tokens
            conv["context_window"] = window
            save_conversation(conv)
        self.bus.publish("context", {"conversation_id": conv_id,
                                     "tokens": tokens, "window": window})

    # -- inbound -----------------------------------------------------------

    def post_operator_message(self, conv_id: str | None, text: str) -> dict[str, Any]:
        """The single inbound door — create/extend a conversation, dispatch
        the turn. Returns ``{"ok": True, "conversation_id": …}`` immediately;
        everything after is SSE."""
        text = text.strip()
        if not text:
            return {"ok": False, "reason": "empty message"}
        if conv_id is None:
            conv_id = new_conversation_id()
            scope, agenda = parse_scope(text)
            title = (agenda or text)[:64]
            if scope:
                title = f"@{scope} · {title}"
            conv = {"id": conv_id, "title": title,
                    "created": time.time(), "messages": []}
            save_conversation(conv)
            self.bus.publish("conversation", {
                "id": conv_id, "title": title, "created": conv["created"],
                "last_ts": conv["created"], "message_count": 0,
            })
            if scope and (Path(str(self.config.get("firms_root", "")))
                          / scope / ".firm").exists():
                # an @firm scope is the first badge, immediately
                self._note_firms(conv_id, [scope])
        elif load_conversation(conv_id) is None:
            return {"ok": False, "reason": f"unknown conversation {conv_id}"}
        self._append(conv_id, "operator", text)
        self._pool.submit(self._work, text, conv_id)
        return {"ok": True, "conversation_id": conv_id}

    def say(self, conv_id: str, text: str) -> dict[str, Any]:
        """The session's mid-turn voice (``cadre chat say``) — and any other
        service-layer post into a conversation."""
        if load_conversation(conv_id) is None:
            return {"ok": False, "reason": f"unknown conversation {conv_id}"}
        self._append(conv_id, "board", text, kind="say")
        return {"ok": True, "conversation_id": conv_id}

    # -- approvals ---------------------------------------------------------

    def create_approval(self, conv_id: str, tool_name: str,
                        tool_input: dict[str, Any], timeout_sec: int) -> dict[str, Any]:
        if load_conversation(conv_id) is None:
            return {"ok": False, "reason": f"unknown conversation {conv_id}"}
        request_id = f"a{int(time.time() * 1000):x}{os.urandom(2).hex()}"
        detail = json.dumps(tool_input, indent=2, sort_keys=True)
        if len(detail) > 600:
            detail = detail[:600] + "\n… (truncated)"
        with self._approvals_guard:
            self.approvals[request_id] = {
                "conversation_id": conv_id,
                "tool_name": tool_name,
                "verdict": None,
                "created": time.time(),
                "timeout_sec": timeout_sec,
            }
        message = self._append(
            conv_id, "board",
            f"Co-Board wants to run: {tool_name}",
            kind="approval",
            extra={"approval_id": request_id, "tool_name": tool_name,
                   "detail": detail, "timeout_sec": timeout_sec, "verdict": None},
        )
        with self._approvals_guard:
            self.approvals[request_id]["message_id"] = message["id"]
        _log(f"🛡 approval requested — {tool_name} (conversation {conv_id})")
        return {"ok": True, "id": request_id}

    def approval_state(self, request_id: str) -> dict[str, Any]:
        with self._approvals_guard:
            entry = self.approvals.get(request_id)
            if entry is None:
                return {"ok": False, "reason": "unknown approval"}
            return {"ok": True, "verdict": entry["verdict"]}

    def set_verdict(self, request_id: str, verdict: str) -> dict[str, Any]:
        if verdict not in ("allow", "deny", "timeout"):
            return {"ok": False, "reason": "verdict must be allow, deny, or timeout"}
        with self._approvals_guard:
            entry = self.approvals.get(request_id)
            if entry is None:
                return {"ok": False, "reason": "unknown approval"}
            if entry["verdict"] is None:
                entry["verdict"] = verdict
            verdict = entry["verdict"]   # first answer wins — no flip-flops
        conv_id = entry["conversation_id"]
        with self._store_guard:
            conv = load_conversation(conv_id)
            if conv is not None:
                for message in conv["messages"]:
                    if message.get("approval_id") == request_id:
                        message["verdict"] = verdict
                        break
                save_conversation(conv)
        self.bus.publish("approval", {"conversation_id": conv_id,
                                      "id": request_id, "verdict": verdict})
        _log(f"🛡 approval {request_id[:8]}… → {verdict}")
        return {"ok": True, "verdict": verdict}

    # -- the turn ----------------------------------------------------------

    def _lock_for(self, conv_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(conv_id, threading.Lock())

    def _note_session(self, conv_id: str, session_id: str) -> None:
        with self._threads_guard:
            self.threads[conv_id] = {"session_id": session_id,
                                     "status": "running",
                                     "last_turn": time.time()}
            save_threads(PROVIDER, self.threads)
        _log(f"  session {session_id[:8]}… announced — conversation {conv_id}")

    def _work(self, text: str, conv_id: str) -> None:
        lock = self._lock_for(conv_id)
        if not lock.acquire(blocking=False):
            entry = self.threads.get(conv_id) or {}
            live = (entry.get("session_id")
                    if entry.get("status") == "running" else None)
            slug = (relay_steer(live, text)
                    if live and self.config.get("midflight_relay", True) else None)
            if slug:
                _log(f"📨 relayed mid-flight into session {(live or '')[:8]}… ({slug})")
                message = self._append(
                    conv_id, "system",
                    "steered into the live turn — its answer folds this in",
                    kind="steer", extra={"receipt": "sent", "slug": slug})
                threading.Thread(
                    target=self._watch_receipt,
                    args=(conv_id, message["id"], slug),
                    daemon=True, name="steer-receipt",
                ).start()
                return
            _log(f"🕐 queued behind the in-flight turn — conversation {conv_id}")
            self._append(conv_id, "system", "queued behind the running turn",
                         kind="queued")
            lock.acquire()   # queue behind the in-flight turn
        started = time.monotonic()
        try:
            self._set_status(conv_id, "running")
            entry = self.threads.get(conv_id) or {}
            resume = entry.get("session_id")
            if self.config.get("ack_posts", True):
                if resume:
                    ack = "⚙️ resuming the session…"
                else:
                    scope, _ = parse_scope(text)
                    seat = f"{scope} boardroom" if scope else "portfolio boardroom"
                    ack = (f"⚙️ on it — opening a {seat} session. The first "
                           "brief can take a few minutes; watch the ticker.")
                self._append(conv_id, "system", ack, kind="ack")
            on_session = lambda sid: self._note_session(conv_id, sid)  # noqa: E731
            on_activity = lambda line: self.bus.publish(               # noqa: E731
                "activity", {"conversation_id": conv_id, "line": line})

            def on_event(obj: dict[str, Any]) -> None:
                slugs = firms_in_event(obj, str(self.config.get("firms_root", "")))
                if slugs:
                    self._note_firms(conv_id, slugs)
                if obj.get("type") == "result" and isinstance(obj.get("usage"), dict):
                    self._note_context(conv_id, obj["usage"])

            result = self._turn_runner(
                self.config,
                text=text,
                conv_id=conv_id,
                resume=resume,
                claude_bin=self.claude_bin,
                on_session=on_session,
                on_activity=on_activity,
                on_event=on_event,
            )
            if resume and not result.ok and not result.session_id:
                # The resume target is gone (pruned elsewhere / foreign
                # machine) — open a fresh session and say so, don't fake it.
                result = self._turn_runner(
                    self.config, text=text, conv_id=conv_id, resume=None,
                    claude_bin=self.claude_bin,
                    on_session=on_session, on_activity=on_activity,
                    on_event=on_event,
                )
                if result.ok:
                    result.text = ("previous session was gone — this is a "
                                   "fresh one.\n\n" + result.text)
            with self._threads_guard:
                sid = result.session_id or (
                    (self.threads.get(conv_id) or {}).get("session_id"))
                if sid:
                    self.threads[conv_id] = {
                        "session_id": sid,
                        "status": "idle",
                        "last_turn": time.time(),
                    }
                    save_threads(PROVIDER, self.threads)
            elapsed = int(time.monotonic() - started)
            if result.ok:
                self._append(conv_id, "board", result.text)
                _log(f"✓ turn done in {elapsed}s — {len(result.text)} chars "
                     f"→ conversation {conv_id}")
            else:
                reason = result.detail or "turn failed"
                self._append(conv_id, "system",
                             reason + (f"\n\n{result.text}" if result.text else ""),
                             kind="error")
                _log(f"✗ turn failed in {elapsed}s — {reason}")
        except Exception as exc:   # a broken turn must not kill the daemon
            try:
                self._append(conv_id, "system", f"rail error: {exc}", kind="error")
            except Exception:
                pass
            _log(f"✗ rail error: {exc}")
        finally:
            self._touch_health()
            self._set_status(conv_id, "idle")
            lock.release()

    def _watch_receipt(self, conv_id: str, message_id: int, slug: str,
                       *, poll_sec: float = 3.0, max_wait_sec: float = 1800.0) -> None:
        """The second check of the iMessage mechanic: poll the relay until the
        steer task reports delivered (fired inside the session's hooks — it's
        in their system messages now) or cleared (the session ran ``relay
        done``). Either way the Board can expect a reply. Gives up silently
        when the relay is unavailable or the window closes — the mark just
        stays at ✓, which is the honest state."""
        deadline = time.monotonic() + max_wait_sec
        while time.monotonic() < deadline:
            state = turns.relay_task_state(slug)
            if state is None:
                return
            if state in ("delivered", "cleared"):
                self._update_message(conv_id, message_id, {"receipt": "delivered"})
                self.bus.publish("receipt", {"conversation_id": conv_id,
                                             "message_id": message_id,
                                             "receipt": "delivered"})
                _log(f"✓✓ steer {slug} landed in the session")
                return
            time.sleep(poll_sec)

    def _touch_health(self) -> None:
        try:
            (provider_dir(PROVIDER) / "health.json").write_text(
                json.dumps({"last_activity": time.time()}), encoding="utf-8")
        except OSError:
            pass

    # -- read models ---------------------------------------------------------

    def state_payload(self) -> dict[str, Any]:
        conversations = list_conversations()
        for conv in conversations:
            conv["status"] = self.status_of(conv["id"])
        return {
            "ok": True,
            "conversations": conversations,
            "mode": self.config.get("mode", "approve"),
            "model": self.config.get("model") or "",
            "updates": bool(self.config.get("updates", True)),
            "firms_root": self.config.get("firms_root", ""),
            "dash_url": str(self.config.get("dash_url") or "http://127.0.0.1:8484"),
            "firms": list_firms(str(self.config.get("firms_root", ""))),
            "seq": self.bus.seq,
        }


# ---------------------------------------------------------------------------
# HTTP — stdlib ThreadingHTTPServer, JSON API + SSE + the single-page UI
# ---------------------------------------------------------------------------

def make_handler(rail: ChatRail) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # quiet the default per-request stderr lines; the rail narrates itself
        def log_message(self, format: str, *args: Any) -> None:   # noqa: A002
            pass

        def _json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                return data if isinstance(data, dict) else {}
            except (ValueError, json.JSONDecodeError):
                return {}

        def do_GET(self) -> None:   # noqa: N802 (http.server API)
            path, _, query = self.path.partition("?")
            if path in ("/", "/index.html"):
                body = _UI_FILE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/manifest.json":
                # PWA manifest — "add to home screen" on the phone gives the
                # boardroom a standalone app frame over tailscale.
                self._json({
                    "name": "Cadre Boardroom",
                    "short_name": "Boardroom",
                    "start_url": "/",
                    "display": "standalone",
                    "background_color": "#0f1211",
                    "theme_color": "#0f1211",
                    "icons": [{"src": "/icon.svg", "sizes": "any",
                               "type": "image/svg+xml"}],
                })
                return
            if path == "/icon.svg":
                body = (
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">'
                    '<rect width="96" height="96" rx="20" fill="#0f1211"/>'
                    '<rect x="8" y="8" width="80" height="80" rx="14" fill="none" '
                    'stroke="#22c55e" stroke-opacity=".35" stroke-width="2"/>'
                    '<text x="48" y="60" text-anchor="middle" font-family="monospace" '
                    'font-size="34" font-weight="bold" fill="#22c55e">CB</text>'
                    "</svg>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/state":
                self._json(rail.state_payload())
                return
            if path.startswith("/api/conversations/"):
                conv = load_conversation(path.rsplit("/", 1)[1])
                if conv is None:
                    self._json({"ok": False, "reason": "unknown conversation"}, 404)
                else:
                    self._json({"ok": True, "conversation": conv,
                                "status": rail.status_of(conv["id"]),
                                "firms": rail.firm_badges(conv),
                                "context": {
                                    "tokens": conv.get("context_tokens", 0),
                                    "window": conv.get("context_window", 0),
                                }})
                return
            if path.startswith("/api/approve/"):
                self._json(rail.approval_state(path.rsplit("/", 1)[1]))
                return
            if path == "/api/events":
                self._serve_events(query)
                return
            self._json({"ok": False, "reason": "not found"}, 404)

        def do_POST(self) -> None:   # noqa: N802 (http.server API)
            path = self.path.partition("?")[0]
            body = self._read_body()
            if path == "/api/messages":
                self._json(rail.post_operator_message(
                    body.get("conversation_id") or None,
                    str(body.get("text", ""))))
                return
            if path == "/api/say":
                self._json(rail.say(str(body.get("conversation_id", "")),
                                    str(body.get("text", ""))))
                return
            if path == "/api/approve":
                self._json(rail.create_approval(
                    str(body.get("conversation_id", "")),
                    str(body.get("tool_name", "")),
                    body.get("tool_input") or {},
                    int(body.get("timeout_sec") or 300)))
                return
            if path.startswith("/api/approve/") and path.endswith("/verdict"):
                request_id = path.split("/")[3]
                self._json(rail.set_verdict(request_id, str(body.get("verdict", ""))))
                return
            self._json({"ok": False, "reason": "not found"}, 404)

        def _serve_events(self, query: str) -> None:
            """SSE — replay from ``since``, then follow the bus. One comment
            heartbeat per idle wait keeps proxies and EventSource alive."""
            since = 0
            for part in query.split("&"):
                if part.startswith("since="):
                    try:
                        since = int(part.split("=", 1)[1])
                    except ValueError:
                        pass
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                while True:
                    events = rail.bus.wait_since(since, timeout=20.0)
                    if not events:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    for seq, kind, data in events:
                        since = max(since, seq)
                        payload = json.dumps(data)
                        self.wfile.write(
                            f"id: {seq}\nevent: {kind}\ndata: {payload}\n\n"
                            .encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return   # client went away — normal SSE lifecycle

    return Handler


def run_serve() -> int:
    """Foreground daemon — config + UI + API, blocks until killed."""
    import sys
    config = chat_config()
    if not config.get("firms_root"):
        print("rail not configured (missing firms_root) — run: cadre chat setup",
              file=sys.stderr)
        return 1
    claude_bin = turns.find_claude()
    if not claude_bin:
        print("claude binary not found — set CADRE_CLAUDE_BIN", file=sys.stderr)
        return 1
    if not _UI_FILE.exists():
        print(f"chat UI missing from the package ({_UI_FILE}) — reinstall cadre",
              file=sys.stderr)
        return 1

    # The firms root is the Co-Board's boot directory — make sure its shipped
    # CLAUDE.md is laid before the first session boots there.
    from firm.dashboard.launch import ensure_boardroom_claude
    ensure_boardroom_claude(config["firms_root"])

    rail = ChatRail(config, claude_bin=claude_bin)
    host = str(config.get("host", DEFAULT_HOST))
    port = int(config.get("port", DEFAULT_PORT))
    server = ThreadingHTTPServer((host, port), make_handler(rail))
    server.daemon_threads = True   # hung SSE clients must not block shutdown
    print(f"cadre chat rail up — {daemon_url(config)}, mode {config.get('mode')}, "
          f"firms root {config['firms_root']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def local_call(url: str, payload: dict[str, Any] | None = None,
               timeout: float = 10.0) -> dict[str, Any]:
    """One call to the running daemon (CLI helpers: say, test). Never raises."""
    try:
        if payload is None:
            req = urllib.request.Request(url, method="GET")
        else:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        return body if isinstance(body, dict) else {"ok": False, "reason": "bad response"}
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": f"daemon unreachable: {exc}"}
