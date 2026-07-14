"""``firm heartbeat`` — autonomous pulse cadence via systemd user timers.

enable/disable/status for a per-firm timer that fires ``firm pulse`` on an
interval. The timer is only a metronome: business hours, per-member frequency,
budget preflight, claimed-unit availability, and the pulse lock still decide
whether anything spawns — a tick that finds nothing due is a near-free no-op.

Unit files land in ``~/.config/systemd/user/`` as
``cadre-heartbeat-<firm>.{service,timer}``. Runtime environment (claude
binary, notify tokens) is captured at enable time from the process env plus
the workspace ``.env`` — re-run ``enable`` after rotating tokens. Requires a
systemd user session, the same dependency the dashboard's detached pulse
(``systemd-run --user``) already carries.
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


def validate_interval(interval: str) -> str:
    """Return *interval* if it is a simple systemd span (30m, 1h, 90s, 2d)."""
    if not _INTERVAL_RE.match(interval):
        raise ValueError(
            f"invalid interval {interval!r} — use <number><unit> with unit "
            "one of s/m/min/h/d, e.g. 30m or 1h"
        )
    return interval


def _read_env_file(workspace: Path) -> dict[str, str]:
    """KEY=VALUE pairs from the workspace .env — for embedding, not for
    mutating this process (contrast dashboard._load_firm_env)."""
    env_path = workspace / ".env"
    out: dict[str, str] = {}
    if not env_path.exists():
        return out
    try:
        for line in env_path.read_text().splitlines():
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

    unit_dir = unit_dir or default_unit_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{_UNIT_PREFIX}{firm_id}"
    env = capture_env(workspace, firm_id, claude_bin)
    (unit_dir / f"{stem}.service").write_text(
        render_service(workspace, firm_id, sys.executable, env)
    )
    (unit_dir / f"{stem}.timer").write_text(render_timer(firm_id, interval))

    for step in (["daemon-reload"], ["enable", "--now", f"{stem}.timer"]):
        rc, out = _systemctl(*step)
        if rc != 0:
            _emit({"ok": False, "reason": f"systemctl {' '.join(step)}: {out}"})
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
        "timer": f"{stem}.timer",
        "unit_dir": str(unit_dir),
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
    unit_dir = unit_dir or default_unit_dir()
    stem = f"{_UNIT_PREFIX}{firm_id}"
    if not (unit_dir / f"{stem}.timer").exists():
        _emit({"ok": False, "reason": f"no heartbeat installed for firm {firm_id!r}"})
        return 1

    # The workspace path lives in the service file — read it BEFORE the
    # unlink, so firm.schedule can be nulled after the units are gone.
    ws_str = _service_workspace(unit_dir / f"{stem}.service") \
        if (unit_dir / f"{stem}.service").exists() else None

    rc, out = _systemctl("disable", "--now", f"{stem}.timer")
    if rc != 0:
        _emit({"ok": False, "reason": f"systemctl disable: {out}"})
        return 1
    for suffix in (".timer", ".service"):
        (unit_dir / f"{stem}{suffix}").unlink(missing_ok=True)
    _systemctl("daemon-reload")
    # A unit that ever failed stays in systemd's runtime as `not-found
    # failed` after its file is removed — permanent ghost noise that reads
    # as a broken firm in every health sweep (fork 005, chief-of-staff).
    _systemctl("reset-failed", f"{stem}.service", f"{stem}.timer")

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
    for line in service_path.read_text().splitlines():
        if line.startswith("WorkingDirectory="):
            return line.partition("=")[2]
    return None


def run_status(*, unit_dir: Path | None = None) -> int:
    unit_dir = unit_dir or default_unit_dir()
    timers = sorted(unit_dir.glob(f"{_UNIT_PREFIX}*.timer"))
    entries = []
    for timer_path in timers:
        stem = timer_path.stem
        firm_id = stem[len(_UNIT_PREFIX):]
        rc, active = _systemctl("is-active", f"{stem}.timer")
        entry: dict = {"firm_id": firm_id, "timer": f"{stem}.timer", "state": active}

        service_path = unit_dir / f"{stem}.service"
        workspace = (
            _service_workspace(service_path) if service_path.exists() else None
        )
        if workspace:
            entry["workspace"] = workspace
            last_pulse = Path(workspace) / ".firm" / "last-pulse.json"
            if last_pulse.exists():
                entry["last_pulse"] = int(last_pulse.stat().st_mtime)

        rc, out = _systemctl(
            "show", f"{stem}.timer",
            "--property=NextElapseUSecRealtime,LastTriggerUSec",
        )
        if rc == 0:
            for line in out.splitlines():
                key, _, val = line.partition("=")
                if key == "NextElapseUSecRealtime" and val:
                    entry["next_fire"] = val
                if key == "LastTriggerUSec" and val:
                    entry["last_fire"] = val
        entries.append(entry)

    _emit({"ok": True, "heartbeats": entries})
    return 0
