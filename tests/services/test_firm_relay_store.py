"""A firm's own message store, and the failures that must never be silent.

#117 moves a firm's base tier, and base keeps the relay inbox in that tier, so
a firm's messaging moves with its graph. That is right -- a firm's internal
traffic belongs to the firm -- but it adds a way for a steer to go nowhere that
looks exactly like the old ones: sender and receiver in DIFFERENT stores.

The steer path used to answer every failure with None and fall back to
queueing, so "not registered", "base is absent" and "wrong store" were one
answer and none of them reached a person. Every assertion here is about the
opposite: the failure names the store it looked in, the title it wanted, and
what was actually there.

The real `base relay` output shapes below were copied out of base 0.15.2 on
2026-09-14, not typed from memory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from firm.services import firm_relay, graph_isolation, relay_title


# --- real output, copied verbatim from base 0.15.2 --------------------------
LISTING_TWO = (
    "Titled sessions (2):\n"
    "  pen  [live · 1m]  ws:acme  session:8d0df910-231a-4f11-a89e-8d0df9100000\n"
    "  editor  [DEAD · 3h]  ws:acme  session:b1c018ab-b609-44dc-a19c-c765b9c00000\n"
)
LISTING_NONE = "No titled sessions.\n"


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _firm(tmp_path: Path) -> Path:
    workspace = tmp_path / "acme"
    (workspace / ".firm").mkdir(parents=True)
    (workspace / ".firm" / "firm.db").write_text("", encoding="utf-8")
    (workspace / ".base").mkdir(parents=True)
    return workspace


def _stub_relay(monkeypatch, *, listing=LISTING_TWO, send_rc=0, send_err=""):
    """Stand in for the `base relay ...` call, and record what was asked for."""
    calls: list[list[str]] = []

    def _run(workspace, args, timeout=30):
        calls.append(list(args))
        if args and args[0] == "sessions":
            return _Result(listing)
        return _Result("", send_rc, send_err)

    monkeypatch.setattr(firm_relay, "_run", _run)
    return calls


# --- where the store is -----------------------------------------------------


def test_the_store_is_inside_the_firms_own_tier(tmp_path):
    workspace = _firm(tmp_path)

    store = firm_relay.store_path(workspace)

    assert store == (workspace / ".firm" / "base-home" / ".base-gbl"
                     / ".base" / "relay-inbox")


def test_the_firm_is_found_by_standing_in_it(tmp_path, monkeypatch):
    workspace = _firm(tmp_path)
    deeper = workspace / "notes" / "drafts"
    deeper.mkdir(parents=True)
    monkeypatch.chdir(deeper)

    assert firm_relay.resolve_firm() == workspace.resolve()


def test_outside_a_firm_there_is_no_firm_to_guess(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert firm_relay.resolve_firm() is None


# --- the failure that must be loud ------------------------------------------


def test_an_unreachable_title_names_the_store_the_title_and_who_is_there(
        monkeypatch, tmp_path):
    workspace = _firm(tmp_path)
    _stub_relay(monkeypatch)

    result = firm_relay.ping(workspace, to="nobody", message="hello")

    assert not result["ok"]
    assert "nobody" in result["reason"]
    assert str(firm_relay.store_path(workspace)) in result["reason"]
    assert "pen" in result["reason"] and "editor" in result["reason"], (
        "a person who typed the wrong name needs to see the right ones")


def test_a_reachable_title_is_delivered(monkeypatch, tmp_path):
    """The control. Without it, a refusal that fired on everything would pass
    the test above and no message would ever be sent."""
    workspace = _firm(tmp_path)
    calls = _stub_relay(monkeypatch)

    result = firm_relay.ping(workspace, to="pen", message="hello")

    assert result["ok"], result["reason"]
    assert ["ping", "--to", "pen", "--from", "cadre", "--msg", "hello"] in calls


def test_an_unreadable_store_is_not_an_empty_one(monkeypatch, tmp_path):
    """Absent is not empty. "I could not look" must never print as "not there"."""
    workspace = _firm(tmp_path)

    def _run(workspace, args, timeout=30):
        return _Result("", 2, "base: could not open the relay store")

    monkeypatch.setattr(firm_relay, "_run", _run)

    result = firm_relay.ping(workspace, to="pen", message="hello")

    assert not result["ok"]
    assert "unknown" in result["reason"]
    assert "could not read" in result["reason"]


def test_a_steer_at_a_session_that_is_not_live_says_so(monkeypatch, tmp_path):
    """A task lands inside a running turn. A dead session has no turn to land
    in, and reporting that as delivered is the silent failure in another hat."""
    workspace = _firm(tmp_path)
    _stub_relay(monkeypatch)

    result = firm_relay.task(workspace, to="editor", summary="do the thing",
                             slug="steer-1")

    assert not result["ok"]
    assert "not live" in result["reason"]
    assert "DEAD" in result["reason"], "quote the row, do not paraphrase it"


def test_a_live_session_takes_the_steer(monkeypatch, tmp_path):
    workspace = _firm(tmp_path)
    calls = _stub_relay(monkeypatch)

    result = firm_relay.task(workspace, to="pen", summary="do the thing",
                             slug="steer-1")

    assert result["ok"], result["reason"]
    assert any(c[:2] == ["task", "--to"] for c in calls)


def test_base_saying_no_is_reported_as_base_saying_no(monkeypatch, tmp_path):
    workspace = _firm(tmp_path)
    _stub_relay(monkeypatch, send_rc=1, send_err="base: inbox is read-only")

    result = firm_relay.ping(workspace, to="pen", message="hello")

    assert not result["ok"]
    assert "exited 1" in result["reason"]
    assert "read-only" in result["reason"]


def test_an_empty_store_still_names_itself(monkeypatch, tmp_path):
    workspace = _firm(tmp_path)
    _stub_relay(monkeypatch, listing=LISTING_NONE)

    result = firm_relay.ping(workspace, to="pen", message="hello")

    assert not result["ok"]
    assert "nobody is registered there" in result["reason"]


# --- the title follows the Member, and a rename does not lose messages ------


def test_a_rename_carries_the_unread_messages_across(tmp_path):
    workspace = _firm(tmp_path)
    store = firm_relay.store_path(workspace)
    old = store / "pen"
    old.mkdir(parents=True)
    (old / "0001-ping.json").write_text('{"from": "chris"}', encoding="utf-8")
    (old / ".watching").write_text("", encoding="utf-8")
    (workspace / ".firm" / relay_title.TITLES_FILE).write_text(
        json.dumps({"MEM-001": "pen"}), encoding="utf-8")

    moved = relay_title._migrate_inbox(store, "pen", "quill")

    assert moved["moved"] == 1
    assert (store / "quill" / "0001-ping.json").exists()
    assert not (old / "0001-ping.json").exists()
    assert old.exists(), "the old inbox is left behind, never deleted"
    pointer = json.loads((old / relay_title.POINTER_FILE).read_text(encoding="utf-8"))
    assert pointer["renamed_to"] == "quill"


def test_the_liveness_sentinel_does_not_travel(tmp_path):
    """`.watching` belongs to the monitor watching the OLD path. Moving it
    would tell the board a dead watcher is fresh."""
    workspace = _firm(tmp_path)
    store = firm_relay.store_path(workspace)
    old = store / "pen"
    old.mkdir(parents=True)
    (old / ".watching").write_text("", encoding="utf-8")

    relay_title._migrate_inbox(store, "pen", "quill")

    assert (old / ".watching").exists()
    assert not (store / "quill" / ".watching").exists()


def test_a_message_already_in_the_new_inbox_is_never_overwritten(tmp_path):
    workspace = _firm(tmp_path)
    store = firm_relay.store_path(workspace)
    (store / "pen").mkdir(parents=True)
    (store / "pen" / "0001.json").write_text("old", encoding="utf-8")
    (store / "quill").mkdir(parents=True)
    (store / "quill" / "0001.json").write_text("new", encoding="utf-8")

    moved = relay_title._migrate_inbox(store, "pen", "quill")

    assert moved["moved"] == 0 and moved["kept"] == 1
    assert (store / "quill" / "0001.json").read_text(encoding="utf-8") == "new"
    assert (store / "pen" / "0001.json").read_text(encoding="utf-8") == "old"


def test_nothing_to_carry_is_said_plainly(tmp_path):
    workspace = _firm(tmp_path)

    moved = relay_title._migrate_inbox(firm_relay.store_path(workspace),
                                       "pen", "quill")

    assert moved["moved"] == 0
    assert "does not exist" in moved["detail"]


# --- the session env that covers a Claude session opened in the firm --------


def test_the_firms_settings_point_a_session_at_the_firms_tier(tmp_path):
    workspace = _firm(tmp_path)

    result = graph_isolation.write_session_env(workspace)

    assert result["written"], result["detail"]
    settings = json.loads(
        (workspace / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert settings["env"]["BASE_HOME"] == str(
        graph_isolation.firm_base_home(workspace))


def test_it_merges_rather_than_replaces(tmp_path):
    workspace = _firm(tmp_path)
    local = workspace / ".claude" / "settings.local.json"
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"outputStyle": "plain",
                                 "env": {"SOMETHING_ELSE": "keep me"}}),
                     encoding="utf-8")

    graph_isolation.write_session_env(workspace)

    settings = json.loads(local.read_text(encoding="utf-8"))
    assert settings["outputStyle"] == "plain"
    assert settings["env"]["SOMETHING_ELSE"] == "keep me"
    assert settings["env"]["BASE_HOME"] == str(
        graph_isolation.firm_base_home(workspace))


def test_the_committed_settings_file_is_left_alone(tmp_path):
    """Firms commit `.claude/settings.json`, and this value is a machine path.

    Writing it there would be right on one machine and wrong on every clone --
    the same reason the session-pulse hook command stays a bare `python3`.
    """
    workspace = _firm(tmp_path)
    shared = workspace / ".claude" / "settings.json"
    shared.parent.mkdir(parents=True)
    shared.write_text(json.dumps({"hooks": {}}), encoding="utf-8")

    graph_isolation.write_session_env(workspace)

    assert json.loads(shared.read_text(encoding="utf-8")) == {"hooks": {}}


# --- escalations do not ride the relay, and this is the arm that shows it ---


def test_an_escalation_reaches_the_board_with_the_relay_unusable(tmp_path):
    """Moving the message store must not move the way a firm shouts for help.

    Two detectors, on two different channels, because one would only prove the
    test agrees with itself:

      1. process — every entry into this module explodes if it is reached, so
         an escalation that quietly used the relay fails here rather than in
         production six weeks later;
      2. filesystem — the firm's message store must not exist afterwards. base
         creates it on first use, so its absence is independent evidence that
         nothing went near it, and it holds even if a future escalation path
         reaches the relay through some module this test never patched.
    """
    import sqlite3

    from firm.core import repo
    from firm.core.migrate import apply_migrations
    from firm.services.escalation import raise_escalation

    workspace = _firm(tmp_path)
    conn = sqlite3.connect(workspace / ".firm" / "firm.db")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    repo.create(conn, "firm", {"id": "zqfirm", "name": "Acme"})
    repo.create(conn, "member", {"id": "MEM-001", "firm_id": "zqfirm",
                                 "name": "Pen", "role": "Writer",
                                 "status": "active"})

    def _explode(*args, **kwargs):
        raise AssertionError(
            "an escalation reached the relay — with per-firm stores that would "
            "deliver the firm's cry for help into the firm's own inbox, where "
            "the Board is not looking")

    import unittest.mock as _mock
    with _mock.patch.multiple(firm_relay, _run=_explode, sessions=_explode,
                              ping=_explode, task=_explode):
        result = raise_escalation(conn, "zqfirm", {
            "raised_by_member_id": "MEM-001",
            "title": "the printer is on fire",
        })

    assert result["escalation"]["title"] == "the printer is on fire"
    rows = repo.find(conn, "escalation", firm_id="zqfirm")
    assert len(rows) == 1, f"the escalation did not land: {rows}"
    conn.close()

    assert not firm_relay.store_path(workspace).exists(), (
        "the firm's message store was created during an escalation, so "
        "something in that path touched the relay")


def test_that_escalation_arm_can_fail(tmp_path):
    """The control on the arm above.

    If the explode-on-touch detector were wired to something the code never
    calls, the arm would pass over an escalation that DID use the relay. So
    this calls the relay deliberately, through the same patch, and requires the
    detector to fire.
    """
    import unittest.mock as _mock

    workspace = _firm(tmp_path)

    def _explode(*args, **kwargs):
        raise AssertionError("reached the relay")

    with _mock.patch.multiple(firm_relay, _run=_explode, sessions=_explode,
                              ping=_explode, task=_explode):
        with pytest.raises(AssertionError, match="reached the relay"):
            firm_relay.ping(workspace, to="pen", message="hello")


def test_an_unreadable_settings_file_is_reported_not_overwritten(tmp_path):
    workspace = _firm(tmp_path)
    local = workspace / ".claude" / "settings.local.json"
    local.parent.mkdir(parents=True)
    local.write_text("{ this is not json", encoding="utf-8")

    result = graph_isolation.write_session_env(workspace)

    assert not result["written"]
    assert "could not be read" in result["detail"]
    assert local.read_text(encoding="utf-8") == "{ this is not json", (
        "someone else's file is not ours to replace")
