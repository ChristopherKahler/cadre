"""`firm board password` — set the operator's board password.

The password is the boardroom's human credential: typed once into the
browser modal, verified against a salted PBKDF2 hash at
``~/.cadre/board.pass``. Interactive-only (getpass, no echo) — a password
that appears in shell history or a pipe defeats the point. Automation
keeps using the machine token file.
"""

from __future__ import annotations

import getpass
import json
import sys


def run_board_password(clear: bool = False) -> int:
    from firm.dashboard import auth as board_auth

    if clear:
        path = board_auth.board_password_path()
        existed = path.exists()
        path.unlink(missing_ok=True)
        print(json.dumps({
            "ok": True, "cleared": existed,
            "hint": f"the boardroom accepts only the machine token now "
                    f"({board_auth.board_token_path()})",
        }))
        return 0

    if not sys.stdin.isatty():
        print(json.dumps({
            "ok": False,
            "error": "interactive terminal required — the password is typed, "
                     "never piped or passed as an argument",
        }))
        return 1

    password = getpass.getpass("New board password: ")
    if not password:
        print(json.dumps({"ok": False, "error": "password must not be empty"}))
        return 1
    if getpass.getpass("Confirm: ") != password:
        print(json.dumps({"ok": False, "error": "passwords did not match"}))
        return 1

    board_auth.set_board_password(password)
    print(json.dumps({
        "ok": True,
        "path": str(board_auth.board_password_path()),
        "hint": "the boardroom modal now accepts this password (asked once "
                "per browser)",
    }))
    return 0
