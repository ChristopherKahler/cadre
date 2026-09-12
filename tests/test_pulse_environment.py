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
import shutil
import subprocess
from pathlib import Path

import pytest

from firm.core.db import connect
from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.secrets.provider import resolve_provider
from tests.platform_marks import host_cannot_exec_a_shebang_script

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


def _queue_work_needing(ws: Path, *tools: str) -> None:
    """Give MEM-001 a claimed Unit and a Contract whose loadout names *tools*."""
    conn = connect(ws / ".firm" / "firm.db")
    create(conn, "contract", {"id": "CON-001", "firm_id": FIRM, "name": "Lead",
                              "runtime_type": "claude_code",
                              "skill_loadout": {"cli": list(tools)}})
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


# ---------------------------------------------------------------------------
# Issue #111: a tool the hub found somewhere no pulse looked
# ---------------------------------------------------------------------------

# The operator's Node CLIs (gws, railway) live in nvm's bin, which only the hub's
# PATH carries. bin/<tool> is a symlink to a `#!/usr/bin/env node` script under
# lib/node_modules, and bin/node is the Node the hub runs it with: v22.11.0,
# while the node on the manager's PATH, /usr/bin/node, is v18.19.1. Measured on
# the operator's machine for issue #111.
NODE_TOOL = "railway"

# A WSL login shell's PATH ends with the Windows PATH, where npm keeps its shims
# (measured: C:/Users/Chris/AppData/Roaming/npm holds railway, railway.cmd and
# railway.ps1).
WINDOWS_NPM_SHIM = f"/mnt/c/Users/chris/AppData/Roaming/npm/{NODE_TOOL}"


def _stand_in_node(path: Path, says: str) -> None:
    """A `node` that prints which one it is and the script it was handed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "{says} ran $1"\n', encoding="utf-8")
    path.chmod(0o755)


def _nvm_install(root: Path, tool: str = NODE_TOOL) -> Path:
    """A Node install laid out the way nvm lays one out; returns its bin."""
    bin_dir = root / "nvm" / "versions" / "node" / "v22.11.0" / "bin"
    script = (bin_dir.parent / "lib" / "node_modules" / tool / "bin"
              / f"{tool}.js")
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    script.chmod(0o755)
    bin_dir.mkdir(parents=True)
    (bin_dir / tool).symlink_to(os.path.relpath(script, bin_dir))
    _stand_in_node(bin_dir / "node", "the node beside the tool")
    return bin_dir


def _firm_dir(tmp_path: Path) -> Path:
    """A firm folder under a firms root, the layout Train requires."""
    ws = tmp_path / "firms" / FIRM
    ws.mkdir(parents=True)
    return ws


def _survey_only_the_node_tool(monkeypatch, tool: str = NODE_TOOL) -> None:
    """Discovery's catalog cut to the tool under test, so neither the hub nor
    preflight probes a real CLI or base on this machine."""
    import firm.dashboard.discovery as discovery

    monkeypatch.setattr(discovery, "_CLI_PROBE",
                        ((tool, "the Node CLI under test", ("whoami",)),))
    monkeypatch.setattr(discovery, "_cli_cache", None)
    monkeypatch.setattr(discovery, "_base_ext_clis", lambda: [])
    monkeypatch.setattr(discovery, "base_survey", lambda: {
        "present": False, "extensions": [], "ext_capable": False})


def _in_the_hub(nvm_bin: Path, monkeypatch) -> None:
    """The hub's environment: its unit's PATH carries nvm's bin."""
    monkeypatch.setenv("PATH", os.pathsep.join([str(nvm_bin), SYSTEMD_USER_PATH]))


