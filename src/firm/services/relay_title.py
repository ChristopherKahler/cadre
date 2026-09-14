"""A Member answers to its own name, in the same inbox, every run (#122).

WHAT CHRIS ASKED FOR, and it is the whole specification: he steers a Member
knowing only that Member's name. Not a row id he has to look up, not a
memorable animal a model picked this run, not a different inbox tomorrow.

WHAT WAS BROKEN, measured 2026-09-14. Two places, and the second undid the
first:

  * `spawn.py` pinned the relay title to `<firm_id>-<member_id>`. Stable, but
    `member_id` is the ROW ID: the Member called Pen is `MEM-001`, so the title
    was `demo-MEM-001` and steering Pen meant first looking up that Pen is
    MEM-001 -- the lookup he was asking to be rid of.
  * The charter handed to every Member said, at the start of every run,
    `base relay register --as <a-short-memorable-title>`. That runs AFTER the
    pinned environment is in place, so it threw the pin away and bound a fresh
    name chosen by a model. Same Member, different title every run.

WHY THE TITLE IS THE BARE NAME. #117 gives each firm its own message store, so
`pen` inside this firm cannot collide with `pen` in another firm or with
anything in the operator's own store. The firm prefix bought namespacing that
the store now provides, and it cost Chris the one thing he asked for.

TWO CASES THIS MODULE EXISTS TO HANDLE HONESTLY, because both are silent
otherwise:

  * **Collision.** Two active Members whose names slug to the same title. A
    deterministic tie-break would send his steer to the wrong Member and look
    like it worked, which is exactly the failure this lane exists to remove.
    So BOTH get `<name>-<member id>` -- never one of them quietly winning --
    and `collisions()` reports it so it can be fixed by renaming.
  * **Rename.** The inbox is a directory named after the title, so renaming a
    Member orphans everything unread in the old one. :func:`bind` moves those
    messages into the new inbox, leaves a pointer behind, and deletes nothing.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Where each Member's bound title is recorded, beside the rest of a firm's
#: per-machine state. Without this, a rename is undetectable: the new title is
#: computed fresh every run and nothing remembers what it used to be.
TITLES_FILE = "relay-titles.json"

#: Left in an inbox that has been renamed away from, so anyone reading the old
#: directory later can see where its messages went.
POINTER_FILE = ".renamed-to"

#: The running monitor's liveness sentinel belongs to the session watching the
#: OLD path; moving it would tell the board a dead watcher is fresh.
_NEVER_MOVE = {".watching", POINTER_FILE}


def slug(name: str) -> str:
    """`Pen` -> `pen`, `Ops Lead` -> `ops-lead`. Stable across runs by design."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def _titles_path(workspace: Path | str) -> Path:
    return Path(workspace) / ".firm" / TITLES_FILE


def _read_titles(workspace: Path | str) -> dict[str, str]:
    path = _titles_path(workspace)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in loaded.items()} if isinstance(loaded, dict) else {}


