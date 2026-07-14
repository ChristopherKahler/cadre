"""Chat rail — conversation store, turn dispatch, event bus, approval gate.

No test here touches a real HTTP socket, systemd, or a real claude: turns
run through an injected ``turn_runner``, the approve gate's HTTP boundary is
``approve_mcp._chat_call`` (monkeypatched), and state lands under a tmp
``CADRE_HOME``. The daemon class is driven directly — the HTTP handler is a
thin JSON shim over these exact methods.
"""

from __future__ import annotations

import json
import os
import stat
import threading

import pytest

import firm.rail as rail
import firm.rail.approve_mcp as approve_mcp
import firm.rail.chat as rail_chat
from firm.rail.chat import ChatRail, EventBus
from firm.rail.turns import TurnResult


@pytest.fixture(autouse=True)
def _tmp_cadre_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CADRE_HOME", str(tmp_path / "cadre-home"))


def _config(**overrides):
    config = rail_chat.chat_config()
    config.update({"firms_root": "/tmp/firms", "mode": "skip"})
    config.update(overrides)
    return config


def _rail(runner, **config_overrides):
    return ChatRail(_config(**config_overrides), claude_bin="/bin/claude",
                    turn_runner=runner)


def _drain_pool(chat_rail: ChatRail) -> None:
    chat_rail._pool.shutdown(wait=True)


# ---------------------------------------------------------------------------
# conversation store
# ---------------------------------------------------------------------------

def test_conversation_files_are_private_and_listed_newest_first():
    a = {"id": "c1aa", "title": "first", "created": 100.0,
         "messages": [{"id": 1, "role": "operator", "kind": "chat",
                       "text": "hi", "ts": 100.0}]}
    b = {"id": "c2bb", "title": "second", "created": 200.0,
         "messages": [{"id": 1, "role": "operator", "kind": "chat",
                       "text": "yo", "ts": 200.0}]}
    rail_chat.save_conversation(a)
    rail_chat.save_conversation(b)
    path = rail_chat.conversations_dir() / "c1aa.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    listed = rail_chat.list_conversations()
    assert [e["id"] for e in listed] == ["c2bb", "c1aa"]
    assert listed[0]["message_count"] == 1
    assert rail_chat.load_conversation("missing") is None


def test_compose_prompt_carries_the_chat_protocol_and_say_command():
    fresh = rail_chat.compose_prompt("what's waiting?", resumed=False)
    assert fresh.startswith("/boardroom\n\nAgenda:\nwhat's waiting?")
    assert "cadre chat rail protocol" in fresh
    assert "chat say" in fresh
    quiet = rail_chat.compose_prompt("what's waiting?", resumed=False, updates=False)
    assert quiet == "/boardroom\n\nAgenda:\nwhat's waiting?"
    assert rail_chat.compose_prompt("approve it", resumed=True) == "approve it"


# ---------------------------------------------------------------------------
# event bus
# ---------------------------------------------------------------------------

def test_event_bus_sequences_and_replays_since():
    bus = EventBus()
    bus.publish("message", {"n": 1})
    bus.publish("status", {"n": 2})
    events = bus.wait_since(0, timeout=0.01)
    assert [(seq, kind) for seq, kind, _ in events] == [(1, "message"), (2, "status")]
    assert bus.wait_since(2, timeout=0.01) == []
    late = []

    def _subscriber():
        late.extend(bus.wait_since(2, timeout=5.0))

    t = threading.Thread(target=_subscriber)
    t.start()
    bus.publish("activity", {"n": 3})
    t.join(timeout=5.0)
    assert [seq for seq, _, _ in late] == [3]


# ---------------------------------------------------------------------------
# turn dispatch
# ---------------------------------------------------------------------------

