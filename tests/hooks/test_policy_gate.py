"""The policy gate's aim — replayed through the REAL installed hook.

This is the test ESC-021 demanded and `policy.json` never had. It does not
re-implement the matcher: it installs the shipped hook with
``install_policy_hook`` and runs that exact file as a subprocess, feeding it
the PreToolUse payload Claude Code feeds it. A matcher a test re-implements
is a matcher nobody tested (UNIT-012 §7 — the offline replica agreed with a
gate that was aiming at nothing).

Two failure classes are pinned here, and they point in opposite directions:

- **False negative (ESC-021, critical):** the seeded patterns were upstream API
  method names (``chat.postMessage``, dotted ``messages.send``) that never
  appear in ``"<tool_name> <tool_input JSON>"``. The Slack send tool is called
  ``slack_send_message``; gws spells send ``gmail users messages send`` — with
  spaces. 6 of 7 must-block scenarios came back ALLOW and no rule had fired in
  the firm's lifetime.
- **False positive (ESC-027/028):** the haystack included a ``Write``'s file
  body, so the gate blocked a Member *writing a report that quoted the rule*
  while the send it aimed at sailed through.

Scenario IDs map to the UNIT-012 §7 table so a future reader can diff the
report against the suite.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from firm.cli.install_hooks import POLICY_HOOK_SCRIPT_NAME, install_policy_hook

# The firm this was found in. Rules mirror the shape the fixed Contracts carry:
# a real tool-name pattern, a space-separated CLI form, and the API-method
# string kept because it is what catches raw `curl` through Bash (ESC-028).
SLACK_RULES = [
    {"match": "*slack_send_message*", "reason": "Slack send — drafts everything, sends nothing.",
     "tool": "slack-desk"},
    {"match": "*slack_post_to_channel*", "reason": "Slack channel post — same NEVER.",
     "tool": "slack-desk"},
    {"match": "*chat.postMessage*", "reason": "Slack send via raw API — same NEVER.",
     "tool": "slack-desk"},
]
GWS_RULES = [
    {"match": "*messages send*", "reason": "Gmail send (CLI form) — no outbound mail.",
     "tool": "gws-acct"},
    {"match": "*drafts send*", "reason": "A draft may be composed, never dispatched.",
     "tool": "gws-acct"},
    {"match": "*messages.send*", "reason": "Gmail send (API form) — no outbound mail.",
     "tool": "gws-acct"},
    {"match": "*events insert*", "reason": "A calendar write emails invites — a send in disguise.",
     "tool": "gws-acct"},
]

POLICY = {
    "MEM-001": GWS_RULES,                    # Reeve — gws-acct, no slack-desk
    "MEM-002": SLACK_RULES + GWS_RULES,      # Dalton — correspondence
    "MEM-003": SLACK_RULES + GWS_RULES,      # Courtney — follow-through
}


@pytest.fixture
def gate(tmp_path: Path):
    """Install the real hook + a materialized policy; return a callable."""
    install_policy_hook(tmp_path)
    firm_dir = tmp_path / ".firm"
    firm_dir.mkdir(parents=True, exist_ok=True)
    (firm_dir / "policy.json").write_text(json.dumps(POLICY), encoding="utf-8")
    hook = tmp_path / ".claude" / "hooks" / POLICY_HOOK_SCRIPT_NAME

    def run(member_id: str, tool_name: str, tool_input: dict) -> dict:
        """Feed the hook a PreToolUse payload; return {} for allow."""
        proc = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "PreToolUse",
                "cwd": str(tmp_path),
                "tool_name": tool_name,
                "tool_input": tool_input,
            }),
            capture_output=True,
            text=True,
            env={"CADRE_MEMBER_ID": member_id, "PATH": "/usr/bin:/bin"},
            timeout=30,
        )
        assert proc.returncode == 0, f"the gate must never brick a session: {proc.stderr}"
        return json.loads(proc.stdout) if proc.stdout.strip() else {}

    run.workspace = tmp_path  # type: ignore[attr-defined]
    return run


def _blocked(decision: dict) -> bool:
    return (decision.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"


# ---------------------------------------------------------------------------
# MUST BLOCK — every one of these came back ALLOW before the re-pattern
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case,member,tool,tool_input", [
    # UNIT-012 §7 row 1 — Dalton DMs a human on Slack, as Chris.
    ("dm-a-human", "MEM-002", "mcp__slack-desk__slack_send_message",
     {"workspace": "CADDY-HQ", "channel_id": "D07L2MVATPF", "text": "on it"}),
    # row 2 — Dalton posts to a channel.
    ("post-to-channel", "MEM-002", "mcp__slack-desk__slack_post_to_channel",
     {"workspace": "EXTENDLY", "channel_id": "C04BMHDBC0Y", "text": "shipping today"}),
    # row 3 — Courtney chases a commitment in Slack.
    ("chase-in-slack", "MEM-003", "mcp__slack-desk__slack_send_message",
     {"workspace": "EXTENDLY", "channel_id": "D06UE9UP52T", "text": "circling back"}),
    # row 4 — Reeve sends mail via gws-acct. Spaces, not dots.
    ("gws-space-form-send", "MEM-001", "Bash",
     {"command": "gws-acct gmail users messages send --to a@b.com --subject hi"}),
    # row 5 — Dalton dispatches a drafted reply.
    ("gws-drafts-send", "MEM-002", "Bash",
     {"command": "gws-acct gmail users drafts send --id r-123"}),
    # row 6 — Courtney creates an event (invite emails = a send in disguise).
    ("calendar-events-insert", "MEM-003", "Bash",
     {"command": "gws-acct calendar events insert --summary 'sync' --attendee a@b.com"}),
    # ESC-028's other half — the API-method string still earns its keep: raw
    # curl through Bash is exactly what it catches.
    ("bash-curl-postmessage", "MEM-002", "Bash",
     {"command": "curl -X POST -d channel=C123 -d text=hi https://slack.com/api/chat.postMessage"}),
])
def test_must_block(gate, case, member, tool, tool_input):
    decision = gate(member, tool, tool_input)
    assert _blocked(decision), f"{case}: the Contract NEVER did not fire"


def test_denial_is_logged_as_evidence(gate):
    """A denial the pulse can turn into Records + an escalation."""
    gate("MEM-002", "mcp__slack-desk__slack_send_message",
         {"channel_id": "D07", "text": "hi"})
    log = gate.workspace / ".firm" / "policy-denials.jsonl"
    assert log.exists(), "the denial left no receipt"
    evt = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert evt["member_id"] == "MEM-002"
    assert evt["tool_name"] == "mcp__slack-desk__slack_send_message"


# ---------------------------------------------------------------------------
# MUST ALLOW — the false-positive class (ESC-027/028)
# ---------------------------------------------------------------------------

# The exact prose that got the UNIT-012 report blocked: it quotes every
# pattern the firm denies. Documentation about a rule must never trip it.
REPORT_TEXT = """# UNIT-012 §7 — the Contract NEVERs fire on the wrong things

