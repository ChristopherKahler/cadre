"""Install Cadre hooks into a Claude Code workspace.

Ships the session-pulse hook as an embedded template (so `pip install cadre`
users don't need the repo cloned). Registers the hook in the workspace's
`.claude/settings.json` under `hooks.SessionStart`. Idempotent.

Unit-completion is NOT installed as a Claude Code hook — it's a callable
function invoked from `firm unit complete` (Phase 2 decision).
"""

from __future__ import annotations

import inspect
import json
import stat
import sys
from pathlib import Path

HOOK_SCRIPT_NAME = "cadre-session-pulse.py"
HOOK_COMMAND = f"python3 $CLAUDE_PROJECT_DIR/.claude/hooks/{HOOK_SCRIPT_NAME}"

_HOOK_TEMPLATE = '''#!/usr/bin/env python3
"""SessionStart:startup entrypoint for Cadre session-pulse.

Installed by `cadre init --install-hooks` into <workspace>/.claude/hooks/.
Reads Claude Code's stdin JSON payload, resolves the workspace from `cwd`,
opens `.firm/firm.db`, and prints tags rendered by
`firm.hooks.session_pulse.render`.

Contract:
- Exit 0 always — hook must never block session start.
- Silent on any failure (missing .firm/, malformed JSON, import error, etc.).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _resolve_workspace() -> Path | None:
    try:
        payload_raw = sys.stdin.read()
        if not payload_raw.strip():
            return Path.cwd()
        payload = json.loads(payload_raw)
    except (json.JSONDecodeError, ValueError, OSError):
        return Path.cwd()
    cwd = payload.get("cwd")
    if cwd:
        return Path(cwd)
    return Path.cwd()


def _add_firm_package_to_path(workspace: Path) -> bool:
    candidates: list[Path] = []
    env_src = os.environ.get("FIRM_SRC")
    if env_src:
        candidates.append(Path(env_src))
    candidates.append(workspace / "src")
    candidates.append(workspace / "apps" / "agent-company-architecture" / "src")

    for candidate in candidates:
        if (candidate / "firm" / "__init__.py").exists():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return True
    # Package may be pip-installed — let normal import resolution try.
    return True


def main() -> int:
    workspace = _resolve_workspace()
    if workspace is None:
        return 0

    db_path = workspace / ".firm" / "firm.db"
    if not db_path.exists():
        return 0

    _add_firm_package_to_path(workspace)

    try:
        from firm.core.db import db_connection, resolve_firm_id
        from firm.hooks.session_pulse import render
    except ImportError:
        return 0

    firm_id = os.environ.get("FIRM_ID")
    now_override_raw = os.environ.get("FIRM_NOW_OVERRIDE")
    now_override = None
    if now_override_raw:
        try:
            from datetime import datetime as _dt
            now_override = _dt.fromisoformat(now_override_raw)
        except ValueError:
            now_override = None

    try:
        with db_connection(workspace) as conn:
            output = render(conn, resolve_firm_id(conn, firm_id), now=now_override)
    except Exception:
        return 0

    if output:
        sys.stdout.write(output)
        if not output.endswith("\\n"):
            sys.stdout.write("\\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
'''


POLICY_HOOK_SCRIPT_NAME = "cadre-policy-gate.py"
POLICY_HOOK_COMMAND = (
    f"python3 $CLAUDE_PROJECT_DIR/.claude/hooks/{POLICY_HOOK_SCRIPT_NAME}"
)