def test_first_message_creates_conversation_and_turn_updates_map():
    seen = []

    def runner(config, *, text, conv_id, resume, claude_bin,
               on_session=None, on_activity=None, **kw):
        seen.append({"text": text, "resume": resume})
        if on_session:
            on_session("sess-1")
        if on_activity:
            on_activity("⚙ Bash")
        return TurnResult(True, "sess-1", f"echo: {text}")

    r = _rail(runner)
    out = r.post_operator_message(None, "first")
    assert out["ok"]
    conv_id = out["conversation_id"]
    _drain_pool(r)

    conv = rail_chat.load_conversation(conv_id)
    kinds = [(m["role"], m["kind"]) for m in conv["messages"]]
    assert kinds[0] == ("operator", "chat")
    assert ("system", "ack") in kinds
    assert kinds[-1] == ("board", "chat")
    assert conv["messages"][-1]["text"] == "echo: first"

    entry = rail.load_threads("chat")[conv_id]
    assert entry["session_id"] == "sess-1"
    assert entry["status"] == "idle"   # running only while a turn is live

    events = [kind for _, kind, _ in r.bus.wait_since(0, timeout=0.01)]
    assert "conversation" in events
    assert "activity" in events
    assert "status" in events


def test_reply_resumes_the_recorded_session():
    seen = []

    def runner(config, *, text, conv_id, resume, claude_bin, **kw):
        seen.append(resume)
        return TurnResult(True, "sess-2", "ok")

    r = _rail(runner)
    conv_id = r.post_operator_message(None, "first")["conversation_id"]
    _drain_pool(r)
    r2 = _rail(runner)   # fresh daemon — the map survives restarts on disk
    r2.post_operator_message(conv_id, "again")
    _drain_pool(r2)
    assert seen == [None, "sess-2"]


def test_gone_resume_target_gets_fresh_session_and_says_so():
    calls = []

    def runner(config, *, text, conv_id, resume, claude_bin, **kw):
        calls.append(resume)
        if resume:
            return TurnResult(False, None, "", "resume target gone")
        return TurnResult(True, "sess-new", "fresh answer")

    r = _rail(runner)
    rail.save_threads("chat", {"cdead": {"session_id": "sess-old",
                                         "status": "idle", "last_turn": 1.0}})
    rail_chat.save_conversation({"id": "cdead", "title": "t", "created": 1.0,
                                 "messages": []})
    r.threads = rail.load_threads("chat")
    r.post_operator_message("cdead", "hello?")
    _drain_pool(r)
    assert calls == ["sess-old", None]
    conv = rail_chat.load_conversation("cdead")
    final = conv["messages"][-1]["text"]
    assert final.startswith("previous session was gone")
    assert rail.load_threads("chat")["cdead"]["session_id"] == "sess-new"


def test_failed_turn_reports_honestly_as_error_message():
    def runner(config, **kw):
        return TurnResult(False, None, "", "claude failed to exec: boom")

    r = _rail(runner)
    conv_id = r.post_operator_message(None, "do a thing")["conversation_id"]
    _drain_pool(r)
    conv = rail_chat.load_conversation(conv_id)
    last = conv["messages"][-1]
    assert last["kind"] == "error"
    assert "claude failed to exec" in last["text"]


def test_midflight_message_steers_instead_of_queueing(monkeypatch):
    release = threading.Event()
    steered = []

    def runner(config, *, conv_id, on_session=None, **kw):
        if on_session:
            on_session("sess-live")
        release.wait(timeout=10)
        return TurnResult(True, "sess-live", "done")

    monkeypatch.setattr(rail_chat, "relay_steer",
                        lambda sid, text: steered.append((sid, text)) or "chat-steer-1")
    monkeypatch.setattr(rail_chat.turns, "relay_task_state", lambda slug: "pending")
    r = _rail(runner)
    conv_id = r.post_operator_message(None, "long turn")["conversation_id"]
    for _ in range(100):   # wait for the turn to announce its session
        if (r.threads.get(conv_id) or {}).get("status") == "running":
            break
        __import__("time").sleep(0.02)
    r.post_operator_message(conv_id, "also do this")
    for _ in range(100):   # the steer branch runs on a worker — wait for it
        conv = rail_chat.load_conversation(conv_id)
        if any(m["kind"] == "steer" for m in conv["messages"]):
            break
        __import__("time").sleep(0.02)
    assert steered == [("sess-live", "also do this")]
    assert any(m["kind"] == "steer" for m in conv["messages"])
    release.set()
    _drain_pool(r)


