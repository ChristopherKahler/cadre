"""What a Member is handed when its session opens.

The landed design put this in the extension manifest: SPARQL in
``hooks.session_start.queries`` and an ``inject`` template rendering the
results. Measured on base 0.15.0, md5 052b9a95d6afbd938ddf21aba26b0601:
**``inject`` does not put query results into its text.** ``{total}``,
``${total}`` and ``{{total}}`` all reach the Member unchanged, while a missing
query file errors on stderr — which proves base opens the query and simply never
feeds the answer back. A briefing written there renders a template with holes in
it. That is issue #6, and it is why this is command code.

**What this deliberately does NOT print, and why.** The firm's rules and its
decisions already arrive through base's domain layer, in the ``[DOMAIN: <firm>]``
and ``[<firm> CONTEXT]`` blocks, on every prompt. Printing them here would put
them in a Member's context twice. A Member's own prompt is about 6 KB and the
hooks already add 12 to 14 KB before any work starts, so a second copy is a real
cost paid on every run.

So this prints the part the domain layer cannot know: **which Units are this
Member's, and what closing one requires.** One line says where the rules came
from, so a Member reading only this is not left thinking there were none.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from firm.services.writeback import CLOSE_VERB, WRITE_BACK_VERB

# Read off the CHECK constraint on unit.status, not from memory:
#   pending, in_progress, blocked, in_review, done, cancelled
# A Unit is this Member's business until it is done or cancelled. Listing the
# FINISHED two and treating everything else as open is deliberate: a migration
# that adds a new working status then shows up in the briefing by default,
# where the opposite spelling would silently drop it.
FINISHED_STATUSES = ("done", "cancelled")


def _clip(text: Any, limit: int) -> str:
    value = str(text or "").strip().replace("\n", " ")
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def resolve_member_id(conn: Any, explicit: str | None = None) -> str | None:
    """Which Member this briefing is for. Never guesses.

    Precedence: an explicit argument, then ``CADRE_MEMBER_ID``, which
    ``spawn_member_run`` exports into every Member run. A Board session has
    neither and gets no Member briefing, which is correct — the Board is not a
    Member and inventing one for it would put somebody else's work on its screen.
    """
    if explicit:
        return explicit
    from_env = (os.environ.get("CADRE_MEMBER_ID") or "").strip()
    return from_env or None


def _member_row(conn: Any, firm_id: str, member_id: str) -> dict[str, Any] | None:
    from firm.core import repo
    rows = repo.find(conn, "member", firm_id=firm_id, id=member_id)
    return rows[0] if rows else None


def _open_units(conn: Any, firm_id: str, member_id: str) -> list[dict[str, Any]]:
    from firm.core import repo
    rows = repo.find(conn, "unit", firm_id=firm_id, assignee_member_id=member_id)
    return [r for r in rows
            if str(r.get("status") or "").lower() not in FINISHED_STATUSES]


def render(conn: Any, firm_id: str, member_id: str | None) -> str:
    """The briefing text. Never raises; an empty string means nothing to say."""
    if not member_id:
        return ""
    member = _member_row(conn, firm_id, member_id)
    if member is None:
        # An id that names no Member is a wiring fault, not an empty briefing.
        # Saying so is the difference between "you have no work" and "I could
        # not find you", and those need different actions from whoever reads it.
        return (f"[CADRE BRIEF] {member_id} is not a Member of {firm_id}. "
                "The run was handed a member id this firm does not have — check "
                "CADRE_MEMBER_ID against `firm member list`.")

    lines: list[str] = []
    name = str(member.get("name") or member_id)
    role = str(member.get("role") or "").strip()
    header = f"[CADRE BRIEF] {name}"
    if role:
        header += f", {role}"
    header += f" — {firm_id}"
    lines.append(header)

    units = _open_units(conn, firm_id, member_id)
    if not units:
        # Absent is not empty. This Member genuinely has nothing open, and
        # saying so plainly stops it inventing work to justify the run.
        lines.append("Open Units: none assigned to you. Do not start work "
                     "nobody asked for — raise it with `firm escalation raise` "
                     "if you think there should be some.")
    else:
        lines.append(f"Open Units ({len(units)}):")
        projects: list[str] = []
        for unit in units:
            unit_id = str(unit.get("id") or "?")
            status = str(unit.get("status") or "?")
            title = _clip(unit.get("name"), 90)
            lines.append(f"  {unit_id} [{status}] {title}")
            criteria = _clip(unit.get("acceptance_criteria"), 160)
            if criteria:
                lines.append(f"      done when: {criteria}")
            project = str(unit.get("project_id") or "").strip()
            if project and project not in projects:
                projects.append(project)
        if projects:
            lines.append(f"Projects: {', '.join(projects)}")

    lines.append(
        "Closing one: `" + CLOSE_VERB + " <id> --outputs <file>`. That opens a "
        "write-back debt on the Unit, and your session will not end until you "
        "settle it: `" + WRITE_BACK_VERB + " <id> --text \"what it taught\"`, "
        "one call per Unit you closed. Use `--type correction` for a mistake "
        "worth not repeating. Queue follow-up work with `firm unit create`.")
    lines.append(
        "Your firm's rules and decisions arrive separately, in the "
        f"[DOMAIN: {firm_id}] and [{firm_id} CONTEXT] blocks — they are not "
        "repeated here.")
    return "\n".join(lines)


def run_brief(workspace: Path | None = None, *, firm_id: str | None = None,
              member_id: str | None = None) -> int:
    """`base cadre brief`. Prints the briefing, returns 0.

    Returns 0 even with nothing to say. This is called from a SessionStart hook
    and a hook that fails a session start over a missing briefing is worse than
    one that says nothing.
    """
    from firm.core.db import db_connection, get_db_path, resolve_firm_id

    root = Path(workspace) if workspace else Path.cwd()
    if not get_db_path(root).exists():
        return 0
    try:
        with db_connection(root) as conn:
            resolved_firm = resolve_firm_id(conn, firm_id)
            resolved_member = resolve_member_id(conn, member_id)
            text = render(conn, resolved_firm, resolved_member)
    except Exception:
        return 0
    if text:
        print(text)
    return 0
