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
    # Google Chat. Carried by the live chief-of-staff contracts and missing
    # here until the noise-hardening fork went looking: the must-block set
    # blocks nothing a rule is not aimed at, so a fixture that lags the real
    # Contract quietly stops testing a route the firm actually locks.
    {"match": "*spaces?messages?create*", "reason": "Chat send (CLI form) — same NEVER.",
     "tool": "gws-acct"},
    {"match": "*chat?+send*", "reason": "Chat send (shorthand form) — same NEVER.",
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

    def run(member_id: str, tool_name: str, tool_input: dict,
            env: dict | None = None) -> dict:
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
            env={"CADRE_MEMBER_ID": member_id, "PATH": "/usr/bin:/bin", **(env or {})},
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


# ---------------------------------------------------------------------------
# A mention is not an invocation (fork: policy-noise-hardening)
#
# Fork 015 fixed prose in Write/Edit and left shells matching whole-string,
# writing the cost off as accepted. It was not: a Member greping for the
# pattern to VERIFY the lock was blocked, and so was a Member escalating to
# REPORT that the lock was open. The gate could not stop the send, but it
# stopped the report about the send — ESC-021's inversion, rebuilt.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case,member,command", [
    # The two denials the live chief-of-staff log was holding un-ingested.
    ("grep-to-verify-the-lock", "MEM-002",
     r'grep -n "SLACK_NO_SEND\|slack_send_message\|slack_post_to_channel" .mcp.json'),
    ("escalate-about-the-lock", "MEM-002",
     'firm escalation raise --member MEM-004 --title "UNIT-022: unrestricted Slack" '
     '--body "slack_send_message is reachable; the NEVER is not enforced"'),
    # The rest of the enumerated mention set.
    ("grep-the-pattern", "MEM-002", "grep -rn slack_send_message src/"),
    ("cat-the-policy", "MEM-001", "cat .firm/policy.json"),
    ("echo-the-pattern", "MEM-002", "echo chat.postMessage"),
    ("base-learn-quoting-a-pattern", "MEM-003",
     'base learn --text "the gate blocks slack_send_message" --domain cadre'),
    ("read-the-denial-log", "MEM-002",
     "tail -5 .firm/policy-denials.jsonl | grep chat.postMessage"),
    # Prose arguments carry shell punctuation; quoted, it is data.
    ("escalation-body-with-punctuation", "MEM-002",
     'firm escalation raise --title x --body "first chat.postMessage; then | done"'),
    ("git-read-piped-to-grep", "MEM-001", "git log --oneline | grep messages.send"),
    ("sed-print-only", "MEM-001", 'sed -n "/slack_send_message/p" file'),
])
def test_a_mention_is_allowed(gate, case, member, command):
    decision = gate(member, "Bash", {"command": command})
    assert not _blocked(decision), f"{case}: the gate read a mention as a send"


@pytest.mark.parametrize("case,member,command", [
    # Everything that can send stays exactly as fail-closed as before.
    ("curl-the-api", "MEM-002",
     "curl -X POST -d text=hi https://slack.com/api/chat.postMessage"),
    ("gws-space-form-send", "MEM-001",
     "gws-acct gmail users messages send --to a@b.com --subject hi"),
    ("gws-chat-spaces-create", "MEM-002",
     "gws-acct extendly chat spaces messages create --space spaces/AAA --text hi"),
    # A read-only head is not a shield for what runs after it.
    ("read-head-piped-into-sender", "MEM-002",
     "grep x | curl -X POST -d text=hi https://slack.com/api/chat.postMessage"),
    ("read-head-chained-into-sender", "MEM-002",
     "cat draft.txt && curl -X POST https://slack.com/api/chat.postMessage"),
    ("sender-in-command-substitution", "MEM-002",
     "echo $(curl -X POST https://slack.com/api/chat.postMessage)"),
    ("sender-in-backticks", "MEM-002",
     "echo `curl -X POST https://slack.com/api/chat.postMessage`"),
    # An allowlisted head that hands off control is not an inspection.
    ("find-exec-a-sender", "MEM-002",
     r"find . -exec curl -d x https://slack.com/api/chat.postMessage \;"),
    ("awk-system-a-sender", "MEM-002",
     'awk \'BEGIN{system("curl -d x https://slack.com/api/chat.postMessage")}\''),
    ("interpreter-is-never-an-inspection", "MEM-002",
     'python3 -c "import urllib.request; urllib.request.urlopen('
     '\'https://slack.com/api/chat.postMessage\')"'),
    ("bash-dash-c", "MEM-002", 'bash -c "curl https://slack.com/api/chat.postMessage"'),
    # A head is only the head it resolves to.
    ("path-shadowed-head", "MEM-002",
     "PATH=/tmp/evil grep -rn chat.postMessage x"),
    # These CLIs act as well as read; only the read verbs are exempt.
    ("firm-notify-dms-the-board", "MEM-002",
     'firm notify --msg "chat.postMessage"'),
])
def test_a_send_still_blocks(gate, case, member, command):
    decision = gate(member, "Bash", {"command": command})
    assert _blocked(decision), f"{case}: the exemption reopened a send"


