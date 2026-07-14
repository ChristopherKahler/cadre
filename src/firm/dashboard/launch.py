"""Launch a Co-Board terminal from the hub — the dash's door into a real session.

Per platform: a WSL hub opens Windows Terminal on the host; native Linux opens
whatever terminal the desktop has; macOS opens Terminal.app via a ``.command``
script; native Windows opens Windows Terminal (or a plain console) running a
PowerShell launcher. Quoting a multi-line agenda through any of those chains
is a losing game (wt treats ``;`` as a pane separator, interop re-quotes
argv), so the agenda always travels as a launcher script on disk and the
terminal is only ever handed a file path.
"""

from __future__ import annotations

import glob
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _which_windows_terminal() -> str | None:
    wt = shutil.which("wt.exe")
    if wt:
        return wt
    # A systemd-spawned hub doesn't carry the Windows interop PATH — find
    # wt.exe where Windows keeps its app-execution aliases.
    hits = glob.glob("/mnt/c/Users/*/AppData/Local/Microsoft/WindowsApps/wt.exe")
    return hits[0] if hits else None


def _which_claude() -> str | None:
    c = shutil.which("claude")
    if c:
        return c
    home = Path.home()
    for cand in (home / ".local" / "bin" / "claude",
                 *sorted(home.glob(".nvm/versions/node/*/bin/claude"), reverse=True)):
        if cand.exists():
            return str(cand)
    return None

_BOARDROOM_CLAUDE = Path(__file__).resolve().parent.parent / "templates" / "boardroom" / "CLAUDE.md"


def ensure_boardroom_claude(root: str | Path) -> None:
    """Lay the boardroom-floor CLAUDE.md at the firms root, once.

    The firms root is exclusively the Co-Board's boot directory, so it gets a
    standardized loadout written for that seat. Write-if-missing — the file
    ships with Cadre, but the operator owns it after it lands.
    """
    target = Path(root) / "CLAUDE.md"
    if target.exists():
        return
    target.write_text(_BOARDROOM_CLAUDE.read_text(encoding="utf-8"))


def _write_script(cwd: str, prompt: str, claude: str, suffix: str = ".sh") -> str:
    # The Board summoned this session to work, not to babysit permission
    # prompts — it runs with the same trust as a terminal he opened himself.
    # (.command on macOS — the extension Terminal.app opens executably.)
    script = ("#!/usr/bin/env bash\n"
              f"cd {shlex.quote(cwd)} || exit 1\n"
              f"exec {shlex.quote(claude)} --dangerously-skip-permissions "
              f"{shlex.quote(prompt)}\n")
    fd, path = tempfile.mkstemp(prefix="cadre-coboard-", suffix=suffix)
    with os.fdopen(fd, "w") as fh:
        fh.write(script)
    os.chmod(path, 0o700)
    return path


def _spawn(argv: list[str]) -> str | None:
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return None
    except OSError as exc:
        return str(exc)


def _summon_unix(cwd: str, firm_id: str, prompt: str, claude: str) -> dict[str, Any]:
    """WSL-first (Windows Terminal on the host), then the desktop's own
    terminals. ``bash -l`` because npm shims still need node from profile."""
    path = _write_script(cwd, prompt, claude)
    wt = _which_windows_terminal()
    if wt is not None:
        err = _spawn([wt, "-w", "0", "nt", "--title", f"{firm_id} · Co-Board",
                      "wsl.exe", "--", "bash", "-l", path])
        if err:
            return {"ok": False, "error": f"could not start Windows Terminal: {err}"}
        return {"ok": True}
    for term, argv in (
        ("gnome-terminal", ["--", "bash", "-l", path]),
        ("konsole", ["-e", "bash", "-l", path]),
        ("x-terminal-emulator", ["-e", f"bash -l {shlex.quote(path)}"]),
        ("xterm", ["-e", f"bash -l {shlex.quote(path)}"]),
    ):
        bin_ = shutil.which(term)
        if bin_ and _spawn([bin_, *argv]) is None:
            return {"ok": True}
    return {"ok": False, "error":
            "no terminal found — tried wt.exe (WSL) plus gnome-terminal, "
            "konsole, x-terminal-emulator, xterm"}


def _summon_macos(cwd: str, firm_id: str, prompt: str, claude: str) -> dict[str, Any]:
    """Terminal.app runs any executable ``.command`` file it is handed."""
    path = _write_script(cwd, prompt, claude, suffix=".command")
    err = _spawn(["open", path])
    if err:
        return {"ok": False, "error": f"could not open Terminal.app: {err}"}
    return {"ok": True}


def _summon_windows(cwd: str, firm_id: str, prompt: str, claude: str) -> dict[str, Any]:
    """Native Windows: a PowerShell launcher (here-string carries the
    multi-line agenda intact) in Windows Terminal, else a plain console."""
    ps = ("Set-Location -LiteralPath '" + cwd.replace("'", "''") + "'\n"
          "& '" + claude.replace("'", "''") + "' "
          "--dangerously-skip-permissions @'\n" + prompt + "\n'@\n")
    fd, path = tempfile.mkstemp(prefix="cadre-coboard-", suffix=".ps1")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(ps)
    inner = ["powershell", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", path]
    wt = shutil.which("wt.exe") or shutil.which("wt")
    if wt and _spawn([wt, "-w", "0", "nt", "--title",
                      f"{firm_id} · Co-Board", *inner]) is None:
        return {"ok": True}
    err = _spawn(["cmd", "/c", "start", f"{firm_id} Co-Board", *inner])
    if err:
        return {"ok": False, "error": f"could not start a console: {err}"}
    return {"ok": True}


def summon(cwd: str, firm_id: str, agenda: str = "") -> dict[str, Any]:
    """Open a terminal tab running ``claude "/boardroom <firm_id>"``.

    An empty *agenda* is a blank summon; a non-empty one is a deploy — it rides
    along after the firm id as the session's opening agenda.

    *cwd* is the firms root, NOT the firm workspace: the Co-Board is an
    overseer, not a member. Booting inside a firm would hand it that firm's
    CLAUDE.md and MCP loadout — the member seat. The /boardroom command is
    hub-first and takes the firm id as its scope, so the session sits above
    the building and is directed INTO the floor.
    """
    # Resolve claude to an absolute path now; the fresh terminal's login
    # shell may or may not rebuild PATH.
    claude = _which_claude()
    if claude is None:
        return {"ok": False, "error": "claude not found — not on the hub's PATH, "
                                      "~/.local/bin, or any nvm node"}
    ensure_boardroom_claude(cwd)
    prompt = f"/boardroom {firm_id}"
    agenda = agenda.strip()
    if agenda:
        prompt += "\n\nAgenda:\n" + agenda

    if sys.platform == "darwin":
        out = _summon_macos(str(cwd), firm_id, prompt, claude)
    elif sys.platform.startswith("win"):
        out = _summon_windows(str(cwd), firm_id, prompt, claude)
    else:
        out = _summon_unix(str(cwd), firm_id, prompt, claude)
    if not out.get("ok"):
        return out
    return {"ok": True, "firm_id": firm_id,
            "mode": "deploy" if agenda else "summon"}
