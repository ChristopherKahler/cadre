"""Board token — the HTTP boundary for dashboard mutations.

Every POST the boardroom serves is a Board action (member management,
authority grants, extension installs, founding). GETs stay open on
loopback; mutations require the operator's board token, because a Member
run shares loopback with the operator and must not be able to drive the
Board surface over HTTP — that would bypass the authority gate the MCP
tools enforce.

The token deliberately lives OUTSIDE the firm vault (``~/.cadre/
board.token``): spawn.py hands the merged vault to every Member run's
environment, so a vault-stored token would be dealt straight into the
hands this gate exists to stop. spawn.py additionally strips
``CADRE_BOARD_TOKEN`` from the child env for the same reason.

This is an LLM-overreach guardrail, not an adversarial sandbox — a
process with shell access can read the token file, the same residual as
writing the firm DB directly. Rotation: delete or rewrite the file; it is
re-read on every check, so no restart is needed.
"""

from __future__ import annotations

import hmac
import secrets
from pathlib import Path

from firm.secrets.vault import cadre_home

HEADER = "X-Cadre-Board-Token"


def board_token_path() -> Path:
    return cadre_home() / "board.token"


def board_token() -> str:
    """Read the operator's board token, minting it on first use."""
    path = board_token_path()
    if path.exists():
        existing = path.read_text().strip()
        if existing:
            return existing
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n")
    path.chmod(0o600)
    return token


def supplied_token(handler) -> str:
    bearer = handler.headers.get("Authorization") or ""
    if bearer.startswith("Bearer "):
        return bearer[len("Bearer "):].strip()
    return (handler.headers.get(HEADER) or "").strip()


def authorized(handler) -> bool:
    supplied = supplied_token(handler)
    return bool(supplied) and hmac.compare_digest(supplied, board_token())


def deny(send, handler) -> None:
    """Send the structured 401 through *send* (the server's _http_send)."""
    send(handler, 401, {
        "ok": False,
        "error": "board_token_required",
        "hint": f"paste the token from {board_token_path()} — "
                "the boardroom asks once and remembers it",
    })
