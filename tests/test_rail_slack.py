"""Slack rail — config store, turn composition, event filtering, approval gate.

No test here touches Slack, systemd, or a real claude: the Web API boundary
is ``firm.rail.slack_call`` (monkeypatched per import site), turns run
through an injected ``turn_runner``, and state lands under a tmp
``CADRE_HOME``.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

import firm.rail as rail
import firm.rail.approve_mcp as approve_mcp
import firm.rail.slack as rail_slack
from firm.rail.slack import SlackRail, TurnResult


@pytest.fixture(autouse=True)
def _tmp_cadre_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CADRE_HOME", str(tmp_path / "cadre-home"))


# ---------------------------------------------------------------------------
# rail store
# ---------------------------------------------------------------------------

def test_config_roundtrip_layers_defaults_and_is_private():
    cfg = rail.load_config("slack")
    assert cfg["mode"] == "approve"          # default
    cfg["channel_id"] = "C123"
    path = rail.save_config("slack", cfg)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    again = rail.load_config("slack")
    assert again["channel_id"] == "C123"
    assert again["turn_timeout_sec"] == 1800  # default survives partial config


def test_threads_roundtrip_and_prune():
    now = 1_000_000.0
    threads = {
        "1.1": {"session_id": "a", "last_turn": now - 40 * 86400},
        "2.2": {"session_id": "b", "last_turn": now - 3600},
    }
    rail.save_threads("slack", threads)
    loaded = rail.load_threads("slack")
    assert set(loaded) == {"1.1", "2.2"}
    pruned = rail.prune_threads(loaded, now=now)
    assert set(pruned) == {"2.2"}


def test_chunk_text_prefers_newlines_and_handles_empty():
    assert rail.chunk_text("") == []
    text = ("line one\n" * 100).strip()
    chunks = rail.chunk_text(text, limit=200)
    assert all(len(c) <= 200 for c in chunks)
    assert "\n".join(chunks).replace("\n\n", "\n") == text


# ---------------------------------------------------------------------------
# turn composition
# ---------------------------------------------------------------------------

def test_scope_prefix_targets_a_firm():
    assert rail_slack.parse_scope("@downstream pulse it") == ("downstream", "pulse it")
    assert rail_slack.parse_scope("no scope here") == ("", "no scope here")
    prompt = rail_slack.compose_prompt("@downstream pulse it", resumed=False)
    assert prompt.startswith("/boardroom downstream\n\nAgenda:\npulse it")


def test_compose_prompt_fresh_vs_resumed():
    fresh = rail_slack.compose_prompt("what's waiting?", resumed=False)
    assert fresh.startswith("/boardroom\n\nAgenda:\nwhat's waiting?")
    assert "Slack rail protocol" in fresh       # in-turn emission rules ride along
    assert "slack say" in fresh                 # with the mid-turn voice command
    # Resumed turns stay clean — the session already carries the protocol.
    assert rail_slack.compose_prompt("approve it", resumed=True) == "approve it"
    # Quiet mode: no protocol block, one answer per turn.
    quiet = rail_slack.compose_prompt("what's waiting?", resumed=False, updates=False)
    assert quiet == "/boardroom\n\nAgenda:\nwhat's waiting?"


def test_build_cmd_approve_mode_routes_permissions_to_the_gate():
    cmd = rail_slack.build_cmd(
        "/bin/claude", mode="approve", prompt="p",
        mcp_config="/tmp/x.json", resume="sid-1")
    joined = " ".join(cmd)
    assert "--permission-prompt-tool mcp__cadre-rail__approve" in joined
    assert "--dangerously-skip-permissions" not in joined
    assert "--strict-mcp-config" in joined
    assert "--mcp-config /tmp/x.json" in joined
    assert "--resume sid-1" in joined
    assert cmd[-2:] == ["-p", "p"]


def test_build_cmd_skip_mode_and_full_load():
    cmd = rail_slack.build_cmd("/bin/claude", mode="skip", prompt="p", full_load=True)
    joined = " ".join(cmd)
    assert "--dangerously-skip-permissions" in joined
    assert "--permission-prompt-tool" not in joined
    assert "--strict-mcp-config" not in joined


def test_build_cmd_model_override_passes_through_verbatim():
    cmd = rail_slack.build_cmd("/bin/claude", mode="skip", prompt="p",
                               model="opus[1m]")
    assert " ".join(cmd).count("--model opus[1m]") == 1
    default = rail_slack.build_cmd("/bin/claude", mode="skip", prompt="p")
    assert "--model" not in default


def test_parse_stream_success_error_and_silence():
    ok = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "session_id": "s-1"}),
        "not json",
        json.dumps({"type": "result", "subtype": "success", "is_error": False,
                    "result": "the answer"}),
    ])
    assert rail_slack.parse_stream(ok) == ("s-1", "the answer", False)

    err = json.dumps({"type": "result", "subtype": "error_during_execution",
                      "is_error": True, "session_id": "s-2"})
    sid, text, is_error = rail_slack.parse_stream(err)
    assert (sid, is_error) == ("s-2", True)
    assert "error_during_execution" in text

    assert rail_slack.parse_stream("") == (None, "", True)   # no result = error


def test_turn_mcp_config_carries_routing_but_never_the_token(tmp_path):
    path = rail_slack.write_turn_mcp_config(
        tmp_path / "t.mcp.json", channel="C1", thread_ts="9.9",
        approvers=["U1", "U2"], approve_timeout_sec=60)
    config = json.loads((tmp_path / "t.mcp.json").read_text())
    server = config["mcpServers"]["cadre-rail"]
    assert server["env"]["CADRE_RAIL_APPROVERS"] == "U1,U2"
    assert server["env"]["CADRE_RAIL_THREAD_TS"] == "9.9"
    assert rail_slack.BOT_TOKEN_KEY not in json.dumps(config)
    assert path == str(tmp_path / "t.mcp.json")


def test_to_mrkdwn_bold_and_headings():
    assert rail_slack.to_mrkdwn("**hi** there") == "*hi* there"
    assert rail_slack.to_mrkdwn("## Agenda\nbody") == "*Agenda*\nbody"


# ---------------------------------------------------------------------------
# the daemon's filter + turn loop (no socket, no subprocess)
# ---------------------------------------------------------------------------

_CFG = {
    "channel_id": "C1",
    "allowlist": ["U-BOARD"],
    "mode": "skip",
    "firms_root": "/tmp",
    "turn_timeout_sec": 5,
    "approve_timeout_sec": 5,
    "full_load": False,
}


def _rail(turn_runner):
    return SlackRail(dict(_CFG), bot_token="xoxb-test", claude_bin="/bin/claude",
                     bot_user_id="U-BOT", turn_runner=turn_runner)


def test_accepts_is_the_structural_allowlist():
    r = _rail(lambda **kw: TurnResult(True, "s", "t"))
    ok = {"type": "message", "channel": "C1", "user": "U-BOARD",
          "text": "hi", "ts": "1.0"}
    assert r.accepts(ok)
    assert not r.accepts({**ok, "channel": "C-OTHER"})
    assert not r.accepts({**ok, "user": "U-STRANGER"})
    assert not r.accepts({**ok, "bot_id": "B1"})
    assert not r.accepts({**ok, "subtype": "message_changed"})
    assert not r.accepts({**ok, "text": "   "})
    assert not r.accepts({**ok, "user": "U-BOT"})


def test_turn_flow_posts_reply_and_resumes_the_thread(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(rail_slack, "slack_call",
                        lambda method, token, **p: calls.append((method, p)) or {"ok": True})
    seen: list[dict] = []

    def runner(config, *, text, resume, on_session=None, **kw):
        seen.append({"text": text, "resume": resume})
        if on_session:
            on_session("sess-1")
        return TurnResult(True, "sess-1", f"echo: {text}")

    r = _rail(runner)
    assert r.handle_event({"type": "message", "channel": "C1", "user": "U-BOARD",
                           "text": "first", "ts": "10.0"})
    assert r.handle_event({"type": "message", "channel": "C1", "user": "U-BOARD",
                           "text": "second", "ts": "11.0", "thread_ts": "10.0"})
    r._pool.shutdown(wait=True)

    assert [s["resume"] for s in seen] == [None, "sess-1"]   # reply resumed
    posted = [p for m, p in calls if m == "chat.postMessage"]
    assert all(p["thread_ts"] == "10.0" for p in posted)
    assert any("echo: first" in p["text"] for p in posted)
    entry = rail.load_threads("slack")["10.0"]
    assert entry["session_id"] == "sess-1"
    assert entry["status"] == "idle"   # running only while a turn is live


def test_relay_steer_sends_a_clearable_task_to_the_live_title(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        class R:
            returncode = 0
            stdout = ("  mantis  [live · 2m]  ws:x  session:sess-9\n"
                      "  otter  [DEAD · 1h]  ws:y  session:sess-0\n")
        return R()

    monkeypatch.setattr(rail_slack.subprocess, "run", fake_run)
    monkeypatch.setattr(rail_slack.shutil, "which", lambda name: "/usr/bin/base")

    assert rail_slack.relay_steer("sess-9", "no more commands, synthesize") is True
    task_cmd = calls[1]
    assert task_cmd[:5] == ["/usr/bin/base", "relay", "task", "--to", "mantis"]
    summary = task_cmd[task_cmd.index("--summary") + 1]
    assert "synthesize" in summary
    assert "slack say" in summary          # the reply path rides along
    assert "relay done slack-steer-" in summary   # receiver can clear it

    calls.clear()
    assert rail_slack.relay_steer("sess-0", "x") is False    # dead session
    assert rail_slack.relay_steer("sess-404", "x") is False  # unknown session


def test_failed_turn_reports_honestly_with_x(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(rail_slack, "slack_call",
                        lambda method, token, **p: calls.append((method, p)) or {"ok": True})
    r = _rail(lambda config, **kw: TurnResult(False, None, "", "turn timed out"))
    r.handle_event({"type": "message", "channel": "C1", "user": "U-BOARD",
                    "text": "boom", "ts": "20.0"})
    r._pool.shutdown(wait=True)

    posted = [p for m, p in calls if m == "chat.postMessage"]
    assert any("turn timed out" in p["text"] for p in posted)
    reactions = [p["name"] for m, p in calls if m == "reactions.add"]
    assert "x" in reactions and "white_check_mark" not in reactions


# ---------------------------------------------------------------------------
# the approval gate — fail closed, approvers only
# ---------------------------------------------------------------------------

def test_verdicts_come_only_from_approvers_and_deny_wins():
    v = approve_mcp.verdict_from_reactions
    approvers = ["U-BOARD"]
    assert v([{"name": "+1", "users": ["U-BOARD"]}], approvers) == "allow"
    assert v([{"name": "thumbsup::skin-tone-3", "users": ["U-BOARD"]}], approvers) == "allow"
    assert v([{"name": "-1", "users": ["U-BOARD"]},
              {"name": "+1", "users": ["U-BOARD"]}], approvers) == "deny"
    assert v([{"name": "+1", "users": ["U-STRANGER"]}], approvers) is None
    assert v([{"name": "tada", "users": ["U-BOARD"]}], approvers) is None


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, sec):
        self.now += sec


def test_wait_for_verdict_polls_then_times_out(monkeypatch):
    answers = iter([
        {"ok": True, "message": {"reactions": []}},
        {"ok": True, "message": {"reactions": [{"name": "+1", "users": ["U-BOARD"]}]}},
    ])
    monkeypatch.setattr(approve_mcp, "slack_call", lambda *a, **k: next(answers))
    verdict = approve_mcp.wait_for_verdict(
        token="t", channel="C1", message_ts="1.0", approvers=["U-BOARD"],
        timeout_sec=10, clock=_FakeClock())
    assert verdict == "allow"

    monkeypatch.setattr(approve_mcp, "slack_call",
                        lambda *a, **k: {"ok": True, "message": {"reactions": []}})
    verdict = approve_mcp.wait_for_verdict(
        token="t", channel="C1", message_ts="1.0", approvers=["U-BOARD"],
        timeout_sec=6, clock=_FakeClock())
    assert verdict == "timeout"


def test_decide_fails_closed_without_routing(monkeypatch):
    for key in ("CADRE_RAIL_CHANNEL", "CADRE_RAIL_THREAD_TS",
                "CADRE_RAIL_APPROVERS", rail_slack.BOT_TOKEN_KEY):
        monkeypatch.delenv(key, raising=False)
    verdict = json.loads(approve_mcp.decide("Bash", {"command": "rm -rf /"}))
    assert verdict["behavior"] == "deny"


def test_decide_denies_when_slack_unreachable(monkeypatch):
    monkeypatch.setenv(rail_slack.BOT_TOKEN_KEY, "xoxb-test")
    monkeypatch.setenv("CADRE_RAIL_CHANNEL", "C1")
    monkeypatch.setenv("CADRE_RAIL_THREAD_TS", "1.0")
    monkeypatch.setenv("CADRE_RAIL_APPROVERS", "U-BOARD")
    monkeypatch.setattr(approve_mcp, "slack_call",
                        lambda *a, **k: {"ok": False, "error": "transport: down"})
    verdict = json.loads(approve_mcp.decide("Bash", {"command": "ls"}))
    assert verdict["behavior"] == "deny"
    assert "down" in verdict["message"]


def test_decide_allow_roundtrip_stamps_the_message(monkeypatch):
    monkeypatch.setenv(rail_slack.BOT_TOKEN_KEY, "xoxb-test")
    monkeypatch.setenv("CADRE_RAIL_CHANNEL", "C1")
    monkeypatch.setenv("CADRE_RAIL_THREAD_TS", "1.0")
    monkeypatch.setenv("CADRE_RAIL_APPROVERS", "U-BOARD")
    monkeypatch.setenv("CADRE_RAIL_APPROVE_TIMEOUT", "10")
    calls: list[tuple[str, dict]] = []

    def fake_call(method, token, **params):
        calls.append((method, params))
        if method == "chat.postMessage":
            return {"ok": True, "ts": "55.5"}
        if method == "reactions.get":
            return {"ok": True, "message": {"reactions": [
                {"name": "+1", "users": ["U-BOARD"]}]}}
        return {"ok": True}

    monkeypatch.setattr(approve_mcp, "slack_call", fake_call)
    verdict = json.loads(approve_mcp.decide("Bash", {"command": "git status"}))
    assert verdict == {"behavior": "allow", "updatedInput": {"command": "git status"}}
    stamped = [p for m, p in calls if m == "chat.update"]
    assert stamped and stamped[0]["ts"] == "55.5"