def _write_titles(workspace: Path | str, titles: dict[str, str]) -> None:
    path = _titles_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(titles, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _roster(workspace: Path | str, firm_id: str) -> list[dict[str, Any]] | None:
    """Every Member row, or None when the firm's db cannot be read.

    None is not an empty firm. A firm whose roster could not be read must not
    be treated as a firm with nobody in it, or the collision check silently
    stops checking.
    """
    try:
        from firm.core.db import db_connection
        from firm.core import repo

        with db_connection(Path(workspace)) as conn:
            if firm_id:
                return repo.find(conn, "member", firm_id=firm_id)
            return repo.find(conn, "member")
    except Exception:
        return None


def member_title(workspace: Path | str, member_id: str,
                 firm_id: str = "") -> dict[str, Any]:
    """The title this Member answers to, and why it is that.

    Never raises, and never invents a name: when the roster cannot be read the
    old `<firm>-<member id>` shape is used and the reason says so, because a
    made-up title is worse than an ugly one -- it is a title nobody can guess.
    """
    out: dict[str, Any] = {"title": "", "source": "", "reason": "",
                           "collision": False}
    fallback = f"{firm_id}-{member_id}" if firm_id else str(member_id)

    rows = _roster(workspace, firm_id)
    if rows is None:
        out.update(title=fallback, source="fallback",
                   reason=("the firm's roster could not be read, so this "
                           "Member's name is unknown and its row id is the "
                           "only stable title available"))
        return out

    me = next((r for r in rows if str(r.get("id")) == str(member_id)), None)
    if me is None or not slug(str(me.get("name") or "")):
        out.update(title=fallback, source="fallback",
                   reason=(f"no Member {member_id!r} with a usable name in "
                           "this firm, so its row id is the title"))
        return out

    mine = slug(str(me["name"]))
    others = [r for r in rows
              if str(r.get("id")) != str(member_id)
              and str(r.get("status") or "active") == "active"
              and slug(str(r.get("name") or "")) == mine]
    if others:
        out.update(
            title=f"{mine}-{str(member_id).lower()}", source="name+id",
            collision=True,
            reason=("another active Member answers to the same name ("
                    + ", ".join(str(r.get("id")) for r in others)
                    + "), so BOTH carry their row id — picking one of them "
                      "silently would send a steer to the wrong Member"))
        return out

    out.update(title=mine, source="name")
    return out


def collisions(workspace: Path | str, firm_id: str = "") -> list[dict[str, Any]]:
    """Names that two or more ACTIVE Members share, for `firm doctor`.

    Empty list when the roster is unreadable is wrong, so that case returns a
    single entry saying the check could not run.
    """
    rows = _roster(workspace, firm_id)
    if rows is None:
        return [{"title": "", "members": [],
                 "reason": "the firm's roster could not be read, so name "
                           "collisions could not be checked"}]
    by_slug: dict[str, list[str]] = {}
    for row in rows:
        if str(row.get("status") or "active") != "active":
            continue
        key = slug(str(row.get("name") or ""))
        if key:
            by_slug.setdefault(key, []).append(str(row.get("id")))
    return [{"title": key, "members": sorted(ids), "reason": ""}
            for key, ids in sorted(by_slug.items()) if len(ids) > 1]


def _migrate_inbox(store: Path, previous: str, title: str) -> dict[str, Any]:
    """Carry unread messages from the old inbox to the new one. Deletes nothing."""
    out: dict[str, Any] = {"moved": 0, "kept": 0, "detail": ""}
    old = store / previous
    if not old.is_dir():
        out["detail"] = (f"nothing to carry over: {old} does not exist")
        return out
    new = store / title
    new.mkdir(parents=True, exist_ok=True)
    for item in sorted(old.iterdir()):
        if item.name in _NEVER_MOVE or not item.is_file():
            continue
        destination = new / item.name
        if destination.exists():
            out["kept"] += 1          # never overwrite a message
            continue
        try:
            shutil.move(str(item), str(destination))
            out["moved"] += 1
        except OSError:
            out["kept"] += 1
    try:
        (old / POINTER_FILE).write_text(
            json.dumps({"renamed_to": title,
                        "at": datetime.now(timezone.utc).isoformat()}) + "\n",
            encoding="utf-8")
    except OSError:
        pass
    out["detail"] = (f"{out['moved']} message(s) carried from {old} to {new}"
                     + (f", {out['kept']} left in place" if out["kept"] else ""))
    return out


def bind(workspace: Path | str, member_id: str,
         firm_id: str = "") -> dict[str, Any]:
    """Resolve this Member's title, remember it, and follow a rename.

    Returns the title plus whatever needs saying about it. Never raises: a
    Member run must not fail because its inbox could not be tidied.
    """
    from firm.services.firm_relay import store_path

    resolved = member_title(workspace, member_id, firm_id)
    title = resolved["title"]
    notes: list[str] = []
    if resolved["reason"]:
        notes.append(resolved["reason"])

    titles = _read_titles(workspace)
    previous = titles.get(str(member_id))
    if previous and previous != title:
        moved = _migrate_inbox(store_path(workspace), previous, title)
        notes.append(f"relay title changed from {previous!r} to {title!r}: "
                     + moved["detail"])
    if previous != title:
        titles[str(member_id)] = title
        try:
            _write_titles(workspace, titles)
        except OSError as exc:
            notes.append(f"could not record the relay title ({exc}), so a "
                         "later rename will not be able to follow it")

    return {"title": title, "previous": previous or "", "notes": notes,
            "collision": resolved["collision"], "source": resolved["source"]}
