"""The adapter seam — how a chat surface becomes a window into the hub.

The chat hub (the cadre-chat addon) owns conversations, turns, approvals,
and state. An *adapter* (Slack, Telegram, …) is a thin translator with two
halves:

* **intake** — normalize the surface's messages into
  :meth:`HubClient.post_message`; the hub finds-or-creates the conversation
  by binding and dispatches the turn.
* **outbound** — follow the hub's SSE bus (:func:`follow_events`, the same
  mechanism the browser UI rides) and mirror messages back onto the surface.
  Echo suppression is data-level: skip any message whose ``source`` is the
  adapter's own name.

This module is deliberately framework-side and stdlib-only: the *protocol*
is open even though the hub implementation ships as an addon.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

_HTTP_TIMEOUT_SEC = 15


def ensure_cli_shim(prog: str) -> str | None:
    """Make *prog* reachable from any shell: a ``~/.local/bin`` shim that
    execs this interpreter's venv console script. pip-installing an addon
    into the framework venv doesn't put its CLI on the operator's PATH —
    every addon's ``setup``/``enable`` calls this so it never has to be a
    manual step. Returns the shim path when (re)written, None otherwise."""
    import stat
    import sys
    target = Path(sys.executable).parent / prog
    if not target.exists():
        return None
    bin_dir = Path.home() / ".local" / "bin"
    shim = bin_dir / prog
    body = f'#!/usr/bin/env bash\nexec "{target}" "$@"\n'
    try:
        if shim.exists() and shim.read_text(encoding="utf-8") == body:
            return None
        bin_dir.mkdir(parents=True, exist_ok=True)
        shim.write_text(body, encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return str(shim)
    except OSError:
        return None


class HubClient:
    """Calls into a running chat hub. Never raises — transport failures come
    back as ``{"ok": False, "reason": …}`` so callers handle hub errors and
    network errors through one shape."""

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")

    def _call(self, path: str, payload: dict[str, Any] | None = None,
              timeout: float = _HTTP_TIMEOUT_SEC) -> dict[str, Any]:
        try:
            if payload is None:
                req = urllib.request.Request(self.url + path, method="GET")
            else:
                req = urllib.request.Request(
                    self.url + path,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
            return body if isinstance(body, dict) else {"ok": False,
                                                        "reason": "bad response"}
        except (urllib.error.URLError, OSError, TimeoutError,
                json.JSONDecodeError) as exc:
            return {"ok": False, "reason": f"hub unreachable: {exc}"}

    # -- reads ---------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        return self._call("/api/state")

    # -- writes ----------------------------------------------------------------

    def post_message(self, *, adapter: str, text: str,
                     conversation_id: str | None = None,
                     binding: dict[str, Any] | None = None,
                     images: list[str] | None = None,
                     operator: str = "") -> dict[str, Any]:
        """Inbound door. *conversation_id* when the adapter already knows the
        conversation (Telegram's reply routing); *binding* when the surface's
        own thread identity is the key (Slack) — the hub finds-or-creates."""
        payload: dict[str, Any] = {"adapter": adapter, "text": text}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if binding:
            payload["binding"] = binding
        if images:
            payload["images"] = images
        if operator:
            payload["operator"] = operator
        return self._call("/api/adapters/messages", payload)

    def bind(self, conversation_id: str, adapter: str,
             binding: dict[str, Any]) -> dict[str, Any]:
        """Register this adapter's anchor on an existing conversation —
        the second half of anchor-at-birth."""
        return self._call(f"/api/conversations/{conversation_id}/bindings",
                          {"adapter": adapter, "binding": binding})

    def verdict(self, approval_id: str, verdict: str) -> dict[str, Any]:
        return self._call(f"/api/approve/{approval_id}/verdict",
                          {"verdict": verdict})


def follow_events(url: str, *, since: int,
                  on_event: Callable[[str, int, dict[str, Any]], None],
                  should_stop: Callable[[], bool] | None = None,
                  reconnect_sec: float = 3.0) -> None:
    """Blocking SSE follower over the hub's ``/api/events`` — parse
    ``id:``/``event:``/``data:`` frames, dispatch ``on_event(kind, seq,
    data)``, reconnect forever from the last seen seq. Callback errors are
    swallowed: one bad post must not kill the mirror. Run it on a daemon
    thread; *should_stop* is checked between connections (and between
    events) for tests and clean shutdowns."""
    seq = since
    while not (should_stop and should_stop()):
        try:
            req = urllib.request.Request(
                f"{url.rstrip('/')}/api/events?since={seq}", method="GET")
            with urllib.request.urlopen(req, timeout=90) as resp:
                event_id, kind, data_lines = 0, "", []
                for raw in resp:
                    if should_stop and should_stop():
                        return
                    line = raw.decode("utf-8", errors="replace").rstrip("\n")
                    if line.startswith(":"):        # keepalive comment
                        continue
                    if line.startswith("id:"):
                        event_id = int(line[3:].strip() or 0)
                    elif line.startswith("event:"):
                        kind = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                    elif line == "" and kind:
                        try:
                            data = json.loads("\n".join(data_lines) or "{}")
                            seq = max(seq, event_id)
                            on_event(kind, event_id, data)
                        except Exception:
                            pass   # a bad frame or handler must not kill the tap
                        event_id, kind, data_lines = 0, "", []
        except (urllib.error.URLError, OSError, TimeoutError, ValueError):
            pass   # hub down or rotated — reconnect from the last seq
        if should_stop and should_stop():
            return
        time.sleep(reconnect_sec)
