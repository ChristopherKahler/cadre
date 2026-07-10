"""Stream-JSON parser for ``claude --print`` output.

Parses NDJSON lines emitted by ``claude --print --output-format stream-json
--verbose`` and extracts structured fields: session ID, assistant text,
token usage, cost, tool calls, rate-limit events, errors, and stop reason.

Specification: BRIEF.md Section 1 (Stream-JSON Format Reference).
"""

from __future__ import annotations

import json
from typing import Any


def parse_stream(stdout: str) -> dict[str, Any]:
    """Parse complete ``claude --print`` stream-json output.

    Args:
        stdout: Raw stdout captured from the subprocess (NDJSON lines).

    Returns:
        Dict with keys: session_id, text, usage, total_cost_usd, is_error,
        stop_reason, tool_calls, rate_limit_events.
    """
    assistant_texts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    rate_limit_events: list[dict[str, Any]] = []
    usage: dict[str, int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read": 0,
        "cache_create": 0,
    }
    session_id: str | None = None
    total_cost_usd: float | None = None
    is_error: bool = False
    stop_reason: str | None = None
    init_tools: list[str] | None = None
    mcp_servers: list[dict[str, Any]] | None = None

    for raw_line in stdout.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue  # Skip non-JSON lines

        if not isinstance(event, dict):
            continue

        etype = event.get("type")

        # --- system events ---
        if etype == "system" and event.get("subtype") == "init":
            session_id = session_id or event.get("session_id")
            # Tool index + MCP server statuses at run start — the runner's
            # MCP startup guard reads these to detect a Member spawned
            # without its firm tools. None (vs []) = no init observed.
            if isinstance(event.get("tools"), list):
                init_tools = [t for t in event["tools"] if isinstance(t, str)]
            if isinstance(event.get("mcp_servers"), list):
                mcp_servers = [s for s in event["mcp_servers"] if isinstance(s, dict)]

        # --- assistant events ---
        elif etype == "assistant":
            msg = event.get("message", {})
            if not isinstance(msg, dict):
                continue
            for block in msg.get("content", []):
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    text = block.get("text", "")
                    if text:
                        assistant_texts.append(text)
                elif block_type == "tool_use":
                    tool_calls.append({
                        "name": block.get("name"),
                        "id": block.get("id"),
                        "input": block.get("input"),
                    })

        # --- rate limit events ---
        elif etype == "rate_limit_event":
            rate_limit_events.append({
                "utilization": event.get("rate_limit_info", {}).get("utilization"),
                "resets_at": (
                    event.get("rate_limit_info", {}).get("resetsAt")
                    or event.get("rate_limit_info", {}).get("resets_at")
                ),
                "rate_limit_type": (
                    event.get("rate_limit_info", {}).get("rateLimitType")
                    or event.get("rate_limit_info", {}).get("rate_limit_type")
                ),
                "is_using_overage": (
                    event.get("rate_limit_info", {}).get("isUsingOverage")
                    or event.get("rate_limit_info", {}).get("is_using_overage")
                ),
            })

        # --- result event ---
        elif etype == "result":
            u = event.get("usage", {})
            if isinstance(u, dict):
                usage["input_tokens"] = u.get("input_tokens", 0)
                usage["output_tokens"] = u.get("output_tokens", 0)
                usage["cache_read"] = (
                    u.get("cache_read_input_tokens", 0)
                    or u.get("cacheReadInputTokens", 0)
                )
                usage["cache_create"] = (
                    u.get("cache_creation_input_tokens", 0)
                    or u.get("cacheCreationInputTokens", 0)
                )

            total_cost_usd = event.get("total_cost_usd", total_cost_usd)
            session_id = session_id or event.get("session_id")
            stop_reason = event.get("stop_reason")

            if event.get("is_error"):
                is_error = True
            if event.get("subtype") == "error":
                is_error = True

    return {
        "session_id": session_id,
        "text": "\n\n".join(assistant_texts).strip(),
        "usage": usage,
        "total_cost_usd": total_cost_usd,
        "is_error": is_error,
        "stop_reason": stop_reason,
        "tool_calls": tool_calls,
        "rate_limit_events": rate_limit_events,
        "init_tools": init_tools,
        "mcp_servers": mcp_servers,
    }
