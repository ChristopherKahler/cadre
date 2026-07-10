"""Tests for firm.notify — deterministic Board notification."""

from __future__ import annotations

import io
import json
import sqlite3
from unittest import mock

from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.notify import remind_interval_hours, send_board_dm


def _conn_with_firm(notify_config=None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    data = {"id": "chrisai", "name": "ChrisAI"}
    if notify_config is not None:
        data["notify_config"] = notify_config
    create(conn, "firm", data)
    return conn


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_no_config_soft_fails():
    conn = _conn_with_firm()
    result = send_board_dm(conn, "chrisai", "hello")
    assert result["sent"] is False
    assert "notify_config" in result["reason"]


def test_slack_missing_token_soft_fails(monkeypatch):
    monkeypatch.delenv("CADRE_SLACK_TOKEN", raising=False)
    conn = _conn_with_firm({"provider": "slack", "slack_user_id": "U123"})
    result = send_board_dm(conn, "chrisai", "hello")
    assert result["sent"] is False
    assert "CADRE_SLACK_TOKEN" in result["reason"]


@mock.patch("firm.notify.urllib.request.urlopen")
def test_slack_dm_sent(mock_open, monkeypatch):
    monkeypatch.setenv("CADRE_SLACK_TOKEN", "xoxb-test")
    mock_open.return_value = _FakeResponse(json.dumps({"ok": True}).encode())
    conn = _conn_with_firm({"provider": "slack", "slack_user_id": "U123"})

    result = send_board_dm(conn, "chrisai", "hello board")

    assert result["sent"] is True
    req = mock_open.call_args.args[0]
    assert req.full_url == "https://slack.com/api/chat.postMessage"
    assert req.get_header("Authorization") == "Bearer xoxb-test"
    payload = json.loads(req.data.decode())
    assert payload == {"channel": "U123", "text": "hello board"}


@mock.patch("firm.notify.urllib.request.urlopen")
def test_slack_api_error_soft_fails(mock_open, monkeypatch):
    monkeypatch.setenv("CADRE_SLACK_TOKEN", "xoxb-test")
    mock_open.return_value = _FakeResponse(
        json.dumps({"ok": False, "error": "channel_not_found"}).encode()
    )
    conn = _conn_with_firm({"provider": "slack", "slack_user_id": "U123"})

    result = send_board_dm(conn, "chrisai", "hello")
    assert result["sent"] is False
    assert "channel_not_found" in result["reason"]


@mock.patch("firm.notify.urllib.request.urlopen")
def test_webhook_provider(mock_open, monkeypatch):
    monkeypatch.setenv("CADRE_NOTIFY_WEBHOOK", "https://hooks.slack.com/services/T/B/x")
    mock_open.return_value = _FakeResponse(b"ok")
    conn = _conn_with_firm({"provider": "webhook"})

    result = send_board_dm(conn, "chrisai", "hello")

    assert result["sent"] is True
    req = mock_open.call_args.args[0]
    assert req.full_url == "https://hooks.slack.com/services/T/B/x"


def test_telegram_missing_chat_id_soft_fails(monkeypatch):
    monkeypatch.setenv("CADRE_TELEGRAM_TOKEN", "123:abc")
    conn = _conn_with_firm({"provider": "telegram"})
    result = send_board_dm(conn, "chrisai", "hello")
    assert result["sent"] is False
    assert "telegram_chat_id" in result["reason"]


def test_telegram_missing_token_soft_fails(monkeypatch):
    monkeypatch.delenv("CADRE_TELEGRAM_TOKEN", raising=False)
    conn = _conn_with_firm({"provider": "telegram", "telegram_chat_id": "42"})
    result = send_board_dm(conn, "chrisai", "hello")
    assert result["sent"] is False
    assert "CADRE_TELEGRAM_TOKEN" in result["reason"]


@mock.patch("firm.notify.urllib.request.urlopen")
def test_telegram_dm_sent(mock_open, monkeypatch):
    monkeypatch.setenv("CADRE_TELEGRAM_TOKEN", "123:abc")
    mock_open.return_value = _FakeResponse(json.dumps({"ok": True}).encode())
    conn = _conn_with_firm({"provider": "telegram", "telegram_chat_id": "42"})

    result = send_board_dm(conn, "chrisai", "gate pending")

    assert result["sent"] is True
    req = mock_open.call_args.args[0]
    assert req.full_url == "https://api.telegram.org/bot123:abc/sendMessage"
    payload = json.loads(req.data.decode())
    assert payload == {"chat_id": "42", "text": "gate pending"}


@mock.patch("firm.notify.urllib.request.urlopen")
def test_telegram_api_error_soft_fails(mock_open, monkeypatch):
    monkeypatch.setenv("CADRE_TELEGRAM_TOKEN", "123:abc")
    mock_open.return_value = _FakeResponse(
        json.dumps({"ok": False, "description": "chat not found"}).encode()
    )
    conn = _conn_with_firm({"provider": "telegram", "telegram_chat_id": "42"})

    result = send_board_dm(conn, "chrisai", "hello")
    assert result["sent"] is False
    assert "chat not found" in result["reason"]


def test_remind_interval_defaults_and_overrides():
    assert remind_interval_hours(None) == 24
    assert remind_interval_hours({}) == 24
    assert remind_interval_hours({"remind_hours": 6}) == 6
    assert remind_interval_hours({"remind_hours": "bogus"}) == 24
    assert remind_interval_hours({"remind_hours": 0}) == 24


def test_cli_notify_delivers(tmp_path, capsys, monkeypatch):
    import json as _json

    from firm.core.db import connect as _connect
    from firm.core.migrate import apply_migrations as _migrate
    from firm.core.repo import create as _create
    import firm.cli.notify as notify_cli

    firm_dir = tmp_path / ".firm"
    firm_dir.mkdir()
    conn = _connect(firm_dir / "firm.db")
    _migrate(conn)
    _create(conn, "firm", {"id": "chrisai", "name": "ChrisAI"})
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        notify_cli, "send_board_dm",
        lambda conn, firm_id, text: {"sent": True, "reason": f"test: {text}"},
    )
    rc = notify_cli.run_notify(tmp_path, "hello board")
    assert rc == 0
    out = _json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is True
