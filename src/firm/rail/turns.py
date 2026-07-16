"""The shared turn core — everything a rail needs that isn't its transport.

Every rail (Slack today, chat and Telegram beside it) does the same four
things per turn: compose a ``/boardroom`` prompt, spawn ``claude --print`` at
the firms root, tap the stream-json output as it flows, and steer a live turn
through ``base relay task``. That machinery lives here once; a provider
module owns only its surface — Socket Mode + reactions for Slack, localhost
HTTP + SSE for chat.

Provider-specific text (the surface's name, its ``say`` command) arrives as
parameters, never as imports — this module must not know any transport.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

APPROVE_TOOL = "mcp__cadre-rail__approve"

_SCOPE_RE = re.compile(r"^@([A-Za-z0-9][A-Za-z0-9_-]*)\s+(.+)$", re.DOTALL)


# ---------------------------------------------------------------------------
# Binary resolution (systemd units carry a bare PATH — resolve explicitly,
# the launch.py/_which_claude lesson)
# ---------------------------------------------------------------------------

def find_claude() -> str | None:
    """``CADRE_CLAUDE_BIN`` → PATH → ``~/.local/bin`` → newest nvm node.

    Same ladder as ``pulse.spawn.resolve_claude_bin`` + ``dashboard.launch``'s
    fallback, inlined because both live in mantis's in-flight working set
    (2026-07-13) — collapse into a shared helper once that set lands.
    """
    env_bin = os.environ.get("CADRE_CLAUDE_BIN")
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        return env_bin
    found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    for cand in (home / ".local" / "bin" / "claude",
                 *sorted(home.glob(".nvm/versions/node/*/bin/claude"), reverse=True)):
        if cand.exists():
            return str(cand)
    return None


def find_base() -> str | None:
    """PATH → base's canonical install home (same ladder as
    ``secrets.provider.BaseVaultProvider.capable`` — systemd PATH is bare)."""
    found = shutil.which("base")
    if found:
        return found
    cand = Path.home() / ".local" / "bin" / "base"
    return str(cand) if cand.exists() else None


# ---------------------------------------------------------------------------
# Turn composition — prompt, argv, stream parse
# ---------------------------------------------------------------------------

def parse_scope(text: str) -> tuple[str, str]:
    """``@downstream do the thing`` → ("downstream", "do the thing").

    Scope only means something on a fresh thread — it rides into
    ``/boardroom <scope>`` so one thread can be one firm's boardroom.
    """
    match = _SCOPE_RE.match(text.strip())
    if match:
        return match.group(1), match.group(2).strip()
    return "", text.strip()


def rail_protocol(*, surface: str, say_cmd: str) -> str:
    """The in-turn emission protocol, appended to every fresh rail turn.

    Designed by the Co-Board itself (2026-07-13, first live session): over a
    rail its narration goes nowhere — without explicit emissions, a long
    turn is indistinguishable from a dead session. Event-driven, not
    time-driven, so it informs instead of paging. Lives HERE and not in the
    boardroom skill because only rail-spawned sessions need it — a terminal
    boardroom narrates to the TTY for free.
    """
    return (
        "---\n"
        f"{surface} rail protocol — you are speaking to the Board over "
        f"{surface}. Your normal output reaches them ONLY when this turn "
        f"ends. Your mid-turn voice is: {say_cmd} \"<message>\" (thread "
        "routing is already in your env). Emit:\n"
        "1. On accepting this turn: one line — what you're about to do.\n"
        "2. The moment a finding changes the plan or direction.\n"
        "3. Before anything long or expensive (member spawns, pulses, spend).\n"
        "4. If ~5 minutes pass with nothing emitted: one still-working line "
        "with where you are.\n"
        "Your final message posts to the thread automatically — do NOT `say` "
        "it too."
    )


def compose_prompt(
    text: str,
    *,
    resumed: bool,
    updates: bool = True,
    surface: str,
    say_cmd: str,
) -> str:
    if resumed:
        return text.strip()
    scope, agenda = parse_scope(text)
    prompt = f"/boardroom {scope}".strip()
    if agenda:
        prompt += "\n\nAgenda:\n" + agenda
    if updates:   # quiet operators get one answer per turn, nothing between
        prompt += "\n\n" + rail_protocol(surface=surface, say_cmd=say_cmd)
    return prompt


def build_cmd(
    claude_bin: str,
    *,
    mode: str,
    prompt: str,
    resume: str | None = None,
    mcp_config: str | None = None,
    full_load: bool = False,
    model: str | None = None,
) -> list[str]:
    """The exact argv for one board turn — mirrors the pulse spawn idiom."""
    cmd = [claude_bin, "--print", "--output-format", "stream-json", "--verbose"]
    if not full_load:
        cmd.append("--strict-mcp-config")
    if mode == "skip":
        cmd.append("--dangerously-skip-permissions")
    else:
        cmd += ["--permission-prompt-tool", APPROVE_TOOL]
    if mcp_config:
        cmd += ["--mcp-config", mcp_config]
    if model:
        cmd += ["--model", model]
    if resume:
        cmd += ["--resume", resume]
    cmd += ["-p", prompt]
    return cmd


def parse_stream(stdout: str) -> tuple[str | None, str, bool]:
    """(session_id, final_text, is_error) from a stream-json transcript.

    The session id is taken from any event carrying one (init races exist —
    the pulse parser learned that); the result event is authoritative for
    text and error state. No result event at all = error.
    """
    session_id: str | None = None
    text = ""
    is_error = True
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            sid = obj.get("session_id")
            if sid:
                session_id = str(sid)
            if obj.get("type") == "result":
                subtype = obj.get("subtype", "")
                is_error = bool(obj.get("is_error", subtype != "success"))
                text = str(obj.get("result") or "")
                if not text and is_error:
                    text = f"turn ended without a result ({subtype or 'unknown'})"
    return session_id, text, is_error


def activity_line(event: dict[str, Any]) -> str | None:
    """One human line for a stream-json event, or None if it isn't telemetry
    worth showing. Deliberately terse — this feeds a live ticker, not a log."""
    if event.get("type") != "assistant":
        return None
    content = ((event.get("message") or {}).get("content")) or []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            return f"⚙ {block.get('name', 'tool')}"
        if block.get("type") == "text":
            text = " ".join(str(block.get("text", "")).split())
            if text:
                return text[:160]
    return None



@dataclasses.dataclass
class TurnResult:
    ok: bool
    session_id: str | None
    text: str
    detail: str = ""


def spawn_turn(
    cmd: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout_sec: int,
    on_session: Callable[[str], None] | None = None,
    on_activity: Callable[[str], None] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    on_spawn: Callable[[Any], None] | None = None,
) -> TurnResult:
    """One board turn's process: spawn → stream-tap → parse. Pure runner —
    the caller composed the argv and env; nothing here knows a transport.

    *on_session* fires the moment the child announces its session id (the
    init event, seconds in) — the daemon records it immediately so a reply
    arriving MID-turn can be steered into the live session instead of
    waiting out a 20-minute brief. *on_activity* fires with one terse line
    per tap-worthy event (see :func:`activity_line`) — the live ticker.
    *on_event* fires with EVERY parsed stream object — providers derive
    their own telemetry from it (firm touches, usage) without this module
    knowing what they look for. Callbacks must never raise; a provider bug
    must not kill the stream tap, so failures are swallowed here.
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=env,
        )
    except OSError as exc:
        return TurnResult(False, None, "", f"claude failed to exec: {exc}")

    # *on_spawn* hands the caller the live process handle — the seam an
    # operator-facing interrupt needs (kill THIS turn, keep the session).
    if on_spawn is not None:
        try:
            on_spawn(proc)
        except Exception:
            pass   # a provider bug must not kill the turn

    stderr_box: list[str] = []
    drain = threading.Thread(
        target=lambda: stderr_box.append(proc.stderr.read() if proc.stderr else ""),
        daemon=True,
    )
    drain.start()
    killed = threading.Event()

    def _timeout_kill() -> None:
        killed.set()
        proc.kill()

    watchdog = threading.Timer(timeout_sec, _timeout_kill)
    watchdog.start()

    lines: list[str] = []
    announced = False
    try:
        for line in proc.stdout or []:
            lines.append(line)
            try:
                obj = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if not announced and on_session is not None:
                sid = obj.get("session_id")
                if sid:
                    announced = True
                    on_session(str(sid))
            if on_activity is not None:
                note = activity_line(obj)
                if note:
                    on_activity(note)
            if on_event is not None:
                try:
                    on_event(obj)
                except Exception:
                    pass   # telemetry must never kill the tap
        proc.wait()
    finally:
        watchdog.cancel()
        drain.join(timeout=2)

    session_id, result_text, is_error = parse_stream("".join(lines))
    if killed.is_set():
        return TurnResult(False, session_id, "", "turn timed out")
    if proc.returncode != 0 and not result_text:
        stderr = (stderr_box[0] if stderr_box else "").strip()
        stderr_tail = stderr.splitlines()[-1:] or ["no stderr"]
        return TurnResult(False, session_id, "", f"exit {proc.returncode}: {stderr_tail[0]}")
    return TurnResult(not is_error, session_id, result_text,
                      "" if not is_error else "session reported an error")


