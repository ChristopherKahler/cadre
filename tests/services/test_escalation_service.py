"""Tests for firm.services.escalation — dedup-aware Board escalations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from firm.core.migrate import apply_migrations
from firm.core.repo import create, find, get
from firm.services.escalation import (
    derive_dedupe_key,
    list_escalations,
    raise_escalation,
    resolve_escalation,
    view_escalation,
)


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    create(conn, "member", {
        "id": "MEM-001", "firm_id": "chrisai", "name": "Sterling",
        "role": "CMO", "status": "active",
    })
    return conn


def _sent(sent=True, reason="test"):
    return {"sent": sent, "reason": reason}


NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)


class TestRaise:

    @mock.patch("firm.services.escalation.notify.send_board_dm", return_value=_sent())
    def test_raise_creates_row_and_notifies(self, mock_dm):
        conn = _fresh_conn()
        result = raise_escalation(conn, "chrisai", {
            "raised_by_member_id": "MEM-001",
            "title": "DOC-003 needs sign-off",
            "body": "Cadence proposal has sat 3 pulses",
            "severity": "high",
        }, now=NOW)

        assert result["deduped"] is False
        assert result["notified"] is True
        esc = result["escalation"]
        assert esc["id"] == "ESC-001"
        assert esc["status"] == "open"
        assert esc["notify_count"] == 1
        assert esc["last_notified_at"] == NOW.isoformat()
        mock_dm.assert_called_once()
        assert "DOC-003 needs sign-off" in mock_dm.call_args.args[2]

        records = find(conn, "records", event_type="escalation.raised")
        assert len(records) == 1

    @mock.patch("firm.services.escalation.notify.send_board_dm", return_value=_sent())
    def test_duplicate_raise_within_window_is_silent(self, mock_dm):
        conn = _fresh_conn()
        raise_escalation(conn, "chrisai", {
            "raised_by_member_id": "MEM-001", "title": "Same issue",
        }, now=NOW)
        # One hour later — well inside the 24h default window
        result = raise_escalation(conn, "chrisai", {
            "raised_by_member_id": "MEM-001", "title": "Same issue",
        }, now=NOW + timedelta(hours=1))

        assert result["deduped"] is True
        assert result["notified"] is False
        assert "reminder window" in result["notify_reason"]
        assert mock_dm.call_count == 1  # only the original
        assert len(find(conn, "escalation")) == 1  # no duplicate row
        assert len(find(conn, "records", event_type="escalation.deduped")) == 1

    @mock.patch("firm.services.escalation.notify.send_board_dm", return_value=_sent())
    def test_duplicate_raise_after_window_sends_reminder(self, mock_dm):
        conn = _fresh_conn()
        raise_escalation(conn, "chrisai", {
            "raised_by_member_id": "MEM-001", "title": "Same issue",
        }, now=NOW)
        result = raise_escalation(conn, "chrisai", {
            "raised_by_member_id": "MEM-001", "title": "Same issue",
        }, now=NOW + timedelta(hours=25))

        assert result["deduped"] is True
        assert result["notified"] is True
        assert mock_dm.call_count == 2
        assert "Reminder #1" in mock_dm.call_args.args[2]
        esc = result["escalation"]
        assert esc["notify_count"] == 2
        assert esc["last_notified_at"] == (NOW + timedelta(hours=25)).isoformat()
        assert len(find(conn, "records", event_type="escalation.reminded")) == 1

    @mock.patch("firm.services.escalation.notify.send_board_dm", return_value=_sent())
    def test_reraise_after_resolution_is_new_escalation(self, mock_dm):
        conn = _fresh_conn()
        first = raise_escalation(conn, "chrisai", {
            "raised_by_member_id": "MEM-001", "title": "Same issue",
        }, now=NOW)
        resolve_escalation(conn, first["escalation"]["id"], resolution="handled")

        result = raise_escalation(conn, "chrisai", {
            "raised_by_member_id": "MEM-001", "title": "Same issue",
        }, now=NOW + timedelta(hours=1))

        assert result["deduped"] is False
        assert result["escalation"]["id"] == "ESC-002"
        assert mock_dm.call_count == 2

    @mock.patch("firm.services.escalation.notify.send_board_dm", return_value=_sent(False, "token env unset"))
    def test_delivery_failure_still_creates_escalation(self, mock_dm):
        conn = _fresh_conn()
        result = raise_escalation(conn, "chrisai", {
            "raised_by_member_id": "MEM-001", "title": "Important thing",
        }, now=NOW)

        assert result["notified"] is False
        esc = get(conn, "escalation", result["escalation"]["id"])
        assert esc is not None
        assert esc["status"] == "open"
        assert esc["notify_count"] == 0  # nothing actually delivered

    @mock.patch("firm.services.escalation.notify.send_board_dm", return_value=_sent(False, "no config"))
    def test_failed_notify_retries_on_next_raise(self, mock_dm):
        """A raise whose DM failed must not start the reminder window —
        the next raise retries delivery instead of going silent."""
        conn = _fresh_conn()
        raise_escalation(conn, "chrisai", {
            "raised_by_member_id": "MEM-001", "title": "Same issue",
        }, now=NOW)
        result = raise_escalation(conn, "chrisai", {
            "raised_by_member_id": "MEM-001", "title": "Same issue",
        }, now=NOW + timedelta(hours=1))

        assert result["deduped"] is True
        assert mock_dm.call_count == 2  # retried because never delivered

    def test_missing_required_fields(self):
        conn = _fresh_conn()
        with pytest.raises(ValueError, match="required"):
            raise_escalation(conn, "chrisai", {"title": "no member"})
        with pytest.raises(ValueError, match="required"):
            raise_escalation(conn, "chrisai", {"raised_by_member_id": "MEM-001"})

    def test_invalid_severity(self):
        conn = _fresh_conn()
        with pytest.raises(ValueError, match="severity"):
            raise_escalation(conn, "chrisai", {
                "raised_by_member_id": "MEM-001", "title": "x", "severity": "apocalyptic",
            })

    @mock.patch("firm.services.escalation.notify.send_board_dm", return_value=_sent())
    def test_target_ref_validated(self, mock_dm):
        conn = _fresh_conn()
        with pytest.raises(ValueError):
            raise_escalation(conn, "chrisai", {
                "raised_by_member_id": "MEM-001", "title": "x",
                "target_entity_type": "unit", "target_entity_id": "UNIT-999",
            })


class TestDedupeKey:

    def test_derivation_normalizes(self):
        assert derive_dedupe_key("DOC-003 Needs  Sign-off!", "document", "DOC-003") == \
            "document:DOC-003:doc-003-needs-sign-off"

    @mock.patch("firm.services.escalation.notify.send_board_dm", return_value=_sent())
    def test_explicit_key_overrides(self, mock_dm):
        conn = _fresh_conn()
        result = raise_escalation(conn, "chrisai", {
            "raised_by_member_id": "MEM-001", "title": "Some phrasing",
            "dedupe_key": "custom-key",
        }, now=NOW)
        assert result["escalation"]["dedupe_key"] == "custom-key"

        result2 = raise_escalation(conn, "chrisai", {
            "raised_by_member_id": "MEM-001", "title": "Different phrasing entirely",
            "dedupe_key": "custom-key",
        }, now=NOW + timedelta(hours=1))
        assert result2["deduped"] is True


class TestLifecycle:

    @mock.patch("firm.services.escalation.notify.send_board_dm", return_value=_sent())
    def test_list_view_resolve(self, mock_dm):
        conn = _fresh_conn()
        raise_escalation(conn, "chrisai", {
            "raised_by_member_id": "MEM-001", "title": "A",
        }, now=NOW)
        raise_escalation(conn, "chrisai", {
            "raised_by_member_id": "MEM-001", "title": "B",
        }, now=NOW)

        assert len(list_escalations(conn, "chrisai")) == 2
        assert len(list_escalations(conn, "chrisai", status="open")) == 2

        esc = view_escalation(conn, "ESC-001")
        assert esc["title"] == "A"

        resolved = resolve_escalation(conn, "ESC-001", resolution="done")
        assert resolved["status"] == "resolved"
        assert resolved["resolution"] == "done"
        assert len(list_escalations(conn, "chrisai", status="open")) == 1
        assert len(find(conn, "records", event_type="escalation.resolved")) == 1

    def test_resolve_invalid_status(self):
        conn = _fresh_conn()
        with pytest.raises(ValueError, match="not found|acknowledged"):
            resolve_escalation(conn, "ESC-404", status="resolved")
