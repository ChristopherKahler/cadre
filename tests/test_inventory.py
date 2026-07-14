"""Tests for the Armory — firm.dashboard.inventory."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest import mock

from firm.dashboard import inventory

_SURVEY_MCP = {"servers": [
    {"name": "skool", "source": "user", "command": "npx", "needs_keys": ["SKOOL_API_KEY"],
     "env_preview": {}, "equipped": False, "available": True, "why_not": ""},
    {"name": "notion", "source": "plugin", "command": "npx", "needs_keys": [],
     "env_preview": {}, "equipped": False, "available": True, "why_not": ""},
]}
_SURVEY_KNOW = {
    "skills": [{"name": "voice-system", "scope": "user", "path": "/s", "description": "voice calibration"}],
    "commands": [{"name": "/social-engine:script", "scope": "user", "path": "/c"}],
    "attached": [],
}
_SURVEY_CLI = [
    {"name": "gws", "what": "Google Workspace", "present": True, "path": "/bin/gws",
     "live": True, "detail": "operator@example.com"},
    {"name": "aws", "what": "AWS", "present": False, "path": "", "live": None, "detail": ""},
]


def _patched(tmp_path):
    return (
        mock.patch.object(inventory, "_path", return_value=tmp_path / "inventory.json"),
        mock.patch("firm.dashboard.discovery.mcp_survey", return_value=_SURVEY_MCP),
        mock.patch("firm.dashboard.discovery.knowledge_survey", return_value=_SURVEY_KNOW),
        mock.patch("firm.dashboard.discovery.cli_survey", return_value=_SURVEY_CLI),
    )


def test_sync_persists_and_load_roundtrips(tmp_path):
    p, m1, m2, m3 = _patched(tmp_path)
    with p, m1, m2, m3:
        inv = inventory.sync()
        assert [s["name"] for s in inv["mcp"]] == ["skool", "notion"]
        assert inventory.load() == inv
    on_disk = json.loads((tmp_path / "inventory.json").read_text())
    assert on_disk["cli"][0]["detail"] == "operator@example.com"


def test_ensure_syncs_when_missing_and_reprobes_when_stale(tmp_path):
    p, m1, m2, m3 = _patched(tmp_path)
    with p, m1 as mcp_mock, m2, m3:
        inv = inventory.ensure()                      # missing → sync
        assert mcp_mock.call_count == 1
        inventory.ensure(max_cli_age_sec=3600)        # fresh → no resync
        assert mcp_mock.call_count == 1
        stale = dict(inv)
        stale["cli_verified_at"] = (
            datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()
        (tmp_path / "inventory.json").write_text(json.dumps(stale))
        inventory.ensure(max_cli_age_sec=3600)        # stale → re-probe
        assert mcp_mock.call_count == 2


def test_view_filters_kind_search_and_exclusions(tmp_path):
    p, m1, m2, m3 = _patched(tmp_path)
    ex = {"mcp": ["notion"], "skills": [], "commands": [], "clis": []}
    with p, m1, m2, m3, mock.patch("firm.dashboard.exclusions.load", return_value=ex):
        v = inventory.view(kind="mcp")
        assert [s["name"] for s in v["mcp"]] == ["skool"]     # notion excluded
        assert v["skills"] == [] and v["cli"] == []           # kind filter
        v = inventory.view(kind="mcp", include_excluded=True)
        names = {s["name"]: s["excluded"] for s in v["mcp"]}
        assert names == {"skool": False, "notion": True}
        v = inventory.view(q="workspace")                     # matches gws 'what'
        assert [c["name"] for c in v["cli"]] == ["gws"]
        assert v["mcp"] == []