_POLICY_HOOK_TEMPLATE = '''#!/usr/bin/env python3
"""PreToolUse policy gate — the Contract's NEVERs, enforced at the boundary.

Installed into <workspace>/.claude/hooks/ when a firm is wired. Reads the
materialized policy (.firm/policy.json, written by firm.services.policy) and
denies any tool call matching the running Member's deny rules — regardless
of what the Member decides, what the prompt got truncated to, or how close
the timeout is. A rule that only works when the Member cooperates is not a
rule, it is a hope (fork 009).

What the rule is aimed AT (fork 015 — read this before widening it):

The gate used to match every rule against the whole tool_input JSON. That
one decision caused both live failure classes at once (chief-of-staff
ESC-021/027/028): it could not block `slack_send_message` (the patterns were
upstream API names that never appear in a tool call) but it DID block a
Member writing a report that *quoted* the pattern. The aim now:

- **The tool NAME is the action.** For a structured call, the name decides
  what happens — `slack_send_message` sends, `slack_search_messages` reads.
  The payload is data, and data is never consulted.
- **Except for shells, where the command IS the action.** `Bash` says nothing
  by itself, so its command string is matched — which is what catches
  `curl ... chat.postMessage`, the reason the API-method patterns still earn
  their place.
- **Prose is never a haystack.** A `Write`'s content, an `Edit`'s strings, a
  search `query` — none can trip a rule. Documentation about a NEVER must
  never trip that NEVER, or the pressure to route around the gate lands on
  the Member, which is how gates get quietly disabled.
- **A rule's `tool` label (fork 014) does NOT scope enforcement.** It rides
  along into the denial receipt so the Board can group rules by equipment,
  and that is all. Scoping by it was tried and rejected here: the gate cannot
  tell a label naming *other* equipment from a typo, so any label-based skip
  lets one bad character silently disable a NEVER — ESC-021's exact defect,
  rebuilt. Scope comes from the tool name, which cannot be misspelled into
  a hole. The label narrows nothing; it explains.
- **A mention is not an invocation** (fork: policy-noise-hardening). Fork 015
  left shells matching on the whole command string and wrote the cost off as
  accepted: "a shell is unparseable and send-capable, so it fails closed."
  Half right. It is unparseable as *prose* — but the one question the gate
  actually asks IS decidable: which programs does this string RUN? That is
  the head of each segment (`firm.hooks.shell_intent`, spliced in below).
  When every segment is an enumerated read-only head, the command is an
  inspection and its arguments are data — exactly like a Write's body. When
  ANY segment is a sender, an interpreter, or simply unknown, nothing
  changes and the whole string is matched as before. This can only ever make
  the gate quieter about `grep`; there is no path here that allows a `curl`.

  It matters because the accepted cost was not theoretical: a Member greping
  for `slack_send_message` to VERIFY the lock was blocked, and so was a
  Member running `firm escalation raise --body "…slack_send_message…"` to
  REPORT that the lock was open. That second one is ESC-021's inversion
  rebuilt — the gate could not stop the send, but it stopped the report
  about the send.

Contract:
- Members only: no CADRE_MEMBER_ID in env means a Board session — allow.
- Deny = JSON permissionDecision on stdout, exit 0. Every denial is appended
  to .firm/policy-denials.jsonl; the next pulse turns it into Records + an
  escalation. This script NEVER opens the DB — it must not fight the pulse
  for locks on every tool call.
- CADRE_POLICY_PROBE in env stamps `probe` on the receipt: a verify harness
  firing every rule on purpose is not a Member drifting, and `ingest_denials`
  must not escalate it (one verify run once became 34 Board escalations,
  ESC-047…080). It marks the RECEIPT and never the DECISION — a probe is
  blocked exactly like anything else. A Member cannot set it: this reads the
  hook process's own environment, which a Member's Bash call cannot reach
  (`CADRE_POLICY_PROBE=1 curl …` sets it for that subshell, not for us).
- Any internal failure allows (exit 0): the gate guards Members, it must
  never brick a session. Stdlib only.
"""

from __future__ import annotations

import fnmatch
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- vendored from firm.hooks.shell_intent (spliced by install_hooks) -------
# The gate runs under the system python3, where `firm` need not be importable
# — and an ImportError must never disable a NEVER. So the resolver is spliced
# in rather than imported. It has exactly one source: edit shell_intent.py,
# never this copy, then `firm doctor --fix` every firm.
__SHELL_INTENT__
# --- end vendored ----------------------------------------------------------

# Fields whose value is a shell command — a string that RUNS things. The only
# payload `shell_intent` is asked about.
SHELL_FIELDS = ("command", "cmd", "script", "shell")

# Fields whose value is an instruction to execute, not prose an author typed.
# A shell command, a URL, an API method on a generic gateway tool. These are
# the ONLY payload the gate reads — everything else is data (fork 015).
ACTING_FIELDS = SHELL_FIELDS + ("url", "endpoint", "method", "api_method")


def _acting_values(node, depth=0):
    """Every (field, value) the payload asks to execute, nested ones included."""
    if depth > 6:
        return []
    out = []
    if isinstance(node, dict):
        for key, val in node.items():
            if str(key).lower() in ACTING_FIELDS and isinstance(val, (str, int, float)):
                out.append((str(key).lower(), str(val)))
            else:
                out.extend(_acting_values(val, depth + 1))
    elif isinstance(node, list):
        for item in node:
            out.extend(_acting_values(item, depth + 1))
    return out


def main() -> int:
    member_id = os.environ.get("CADRE_MEMBER_ID", "")
    if not member_id:
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    workspace = Path(payload.get("cwd") or os.getcwd())
    try:
        rules = (json.loads((workspace / ".firm" / "policy.json").read_text(encoding="utf-8"))
                 or {}).get(member_id) or []
    except Exception:
        return 0
    if not rules:
        return 0

    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    acting = _acting_values(tool_input)

    # The haystack: what this call DOES. Never what it says.
    #
    # A shell command whose every segment is an enumerated read-only head
    # RUNS nothing that can send, so its arguments are data — `grep
    # slack_send_message` names the verb the way a Write's body names it.
    # Drop it. Anything else — a sender, an interpreter, an unknown binary,
    # an unparseable string — is matched whole, exactly as before.
    #
    # The tool name is never dropped: the exemption narrows what the gate
    # reads, never which calls it governs.
    read = []
    heads = []
    for field, value in acting:
        if field in SHELL_FIELDS:
            heads.extend(command_heads(value))
            if is_inspection(value):
                continue
        read.append(value)
    hay = " ".join([tool] + read).lower()

    # A verify harness fires every rule on purpose; that is not a Member
    # drifting, and the Board must not be paged for it. Marks the receipt
    # only — the decision below is untouched.
    probe = bool(os.environ.get("CADRE_POLICY_PROBE"))

    for rule in rules:
        pat = str(rule.get("match") or "").lower().strip()
        if not pat:
            continue
        # A bare string is a substring; *?[ make it a real glob.
        glob = pat if any(c in pat for c in "*?[") else f"*{pat}*"
        if not fnmatch.fnmatchcase(hay, glob):
            continue
        reason = str(rule.get("reason") or "the Contract forbids this")
        try:
            with (workspace / ".firm" / "policy-denials.jsonl").open(
                    "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": datetime.now(tz=timezone.utc).isoformat(),
                    "member_id": member_id,
                    "tool_name": tool,
                    "match": rule.get("match"),
                    "equips": rule.get("tool") or "",
                    "reason": reason,
                    # What the gate actually read — the acting fields, not the
                    # payload. A receipt that quoted a Write's body would put
                    # the prose we refuse to match into the evidence log.
                    "input_head": " ".join(read)[:300],
                    # The programs the command resolved to, and whether a
                    # harness fired this on purpose. `ingest_denials` reads
                    # `probe`; `heads` is for whoever debugs a mis-block.
                    "heads": heads,
                    "probe": probe,
                }) + "\\n")
        except Exception:
            pass          # the denial still stands; only the receipt failed
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Contract NEVER: {reason} (deny rule: {rule.get('match')}). "
                    "This is enforced policy, not a suggestion — do not retry "
                    "variations. If the work genuinely requires it, raise a "
                    "Gate and stop."
                ),
            }
        }))
        return 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
'''