The Slack send tool is named `slack_send_message`, not `chat.postMessage`.
The gws CLI spells it `gmail users messages send` — spaces, not dots — so
`*messages.send*` cannot match. Recommended: `*chat.postMessage*` →
`*slack_send_message*` and `*slack_post_to_channel*`; `*drafts.send*` →
`*drafts send*`; `*events.insert*` → `*events insert*`.
"""


@pytest.mark.parametrize("case,member,tool,tool_input", [
    # UNIT-012 §7 row 7 — the control: an ordinary read.
    ("read-a-profile", "MEM-001", "Bash",
     {"command": "gws-acct gmail users getProfile --account chris"}),
    # ESC-027 — the report that documents the rule. This is the one that fired.
    ("write-report-quoting-rules", "MEM-001", "Write",
     {"file_path": "/tmp/UNIT-012-armory-capability-report.md", "content": REPORT_TEXT}),
    # Same class, other verb.
    ("edit-doc-quoting-rules", "MEM-001", "Edit",
     {"file_path": "/tmp/ENGINEERING.md", "old_string": "old text",
      "new_string": REPORT_TEXT}),
    ("multiedit-doc-quoting-rules", "MEM-002", "MultiEdit",
     {"file_path": "/tmp/policy.md",
      "edits": [{"old_string": "x", "new_string": REPORT_TEXT}]}),
    # Ordinary reads of a file whose very path names the forbidden verb.
    ("read-a-file", "MEM-002", "Read",
     {"file_path": "/home/chris/notes/how-messages-send-works.md"}),
    # Reading Slack is the job. Only sending is forbidden.
    ("read-slack-unreads", "MEM-002", "mcp__slack-desk__slack_get_unreads", {}),
    ("search-slack", "MEM-003", "mcp__slack-desk__slack_search_messages",
     {"query": "chat.postMessage"}),
    # An emoji ack is not a send.
    ("react-in-slack", "MEM-002", "mcp__slack-desk__slack_add_reaction",
     {"channel_id": "C0BDWCXDPE3", "name": "white_check_mark"}),
])
def test_must_allow(gate, case, member, tool, tool_input):
    decision = gate(member, tool, tool_input)
    assert not _blocked(decision), f"{case}: the gate blocked work the Contract permits"


def test_board_session_is_never_gated(gate):
    """No CADRE_MEMBER_ID = a Board session. The gate governs Members only."""
    hook = gate.workspace / ".claude" / "hooks" / POLICY_HOOK_SCRIPT_NAME
    proc = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps({"cwd": str(gate.workspace),
                          "tool_name": "mcp__slack-desk__slack_send_message",
                          "tool_input": {"text": "board acting deliberately"}}),
        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, timeout=30,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "", "the gate must not gate the Board"


def test_a_member_with_no_rules_is_allowed(gate):
    """MEM-009 is not in policy.json — nothing to enforce, nothing to deny."""
    assert not _blocked(gate("MEM-009", "mcp__slack-desk__slack_send_message",
                             {"text": "hi"}))


# ---------------------------------------------------------------------------
# The `tool` label (fork 014) explains a rule; it must never disable one
# ---------------------------------------------------------------------------

def test_unlabeled_rule_still_enforces(gate, tmp_path):
    """A rule with no `tool` label is a rule, not a suggestion."""
    (tmp_path / ".firm" / "policy.json").write_text(json.dumps({
        "MEM-007": [{"match": "*slack_send_message*", "reason": "legacy rule, no label"}],
    }), encoding="utf-8")
    assert _blocked(gate("MEM-007", "mcp__slack-desk__slack_send_message",
                         {"text": "hi"}))


def test_a_typod_label_cannot_disable_a_never(gate, tmp_path):
    """One wrong character in a label must not silently unlock a send.

    Scoping enforcement by the label was tried and rejected: the gate cannot
    tell `slakc-desk` (a typo) from a label naming other equipment, so any
    label-based skip rebuilds ESC-021 — a rule that looks enforced and fires
    at nothing. The label is metadata; the tool name does the scoping.
    """
    (tmp_path / ".firm" / "policy.json").write_text(json.dumps({
        "MEM-008": [{"match": "*slack_send_message*", "reason": "typo'd label",
                     "tool": "slakc-desk"}],
    }), encoding="utf-8")
    assert _blocked(gate("MEM-008", "mcp__slack-desk__slack_send_message",
                         {"text": "hi"}))


def test_label_reaches_the_denial_receipt(gate):
    """Fork 014's actual job: the Board reads denials grouped by equipment."""
    gate("MEM-002", "mcp__slack-desk__slack_send_message", {"text": "hi"})
    evt = json.loads((gate.workspace / ".firm" / "policy-denials.jsonl")
                     .read_text(encoding="utf-8").splitlines()[0])
    assert evt["equips"] == "slack-desk"
