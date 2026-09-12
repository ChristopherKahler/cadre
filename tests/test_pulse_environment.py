"""A pulse makes its own environment whole (issue #107).

A timer pulse is a systemd user unit. It starts with the user manager's bare
PATH and no notify token, so on every unattended pulse a firm with a notify
channel raised "Board notify rail is unresolvable", and preflight held back
every Member whose tool lives off that PATH. Pulse now forwarded only the Slack
token, so a Telegram firm hit the same rail error there.

Nothing here spawns ``claude`` or calls systemd: Member runs go to a fake
runner, the rail's one network call is recorded instead of sent, and Pulse now
dispatches to a fake scheduler.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from firm.core.db import connect
from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.secrets.provider import resolve_provider

# The systemd user manager's PATH on the operator's machine, measured for
# issue #107: no ~/.local/bin, no .firm/bin.
SYSTEMD_USER_PATH = ("/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:"
                     "/sbin:/bin:/usr/games:/usr/local/games:/snap/bin")
NOTIFY_TOKEN_NAMES = ("CADRE_SLACK_TOKEN", "CADRE_SLACK_BOT_TOKEN",
                      "CADRE_TELEGRAM_TOKEN", "CADRE_NOTIFY_WEBHOOK")
FIRM = "acme"

# notify_config exactly as the founding Manifest writes it
# (firm.dashboard.founding.set_manifest).
SLACK_CONFIG = {"provider": "slack", "remind_hours": 24,
                "slack_user_id": "U123", "slack_token_env": "CADRE_SLACK_TOKEN"}
TELEGRAM_CONFIG = {"provider": "telegram", "remind_hours": 24,
                   "telegram_chat_id": "42",
                   "telegram_token_env": "CADRE_TELEGRAM_TOKEN"}
# The webhook shape firm.notify documents; founding does not write one.
WEBHOOK_CONFIG = {"provider": "webhook", "remind_hours": 24,
                  "webhook_url_env": "CADRE_NOTIFY_WEBHOOK"}


def _firm(ws: Path, notify_config: dict | None = None) -> Path:
    """A temp firm with one active Member and nothing queued for it."""
    conn = connect(ws / ".firm" / "firm.db")
    apply_migrations(conn)
    firm = {"id": FIRM, "name": "Acme"}
    if notify_config is not None:
        firm["notify_config"] = notify_config
    create(conn, "firm", firm)
    create(conn, "member", {"id": "MEM-001", "firm_id": FIRM, "name": "Lead",
                            "role": "lead", "status": "active"})
    conn.commit()
    conn.close()
    return ws


def _queue_work_needing(ws: Path, tool: str) -> None:
    """Give MEM-001 a claimed Unit and a Contract whose loadout names *tool*."""
    conn = connect(ws / ".firm" / "firm.db")
    create(conn, "contract", {"id": "CON-001", "firm_id": FIRM, "name": "Lead",
                              "runtime_type": "claude_code",
                              "skill_loadout": {"cli": [tool]}})
    conn.execute("UPDATE member SET contract_id = 'CON-001' WHERE id = 'MEM-001'")
    create(conn, "operation", {"id": "OPS-001", "firm_id": FIRM, "name": "Ops"})
    create(conn, "project", {"id": "PROJ-001", "firm_id": FIRM,
                             "operation_id": "OPS-001", "name": "Work",
                             "status": "in_progress", "due_date": "2099-12-31"})
    create(conn, "unit", {"id": "UNIT-001", "firm_id": FIRM,
                          "project_id": "PROJ-001", "name": "Do it",
                          "status": "pending", "claimed_by": "MEM-001"})
    conn.commit()
    conn.close()


def _start_like_a_timer_unit(monkeypatch) -> None:
    """The environment a timer unit starts ``firm pulse`` in: the user
    manager's bare PATH and no notify token."""
    monkeypatch.setenv("PATH", SYSTEMD_USER_PATH)
    for name in NOTIFY_TOKEN_NAMES:
        monkeypatch.setenv(name, "")   # so teardown puts the original back
        monkeypatch.delenv(name)


def _record_rail_calls(monkeypatch) -> list[dict]:
    """The rail's network call, recorded instead of sent."""
    import firm.notify as notify

    calls: list[dict] = []

    def fake_post_json(url, payload, headers):
        calls.append({"url": url, "auth": headers.get("Authorization")})
        return {"ok": True}

    monkeypatch.setattr(notify, "_post_json", fake_post_json)
    return calls


def _pulse_now_dispatch_env(ws: Path, monkeypatch) -> dict[str, str]:
    """The environment the hub's Pulse now hands the scheduler."""
    from firm.dashboard.server import _fire_pulse

    dispatched: dict[str, str] = {}

    class _Scheduler:
        name = "fake"

        def spawn_detached(self, argv, *, workdir, env, unit=None):
            dispatched.update(env)
            return {"via": "detached-popen", "pid": 0}

    monkeypatch.setattr("firm.sched.resolve_scheduler", lambda: _Scheduler())
    _fire_pulse(ws, FIRM)
    return dispatched