def _hub_equips(ws: Path, route: str) -> None:
    """The Board gives MEM-001 the tool, from Equip or from Train."""
    if route == "equip":
        from firm.dashboard.server import equip_member

        conn = connect(ws / ".firm" / "firm.db")
        try:
            equip_member(conn, ws, FIRM, "MEM-001",
                         {"kind": "cli", "name": NODE_TOOL})
            conn.commit()
        finally:
            conn.close()
        return
    from firm.dashboard import wiring

    plan = {"members": [{"name": "Lead", "skills": [], "commands": [], "mcp": [],
                         "cli": [NODE_TOOL], "knowledge": []}],
            "mcp": [], "gaps": []}
    result = wiring.commit(ws.parent, FIRM, plan)
    assert result["ok"], result.get("error")


def _start_like_a_timer_unit_with_a_system_node(tmp_path: Path,
                                                monkeypatch) -> None:
    """A timer unit's start, as a new process: the manager's PATH, which holds
    a node of its own, and no survey cached from the hub."""
    import firm.dashboard.discovery as discovery

    _start_like_a_timer_unit(monkeypatch)
    system_bin = tmp_path / "system-bin"
    if not (system_bin / "node").exists():
        _stand_in_node(system_bin / "node", "the node on the manager's PATH")
    monkeypatch.setenv("PATH",
                       os.pathsep.join([str(system_bin), SYSTEMD_USER_PATH]))
    monkeypatch.setattr(discovery, "_cli_cache", None)


def _enable_timer(ws: Path, tmp_path: Path, monkeypatch) -> str:
    """``firm heartbeat enable`` for the temp firm from the current environment,
    with its unit written under *tmp_path*. Returns the service unit's text."""
    import firm.cli.heartbeat as hb
    import firm.sched.systemd as sysd

    monkeypatch.setattr(sysd, "run_cmd", lambda argv, timeout=30: (0, ""))
    # The unit bakes the suite's unusable claude path, so copying the unit's
    # environment into a pulse leaves the no-claude fence exactly where it was.
    monkeypatch.setattr(hb, "resolve_claude_bin",
                        lambda: (os.environ["CADRE_CLAUDE_BIN"], "test"))
    unit_dir = tmp_path / "units"
    assert hb.run_enable(ws, FIRM, "15m", unit_dir=unit_dir) == 0
    return (unit_dir / f"cadre-heartbeat-{FIRM}.service").read_text(
        encoding="utf-8")


def _escalation_text(ws: Path, dedupe_key: str) -> list[tuple[str, str]]:
    conn = connect(ws / ".firm" / "firm.db")
    try:
        return [(r["title"], r["body"]) for r in conn.execute(
            "SELECT title, body FROM escalation WHERE dedupe_key = ?",
            (dedupe_key,))]
    finally:
        conn.close()


@host_cannot_exec_a_shebang_script
@pytest.mark.parametrize("route", ["equip", "train"])
def test_a_tool_the_hub_found_off_the_floor_runs_in_a_timer_pulse(
        route, tmp_path, monkeypatch, capsys):
    """Done when 1 to 3: the timer pulse runs the Member, the tool runs under
    the node beside it, and the PATH is the one Pulse now dispatches with."""
    ws = _firm(_firm_dir(tmp_path))
    _queue_work_needing(ws)
    nvm_bin = _nvm_install(tmp_path)
    _survey_only_the_node_tool(monkeypatch)
    _in_the_hub(nvm_bin, monkeypatch)
    _hub_equips(ws, route)

    _start_like_a_timer_unit_with_a_system_node(tmp_path, monkeypatch)
    pulse_now_path = _pulse_now_dispatch_env(ws, monkeypatch)["PATH"]
    ran: list[tuple[str, str, str]] = []

    def runner(conn, member):
        # A Member run gets a copy of the pulse's environment (firm.pulse.spawn).
        tool = subprocess.run([NODE_TOOL, "whoami"], env=dict(os.environ),
                              capture_output=True, text=True, timeout=30)
        ran.append((member["id"], tool.stdout.strip(), os.environ["PATH"]))
        return {"status": "completed"}

    out = _pulse(ws, monkeypatch, capsys, runner=runner)

    assert _escalation_text(ws, f"preflight:{NODE_TOOL}") == []
    assert [r[0] for r in ran] == ["MEM-001"], out.get("skip_reasons")
    _, tool_said, member_path = ran[0]
    assert tool_said.startswith("the node beside the tool ran "), tool_said
    assert member_path == pulse_now_path
    assert str(nvm_bin) in member_path.split(os.pathsep)


