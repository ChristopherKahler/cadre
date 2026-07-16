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

import hashlib
import hmac
import secrets
from pathlib import Path

from firm.secrets.vault import cadre_home

HEADER = "X-Cadre-Board-Token"

PASSWORD_SCHEME = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 600_000

# Success cache: (board.pass mtime_ns, sha256 of the accepted credential).
# PBKDF2 at 600k iterations is deliberately slow (~0.3s) — the right price
# for a guess, the wrong price for every legitimate Board click. Only a
# credential that already survived the full derivation lands here, and a
# password change on disk (new mtime) invalidates it.
_verified: tuple[int, bytes] | None = None


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


def board_password_path() -> Path:
    return cadre_home() / "board.pass"


def set_board_password(password: str) -> None:
    """Store the operator's board password — salted hash only, never plaintext.

    The password is the HUMAN credential (typed once into the boardroom
    modal); the token file stays as the MACHINE credential for co-board and
    automation. Only the password's hash touches disk, so a process that can
    read ~/.cadre learns nothing it can replay as the operator's password.
    """
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, _PBKDF2_ITERATIONS,
    )
    path = board_password_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{PASSWORD_SCHEME}${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}\n"
    )
    path.chmod(0o600)


def _password_matches(supplied: str) -> bool:
    global _verified
    path = board_password_path()
    if not path.exists():
        return False
    try:
        mtime = path.stat().st_mtime_ns
        fingerprint = hashlib.sha256(supplied.encode()).digest()
        if _verified is not None and _verified[0] == mtime \
                and hmac.compare_digest(_verified[1], fingerprint):
            return True
        scheme, iters, salt_hex, hash_hex = path.read_text().strip().split("$")
        if scheme != PASSWORD_SCHEME:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", supplied.encode(), bytes.fromhex(salt_hex), int(iters),
        )
        if hmac.compare_digest(digest.hex(), hash_hex):
            _verified = (mtime, fingerprint)
            return True
        return False
    except (ValueError, OSError):
        return False


def supplied_token(handler) -> str:
    bearer = handler.headers.get("Authorization") or ""
    if bearer.startswith("Bearer "):
        return bearer[len("Bearer "):].strip()
    return (handler.headers.get(HEADER) or "").strip()


def authorized(handler) -> bool:
    supplied = supplied_token(handler)
    if not supplied:
        return False
    if hmac.compare_digest(supplied, board_token()):
        return True
    return _password_matches(supplied)


def deny(send, handler) -> None:
    """Send the structured 401 through *send* (the server's _http_send)."""
    send(handler, 401, {
        "ok": False,
        "error": "board_token_required",
        "hint": "enter your board password (set one with `firm board "
                f"password`); automation reads {board_token_path()}",
    })