def _pulse(ws: Path, monkeypatch, capsys, runner=None) -> dict:
    """``firm pulse`` for the temp firm, in-process, with a fake runner."""
    import firm.cli.pulse as pulse_cli
    import firm.pulse.spawn as spawn_mod

    monkeypatch.setattr(spawn_mod, "resolve_claude_bin",
                        lambda: ("/bin/true", "test"))
    monkeypatch.setattr(
        pulse_cli, "make_runner",
        lambda firm_id, cwd: runner or (lambda conn, m: {"status": "completed"}))
    rc = pulse_cli.run_pulse(ws, firm_id=FIRM)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 0, out
    return out


def _escalations(ws: Path, dedupe_key: str) -> list[str]:
    conn = connect(ws / ".firm" / "firm.db")
    try:
        return [r["title"] for r in conn.execute(
            "SELECT title FROM escalation WHERE dedupe_key = ?", (dedupe_key,))]
    finally:
        conn.close()


def test_timer_pulse_resolves_the_slack_rail_from_the_vault(
        tmp_path, monkeypatch, capsys):
    ws = _firm(tmp_path, SLACK_CONFIG)
    resolve_provider().set(ws, "CADRE_SLACK_TOKEN", "xoxb-from-vault", "firm")
    _start_like_a_timer_unit(monkeypatch)
    calls = _record_rail_calls(monkeypatch)

    out = _pulse(ws, monkeypatch, capsys)

    assert [c["auth"] for c in calls] == ["Bearer xoxb-from-vault"], \
        out.get("error_details")
    assert _escalations(ws, "preflight:notify-rail") == []
    assert out["errors"] == 0, out.get("error_details")


def test_timer_pulse_resolves_the_telegram_rail_from_the_vault(
        tmp_path, monkeypatch, capsys):
    ws = _firm(tmp_path, TELEGRAM_CONFIG)
    resolve_provider().set(ws, "CADRE_TELEGRAM_TOKEN", "123:from-vault", "firm")
    _start_like_a_timer_unit(monkeypatch)
    calls = _record_rail_calls(monkeypatch)

    out = _pulse(ws, monkeypatch, capsys)

    assert [c["url"] for c in calls] == [
        "https://api.telegram.org/bot123:from-vault/getMe"], \
        out.get("error_details")
    assert _escalations(ws, "preflight:notify-rail") == []
    assert out["errors"] == 0, out.get("error_details")


def test_timer_pulse_resolves_the_webhook_rail_from_the_vault(
        tmp_path, monkeypatch, capsys):
    ws = _firm(tmp_path, WEBHOOK_CONFIG)
    resolve_provider().set(ws, "CADRE_NOTIFY_WEBHOOK",
                           "https://hooks.example/from-vault", "firm")
    _start_like_a_timer_unit(monkeypatch)
    _record_rail_calls(monkeypatch)   # a webhook rail has no probe to send

    out = _pulse(ws, monkeypatch, capsys)

    assert _escalations(ws, "preflight:notify-rail") == []
    assert out["errors"] == 0, out.get("error_details")


def test_timer_unit_still_reaches_a_token_in_the_workspace_env(
        tmp_path, monkeypatch, capsys):
    """``firm heartbeat enable`` used to copy a token from the workspace .env
    into the unit. It no longer copies any, so the pulse must read .env itself:
    enable the timer, then start the pulse from exactly the unit's environment.
    """
    import firm.cli.heartbeat as hb
    import firm.sched.systemd as sysd

    ws = _firm(tmp_path, TELEGRAM_CONFIG)
    (ws / ".env").write_text("CADRE_TELEGRAM_TOKEN=123:from-dotenv\n",
                             encoding="utf-8")
    _start_like_a_timer_unit(monkeypatch)
    monkeypatch.setattr(sysd, "run_cmd", lambda argv, timeout=30: (0, ""))
    # The unit bakes the suite's unusable claude path, so copying the unit's
    # environment below leaves the no-claude fence exactly where it was.
    monkeypatch.setattr(hb, "resolve_claude_bin",
                        lambda: (os.environ["CADRE_CLAUDE_BIN"], "test"))
    unit_dir = tmp_path / "units"
    assert hb.run_enable(ws, FIRM, "15m", unit_dir=unit_dir) == 0
    capsys.readouterr()
    service = (unit_dir / f"cadre-heartbeat-{FIRM}.service").read_text(
        encoding="utf-8")
    for line in service.splitlines():
        if line.startswith('Environment="') and line.endswith('"'):
            key, _, value = line[len('Environment="'):-1].partition("=")
            monkeypatch.setenv(key, value)
    calls = _record_rail_calls(monkeypatch)

    out = _pulse(ws, monkeypatch, capsys)

    assert [c["url"] for c in calls] == [
        "https://api.telegram.org/bot123:from-dotenv/getMe"], \
        out.get("error_details")
    assert _escalations(ws, "preflight:notify-rail") == []
    assert out["errors"] == 0, out.get("error_details")