@host_cannot_exec_a_shebang_script
def test_a_tool_gone_from_where_the_hub_found_it_holds_its_member_back(
        tmp_path, monkeypatch, capsys):
    """Done when 4: nvm moved on, so the recorded place is empty. The Member is
    held back and the Board is told the path, so it can equip the tool again."""
    ws = _firm(_firm_dir(tmp_path))
    _queue_work_needing(ws)
    nvm_bin = _nvm_install(tmp_path)
    _survey_only_the_node_tool(monkeypatch)
    _in_the_hub(nvm_bin, monkeypatch)
    _hub_equips(ws, "equip")
    nvm_bin.parent.rename(nvm_bin.parent.with_name("v22.12.0"))

    _start_like_a_timer_unit_with_a_system_node(tmp_path, monkeypatch)
    ran: list[str] = []

    def runner(conn, member):
        ran.append(member["id"])
        return {"status": "completed"}

    _pulse(ws, monkeypatch, capsys, runner=runner)

    assert ran == []
    [(title, body)] = _escalation_text(ws, f"preflight:{NODE_TOOL}")
    missing = str(nvm_bin / NODE_TOOL)
    assert missing in title and missing in body
    assert "equip it again" in body


@host_cannot_exec_a_shebang_script
def test_a_tool_equipped_before_the_hub_recorded_it_needs_one_equip_again(
        tmp_path, monkeypatch, capsys):
    """Done when 5, for a timer that already exists: a loadout written before
    this fix names the tool and no place. The pulse tells the Board the one
    thing to do, and after the Board does it in the hub, the timer pulse runs
    the Member."""
    from firm.dashboard.server import equip_member, unequip_member

    ws = _firm(_firm_dir(tmp_path))
    _queue_work_needing(ws, NODE_TOOL)
    nvm_bin = _nvm_install(tmp_path)
    _survey_only_the_node_tool(monkeypatch)
    ran: list[str] = []

    def runner(conn, member):
        ran.append(member["id"])
        return {"status": "completed"}

    _start_like_a_timer_unit_with_a_system_node(tmp_path, monkeypatch)
    _pulse(ws, monkeypatch, capsys, runner=runner)

    assert ran == []
    [(_, body)] = _escalation_text(ws, f"preflight:{NODE_TOOL}")
    assert "remove it from the Member and equip it again in the hub" in body

    _in_the_hub(nvm_bin, monkeypatch)
    conn = connect(ws / ".firm" / "firm.db")
    try:
        unequip_member(conn, FIRM, "MEM-001", {"kind": "cli", "name": NODE_TOOL})
        equip_member(conn, ws, FIRM, "MEM-001", {"kind": "cli", "name": NODE_TOOL})
        conn.commit()
    finally:
        conn.close()

    _start_like_a_timer_unit_with_a_system_node(tmp_path, monkeypatch)
    out = _pulse(ws, monkeypatch, capsys, runner=runner)

    assert ran == ["MEM-001"], out.get("skip_reasons")


