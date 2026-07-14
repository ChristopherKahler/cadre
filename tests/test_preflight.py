"""Tests for the credential preflight — fork 014: unknown is not absent.

The probe catalog (`discovery._CLI_PROBE`) is a closed set. An operator
wrapper like ``gws-acct`` (fork 013's governed door) is unknown to it — and
unknown must degrade to a presence check, never to a confident false
"not installed" that bricks every pulse.
"""

from __future__ import annotations

import json
import sqlite3
from unittest import mock

from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.pulse.preflight import dead_tools

_CATALOG = [
    {"name": "gws", "what": "Google Workspace", "present": True, "path": "/usr/bin/gws",
     "live": True, "detail": "operator@example.com"},
    {"name": "stripe", "what": "Stripe", "present": True, "path": "/usr/bin/stripe",
     "live": False, "detail": ""},
    {"name": "aws", "what": "AWS", "present": False, "path": "", "live": None, "detail": ""},
]


def _conn(clis: list[str]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    create(conn, "contract", {
        "id": "CON-001", "firm_id": "chrisai", "name": "Standard",
        "runtime_type": "claude_code",
        "skill_loadout": json.dumps({"cli": clis}),
    })
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai", "name": "Sterling",
        "role": "CMO", "status": "active", "contract_id": "CON-001",
    })
    return conn


def _dead(clis, which):
    with mock.patch("firm.dashboard.discovery.cli_survey", return_value=_CATALOG), \
         mock.patch("firm.pulse.preflight.shutil.which", side_effect=which):
        return dead_tools(_conn(clis), "chrisai")


def test_unknown_wrapper_on_path_is_not_dead():
    """The governed door: unknown to the catalog, resolvable on PATH → alive."""
    dead = _dead(["gws-acct"], lambda n: "/home/x/.local/bin/" + n)
    assert dead == {}


def test_unknown_wrapper_absent_fails_and_names_the_path():
    dead = _dead(["gws-acct"], lambda n: None)
    assert "gws-acct" in dead
    assert "PATH" in dead["gws-acct"]          # names what it searched, never lies
    assert "not installed" not in dead["gws-acct"]


def test_catalog_behaviors_unchanged():
    dead = _dead(["gws", "stripe", "aws"], lambda n: None)
    assert "gws" not in dead                                   # live
    assert "not signed in" in dead["stripe"]                   # probe failed
    assert "aws" in dead and "PATH" in dead["aws"]             # absent, names PATH


def test_mixed_loadout_blocks_only_the_dead_surface():
    dead = _dead(["gws", "gws-acct"], lambda n: "/usr/local/bin/" + n)
    assert dead == {}
