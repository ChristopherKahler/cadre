"""The policy service — what reaches the gate, and what says a rule is blind.

`materialize` writes the only file the gate reads, so anything this drops is
a NEVER that does not exist. `unfireable_members` is the standing detector for
the ESC-021 class: rules that read as protection and can never fire.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.services import policy as policy_svc

FIRM = "acme"


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_migrations(c)
    create(c, "firm", {"id": FIRM, "name": "Acme"})
    return c


def _staff(conn, member_id: str, contract_id: str, *, deny, mcp=("slack-desk",)):
    create(conn, "contract", {
        "id": contract_id, "firm_id": FIRM, "name": f"{member_id} contract",
        "runtime_type": "claude_code",
        "skill_loadout": json.dumps({"mcp": list(mcp), "cli": [], "skills": []}),
        "validation_config": json.dumps({"deny": deny}),
    })
    create(conn, "member", {
        "id": member_id, "firm_id": FIRM, "name": member_id, "role": "operator",
        "contract_id": contract_id, "status": "active",
    })


# ---------------------------------------------------------------------------
# materialize — the gate reads this file and nothing else
# ---------------------------------------------------------------------------

def test_materialize_carries_the_tool_label(conn, tmp_path):
    """Fork 014's label reached the Contract and died here.

    The founding flow writes `tool` on every rule so the Board can read a
    denial by equipment. member_denies kept only match+reason, so the label
    never reached policy.json — written, stored, and invisible.
    """
    _staff(conn, "MEM-001", "CON-001", deny=[
        {"match": "*slack_send_message*", "reason": "no sends", "tool": "slack-desk"},
    ])
    path = policy_svc.materialize(conn, tmp_path, FIRM)
    rule = json.loads(path.read_text(encoding="utf-8"))["MEM-001"][0]
    assert rule["tool"] == "slack-desk"
    assert rule["match"] == "*slack_send_message*"


def test_materialize_skips_a_member_with_no_rules(conn, tmp_path):
    _staff(conn, "MEM-002", "CON-002", deny=[])
    written = json.loads(policy_svc.materialize(conn, tmp_path, FIRM)
                         .read_text(encoding="utf-8"))
    assert "MEM-002" not in written


# ---------------------------------------------------------------------------
# unfireable_members — the ESC-021 detector
# ---------------------------------------------------------------------------

def test_all_api_method_rules_are_reported_blind(conn):
    """chief-of-staff's live state, in miniature: 24 rules, none able to fire."""
    _staff(conn, "MEM-001", "CON-001", deny=[
        {"match": "*chat.postMessage*", "reason": "no slack sends"},
        {"match": "*messages.send*", "reason": "no mail"},
    ], mcp=("slack-desk", "notion"))
    found = policy_svc.unfireable_members(conn, FIRM)
    assert [f["member_id"] for f in found] == ["MEM-001"]
    assert found[0]["servers"] == ["slack-desk", "notion"]


def test_one_tool_name_rule_clears_the_finding(conn):
    """The API-method form is fine AS the curl backstop — just not alone."""
    _staff(conn, "MEM-001", "CON-001", deny=[
        {"match": "*chat.postMessage*", "reason": "catches raw curl"},
        {"match": "*slack_send_message*", "reason": "catches the tool"},
    ])
    assert policy_svc.unfireable_members(conn, FIRM) == []


def test_a_member_with_no_mcp_is_not_flagged(conn):
    """No MCP route means no MCP route left unlocked."""
    _staff(conn, "MEM-001", "CON-001", deny=[
        {"match": "*messages.send*", "reason": "no mail"},
    ], mcp=())
    assert policy_svc.unfireable_members(conn, FIRM) == []


def test_detector_survives_a_parsed_json_column(conn):
    """The repo hands JSON columns back parsed; the detector must cope.

    This is the test that caught the detector silently disabling itself:
    `json.loads` on an already-parsed dict raises TypeError, the except
    swallowed it, the loadout came back empty and every member looked
    clean — a check that reads as protection and enforces nothing, which
    is the very defect it exists to find.
    """
    _staff(conn, "MEM-001", "CON-001", deny=[
        {"match": "*chat.postMessage*", "reason": "no sends"},
    ])
    from firm.core import repo
    contract = repo.find(conn, "contract", firm_id=FIRM)[0]
    assert isinstance(contract["skill_loadout"], dict), (
        "repo parses JSON columns — the detector must not assume a string")
    assert len(policy_svc.unfireable_members(conn, FIRM)) == 1


@pytest.mark.parametrize("pattern,is_api_form", [
    ("*chat.postMessage*", True),
    ("*messages.send*", True),
    ("*slack_send_message*", False),      # the real MCP tool name
    ("*messages send*", False),           # the real CLI verb form
    ("*slack_post_to_channel*", False),
])
def test_api_method_form_detection(pattern, is_api_form):
    assert policy_svc._is_api_method_form(pattern) is is_api_form