def render_policy_hook() -> str:
    """The gate, with `firm.hooks.shell_intent` spliced in.

    The hook cannot import the resolver (system python3, no `firm`), and a
    second hand-written copy of it would be ESC-021's offline replica: a
    model of the gate that agrees with itself while the real gate does
    something else. So there is one source, vendored at install time.

    Every caller that needs the gate's text goes through here — installing
    it AND the doctor's drift check — or the check would compare a firm's
    hook against a template that is not what we install.
    """
    from firm.hooks import shell_intent

    source = inspect.getsource(shell_intent)
    # `from __future__` is only legal at the top of a file, and the gate
    # already has its own.
    body = "\n".join(
        line for line in source.splitlines()
        if not line.startswith("from __future__ import")
    )
    return _POLICY_HOOK_TEMPLATE.replace("__SHELL_INTENT__", body)


def _load_settings(settings_path: Path) -> dict:
    if not settings_path.exists():
        return {}
    raw = settings_path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    return json.loads(raw)


def _register_hook(settings: dict) -> bool:
    """Add the hook entry if not present. Returns True if modified."""
    hooks = settings.setdefault("hooks", {})
    session_start = hooks.setdefault("SessionStart", [])
    for entry in session_start:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks", []) or []:
            if isinstance(hook, dict) and hook.get("command") == HOOK_COMMAND:
                return False
    session_start.append({
        "matcher": "startup",
        "hooks": [{"type": "command", "command": HOOK_COMMAND}],
    })
    return True