def test_steer_receipt_flips_to_delivered_when_the_task_lands(monkeypatch):
    r = _rail(lambda config, **kw: TurnResult(True, "s", "x"))
    rail_chat.save_conversation({"id": "c5", "title": "t", "created": 1.0,
                                 "messages": []})
    message = r._append("c5", "system", "steered…", kind="steer",
                        extra={"receipt": "sent", "slug": "chat-steer-9"})
    states = iter(["pending", "delivered"])
    monkeypatch.setattr(rail_chat.turns, "relay_task_state",
                        lambda slug: next(states))
    r._watch_receipt("c5", message["id"], "chat-steer-9", poll_sec=0.01)
    conv = rail_chat.load_conversation("c5")
    mark = next(m for m in conv["messages"] if m["kind"] == "steer")
    assert mark["receipt"] == "delivered"
    receipts = [d for _, k, d in r.bus.wait_since(0, timeout=0.01) if k == "receipt"]
    assert receipts == [{"conversation_id": "c5", "message_id": message["id"],
                         "receipt": "delivered"}]

    # Relay unavailable → give up silently; the mark honestly stays "sent".
    second = r._append("c5", "system", "steered…", kind="steer",
                       extra={"receipt": "sent", "slug": "chat-steer-10"})
    monkeypatch.setattr(rail_chat.turns, "relay_task_state", lambda slug: None)
    r._watch_receipt("c5", second["id"], "chat-steer-10", poll_sec=0.01)
    conv = rail_chat.load_conversation("c5")
    kept = next(m for m in conv["messages"] if m["id"] == second["id"])
    assert kept["receipt"] == "sent"


def test_relay_task_state_parses_pending_delivered_cleared(monkeypatch):
    listing = ("  chat-steer-1 (task) → mantis [pending, high] · 1m · from chat-rail\n"
               "  chat-steer-2 (task) → mantis [delivered, high] · 1m · from chat-rail\n")

    class R:
        returncode = 0
        stdout = listing

    monkeypatch.setattr(rail_chat.turns.subprocess, "run", lambda cmd, **kw: R())
    monkeypatch.setattr(rail_chat.turns.shutil, "which", lambda name: "/usr/bin/base")
    assert rail_chat.turns.relay_task_state("chat-steer-1") == "pending"
    assert rail_chat.turns.relay_task_state("chat-steer-2") == "delivered"
    assert rail_chat.turns.relay_task_state("chat-steer-404") == "cleared"


def test_say_appends_board_message_with_midturn_kind():
    r = _rail(lambda config, **kw: TurnResult(True, "s", "x"))
    rail_chat.save_conversation({"id": "c9", "title": "t", "created": 1.0,
                                 "messages": []})
    out = r.say("c9", "still working — pulling briefs")
    assert out["ok"]
    conv = rail_chat.load_conversation("c9")
    assert conv["messages"][-1]["kind"] == "say"
    assert conv["messages"][-1]["role"] == "board"
    assert r.say("missing", "x")["ok"] is False


# ---------------------------------------------------------------------------
# firm badges + context telemetry
# ---------------------------------------------------------------------------

def _fake_firm(root, slug, display=None):
    firm_dir = root / slug / ".firm"
    firm_dir.mkdir(parents=True)
    if display:
        import sqlite3
        con = sqlite3.connect(firm_dir / "firm.db")
        con.execute("CREATE TABLE firm (id TEXT, name TEXT)")
        con.execute("INSERT INTO firm VALUES (?, ?)", (slug, display))
        con.commit()
        con.close()


def test_firm_display_name_reads_db_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(rail_chat, "_FIRM_NAME_CACHE", {})
    root = tmp_path / "firms"
    _fake_firm(root, "downstream", display="Downstream Media")
    _fake_firm(root, "lab")   # no db → slug
    assert rail_chat.firm_display_name(str(root), "downstream") == "Downstream Media"
    assert rail_chat.firm_display_name(str(root), "lab") == "lab"
    assert rail_chat.firm_display_name(str(root), "ghost") == "ghost"