@host_cannot_exec_a_shebang_script
def test_enabling_a_timer_records_tools_equipped_before_the_hub_recorded_them(
        tmp_path, monkeypatch, capsys):
    """Done when 5, for a firm that gets its timer now: a firm needs a recorded
    place only once a timer pulses it, and every timer is made by heartbeat
    enable (the hub's Pulse switch calls it too). So enable records the tools a
    loadout carries with no place yet, from its own PATH, and the Board is
    never handed an equip-again step."""
    tool = "gws"
    ws = _firm(_firm_dir(tmp_path))
    _queue_work_needing(ws, tool)   # the loadout from before #111: gws, no place
    nvm_bin = _nvm_install(tmp_path, tool)
    _survey_only_the_node_tool(monkeypatch, tool)

    # The Board switches the pulse on in the hub, whose PATH carries nvm's bin.
    _in_the_hub(nvm_bin, monkeypatch)
    service = _enable_timer(ws, tmp_path, monkeypatch)
    capsys.readouterr()
    assert "PATH=" not in service and str(nvm_bin) not in service

    # The timer starts the pulse from the unit's environment and the manager's
    # bare PATH.
    _start_like_a_timer_unit_with_a_system_node(tmp_path, monkeypatch)
    for line in service.splitlines():
        if line.startswith('Environment="') and line.endswith('"'):
            key, _, value = line[len('Environment="'):-1].partition("=")
            monkeypatch.setenv(key, value)
    ran: list[tuple[str, str]] = []

    def runner(conn, member):
        # A Member run gets a copy of the pulse's environment (firm.pulse.spawn).
        run = subprocess.run([tool, "--version"], env=dict(os.environ),
                             capture_output=True, text=True, timeout=30)
        ran.append((member["id"], run.stdout.strip()))
        return {"status": "completed"}

    out = _pulse(ws, monkeypatch, capsys, runner=runner)

    assert _escalation_text(ws, f"preflight:{tool}") == []
    assert [r[0] for r in ran] == ["MEM-001"], out.get("skip_reasons")
    assert ran[0][1].startswith("the node beside the tool ran "), ran[0][1]


@pytest.mark.parametrize("route", ["equip", "train", "enable"])
def test_a_tool_found_only_on_a_windows_drive_is_never_recorded(
        route, tmp_path, monkeypatch, capsys):
    """Recording the Windows npm shim a WSL login shell resolves would put a
    Windows folder ahead of /usr/bin on every timer pulse. So Equip, Train and
    enable record nothing for it, and the Member is held back as before."""
    from firm.pulse.preflight import firm_cli_paths

    ws = _firm(_firm_dir(tmp_path))
    _queue_work_needing(ws, *([NODE_TOOL] if route == "enable" else []))
    _survey_only_the_node_tool(monkeypatch)
    which = shutil.which

    with monkeypatch.context() as login_shell:
        login_shell.setattr(shutil, "which", lambda name, *a, **k: (
            WINDOWS_NPM_SHIM if name == NODE_TOOL else which(name, *a, **k)))
        if route == "enable":
            _enable_timer(ws, tmp_path, login_shell)
            capsys.readouterr()
        else:
            _hub_equips(ws, route)

    conn = connect(ws / ".firm" / "firm.db")
    try:
        assert firm_cli_paths(conn, FIRM) == {}
    finally:
        conn.close()

    _start_like_a_timer_unit_with_a_system_node(tmp_path, monkeypatch)
    assert "/mnt/" not in _pulse_now_dispatch_env(ws, monkeypatch)["PATH"]
    ran: list[str] = []

    def runner(conn, member):
        ran.append(member["id"])
        return {"status": "completed"}

    _pulse(ws, monkeypatch, capsys, runner=runner)

    assert ran == []
    assert len(_escalation_text(ws, f"preflight:{NODE_TOOL}")) == 1


def test_a_binary_built_for_another_platform_is_never_recorded(tmp_path):
    """The foreign-binary rule cadre already applies to base
    (firm.sysconfig.binaries): a binary this host's kernel does not run is not
    recorded, wherever it sits. A script is no image format and is recorded."""
    from firm.pulse.preflight import recordable_tool_path
    from firm.sysconfig.binaries import native_image_format

    foreign = tmp_path / "foreign-tool"
    foreign.write_bytes((b"\x7fELF" if native_image_format() == "pe" else b"MZ")
                        + b"\x00" * 60)
    script = tmp_path / "script-tool"
    script.write_text("#!/usr/bin/env node\n", encoding="utf-8")

    assert recordable_tool_path(str(foreign)) is None
    assert recordable_tool_path(str(script)) == os.path.abspath(script)