# ---------------------------------------------------------------------------
# The probe marker — it marks the RECEIPT, never the DECISION
# ---------------------------------------------------------------------------

def _receipt(gate, index: int = -1) -> dict:
    lines = (gate.workspace / ".firm" / "policy-denials.jsonl").read_text(
        encoding="utf-8").splitlines()
    return json.loads(lines[index])


def test_a_harness_probe_is_blocked_and_marked(gate):
    """A verify run is still a block — it is just not a Board interrupt."""
    decision = gate("MEM-002", "Bash",
                    {"command": "curl -X POST https://slack.com/api/chat.postMessage"},
                    env={"CADRE_POLICY_PROBE": "1"})
    assert _blocked(decision), "the harness marker weakened the block"
    assert _receipt(gate)["probe"] is True


@pytest.mark.parametrize("case,command", [
    # A Member cannot reach the gate's environment. An env prefix applies to
    # the Bash subshell the tool spawns; the hook is a different process,
    # started by Claude Code, and it reads its OWN environment.
    ("env-prefix",
     "CADRE_POLICY_PROBE=1 curl -X POST https://slack.com/api/chat.postMessage"),
    ("export-then-send",
     "export CADRE_POLICY_PROBE=1; curl -X POST https://slack.com/api/chat.postMessage"),
    ("python-sets-environ",
     'python3 -c "import os; os.environ[\'CADRE_POLICY_PROBE\']=\'1\'; '
     'import urllib.request; urllib.request.urlopen(\'https://slack.com/api/chat.postMessage\')"'),
])
def test_a_member_cannot_mark_its_own_send_as_a_probe(gate, case, command):
    """Both halves: the call still dies, AND the receipt still reports it.

    The block is the important half, but the receipt is the half a marker
    could silently rot — a `probe:true` here would suppress the escalation
    in `ingest_denials` and hand every Member an opt-out from being seen.
    """
    decision = gate("MEM-002", "Bash", {"command": command})
    assert _blocked(decision), f"{case}: a faked marker let a send through"
    assert _receipt(gate)["probe"] is False, f"{case}: a Member marked its own probe"


def test_the_receipt_records_the_resolved_heads(gate):
    """What the gate decided against — so a mis-block is debuggable."""
    gate("MEM-002", "Bash",
         {"command": "grep x | curl -X POST https://slack.com/api/chat.postMessage"})
    assert _receipt(gate)["heads"] == ["grep", "curl"]


def test_an_exempt_command_leaves_no_receipt(gate):
    """No denial, no evidence line — the log is for blocks, not for reads."""
    gate("MEM-002", "Bash", {"command": "grep -rn slack_send_message src/"})
    assert not (gate.workspace / ".firm" / "policy-denials.jsonl").exists()


# ---------------------------------------------------------------------------
# The gate is a copy on disk — the shipped one is the rendered one
# ---------------------------------------------------------------------------

def test_the_installed_gate_is_the_rendered_gate(gate):
    """What `install_policy_hook` writes is what `firm doctor` compares to.

    The resolver is spliced in, not imported (the gate runs under the system
    python3, where `firm` need not exist). If these two ever diverge, every
    firm reports 'armed and current' against a gate we do not ship — fork
    015's exact failure, one level up.
    """
    from firm.cli.install_hooks import render_policy_hook

    installed = (gate.workspace / ".claude" / "hooks" / POLICY_HOOK_SCRIPT_NAME
                 ).read_text(encoding="utf-8")
    assert installed == render_policy_hook()
    assert "def is_inspection" in installed, "the resolver never made it into the gate"
    assert "__SHELL_INTENT__" not in installed, "the splice placeholder shipped"