def test_scope_prefix_becomes_the_first_badge(tmp_path, monkeypatch):
    monkeypatch.setattr(rail_chat, "_FIRM_NAME_CACHE", {})
    root = tmp_path / "firms"
    _fake_firm(root, "downstream", display="Downstream Media")

    def runner(config, **kw):
        return TurnResult(True, "s", "x")

    r = _rail(runner, firms_root=str(root))
    conv_id = r.post_operator_message(None, "@downstream pulse it")["conversation_id"]
    _drain_pool(r)
    conv = rail_chat.load_conversation(conv_id)
    assert conv["firms"] == ["downstream"]
    firms_events = [d for _, k, d in r.bus.wait_since(0, timeout=0.01) if k == "firms"]
    assert firms_events[0]["firms"] == [{"slug": "downstream",
                                         "name": "Downstream Media"}]


def test_event_stream_appends_touched_firms_and_context(tmp_path, monkeypatch):
    monkeypatch.setattr(rail_chat, "_FIRM_NAME_CACHE", {})
    root = tmp_path / "firms"
    _fake_firm(root, "downstream", display="Downstream Media")
    _fake_firm(root, "chrisai", display="ChrisAI")

    def runner(config, *, on_event=None, **kw):
        if on_event:
            on_event({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read",
                 "input": {"file_path": f"{root}/downstream/PLAN.md"}}]}})
            on_event({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash",
                 "input": {"command": f"ls {root}/chrisai/"}}]}})
            on_event({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "the word confirms/nothing here"}]}})
            on_event({"type": "result", "usage": {
                "input_tokens": 1200, "cache_read_input_tokens": 398800,
                "cache_creation_input_tokens": 0, "output_tokens": 900}})
        return TurnResult(True, "s", "done")

    r = _rail(runner, firms_root=str(root), model="opus[1m]")
    conv_id = r.post_operator_message(None, "brief me")["conversation_id"]
    _drain_pool(r)

    conv = rail_chat.load_conversation(conv_id)
    assert conv["firms"] == ["downstream", "chrisai"]   # order of first touch
    assert conv["context_tokens"] == 400_000
    assert conv["context_window"] == 1_000_000

    events = list(r.bus.wait_since(0, timeout=0.01))
    firm_payloads = [d for _, k, d in events if k == "firms"]
    assert firm_payloads[-1]["firms"] == [
        {"slug": "downstream", "name": "Downstream Media"},
        {"slug": "chrisai", "name": "ChrisAI"}]
    ctx = [d for _, k, d in events if k == "context"]
    assert ctx == [{"conversation_id": conv_id,
                    "tokens": 400_000, "window": 1_000_000}]

    payload = r.state_payload()   # the switcher + badge links ride this
    assert {"slug": "chrisai", "name": "ChrisAI"} in payload["firms"]
    assert {"slug": "downstream", "name": "Downstream Media"} in payload["firms"]
    assert payload["dash_url"] == "http://127.0.0.1:8484"


def test_context_window_defaults_without_1m_flag():
    assert rail_chat.context_window_for("opus[1m]") == 1_000_000
    assert rail_chat.context_window_for("opus") == 200_000
    assert rail_chat.context_window_for("") == 200_000


# ---------------------------------------------------------------------------
# approvals — fail closed everywhere
# ---------------------------------------------------------------------------

def test_approval_lifecycle_allow_and_first_verdict_wins():
    r = _rail(lambda config, **kw: TurnResult(True, "s", "x"))
    rail_chat.save_conversation({"id": "c1", "title": "t", "created": 1.0,
                                 "messages": []})
    created = r.create_approval("c1", "Bash", {"command": "rm -rf /tmp/x"}, 300)
    assert created["ok"]
    rid = created["id"]
    assert r.approval_state(rid) == {"ok": True, "verdict": None}
    assert r.set_verdict(rid, "allow")["verdict"] == "allow"
    assert r.set_verdict(rid, "deny")["verdict"] == "allow"   # first answer wins
    conv = rail_chat.load_conversation("c1")
    card = next(m for m in conv["messages"] if m["kind"] == "approval")
    assert card["verdict"] == "allow"
    assert card["tool_name"] == "Bash"
    assert r.approval_state("nope")["ok"] is False
    assert r.create_approval("missing", "Bash", {}, 300)["ok"] is False


