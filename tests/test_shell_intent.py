"""Mention vs invocation — the head resolver the gate and ingest both read.

`is_inspection` decides one thing: may a caller stop matching send-patterns
against this command's arguments? It is allowed to be wrong in exactly one
direction. A false "no" costs noise (a Member gets blocked for grepping);
a false "yes" costs the firm's entire premise (a send executes). So the
must-block half of this file is the load-bearing half, and it carries every
route a read-only head can be turned into a sender.

The end-to-end versions of these cases run through the REAL installed gate
in tests/hooks/test_policy_gate.py — this file pins the resolver itself.
"""

from __future__ import annotations

import pytest

from firm.hooks.shell_intent import command_heads, is_inspection

# ---------------------------------------------------------------------------
# A mention is not an invocation — the false positives that started the fork
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case,command", [
    # The live denials the CoS log was still holding un-ingested (#45, #48).
    ("cooper-greps-to-verify-the-lock",
     r'grep -n "SLACK_NO_SEND\|slack_send_message\|slack_post_to_channel" .mcp.json'),
    ("member-escalates-about-the-lock",
     'firm escalation raise --member MEM-004 --title "UNIT-022: unrestricted Slack" '
     '--body "slack_send_message is reachable; the NEVER is not enforced"'),
    # The rest of the enumerated mention set.
    ("grep-the-pattern", "grep -rn slack_send_message src/"),
    ("cat-the-policy", "cat .firm/policy.json"),
    ("echo-the-pattern", "echo chat.postMessage"),
    ("base-learn-quoting-a-pattern",
     'base learn --text "the gate blocks slack_send_message" --domain cadre'),
    # Prose arguments carry shell punctuation. Quoted, it is data, not syntax
    # — this is the case a naive split-on-`;` implementation regresses.
    ("escalation-body-with-punctuation",
     'firm escalation raise --title x --body "first chat.postMessage; then | done"'),
    ("env-prefix-is-ordinary", "FIRM_ID=cos firm escalation raise --title x"),
    ("cd-then-grep", "cd /home/x && grep -rn chat.postMessage ."),
    ("git-read-piped-to-grep", "git log --oneline | grep chat.postMessage"),
    ("sed-print-only", 'sed -n "/slack_send_message/p" file'),
    ("redirect-target-is-a-file", "grep chat.postMessage src > out.txt"),
    ("find-by-name", 'find . -name "*chat.postMessage*"'),
    ("absolute-path-head", "/usr/bin/grep -rn messages.send src/"),
])
def test_inspections_are_recognised(case, command):
    assert is_inspection(command), f"{case}: a mention was read as an invocation"


# ---------------------------------------------------------------------------
# MUST BLOCK — every one of these must stay fail-closed. A regression here
# is not noise, it is a send.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case,command", [
    ("curl-the-api",
     "curl -X POST -d text=hi https://slack.com/api/chat.postMessage"),
    ("gws-space-form-send",
     "gws-acct gmail users messages send --to a@b.com --subject hi"),
    ("gws-chat-spaces-create",
     "gws-acct extendly chat spaces messages create --space spaces/AAA --text hi"),
    # A read-only head is not a shield for what comes after it.
    ("read-head-piped-into-sender",
     "grep x | curl -X POST https://slack.com/api/chat.postMessage"),
    ("read-head-chained-into-sender",
     "cat a && curl -X POST https://slack.com/api/chat.postMessage"),
    ("read-head-semicolon-sender",
     "cat a; curl -X POST https://slack.com/api/chat.postMessage"),
    ("sender-in-command-substitution",
     "echo $(curl -X POST https://slack.com/api/chat.postMessage)"),
    ("sender-in-backticks",
     "echo `curl -X POST https://slack.com/api/chat.postMessage`"),
    # An allowlisted head that hands control to another program is not an
    # inspection — this is the class that makes a bare head allowlist unsafe.
    ("find-exec-a-sender",
     r"find . -exec curl -d x https://slack.com/api/chat.postMessage \;"),
    ("find-ok-a-sender",
     r"find . -ok curl -d x https://slack.com/api/chat.postMessage \;"),
    ("awk-system-a-sender",
     'awk \'BEGIN{system("curl -d x https://slack.com/api/chat.postMessage")}\''),
    ("sed-execute-flag",
     'sed "s/x/curl https:\\/\\/slack.com\\/api\\/chat.postMessage/e" f'),
    ("sed-e-command", 'sed "1e curl https://slack.com/api/chat.postMessage" f'),
    # Interpreters and dispatchers are never inspections.
    ("python-inline", 'python3 -c "import requests; requests.post(url)"'),
    ("bash-dash-c", 'bash -c "curl https://slack.com/api/chat.postMessage"'),
    ("xargs-a-sender", "xargs curl < urls.txt"),
    ("env-runs-a-sender", "env curl https://slack.com/api/chat.postMessage"),
    ("eval-a-string", 'eval "curl https://slack.com/api/chat.postMessage"'),
    # A head is only the head it resolves to.
    ("path-shadowed-head", "PATH=/tmp/evil grep chat.postMessage x"),
    ("ld-preload-head", "LD_PRELOAD=/tmp/x.so cat file"),
    # These CLIs act as well as read; only the read verbs are exempt.
    ("firm-pulse-spawns-members", "firm pulse"),
    ("firm-notify-dms-the-board", 'firm notify --msg "hi"'),
    ("git-push-is-a-send", "git push origin main"),
    ("base-verb-not-on-the-list", 'base relay send --to x --msg "hi"'),
    # Unparseable is not inspectable.
    ("unbalanced-quotes", 'grep "unclosed chat.postMessage'),
    ("empty-command", "   "),
    # The abuse route: a Member cannot mark its own send as a harness probe.
    ("member-fakes-the-probe-marker",
     "CADRE_POLICY_PROBE=1 curl -X POST https://slack.com/api/chat.postMessage"),
    ("member-exports-the-probe-marker",
     "export CADRE_POLICY_PROBE=1; curl -X POST https://slack.com/api/chat.postMessage"),
])
def test_senders_stay_fail_closed(case, command):
    assert not is_inspection(command), f"{case}: a send was read as an inspection"


def test_unknown_heads_fail_closed():
    """The allowlist is the whole exemption — a binary nobody listed is denied."""
    assert not is_inspection("some-new-tool --send everything")


def test_heads_are_stamped_for_the_receipt():
    """What the gate resolved, recorded — so nothing re-derives it later."""
    assert command_heads("grep x | curl https://slack.com/api/chat.postMessage") == [
        "grep", "curl"]
    assert command_heads("FIRM_ID=cos firm escalation raise --title x") == ["firm"]
    assert command_heads('grep "unclosed') == []
