#!/usr/bin/env python3
"""Re-pattern chief-of-staff's Contract NEVERs against the real haystack.

chief-of-staff shipped with every deny rule written as an upstream API method
name — `chat.postMessage`, `messages.send`. The gate matches against the tool
call, where those strings never appear: the Slack tool is `slack_send_message`
and the gws CLI spells send `messages send`, with a space. Not one rule fired
in the firm's lifetime (ESC-021); meanwhile three Members held Slack USER
tokens that act as the operator himself.

Each rule below is anchored to something in that Member's own loadout, and
each forbidden act is locked on every route the Member can reach it by:

  `*slack_send_message*`  the MCP tool they actually carry
  `*messages?send*`       `?` is exactly one char — catches the CLI's
                          `messages send` AND the API's `messages.send`
                          in one rule, without `*messages*send*`'s appetite
                          for reads like `--query from:send@x.com`
  `*chat.postMessage*`    kept deliberately: raw `curl` through Bash is the
                          one place an API method name really does appear

Writes through `update_contract` (Records-logged), preserves `gates_required`,
then materializes — an unmaterialized policy is a policy that does not exist.
Idempotent: re-running writes the same rules.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from firm.core.db import connect, get_db_path
from firm.services import policy as policy_svc
from firm.services.contract import update_contract

WORKSPACE = Path("/home/chriskahler/firms/chief-of-staff")
FIRM_ID = "chief-of-staff"

# The firm's first premise: it drafts everything and sends nothing.
SLACK = "slack-desk"
GWS = "gws-acct"

SLACK_SEND = [
    {"match": "*slack_send_message*", "tool": SLACK,
     "reason": "Slack send — the firm drafts everything and sends nothing. These tokens act as the operator himself."},
    {"match": "*slack_post_to_channel*", "tool": SLACK,
     "reason": "Slack channel post — same NEVER, and public."},
    {"match": "*chat.postMessage*", "tool": SLACK,
     "reason": "Slack send via the raw HTTP API — the shell route to the same act."},
]

DENY: dict[str, list[dict[str, str]]] = {
    # Reeve — Chief of Staff. gws-acct, no slack-desk.
    "CON-001": [
        {"match": "*messages?send*", "tool": GWS,
         "reason": "Firm NEVER: drafts everything, sends nothing — no outbound mail under any condition."},
        {"match": "*drafts?send*", "tool": GWS,
         "reason": "Firm NEVER: a draft may be composed but never dispatched."},
        {"match": "*messages?delete*", "tool": GWS,
         "reason": "Permanent deletion is irreversible and outside the mandate; trash/label only."},
        {"match": "*threads?delete*", "tool": GWS,
         "reason": "Permanent thread destruction is never part of writing the morning page."},
    ],
    # Dalton — Correspondence Lead. The drafting hand, and the one with Slack.
    "CON-002": SLACK_SEND + [
        {"match": "*messages?send*", "tool": GWS,
         "reason": "Firm NEVER: every reply is drafted, none are sent — this is the lock, not the guideline."},
        {"match": "*drafts?send*", "tool": GWS,
         "reason": "Firm NEVER: the drafting scope carries send; this removes it."},
        {"match": "*messages?delete*", "tool": GWS,
         "reason": "Triage labels and archives; it never permanently destroys a thread."},
        {"match": "*threads?delete*", "tool": GWS,
         "reason": "Permanent deletion of a correspondence thread is irreversible and outside triage."},
        {"match": "*threads?batchDelete*", "tool": GWS,
         "reason": "The bulk form of the same irreversible act."},
        {"match": "*chat.spaces.messages.create*", "tool": GWS,
         "reason": "Google Chat send — same NEVER as email; gws scopes carry it and the mandate forbids it."},
    ],
    # Courtney — Follow-Through Officer. Reads the calendar, never writes it:
    # every calendar write emails the attendees, which is a send in disguise.
    "CON-003": SLACK_SEND + [
        {"match": "*messages?send*", "tool": GWS,
         "reason": "Firm NEVER: chasing a commitment produces a draft for the operator, never an email from her."},
        {"match": "*drafts?send*", "tool": GWS,
         "reason": "Firm NEVER: the send verb is removed even though her Gmail scope permits it."},
        {"match": "*events?insert*", "tool": GWS,
         "reason": "Board NEVER: a calendar write emails invites to attendees — a send in disguise."},
        {"match": "*events?update*", "tool": GWS,
         "reason": "Board NEVER: updating an event notifies every attendee by email; she reads the calendar, she does not write it."},
        {"match": "*events?patch*", "tool": GWS,
         "reason": "Board NEVER: same invite-email side effect as update, via a different verb."},
        {"match": "*events?delete*", "tool": GWS,
         "reason": "Board NEVER: cancelling an event blasts a cancellation email to attendees."},
        {"match": "*messages?delete*", "tool": GWS,
         "reason": "A dead commitment gets cut or snoozed in BASE, never erased from the record."},
    ],
    # Cooper — Operations Engineer. The firm's only `rw` hand, so the only
    # one whose credential would actually let the destructive half through.
    "CON-004": SLACK_SEND + [
        {"match": "*messages?send*", "tool": GWS,
         "reason": "Firm NEVER: the gmail settings scope he needs for labels and filters carries send; this removes it."},
        {"match": "*drafts?send*", "tool": GWS,
         "reason": "Firm NEVER: no Member of this firm dispatches mail, including the one who maintains the mailbox."},
        {"match": "*messages?delete*", "tool": GWS,
         "reason": "Cleaning an inbox means labeling and filtering it, never permanently destroying mail."},
        {"match": "*threads?delete*", "tool": GWS,
         "reason": "Permanent thread deletion is irreversible; hygiene work must be undoable."},
        {"match": "*events?delete*", "tool": GWS,
         "reason": "'The calendar reads cleanly' is a labeling and hygiene outcome — deleting an event emails a cancellation to every attendee."},
        {"match": "*filters?delete*", "tool": GWS,
         "reason": "Filter writes are Board-gated; deletion is the destructive half the credential over-grants and no gate should have to catch."},
    ],
    # Ezra — Tome Keeper. Carried slack-desk with NO deny rules at all: not a
    # dead rule, no rule. The armory report named Dalton, Courtney and Cooper
    # as the exposed Members and missed him entirely, because a Member with an
    # empty policy does not appear in policy.json to be checked.
    "CON-005": list(SLACK_SEND),
}


def main() -> int:
    conn = connect(get_db_path(WORKSPACE))
    changed = []
    for contract_id, deny in DENY.items():
        row = conn.execute(
            "SELECT validation_config FROM contract WHERE id = ? AND firm_id = ?",
            (contract_id, FIRM_ID),
        ).fetchone()
        if row is None:
            print(f"  !! {contract_id} not found — skipped")
            continue
        vc = policy_svc._parse_json_col(dict(row), "validation_config")
        before = len(vc.get("deny") or [])
        vc["deny"] = deny                      # gates_required and the rest stand
        update_contract(conn, contract_id, {"validation_config": json.dumps(vc)})
        changed.append(f"{contract_id}: {before} → {len(deny)} rules")
    conn.commit()

    path = policy_svc.materialize(conn, WORKSPACE, FIRM_ID)
    blind = policy_svc.unfireable_members(conn, FIRM_ID)
    conn.close()

    for line in changed:
        print("  " + line)
    print(f"\n  materialized: {path}")
    print(f"  blind members remaining: {[b['name'] for b in blind] or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
