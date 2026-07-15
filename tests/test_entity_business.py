"""Business/domain tags on firm entities (fork cadre-entity-business-tags).

The proof-of-need: ESC-012 was a mixed-business escalation (Meet Caddy prospects
+ a ChrisAI sub-item) and the Board had to hand-annotate the tag because the
field didn't exist. These guard the field + the audited set path + the
self-populating suggestion set.
"""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations, applied_migration_names
from firm.core.repo import create, find, get
from firm.services import tagging


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    apply_migrations(c)
    create(c, "firm", {"id": "cos", "name": "Chief of Staff"})
    create(c, "member", {"id": "MEM-001", "firm_id": "cos", "name": "Cooper",
                         "role": "CoS", "status": "active"})
    return c


def _esc(c: sqlite3.Connection, eid: str = "ESC-001") -> str:
    create(c, "escalation", {
        "id": eid, "firm_id": "cos", "raised_by_member_id": "MEM-001",
        "severity": "high", "title": "Mixed batch", "body": "5 caddy + 1 chrisai",
        "target_entity_type": "member", "target_entity_id": "MEM-001",
        "dedupe_key": eid, "status": "open"})
    return eid


def test_migration_adds_business_columns():
    c = _conn()
    for et in ("escalation", "gate", "unit"):
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({et})")}
        assert "business" in cols, et
    assert "013_entity_business" in applied_migration_names(c)


def test_set_business_stamps_and_logs():
    c = _conn(); eid = _esc(c)
    out = tagging.set_business(c, "escalation", eid, "meet-caddy")
    assert out["business"] == "meet-caddy"
    assert get(c, "escalation", eid)["business"] == "meet-caddy"
    recs = find(c, "records", event_type="escalation.business_set")
    assert len(recs) == 1 and recs[0]["target_entity_id"] == eid


def test_retag_records_previous():
    c = _conn(); eid = _esc(c)
    tagging.set_business(c, "escalation", eid, "meet-caddy")
    tagging.set_business(c, "escalation", eid, "chrisai")
    assert get(c, "escalation", eid)["business"] == "chrisai"
    # repo.find deserializes the JSON `details` column → already a dict
    detail = [r["details"] for r in
              find(c, "records", event_type="escalation.business_set")][-1]
    assert detail["business"] == "chrisai" and detail["previous"] == "meet-caddy"


def test_rejects_empty_untaggable_and_missing():
    c = _conn(); eid = _esc(c)
    with pytest.raises(ValueError):
        tagging.set_business(c, "escalation", eid, "   ")
    with pytest.raises(ValueError):
        tagging.set_business(c, "firm", "cos", "x")       # firm is not taggable
    with pytest.raises(ValueError):
        tagging.set_business(c, "escalation", "ESC-404", "x")


def test_firm_businesses_self_populates_sorted_and_deduped():
    c = _conn()
    tagging.set_business(c, "escalation", _esc(c, "ESC-001"), "meet-caddy")
    tagging.set_business(c, "escalation", _esc(c, "ESC-002"), "chrisai")
    tagging.set_business(c, "escalation", _esc(c, "ESC-003"), "meet-caddy")  # dup
    assert tagging.firm_businesses(c, "cos") == ["chrisai", "meet-caddy"]


def test_entity_business_action_and_state_surface():
    from firm.dashboard.server import assemble_state, perform_action
    c = _conn(); eid = _esc(c)
    perform_action(c, "entity-business", eid,
                   {"entity_type": "escalation", "business": "meet-caddy"})
    st = assemble_state(c, "cos")
    esc = next(e for e in st["escalations"] if e["id"] == eid)
    assert esc["business"] == "meet-caddy"        # surfaced on the entity row
    assert st["businesses"] == ["meet-caddy"]     # suggestion/filter set in state
    with pytest.raises(ValueError):               # kind guard holds through the action
        perform_action(c, "entity-business", eid,
                       {"entity_type": "firm", "business": "x"})