def _register_policy_hook(settings: dict) -> bool:
    """Add the PreToolUse policy-gate entry if not present."""
    hooks = settings.setdefault("hooks", {})
    pre_tool = hooks.setdefault("PreToolUse", [])
    for entry in pre_tool:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks", []) or []:
            if isinstance(hook, dict) and hook.get("command") == POLICY_HOOK_COMMAND:
                return False
    pre_tool.append({
        "matcher": "*",
        "hooks": [{"type": "command", "command": POLICY_HOOK_COMMAND}],
    })
    return True


def install_policy_hook(workspace: Path) -> tuple[int, list[str]]:
    """Install the policy-gate hook + register it. Idempotent.

    The script is REWRITTEN on every call (unlike the session-pulse hook):
    it is framework law, not user-editable — wiring must always leave the
    current version enforcing.
    """
    messages: list[str] = []

    hooks_dir = workspace / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / POLICY_HOOK_SCRIPT_NAME
    dest.write_text(render_policy_hook(), encoding="utf-8")
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    messages.append(f"Installed policy gate: {dest}")

    settings_path = workspace / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings = _load_settings(settings_path)
    if _register_policy_hook(settings):
        settings_path.write_text(json.dumps(settings, indent=2) + "\n",
                                 encoding="utf-8")
        messages.append(f"Registered policy gate in {settings_path}")
    else:
        messages.append(f"Policy gate already registered in {settings_path}")

    return 0, messages


def install_hooks(workspace: Path) -> tuple[int, list[str]]:
    """Install cadre-session-pulse hook + register in settings.json.

    Returns (exit_code, list of status messages).
    """
    messages: list[str] = []

    hooks_dir = workspace / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / HOOK_SCRIPT_NAME

    if dest.exists():
        messages.append(f"Hook already installed: {dest}")
    else:
        dest.write_text(_HOOK_TEMPLATE, encoding="utf-8")
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        messages.append(f"Installed hook: {dest}")

    settings_path = workspace / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings = _load_settings(settings_path)
    if _register_hook(settings):
        settings_path.write_text(json.dumps(settings, indent=2) + "\n",
                                 encoding="utf-8")
        messages.append(f"Registered hook in {settings_path}")
    else:
        messages.append(f"Hook already registered in {settings_path}")

    return 0, messages


WRITEBACK_HOOK_SCRIPT_NAME = "cadre-writeback-gate.py"


def writeback_hook_command() -> str:
    """The command line the Stop gate is registered under.

    The two hooks above hardcode ``python3``. That resolves on Linux and macOS
    and does not exist on a default Windows install, and Windows is a real host
    for this framework now — WindowsScheduler resolves and reports available().
    The interpreter running this install is present by definition and can run a
    stdlib-only script, so that is the one written in. It is quoted because a
    Windows interpreter path routinely contains a space; the script path keeps
    the unquoted ``$CLAUDE_PROJECT_DIR`` form the other two hooks use, so the
    expansion behaves identically to the hooks already proven in the field.
    """
    return (f'"{sys.executable}" '
            f"$CLAUDE_PROJECT_DIR/.claude/hooks/{WRITEBACK_HOOK_SCRIPT_NAME}")


