"""The debt a Member owes when it closes a Unit and tells nobody what it learned.

The firm's seeded rule says Members "read the firm's own graph before acting and
write what they learn back to it, so the next run starts where this one
finished." That rule was a hope. Nothing checked it, and a rule nothing checks
is a rule that gets skipped on the runs that are in a hurry — which are exactly
the runs whose lessons are worth the most.

**Why this file exists rather than a hook that reads the graph.** The obvious
design is a Stop hook that asks base whether this Member wrote anything. It
cannot: ``base learn`` writes into the graph and leaves nothing a hook can
cheaply see, and the firm has no write-back journal of its own. So the hook
needs a marker, and the thing that writes the marker is ``base cadre learn``.
The gate and the command are one piece — either both ship or neither does,
because a gate with nothing writing its marker blocks every Member forever, and
a command with no gate reading its marker is a suggestion again.

**The shape.** Two files per Unit, under ``.firm/writeback/``:

``<unit>.learned.json``
    A receipt. ``base cadre learn --unit <id>`` wrote it. Permanent, because a
    Member that learned before closing must not be asked again afterwards.

``<unit>.open.json``
    A debt. ``base cadre complete <id>`` wrote it, because that Unit closed with
    no receipt beside it. ``base cadre learn --unit <id>`` deletes it.

The gate blocks on debts and ignores receipts. Ordinary JSON in an ordinary
directory, so the hook that reads them needs no import of this package and works
on any host — which matters, because the Stop hook runs under whatever
interpreter the workspace has, not under the firm's own environment.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WRITEBACK_DIRNAME = "writeback"

#: The two verbs a Member is told to use, in one place because two surfaces name
#: them and a Member obeys whichever it happens to read.
#:
#: Before the gate existed, a briefing naming the wrong verb cost nothing. With
#: the gate it is a deadlock: the Member does exactly what its briefing says,
#: the gate blocks, and the briefing never mentions the command that clears it.
#: So the gate's stderr and the briefing are rendered from THESE, and the test
#: that guards them compares the two rendered artifacts rather than these
#: constants - two surfaces reading one constant still drift if only one of them
#: is actually wired to it.
CLOSE_VERB = "base cadre complete"
WRITE_BACK_VERB = "base cadre learn --unit"
OPEN_SUFFIX = ".open.json"
LEARNED_SUFFIX = ".learned.json"

# base's own vocabulary, read off `base learn --help` against 0.15.0, md5
# 052b9a95d6afbd938ddf21aba26b0601. Restating it here rather than passing
# anything through is deliberate: an unknown --type makes base reject the whole
# write, and finding that out through a non-zero exit code inside a Stop gate
# is the worst possible place to find it out.
LEARN_TYPES = ("insight", "correction", "decision", "commitment", "shift")


def writeback_dir(workspace: Path) -> Path:
    """Where the receipts and debts live. Beside the firm database, never global."""
    return Path(workspace) / ".firm" / WRITEBACK_DIRNAME


def _safe_unit_name(unit_id: str) -> str:
    """A filename that cannot escape the writeback directory.

    A Unit id arrives from the command line. Without this, ``--unit
    ../../../etc/passwd`` writes outside the firm, and on Windows a bare colon
    makes an alternate data stream instead of the file anybody looked for.
    """
    keep = [c if (c.isalnum() or c in "-_.") else "_" for c in str(unit_id).strip()]
    cleaned = "".join(keep).strip("._") or "unnamed"
    return cleaned[:120]


def _read_marker(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        loaded = {}
    if not isinstance(loaded, dict):
        loaded = {}
    loaded.setdefault("unit_id", path.name.split(".")[0])
    return loaded


def open_debts(workspace: Path, member_id: str | None = None) -> list[dict[str, Any]]:
    """Units this Member closed and never wrote back about.

    ``member_id`` None means every Member's debts, which is what a report wants.
    The gate always passes one: a Member must never be blocked by somebody
    else's unfinished business.
    """
    directory = writeback_dir(workspace)
    if not directory.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*" + OPEN_SUFFIX)):
        marker = _read_marker(path)
        if member_id is not None and str(marker.get("member_id") or "") != member_id:
            continue
        found.append(marker)
    return found


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_closure(workspace: Path, firm_id: str, member_id: str,
                   unit_id: str) -> dict[str, Any]:
    """Note that a Unit closed. Opens a debt unless a receipt already exists.

    Order is the Member's to choose. Learning first and closing second is the
    better habit and must not be punished, so a Unit with a receipt beside it
    opens no debt at all.
    """
    directory = writeback_dir(workspace)
    safe = _safe_unit_name(unit_id)
    receipt = directory / (safe + LEARNED_SUFFIX)
    if receipt.exists():
        return {"debt": False, "reason": "already written back", "path": str(receipt)}
    debt = directory / (safe + OPEN_SUFFIX)
    payload = {"unit_id": unit_id, "member_id": member_id, "firm_id": firm_id,
               "closed_at": _now()}
    try:
        directory.mkdir(parents=True, exist_ok=True)
        debt.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        # A Unit that closed is closed. Failing to record the debt must not
        # unwind the close, so this reports and does not raise.
        return {"debt": False, "reason": f"could not record: {exc}", "path": str(debt)}
    return {"debt": True, "path": str(debt), "unit_id": unit_id}


def _base_learn(workspace: Path, domain: str, entity: str, text: str,
                note_type: str) -> dict[str, Any]:
    """Hand the lesson to base. Never raises; reports what actually happened.

    A firm on a machine without base is degraded, never broken — the same rule
    base_domain works to. So "base is absent" and "base said no" are two
    different answers here, and only the second one is a failure.
    """
    import subprocess

    from firm.services.base_domain import _base_env
    from firm.sysconfig.service import which_base

    base = which_base()
    if not base:
        return {"ok": True, "graph": "skipped", "detail": "base is not installed"}
    try:
        done = subprocess.run(
            [base, "learn", "--domain", domain, "--entity", entity,
             "--type", note_type, "--text", text],
            capture_output=True, text=True, timeout=60,
            cwd=str(workspace), env=_base_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "graph": "failed", "detail": str(exc)}
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        return {"ok": False, "graph": "failed",
                "detail": detail[-1] if detail else f"rc={done.returncode}"}
    return {"ok": True, "graph": "written", "detail": (done.stdout or "").strip()}


def record_learning(workspace: Path, firm_id: str, member_id: str, text: str,
                    *, unit_id: str | None = None,
                    note_type: str = "insight") -> dict[str, Any]:
    """Write a lesson to the firm's graph and, with ``unit_id``, clear its debt.

    The receipt is written only when the graph write actually succeeded. A
    failed ``base learn`` that still cleared the debt would be the worst of both
    worlds: the Member walks away believing it wrote back, and nothing did.
    """
    if note_type not in LEARN_TYPES:
        return {"ok": False, "reason": f"unknown --type {note_type!r}; base takes "
                                       + ", ".join(LEARN_TYPES)}
    if not str(text or "").strip():
        return {"ok": False, "reason": "empty --text; a blank lesson is not a lesson"}

    result = _base_learn(workspace, firm_id, member_id, text, note_type)
    if not result["ok"]:
        return {"ok": False, "reason": f"base learn failed: {result['detail']}",
                "graph": result["graph"]}

    out: dict[str, Any] = {"ok": True, "graph": result["graph"],
                           "detail": result.get("detail", ""), "cleared": False}
    if not unit_id:
        return out

    directory = writeback_dir(workspace)
    safe = _safe_unit_name(unit_id)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (safe + LEARNED_SUFFIX)).write_text(
            json.dumps({"unit_id": unit_id, "member_id": member_id,
                        "firm_id": firm_id, "learned_at": _now(),
                        "graph": result["graph"]}, indent=2) + "\n",
            encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "reason": f"lesson reached the graph but the receipt "
                                       f"could not be written: {exc}",
                "graph": result["graph"]}
    debt = directory / (safe + OPEN_SUFFIX)
    if debt.exists():
        try:
            debt.unlink()
            out["cleared"] = True
        except OSError as exc:
            out["cleared"] = False
            out["detail"] = f"receipt written but the debt file survived: {exc}"
    out["unit_id"] = unit_id
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve(workspace: Path | None, firm_id: str | None,
             member_id: str | None) -> tuple[Path, str, str] | None:
    """(workspace, firm_id, member_id) or None, having said why on stderr."""
    import sys

    from firm.cli.unit import caller_member_id
    from firm.core.db import db_connection, get_db_path, resolve_firm_id

    root = Path(workspace) if workspace else Path.cwd()
    if not get_db_path(root).exists():
        print(f"Error: .firm/firm.db not found at {get_db_path(root)}. "
              f"Run 'firm init {root}' first.", file=sys.stderr)
        return None
    member = member_id or caller_member_id()
    if not member:
        print("Error: no member. Pass --member, or run inside a Member session "
              "where spawn_member_run exports CADRE_MEMBER_ID.", file=sys.stderr)
        return None
    try:
        with db_connection(root) as conn:
            resolved_firm = resolve_firm_id(conn, firm_id)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None
    return root, resolved_firm, member


def run_learn(workspace: Path | None = None, *, text: str,
              unit_id: str | None = None, note_type: str = "insight",
              firm_id: str | None = None, member_id: str | None = None) -> int:
    """`base cadre learn`. Writes the lesson, clears the Unit's debt."""
    import sys

    resolved = _resolve(workspace, firm_id, member_id)
    if resolved is None:
        return 1
    root, firm, member = resolved
    result = record_learning(root, firm, member, text,
                             unit_id=unit_id, note_type=note_type)
    if not result["ok"]:
        print(f"Error: {result['reason']}", file=sys.stderr)
        return 1
    where = {"written": f"recorded in the {firm} domain against {member}",
             "skipped": "not recorded — base is not installed on this machine"}
    print(where.get(result["graph"], result["graph"]))
    if unit_id:
        print(f"write-back for {unit_id}: "
              + ("debt cleared" if result["cleared"] else "receipt filed"))
    return 0


def run_complete(workspace: Path | None = None, *, unit_id: str,
                 outputs: list[str] | None = None, dry_run: bool = False,
                 firm_id: str | None = None, member_id: str | None = None) -> int:
    """`base cadre complete`. Closes the Unit, then opens its write-back debt.

    The close is `firm unit complete` unchanged — deliverable registration,
    audit record and AC rollup all still belong to it. This adds the one thing
    that surface has no opinion about: whether anybody ever said what the work
    taught. A dry run opens no debt, because nothing closed.
    """
    import sys

    from firm.cli.unit import run_unit_complete

    resolved = _resolve(workspace, firm_id, member_id)
    if resolved is None:
        return 1
    root, firm, member = resolved
    code = run_unit_complete(root, unit_id, member, outputs=outputs,
                             dry_run=dry_run, firm_id=firm)
    if code != 0 or dry_run:
        return code
    noted = record_closure(root, firm, member, unit_id)
    if noted["debt"]:
        print(f"write-back owed on {unit_id} — record what it taught "
              f"with `{WRITE_BACK_VERB} {unit_id} --text \"...\"` before "
              "you finish, or the session will not close.")
    else:
        print(f"write-back for {unit_id}: {noted['reason']}")
    return 0
