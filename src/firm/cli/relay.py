"""`cadre relay` — talk to the sessions inside one firm's own message store.

A firm keeps its own store because #117 gives it its own BASE_HOME, and base
keeps the relay inbox in that tier. `base relay ping` typed by hand reaches
whatever store the environment happens to name, which is the operator's; a
Member running inside a firm is not registered there and never hears it.

So this verb does the three things a person would otherwise have to do by hand,
and does them the same way every time: find the firm (explicitly, or by walking
up from the current directory), point base at that firm's tier, and drive
base's own relay there. When the target is not reachable it says so and exits
non-zero, naming the store it looked in -- a steer that silently falls back is
the failure this whole command exists to make impossible.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from firm.services import firm_relay


def _firm_or_none(explicit: Path | None) -> Path | None:
    if explicit:
        workspace = Path(explicit).expanduser()
        if not (workspace / ".firm" / "firm.db").exists():
            print(f"Error: {workspace} is not a firm — no .firm/firm.db there.",
                  file=sys.stderr)
            return None
        return workspace
    found = firm_relay.resolve_firm()
    if found is None:
        print(f"Error: no firm here. {Path.cwd()} and none of its parents hold "
              ".firm/firm.db, so there is no message store to talk to. Pass "
              "--firm <dir>, or run this from inside a firm.", file=sys.stderr)
    return found


def run_relay(args) -> int:
    """Dispatch one relay verb against one firm. Exit code is the verb's."""
    workspace = _firm_or_none(getattr(args, "firm_dir", None))
    if workspace is None:
        return 2

    if args.relay_command == "sessions":
        result = firm_relay.sessions(workspace)
        print(f"store: {result['store']}")
        if not result["ok"]:
            print(f"Error: {result['reason']}", file=sys.stderr)
            return 1
        if not result["titles"]:
            print("no sessions registered in this firm's store yet")
            return 0
        for title, info in sorted(result["titles"].items()):
            print(f"  {info['line']}")
        print(f"{len(result['titles'])} session(s) in this firm's store")
        return 0

    if args.relay_command == "ping":
        result = firm_relay.ping(workspace, to=args.to, message=args.msg,
                                 from_name=args.from_name)
    elif args.relay_command == "task":
        slug = args.slug or f"cadre-steer-{int(time.time())}"
        result = firm_relay.task(workspace, to=args.to, summary=args.summary,
                                 slug=slug, from_name=args.from_name)
    else:
        print(f"Error: unknown relay command {args.relay_command!r}",
              file=sys.stderr)
        return 2

    if not result["ok"]:
        print(f"Error: {result['reason']}", file=sys.stderr)
        return 1
    print(f"delivered to {result['to']} in {result['store']}")
    return 0