def test_chat_gate_allows_on_board_click(monkeypatch):
    monkeypatch.setenv("CADRE_RAIL_PROVIDER", "chat")
    monkeypatch.setenv("CADRE_RAIL_CHAT_URL", "http://127.0.0.1:7787")
    monkeypatch.setenv("CADRE_RAIL_THREAD_TS", "c1")
    monkeypatch.setenv("CADRE_RAIL_APPROVE_TIMEOUT", "5")
    calls = []

    def fake_call(url, payload=None, timeout=10.0):
        calls.append(url)
        if url.endswith("/api/approve"):
            return {"ok": True, "id": "a1"}
        return {"ok": True, "verdict": "allow"}

    monkeypatch.setattr(approve_mcp, "_chat_call", fake_call)
    verdict = json.loads(approve_mcp.decide("Bash", {"command": "ls"}))
    assert verdict["behavior"] == "allow"
    assert verdict["updatedInput"] == {"command": "ls"}


def test_chat_gate_fails_closed_on_deny_unreachable_and_misconfig(monkeypatch):
    monkeypatch.setenv("CADRE_RAIL_PROVIDER", "chat")
    monkeypatch.setenv("CADRE_RAIL_CHAT_URL", "http://127.0.0.1:7787")
    monkeypatch.setenv("CADRE_RAIL_THREAD_TS", "c1")

    monkeypatch.setattr(approve_mcp, "_chat_call",
                        lambda url, payload=None, timeout=10.0: {"ok": False})
    assert json.loads(approve_mcp.decide("Bash", {}))["behavior"] == "deny"

    def denying(url, payload=None, timeout=10.0):
        if url.endswith("/api/approve"):
            return {"ok": True, "id": "a2"}
        return {"ok": True, "verdict": "deny"}

    monkeypatch.setattr(approve_mcp, "_chat_call", denying)
    assert json.loads(approve_mcp.decide("Bash", {}))["behavior"] == "deny"

    monkeypatch.delenv("CADRE_RAIL_CHAT_URL")
    assert json.loads(approve_mcp.decide("Bash", {}))["behavior"] == "deny"


def test_slack_gate_still_routes_when_provider_absent(monkeypatch):
    # No CADRE_RAIL_PROVIDER → the original slack path (misconfigured here,
    # so it denies) — pre-chat turn mcp-configs keep working.
    monkeypatch.delenv("CADRE_RAIL_PROVIDER", raising=False)
    for key in ("CADRE_RAIL_CHANNEL", "CADRE_RAIL_THREAD_TS",
                "CADRE_RAIL_APPROVERS", approve_mcp.BOT_TOKEN_KEY):
        monkeypatch.delenv(key, raising=False)
    verdict = json.loads(approve_mcp.decide("Bash", {}))
    assert verdict["behavior"] == "deny"
    assert "misconfigured" in verdict["message"]


# ---------------------------------------------------------------------------
# state payload (the dashboard/UI read model)
# ---------------------------------------------------------------------------

def test_state_payload_reports_running_conversations():
    release = threading.Event()

    def runner(config, *, on_session=None, **kw):
        if on_session:
            on_session("sess-9")
        release.wait(timeout=10)
        return TurnResult(True, "sess-9", "done")

    r = _rail(runner)
    conv_id = r.post_operator_message(None, "brief me")["conversation_id"]
    for _ in range(100):
        if (r.threads.get(conv_id) or {}).get("status") == "running":
            break
        __import__("time").sleep(0.02)
    payload = r.state_payload()
    entry = next(c for c in payload["conversations"] if c["id"] == conv_id)
    assert entry["status"] == "running"
    assert payload["mode"] == "skip"
    release.set()
    _drain_pool(r)
