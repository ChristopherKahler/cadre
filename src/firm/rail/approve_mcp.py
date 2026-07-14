"""The 👍/👎 permission gate — a one-tool MCP server for approve-mode turns.

Claude Code's ``--permission-prompt-tool`` calls :func:`approve` whenever a
headless board turn wants to do something its permission rules would normally
prompt for. The tool surfaces the request to the Board on the turn's rail —
a 👍/👎 message in the Slack thread, an Allow/Deny card in the chat UI —
waits for the verdict, and answers with the documented contract::

    {"behavior": "allow", "updatedInput": {...}}   # approved by the Board
    {"behavior": "deny",  "message": "..."}        # denied, timeout, or any failure

Fail-closed is the whole design: no config, no token, transport error, or an
unanswered request all deny. A permission gate that fails open is not a gate.

Routing arrives via env from the per-turn ``--mcp-config`` the daemon writes;
``CADRE_RAIL_PROVIDER`` picks the transport (``slack`` when absent — the
original rail). Secrets (the Slack bot token) are inherited from the daemon's
process env and never touch disk; the chat rail needs none — its daemon is
localhost-only.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from firm.rail import slack_call
from firm.rail.slack import BOT_TOKEN_KEY

_POLL_SEC = 2.0
_ALLOW_NAMES = {"+1", "thumbsup"}
_DENY_NAMES = {"-1", "thumbsdown"}


def _deny(message: str) -> str:
    return json.dumps({"behavior": "deny", "message": message})


def _allow(updated_input: dict[str, Any]) -> str:
    return json.dumps({"behavior": "allow", "updatedInput": updated_input})


def format_request(tool_name: str, tool_input: dict[str, Any], timeout_sec: int) -> str:
    detail = json.dumps(tool_input, indent=2, sort_keys=True)
    if len(detail) > 600:
        detail = detail[:600] + "\n… (truncated)"
    return (
        f":shield: *Co-Board wants to run:* `{tool_name}`\n"
        f"```{detail}```\n"
        f":thumbsup: allow · :thumbsdown: deny — waiting up to {timeout_sec}s"
    )


def verdict_from_reactions(
    reactions: list[dict[str, Any]], approvers: list[str],
) -> str | None:
    """"allow" / "deny" / None(keep waiting). Deny wins a tie — fail closed.
    Reactions from non-approvers are ignored entirely."""
    allowed = denied = False
    for reaction in reactions:
        users = reaction.get("users", [])
        if not any(u in approvers for u in users):
            continue
        name = str(reaction.get("name", "")).split("::")[0]   # strip skin tones
        if name in _DENY_NAMES:
            denied = True
        elif name in _ALLOW_NAMES:
            allowed = True
    if denied:
        return "deny"
    if allowed:
        return "allow"
    return None


def wait_for_verdict(
    *,
    token: str,
    channel: str,
    message_ts: str,
    approvers: list[str],
    timeout_sec: int,
    poll_sec: float = _POLL_SEC,
    clock: Any = time,
) -> str:
    """Poll ``reactions.get`` until an approver reacts or the window closes."""
    deadline = clock.monotonic() + timeout_sec
    while clock.monotonic() < deadline:
        result = slack_call("reactions.get", token,
                            channel=channel, timestamp=message_ts)
        if result.get("ok"):
            reactions = (result.get("message") or {}).get("reactions", [])
            verdict = verdict_from_reactions(reactions, approvers)
            if verdict:
                return verdict
        clock.sleep(poll_sec)
    return "timeout"


def _chat_call(url: str, payload: dict[str, Any] | None = None,
               timeout: float = 10.0) -> dict[str, Any]:
    """One call to the chat rail daemon (localhost). Never raises — any
    failure comes back as ``{"ok": False, ...}`` so the gate fails closed."""
    try:
        if payload is None:
            req = urllib.request.Request(url, method="GET")
        else:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        return body if isinstance(body, dict) else {"ok": False}
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError):
        return {"ok": False}


def decide_chat(tool_name: str, tool_input: dict[str, Any]) -> str:
    """The chat-rail gate: register the request with the daemon, poll for the
    Board's Allow/Deny click. The daemon renders the card and owns the UI;
    this process owns the deadline and the fail-closed default."""
    base_url = os.environ.get("CADRE_RAIL_CHAT_URL", "").rstrip("/")
    conv_id = os.environ.get("CADRE_RAIL_THREAD_TS", "")
    try:
        timeout_sec = int(os.environ.get("CADRE_RAIL_APPROVE_TIMEOUT", "300"))
    except ValueError:
        timeout_sec = 300
    if not (base_url and conv_id):
        return _deny("chat approval gate misconfigured (missing daemon url/"
                     "conversation) — denying by default")

    created = _chat_call(f"{base_url}/api/approve", {
        "conversation_id": conv_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "timeout_sec": timeout_sec,
    })
    request_id = str(created.get("id", ""))
    if not created.get("ok") or not request_id:
        return _deny("could not reach the chat rail daemon — denying by default")

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        state = _chat_call(f"{base_url}/api/approve/{request_id}")
        verdict = state.get("verdict") if state.get("ok") else None
        if verdict == "allow":
            return _allow(tool_input)
        if verdict == "deny":
            return _deny("the Board denied this action in the chat")
        time.sleep(_POLL_SEC)
    _chat_call(f"{base_url}/api/approve/{request_id}/verdict",
               {"verdict": "timeout"})
    return _deny(f"the Board did not answer within {timeout_sec}s")


def decide(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Route to the turn's rail — ``CADRE_RAIL_PROVIDER`` from the per-turn
    mcp-config; absent means slack (the original rail)."""
    provider = os.environ.get("CADRE_RAIL_PROVIDER") or "slack"
    if provider == "chat":
        return decide_chat(tool_name, tool_input)
    return decide_slack(tool_name, tool_input)