def test_timer_pulse_runs_with_the_path_pulse_now_dispatches_with(
        tmp_path, monkeypatch, capsys):
    """A Member whose tool sits in the firm's .firm/bin is not held back."""
    ws = _firm(tmp_path)
    _queue_work_needing(ws, "firm-local-tool")
    bin_dir = ws / ".firm" / "bin"
    bin_dir.mkdir()
    for name in ("firm-local-tool", "firm-local-tool.cmd"):   # POSIX, Windows
        (bin_dir / name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    # Preflight's catalog probes would run the machine's real CLIs.
    monkeypatch.setattr("firm.dashboard.discovery.cli_survey", lambda: [])
    _start_like_a_timer_unit(monkeypatch)
    pulse_now_path = _pulse_now_dispatch_env(ws, monkeypatch)["PATH"]
    ran: list[tuple[str, str]] = []

    def runner(conn, member):
        ran.append((member["id"], os.environ["PATH"]))
        return {"status": "completed"}

    out = _pulse(ws, monkeypatch, capsys, runner=runner)

    assert ran == [("MEM-001", pulse_now_path)], out.get("skip_reasons")
    assert str(Path.home() / ".local" / "bin") in pulse_now_path.split(os.pathsep)
    assert str(bin_dir) in pulse_now_path.split(os.pathsep)
    assert _escalations(ws, "preflight:firm-local-tool") == []


def test_pulse_now_on_a_telegram_firm_raises_no_unresolvable_rail(
        tmp_path, monkeypatch, capsys):
    ws = _firm(tmp_path, TELEGRAM_CONFIG)
    resolve_provider().set(ws, "CADRE_TELEGRAM_TOKEN", "123:from-vault", "firm")
    # systemd-run starts the unit from the user manager's environment plus
    # whatever the dispatch sets.
    _start_like_a_timer_unit(monkeypatch)
    dispatched = _pulse_now_dispatch_env(ws, monkeypatch)
    for key, value in dispatched.items():
        monkeypatch.setenv(key, value)
    calls = _record_rail_calls(monkeypatch)

    out = _pulse(ws, monkeypatch, capsys)

    assert [c["url"] for c in calls] == [
        "https://api.telegram.org/bot123:from-vault/getMe"], \
        out.get("error_details")
    assert _escalations(ws, "preflight:notify-rail") == []
    assert out["errors"] == 0, out.get("error_details")
    # The token reached the pulse without passing through the scheduler.
    assert not set(dispatched) & set(NOTIFY_TOKEN_NAMES)


def test_pulse_now_still_reaches_a_slack_token_inline_in_mcp_json(
        tmp_path, monkeypatch, capsys):
    """A firm from before the vault carries its Slack token inline in .mcp.json.
    Pulse now used to forward it; the pulse now reads it itself."""
    ws = _firm(tmp_path, SLACK_CONFIG)
    launch = (f"FIRM_ID={FIRM} FIRM_WORKSPACE={ws} "
              "CADRE_SLACK_TOKEN=xoxb-inline-legacy exec firm-mcp")
    (ws / ".mcp.json").write_text(json.dumps({"mcpServers": {"firm": {
        "command": "bash", "args": ["-c", launch]}}}), encoding="utf-8")
    _start_like_a_timer_unit(monkeypatch)
    dispatched = _pulse_now_dispatch_env(ws, monkeypatch)
    for key, value in dispatched.items():
        monkeypatch.setenv(key, value)
    calls = _record_rail_calls(monkeypatch)

    out = _pulse(ws, monkeypatch, capsys)

    assert [c["auth"] for c in calls] == ["Bearer xoxb-inline-legacy"], \
        out.get("error_details")
    assert _escalations(ws, "preflight:notify-rail") == []
    assert out["errors"] == 0, out.get("error_details")
    assert not set(dispatched) & set(NOTIFY_TOKEN_NAMES)


def test_path_fix_text_says_what_every_pulse_does():
    from firm.pulse.preflight import _absent_reason, _fix_for

    fix = _fix_for(_absent_reason("gws-acct"))

    # main claimed the pulse dispatch carries a full PATH, which only Pulse now
    # did, and blamed a stale systemd --user env a timer pulse never had.
    assert "dispatch now carries a full PATH" not in fix
    assert "stale systemd" not in fix
    # It names the directories every pulse now leads its PATH with.
    assert "~/.local/bin" in fix and ".firm/bin" in fix
