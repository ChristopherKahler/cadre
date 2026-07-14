"""``firm heartbeat`` — autonomous pulse cadence via the host scheduler.

enable/disable/status for a per-firm timer that fires ``firm pulse`` on an
interval. The timer is only a metronome: business hours, per-member frequency,
budget preflight, claimed-unit availability, and the pulse lock still decide
whether anything spawns — a tick that finds nothing due is a near-free no-op.

The mechanism is the platform scheduler behind ``firm.sched`` — systemd user
timers on Linux/WSL2, launchd LaunchAgents on macOS, Task Scheduler on
Windows. Runtime environment (claude binary, notify tokens) is captured at
enable time from the process env plus the workspace ``.env`` — re-run
``enable`` after rotating tokens.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from firm.core.db import connect, get_db_path, resolve_firm_id
from firm.pulse.spawn import resolve_claude_bin
from firm.sched import resolve_scheduler
from firm.sched.base import SchedulerError, interval_to_seconds

_UNIT_PREFIX = "cadre-heartbeat-"
_INTERVAL_RE = re.compile(r"^\d+(s|m|min|h|d)$")
_CAPTURED_ENV_KEYS = (
    "CADRE_SLACK_TOKEN",
    "CADRE_TELEGRAM_TOKEN",
    "CADRE_NOTIFY_WEBHOOK",
    "CADRE_DB_URL",
    "CADRE_DB_TOKEN",
)


def default_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _sched(unit_dir: Path | None = None):
    """The platform scheduler; an explicit *unit_dir* pins the systemd
    backend (the tests' knob, meaningless on other platforms)."""
    if unit_dir is not None:
        from firm.sched.systemd import SystemdScheduler
        return SystemdScheduler(unit_dir=unit_dir)
    return resolve_scheduler()


def validate_interval(interval: str) -> str:
    """Return *interval* if it is a simple span (30m, 1h, 90s, 2d)."""
    interval_to_seconds(interval)   # raises ValueError with the usage line
    return interval


def _read_env_file(workspace: Path) -> dict[str, str]:
    """KEY=VALUE pairs from the workspace .env — for embedding, not for
    mutating this process (contrast dashboard._load_firm_env)."""
    env_path = workspace / ".env"
    out: dict[str, str] = {}
    if not env_path.exists():
        return out
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def capture_env(workspace: Path, firm_id: str, claude_bin: str) -> dict[str, str]:
    """Environment to bake into the service unit, captured at enable time.

    Process env wins over the workspace .env so an operator export can
    override a stale file value.
    """
    file_env = _read_env_file(workspace)
    env = {"FIRM_ID": firm_id, "CADRE_CLAUDE_BIN": claude_bin}
    for key in _CAPTURED_ENV_KEYS:
        val = os.environ.get(key) or file_env.get(key)
        if val:
            env[key] = val
    return env


def render_service(
    workspace: Path,
    firm_id: str,
    python_bin: str,
    env: dict[str, str],
) -> str:
    env_lines = "\n".join(
        f'Environment="{k}={v}"' for k, v in sorted(env.items())
    )
    return f"""[Unit]
Description=Cadre heartbeat pulse — firm {firm_id}

[Service]
Type=oneshot
WorkingDirectory={workspace}
{env_lines}
ExecStart={python_bin} -m firm pulse --workspace {workspace} --firm-id {firm_id}
"""


def render_timer(firm_id: str, interval: str) -> str:
    return f"""[Unit]
Description=Cadre heartbeat timer — firm {firm_id} (every {interval})

[Timer]
OnBootSec=2m
OnUnitActiveSec={interval}
RandomizedDelaySec=30
Persistent=false

[Install]
WantedBy=timers.target
"""


def _systemctl(*args: str) -> tuple[int, str]:
    """Run ``systemctl --user`` with *args*. Returns (rc, combined output)."""
    try:
        proc = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return 1, f"systemctl unavailable: {exc}"
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def run_enable(
    workspace: Path,
    firm_id: str | None,
    interval: str,
    *,
    unit_dir: Path | None = None,
) -> int:
    workspace = workspace.expanduser().resolve()
    if not get_db_path(workspace).exists():
        _emit({"ok": False, "reason": "db-not-found", "workspace": str(workspace)})
        return 1
    if not firm_id:
        conn = connect(get_db_path(workspace))
        try:
            firm_id = resolve_firm_id(conn)
        except ValueError as exc:
            _emit({"ok": False, "reason": str(exc)})
            return 1
        finally:
            conn.close()
    try:
        interval = validate_interval(interval)
    except ValueError as exc:
        _emit({"ok": False, "reason": str(exc)})
        return 1
    claude_bin, detail = resolve_claude_bin()
    if not claude_bin:
        _emit({"ok": False, "reason": f"claude runtime not wired: {detail}"})
        return 1

    sched = _sched(unit_dir)
    stem = f"{_UNIT_PREFIX}{firm_id}"
    env = capture_env(workspace, firm_id, claude_bin)
    try:
        installed = sched.install_timer(
            stem,
            description=f"Cadre heartbeat pulse — firm {firm_id}",
            workdir=workspace,
            env=env,
            argv=[sys.executable, "-m", "firm", "pulse",
                  "--workspace", str(workspace), "--firm-id", firm_id],
            interval=interval,
        )
    except SchedulerError as exc:
        _emit({"ok": False, "reason": str(exc)})
        return 1

    # firm.schedule is the single source of truth for cadence (fork 005) —
    # the hub reads it to tell "not operational" from "healthy and idle",
    # two states that looked identical while the whole portfolio sat in the
    # first one. The timer is the mechanism; the row is the record. A DB that
    # can't take the write (mid-init, unmigrated) doesn't undo a timer that
    # is already running — but the miss is reported, never swallowed.
    schedule_recorded = True
    try:
        from firm.core import repo
        conn = connect(get_db_path(workspace))
        try:
            repo.update(conn, "firm", firm_id, {"schedule": interval})
        finally:
            conn.close()
    except Exception:
        schedule_recorded = False

    _emit({
        "schedule_recorded": schedule_recorded,
        "ok": True,
        "firm_id": firm_id,
        "interval": interval,
        "timer": installed.get("unit", stem),
        "scheduler": sched.name,
        "unit_dir": installed.get("unit_dir", ""),
        "claude_bin": claude_bin,
        "env_keys": sorted(env),
    })
    return 0


def run_disable(firm_id: str | None = None, *, unit_dir: Path | None = None) -> int:
    if not firm_id:
        db = get_db_path(Path.cwd())
        if db.exists():
            conn = connect(db)
            try:
                firm_id = resolve_firm_id(conn)
            except ValueError:
                firm_id = None
            finally:
                conn.close()
    if not firm_id:
        _emit({"ok": False, "reason": "no firm id — pass --firm-id or run "
                                      "from a firm workspace"})
        return 1
    sched = _sched(unit_dir)
    stem = f"{_UNIT_PREFIX}{firm_id}"
    st = sched.status(stem)
    if not st.get("installed"):
        _emit({"ok": False, "reason": f"no heartbeat installed for firm {firm_id!r}"})
        return 1

    # The workspace path lives in the installed unit — read it BEFORE the
    # removal, so firm.schedule can be nulled after the units are gone.
    ws_str = st.get("workdir")

    sched.remove(stem)

    schedule_recorded = False
    if ws_str:
        try:
            from firm.core import repo
            db = get_db_path(Path(ws_str))
            if db.exists():
                conn = connect(db)
                try:
                    repo.update(conn, "firm", firm_id, {"schedule": None})
                    schedule_recorded = True
                finally:
                    conn.close()
        except Exception:
            pass

    _emit({"ok": True, "firm_id": firm_id, "removed": f"{stem}.timer",
           "schedule_recorded": schedule_recorded})
    return 0


def _service_workspace(service_path: Path) -> str | None:
    for line in service_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("WorkingDirectory="):
            return line.partition("=")[2]
    return None


def run_status(*, unit_dir: Path | None = None) -> int:
    sched = _sched(unit_dir)
    entries = []
    for stem in sched.list_installed(_UNIT_PREFIX):
        firm_id = stem[len(_UNIT_PREFIX):]
        st = sched.status(stem)
        entry: dict = {"firm_id": firm_id, "timer": stem,
                       "state": st.get("state", "unknown"),
                       "scheduler": sched.name}
        workspace = st.get("workdir")
        if workspace:
            entry["workspace"] = workspace
            last_pulse = Path(workspace) / ".firm" / "last-pulse.json"
            if last_pulse.exists():
                entry["last_pulse"] = int(last_pulse.stat().st_mtime)
        for k in ("next_fire", "last_fire"):
            if st.get(k):
                entry[k] = st[k]
        entries.append(entry)

    _emit({"ok": True, "heartbeats": entries})
    return 0