def relay_register(session_id: str, title: str) -> bool:
    """Bind *session_id* to a stable relay *title* (re-binding is safe).

    Rail-spawned ``--print`` sessions never self-register — no operator ran
    ``base relay register`` inside them — so :func:`relay_steer`'s title
    lookup always missed and every mid-flight message silently fell back to
    queueing. The rail daemon holds both halves (the announced session id
    and its own stable name for the thread), so it binds them the moment
    the session announces itself; the session's own hook activity keeps the
    binding live for the rest of the turn. Best-effort: no BASE, no bind —
    the queue fallback stays honest."""
    base = find_base()
    if not base:
        return False
    try:
        done = subprocess.run(
            [base, "relay", "register", "--as", title, "--session", session_id],
            capture_output=True, text=True, timeout=10,
        )
        return done.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def relay_steer(
    session_id: str,
    text: str,
    *,
    from_name: str,
    slug_prefix: str,
    say_cmd: str,
) -> str | None:
    """Deliver *text* into a LIVE session via ``base relay task``.

    A ``--print`` session can't be resumed while its process is alive — but
    a relay task fires inside its hooks on its next tool call (mid-run),
    which is how cadre steers any live agent. Task, not ping, on purpose:
    the receiver clears a task itself (``base relay done <slug>``), while a
    ping only clears on a reply to the sender — and the rail daemon is not
    a registered session, so a ping's alert would re-fire forever (proven
    live, 2026-07-13). The message carries the reply path: the session
    posts into its own thread via *say_cmd* (its env already holds the
    thread routing).

    Returns the task slug on success (the caller can watch its delivery
    state — see :func:`relay_task_state`), None on any failure. Best-effort:
    BASE is optional for licensees; None = fall back to queue.
    """
    base = find_base()
    if not base:
        return None
    try:
        listing = subprocess.run(
            [base, "relay", "sessions"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        title = next(
            (line.split()[0] for line in listing.splitlines()
             if f"session:{session_id}" in line and "[live" in line),
            None,
        )
        if not title:
            return None
        slug = f"{slug_prefix}-{int(time.time())}"
        summary = (
            f"BOARD (mid-turn, via the {from_name}): {text} "
            f"— ACT ON THIS NOW within your running turn. Reply directly "
            f"into your thread: {say_cmd} \"<your reply>\" "
            f"(thread routing is already in your env). Then clear this "
            f"alert: {base} relay done {slug}"
        )
        sent = subprocess.run(
            [base, "relay", "task", "--to", title, "--from", from_name,
             "--slug", slug, "--summary", summary],
            capture_output=True, text=True, timeout=10,
        )
        return slug if sent.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def relay_task_state(slug: str) -> str | None:
    """Delivery state of a steer task: ``pending`` (in the inbox, not yet
    fired), ``delivered`` (fired inside the session's hooks — it's in their
    system messages now), ``cleared`` (the session ran ``relay done`` on it),
    or None when the relay itself is unavailable. This is what turns a steer
    into an iMessage-style receipt: sent ✓, landed ✓✓."""
    base = find_base()
    if not base:
        return None
    try:
        listing = subprocess.run(
            [base, "relay", "tasks"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in listing.splitlines():
        if slug in line:
            return "delivered" if "[delivered" in line else "pending"
    return "cleared"