def decide_slack(tool_name: str, tool_input: dict[str, Any]) -> str:
    """The Slack gate: post → wait for 👍/👎 → stamp the outcome."""
    token = os.environ.get(BOT_TOKEN_KEY, "")
    channel = os.environ.get("CADRE_RAIL_CHANNEL", "")
    thread_ts = os.environ.get("CADRE_RAIL_THREAD_TS", "")
    approvers = [u for u in os.environ.get("CADRE_RAIL_APPROVERS", "").split(",") if u]
    try:
        timeout_sec = int(os.environ.get("CADRE_RAIL_APPROVE_TIMEOUT", "300"))
    except ValueError:
        timeout_sec = 300
    if not (token and channel and thread_ts and approvers):
        return _deny("rail approval gate misconfigured (missing token/channel/"
                     "thread/approvers) — denying by default")

    posted = slack_call("chat.postMessage", token,
                        channel=channel, thread_ts=thread_ts,
                        text=format_request(tool_name, tool_input, timeout_sec))
    if not posted.get("ok"):
        return _deny(f"could not reach the Board on Slack "
                     f"({posted.get('error')}) — denying by default")
    message_ts = str(posted.get("ts", ""))

    verdict = wait_for_verdict(token=token, channel=channel,
                               message_ts=message_ts, approvers=approvers,
                               timeout_sec=timeout_sec)

    stamp = {"allow": "✅ allowed by the Board",
             "deny": "⛔ denied by the Board",
             "timeout": f"⌛ no answer in {timeout_sec}s — denied"}[verdict]
    slack_call("chat.update", token, channel=channel, ts=message_ts,
               text=f"~Co-Board wanted to run:~ `{tool_name}` — {stamp}")

    if verdict == "allow":
        return _allow(tool_input)
    if verdict == "timeout":
        return _deny(f"the Board did not answer within {timeout_sec}s")
    return _deny("the Board denied this action via Slack")


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("cadre-rail")

    @mcp.tool()
    def approve(tool_name: str, input: dict, tool_use_id: str = "") -> str:
        """Ask the Board on Slack (👍/👎 in the thread) whether this tool call
        may run. Returns the permission-prompt verdict JSON."""
        try:
            return decide(tool_name, input or {})
        except Exception as exc:   # the gate itself failing = deny, loudly
            return _deny(f"approval gate crashed ({exc}) — denying by default")

    mcp.run()


if __name__ == "__main__":
    main()