_WRITEBACK_HOOK_TEMPLATE = '''#!/usr/bin/env python3
"""Stop gate — a Member does not walk away from a closed Unit in silence.

The firm's seeded rule tells every Member to "write what they learn back to it,
so the next run starts where this one finished". Nothing checked it, so it was
advice. This is the check.

WHAT IT CANNOT DO, and why the shape is what it is. The obvious gate asks base
whether this Member wrote anything. It cannot: `base learn` writes into the
graph and leaves nothing a hook can cheaply see, and the firm keeps no
write-back journal. So `base cadre complete` leaves a debt file behind and
`base cadre learn --unit <id>` deletes it, and this reads the directory. The
gate and those commands are one piece; a gate installed without them would
block nothing, and they without it would be optional again.

Deliberately stdlib only, and deliberately no `import firm`. It runs under
whatever interpreter the workspace has rather than the firm's own environment,
and a gate that fails to import is a gate that is off.

Contract (Stop event):
    exit 2  block the stop; stderr reaches the Member
    exit 0  let the session end
Fails OPEN on everything else. A Member that cannot finish its session because
this script has a bug is a worse outcome than a lesson going unrecorded.
"""

from __future__ import annotations

import glob
import json
import os
import sys

OPEN_SUFFIX = ".open.json"
# The quote character by number. Spelling it as an escape here would need
# escaping again in the template that carries this file, and one missed
# layer renders a gate that does not parse -- which fails OPEN and silently
# switches the guard off. chr(34) survives any number of layers.
Q = chr(34)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0

    # Already re-running because this gate blocked once. Blocking again would
    # trap the session in a loop it cannot leave, which is how a guardrail
    # earns being switched off.
    if payload.get("stop_hook_active"):
        return 0

    workspace = payload.get("cwd") or os.getcwd()
    member = (os.environ.get("CADRE_MEMBER_ID") or "").strip()
    if not member:
        # A Board session is not a Member and owes no Unit write-back.
        return 0

    directory = os.path.join(workspace, ".firm", "writeback")
    if not os.path.isdir(directory):
        return 0

    owed = []
    for path in sorted(glob.glob(os.path.join(directory, "*" + OPEN_SUFFIX))):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                marker = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(marker, dict):
            continue
        if str(marker.get("member_id") or "") != member:
            continue     # never block a Member for somebody else's debt
        owed.append(str(marker.get("unit_id") or ""))

    if not owed:
        return 0

    listed = ", ".join(u for u in owed if u)
    first = next((u for u in owed if u), "<unit-id>")
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(
        "You closed " + str(len(owed)) + " Unit(s) and recorded nothing about "
        "them: " + listed + ". The next run starts from the firm's graph, so a "
        "lesson you do not write is a lesson the firm pays for twice. Record it "
        "now: __WRITE_BACK_VERB__ " + first + " --text " + Q +
        "what this taught" + Q + " "
        "-- one call per Unit listed. Use --type correction if it was a mistake "
        "worth not repeating. Then finish.",
        file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)     # fail open, always
'''


def render_writeback_hook() -> str:
    """The gate script, as it lands on disk.

    The verb is substituted in from ``writeback.WRITE_BACK_VERB`` rather than
    written into the template, so the command the gate demands on stderr and
    the command the briefing tells a Member to run cannot drift apart. They
    drifted once already, and once the gate shipped that drift stopped being a
    typo and became a deadlock: the Member does exactly what its briefing says,
    the gate blocks, and the briefing never names the command that clears it.
    """
    from firm.services.writeback import WRITE_BACK_VERB

    rendered = _WRITEBACK_HOOK_TEMPLATE.replace("__WRITE_BACK_VERB__",
                                                WRITE_BACK_VERB)
    if "__WRITE_BACK_VERB__" in rendered:
        raise AssertionError(
            "the gate template's verb placeholder was renamed but this "
            "substitution was not, so the gate would ship telling a Member to "
            "run a literal placeholder")
    return rendered


def _register_writeback_hook(settings: dict) -> bool:
    """Add the Stop gate entry if not present. Returns True if modified."""
    command = writeback_hook_command()
    hooks = settings.setdefault("hooks", {})
    stop = hooks.setdefault("Stop", [])
    for entry in stop:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks", []) or []:
            if not isinstance(hook, dict):
                continue
            existing = str(hook.get("command") or "")
            # Match on the SCRIPT, not the whole command line. The interpreter
            # path moves when the operator rebuilds a virtual environment, and
            # matching the full string would then register a second copy of the
            # same gate and block the Member twice for one debt.
            if WRITEBACK_HOOK_SCRIPT_NAME in existing:
                if existing == command:
                    return False
                hook["command"] = command      # re-point at the live interpreter
                return True
    stop.append({"hooks": [{"type": "command", "command": command, "timeout": 10}]})
    return True


def install_writeback_hook(workspace: Path) -> tuple[int, list[str]]:
    """Install the write-back Stop gate and register it. Idempotent.

    This is the ONE blocking hook Cadre installs. ibis' ruling caps the
    blocking hook at one, not the number of hooks Cadre installs; the session
    pulse and the policy gate are the other two and neither of them is
    installed by this function.
    """
    messages: list[str] = []
    hooks_dir = workspace / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    script = hooks_dir / WRITEBACK_HOOK_SCRIPT_NAME
    script.write_text(render_writeback_hook(), encoding="utf-8")
    try:
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    except OSError:
        pass                       # Windows has no executable bit to set
    messages.append(f"Wrote {script}")

    settings_path = workspace / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings = _load_settings(settings_path)
    if _register_writeback_hook(settings):
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        messages.append(f"Registered write-back gate in {settings_path}")
    else:
        messages.append(f"Write-back gate already registered in {settings_path}")
    return 0, messages
