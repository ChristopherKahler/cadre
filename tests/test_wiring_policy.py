"""What a fresh Train emits must be able to fire.

chief-of-staff's dead rules were not a typo — the wiring prompt *taught* them.
It said to write a deny tight, "e.g. `messages.send`": an upstream API name
that appears in no tool call ever made. Every firm founded under that sentence
inherits the defect, so re-patterning one firm fixes one firm and the next
Train re-creates it.

The end-to-end test below is the one that matters: an architect plan shaped
the way the prompt now asks goes through the real sanitizer, onto a real
Contract, through the real materializer, and into the real gate — which must
BLOCK a real send. Every link is the shipped code.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys

import pytest

from firm.cli.install_hooks import POLICY_HOOK_SCRIPT_NAME, install_policy_hook
from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.dashboard import wiring
from firm.services import policy as policy_svc

FIRM = "acme"

ALLOWED = {
    "skills": {"voice-system"},
    "commands": {"/pulse"},
    "mcp": {"slack-desk", "notion"},
    "cli": {"gws-acct", "jq"},
    "folders": {"docs/"},
}

# What the architect returns when it follows the prompt: patterns naming the
# tools it just issued, each carrying the equipment label (fork 014).
TRAINED_PLAN = {
    "members": [{
        "name": "Dalton",
        "skills": ["voice-system"], "commands": [], "cli": ["gws-acct"],
        "mcp": ["slack-desk"], "knowledge": [],
        "deny": [
            {"match": "*slack_send_message*", "tool": "slack-desk",
             "reason": "The firm drafts everything and sends nothing."},
            {"match": "*slack_post_to_channel*", "tool": "slack-desk",
             "reason": "Same NEVER, and public."},
            {"match": "*chat.postMessage*", "tool": "slack-desk",
             "reason": "The raw-curl route to the same act."},
            {"match": "*messages?send*", "tool": "gws-acct",
             "reason": "No outbound mail, CLI or API."},
        ],
        "note": "Drafts correspondence; dispatches nothing.",
    }],
    "gaps": [],
}


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_migrations(c)
    create(c, "firm", {"id": FIRM, "name": "Acme"})
    return c


# ---------------------------------------------------------------------------
# The sanitizer between the architect and the Contract
# ---------------------------------------------------------------------------

def test_validate_carries_the_label_and_the_pattern():
    members = [{"name": "Dalton"}]
    out = wiring._validate(TRAINED_PLAN, members, ALLOWED)
    deny = out["members"][0]["deny"]
    assert [d["match"] for d in deny] == [
        "*slack_send_message*", "*slack_post_to_channel*",
        "*chat.postMessage*", "*messages?send*"]
    assert [d["tool"] for d in deny] == [
        "slack-desk", "slack-desk", "slack-desk", "gws-acct"]


def test_validate_drops_a_rule_with_no_pattern():
    """A reason with nothing to match on is a comment, not a control."""
    plan = {"members": [{"name": "Dalton", "deny": [
        {"match": "", "reason": "no sends"},
        {"match": "*slack_send_message*", "reason": "no sends"},
    ]}], "gaps": []}
    out = wiring._validate(plan, [{"name": "Dalton"}], ALLOWED)
    assert [d["match"] for d in out["members"][0]["deny"]] == ["*slack_send_message*"]


# ---------------------------------------------------------------------------
# The prompt is the generator. Pin what it teaches.
# ---------------------------------------------------------------------------

def test_the_prompt_no_longer_teaches_api_method_patterns():
    """The one sentence that seeded ESC-021 across every firm founded.

    It read: write them tight, `e.g. "messages.send"`. An architect obeying
    it produced a policy that could never fire.
    """
    assert 'e.g. "messages.send"' not in wiring._WIRING_PROMPT


def test_the_prompt_teaches_call_shaped_patterns():
    prompt = wiring._WIRING_PROMPT
    assert "slack_send_message" in prompt, "must name the real tool form"
    assert "messages send" in prompt, "must name the CLI's space form"
    assert "curl" in prompt, "must say why the API form is still kept"
    assert "loadout" in prompt, "must anchor rules to what the Member carries"


# ---------------------------------------------------------------------------
# End to end: what Train emits actually blocks a send
# ---------------------------------------------------------------------------

def test_a_freshly_trained_policy_blocks_a_real_send(conn, tmp_path):
    """Architect plan → sanitizer → Contract → materialize → the live gate.

    The proof the firm never had: not that the rules look right, but that a
    real `slack_send_message` call dies against them.
    """
    plan = wiring._validate(TRAINED_PLAN, [{"name": "Dalton"}], ALLOWED)
    create(conn, "contract", {
        "id": "CON-001", "firm_id": FIRM, "name": "Dalton contract",
        "runtime_type": "claude_code",
        "skill_loadout": json.dumps({"mcp": ["slack-desk"], "cli": ["gws-acct"]}),
        "validation_config": json.dumps({"deny": plan["members"][0]["deny"]}),
    })
    create(conn, "member", {
        "id": "MEM-001", "firm_id": FIRM, "name": "Dalton", "role": "operator",
        "contract_id": "CON-001", "status": "active",
    })

    # No member of this firm is blind to its own NEVERs.
    assert policy_svc.unfireable_members(conn, FIRM) == []

    policy_svc.materialize(conn, tmp_path, FIRM)
    install_policy_hook(tmp_path)
    hook = tmp_path / ".claude" / "hooks" / POLICY_HOOK_SCRIPT_NAME

    def decide(tool: str, tool_input: dict) -> bool:
        proc = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({"cwd": str(tmp_path), "tool_name": tool,
                              "tool_input": tool_input}),
            capture_output=True, text=True,
            env={"CADRE_MEMBER_ID": "MEM-001", "PATH": "/usr/bin:/bin"},
            timeout=30,
        )
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
        return (out.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"

    assert decide("mcp__slack-desk__slack_send_message",
                  {"channel_id": "D07", "text": "hi"}), "the trained rule did not fire"
    assert decide("Bash", {"command": "gws-acct gmail users messages send --to a@b"})
    assert decide("Bash", {"command": "curl -d x https://slack.com/api/chat.postMessage"})
    # ...and the firm can still do its job.
    assert not decide("mcp__slack-desk__slack_get_unreads", {})
    assert not decide("Write", {"file_path": "/tmp/r.md",
                                "content": "we deny *slack_send_message* here"})
