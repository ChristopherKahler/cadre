"""``firm heartbeat`` — autonomous pulse cadence via the host scheduler.

enable/disable/status for a per-firm timer that fires ``firm pulse`` on an
interval. The timer is only a metronome: business hours, per-member frequency,
budget preflight, claimed-unit availability, and the pulse lock still decide
whether anything spawns — a tick that finds nothing due is a near-free no-op.

The mechanism is the platform scheduler behind ``firm.sched`` — systemd user
timers on Linux/WSL2, launchd LaunchAgents on macOS, Task Scheduler on
Windows. The claude binary and the shared-database settings are captured at
enable time from the process env plus the workspace ``.env`` — re-run
``enable`` after changing them. Notify credentials are never written into a
unit: the pulse reads them itself when it starts (``firm.pulse.environment``),
from the firm vault and then the workspace ``.env``, and adds the full PATH —
so rotating a token needs no re-enable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from firm.core.db import connect, db_is_remote, get_db_path, resolve_firm_id
from firm.core.proc import run_utf8
from firm.pulse.environment import read_env_file
from firm.pulse.spawn import resolve_claude_bin
from firm.sched import resolve_scheduler
from firm.sched.base import SchedulerError, interval_to_seconds
from firm.services import pulse_ledger

_UNIT_PREFIX = "cadre-heartbeat-"
_INTERVAL_RE = re.compile(r"^\d+(s|m|min|h|d)$")
# Notify credentials (CADRE_SLACK_TOKEN, CADRE_TELEGRAM_TOKEN,
# CADRE_NOTIFY_WEBHOOK) are deliberately NOT captured (#107). A captured one
# sat in plain text in the unit -- including a token the hub had loaded from a
# firm vault into its own environment -- and, because a set variable wins over
# the vault, it went on overriding a token the Board later rotated there. The
# pulse reads them itself when it starts, from the vault and then this same
# workspace .env (firm.pulse.environment.notify_token).
_CAPTURED_ENV_KEYS = (
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


def capture_env(workspace: Path, firm_id: str, claude_bin: str) -> dict[str, str]:
    """Environment to bake into the service unit, captured at enable time.

    Process env wins over the workspace .env so an operator export can
    override a stale file value. Never a notify credential — see
    ``_CAPTURED_ENV_KEYS``.
    """
    file_env = read_env_file(workspace)
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
        proc = run_utf8(
            ["systemctl", "--user", *args],
            capture_output=True,
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

    # A timer pulse starts from the scheduler's bare PATH and finds a loadout
    # tool outside ~/.local/bin and .firm/bin only through the path recorded
    # when the tool was equipped (#111). A tool equipped before those were
    # recorded has none, so record it now from this process's PATH -- the hub's
    # when the Board switches the pulse on there -- before the timer can fire.
    # Nothing goes into the unit: the pulse reads the record from the database.
    try:
        from firm.pulse.preflight import record_cli_paths
        conn = connect(get_db_path(workspace))
        try:
            tool_paths_recorded = record_cli_paths(conn, firm_id)
        finally:
            conn.close()
    except Exception:
        tool_paths_recorded = {}   # the timer still starts; preflight reports any miss

    sched = _sched(unit_dir)
    stem = f"{_UNIT_PREFIX}{firm_id}"
    env = capture_env(workspace, firm_id, claude_bin)
    try:
        installed = sched.install_timer(
            stem,
            description=f"Cadre heartbeat pulse — firm {firm_id}",
            workdir=workspace,
            env=env,
            # --source heartbeat is what makes a timer pulse tell itself
            # apart from a Board one in the ledger (#128 D3). It goes in the
            # INSTALLED argv, so doctor can read a timer's label out of the
            # unit without running a pulse -- and so a timer installed before
            # this flag existed keeps recording "unset", which is the true
            # answer for it, rather than being guessed at.
            argv=[sys.executable, "-m", "firm", "pulse",
                  "--workspace", str(workspace), "--firm-id", firm_id,
                  "--source", pulse_ledger.HEARTBEAT],
            interval=interval,
        )
    except SchedulerError as exc:
        _emit({"ok": False, "reason": str(exc)})
        return 1

    # firm.pulse_interval is the single source of truth for cadence (fork 005) —
    # the hub reads it to tell "not operational" from "healthy and idle",
    # two states that looked identical while the whole portfolio sat in the
    # first one. The timer is the mechanism; the row is the record. A DB that
    # can't take the write (mid-init, unmigrated) doesn't undo a timer that
    # is already running — but the miss is reported, never swallowed.
    #
    # Never firm.schedule: that column is the firm's business hours, and an
    # interval written over them reads as "always open" to the pulse's
    # business-hours gate (#134). The payload key keeps its old name.
    schedule_recorded = True
    try:
        from firm.core import repo
        conn = connect(get_db_path(workspace))
        try:
            repo.update(conn, "firm", firm_id, {"pulse_interval": interval})
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
        "tool_paths_recorded": tool_paths_recorded,
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
    # removal, so firm.pulse_interval can be cleared after the units are gone.
    # Never firm.schedule, which holds the firm's business hours (#134).
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
                    repo.update(conn, "firm", firm_id, {"pulse_interval": None})
                    schedule_recorded = True
                finally:
                    conn.close()
        except Exception:
            pass

    # THE TASK IS GONE; THE FIRM'S ROWS ARE NOT (#141, condition C1). Ending
    # the task ends the pulse tree, and leaves `pulse_lock` held by a dead
    # holder for up to its ten-minute TTL and the Member's `member_run` marked
    # running for good -- the reaper that would close it only runs inside a
    # pulse, and after this verb no pulse comes.
    #
    # Two lines and an import, on purpose: the logic lives in
    # `firm.pulse.cleanup` so that this file, which two other open PRs also
    # change, keeps the smallest possible surface.
    cleanup: dict = {"lock": "not-attempted",
                     "reason": "the unit did not record a workspace, so there "
                               "is no firm database to clean up"}
    if ws_str:
        from firm.pulse.cleanup import release_and_finalize

        # The bounded wait is because the task has only just been ended: a
        # contained tree is on its way out but may not be gone yet, and reading
        # "still alive" too early would leave a lock that was about to free
        # itself. It is bounded because the other case -- a host where
        # containment failed -- is a pulse that is not going anywhere, and a
        # verb that waited for that would hang.
        cleanup = release_and_finalize(Path(ws_str), firm_id,
                                       by="heartbeat disable",
                                       wait_seconds=5.0)

    _emit({"ok": True, "firm_id": firm_id, "removed": f"{stem}.timer",
           "schedule_recorded": schedule_recorded,
           "cleanup": cleanup})
    return 0


def _service_workspace(service_path: Path) -> str | None:
    for line in service_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("WorkingDirectory="):
            return line.partition("=")[2]
    return None


def _service_python(stem: str, unit_dir: Path | None) -> str | None:
    """The interpreter baked into a unit's ExecStart, read from the unit file.

    Worth reporting because a timer holds a PATH, and a reinstall changes the
    bytes behind that path leaving no trace in the timer. A unit still pointing
    at an interpreter that no longer has Cadre in it, or has a different Cadre
    in it, looks perfectly healthy from every other angle.

    Read here rather than added to the scheduler backends, which are godwit's
    (#113, #119). Absent is returned as None and rendered as "not recorded",
    matching the scheduler contract: keys the platform cannot answer are
    absent, never guessed.
    """
    base = unit_dir or default_unit_dir()
    for candidate in (base / f"{stem}.service", base / f"{stem}.plist"):
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("ExecStart="):
                first = line.partition("=")[2].strip().split()
                return first[0] if first else None
    return None


def _last_pulse(workspace: Path, firm_id: str) -> tuple[str | None, str | None]:
    """When this firm last pulsed, read from the ledger; or why it is unknown.

    NOT the mtime of .firm/last-pulse.json, which is what this used to be. That
    file is written by exactly one launcher, the hub's _fire_pulse, so a firm
    whose pulses come from its own timer -- the firms this verb exists to
    report on -- had no file and read as a firm that had never pulsed. The
    ledger is written by every pulse (#128 D3).

    Returns (started_at, None) when the ledger answered, (None, None) when it
    answered and the firm has no pulses, and (None, reason) when it could not
    be read at all.
    """
    db_path = get_db_path(workspace)
    if not db_is_remote() and not db_path.exists():
        return None, f"no firm database at {db_path}"

    def work() -> str | None:
        conn = connect(db_path)
        try:
            return pulse_ledger.last_started_at(conn, firm_id)
        finally:
            conn.close()

    started, reason = pulse_ledger.best_effort(work)
    return started, reason


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
            started, unavailable = _last_pulse(Path(workspace), firm_id)
            if started is not None:
                entry["last_pulse"] = started
            elif unavailable is not None:
                # THREE STATES, THREE KEYS (law 7, law 48). A ledger that
                # cannot be read is not a firm that has never pulsed, and a
                # reader that saw the same absence for both would go looking
                # for a dead timer when the real answer is a missing
                # migration.
                entry["last_pulse_unavailable"] = unavailable
        for k in ("next_fire", "last_fire"):
            if st.get(k):
                entry[k] = st[k]
        # CONDITION C3 IS ABOUT THIS SURFACE, not the layer below it. The
        # scheduler answers whether the pulse tree is contained; this verb is
        # where an operator reads it, and until now the answer was produced and
        # then dropped here -- every leg that checked it called `status()`
        # directly, one layer down, so all of them passed over the gap
        # (avocet, #146 FINDING 3).
        #
        # `in st`, NEVER `st.get(k)`: `contained` is False in exactly the case
        # this exists to report, and a truthiness test would drop the one
        # answer that matters while keeping the harmless one.
        #
        # Copied only when the scheduler answered. systemd and launchd have no
        # job objects and say nothing here; inventing `contained: null` for
        # them would be a claim about a mechanism those hosts never had.
        for k in ("contained", "containment_reason", "containment_flags"):
            if k in st:
                entry[k] = st[k]
        entry["interpreter"] = _service_python(stem, unit_dir)
        entries.append(entry)

    # The identity block is the SAME dict cadre identity prints and
    # cadre doctor --install checks. One producer, several consumers: three
    # readers that each compute their own answer agree until the day one of
    # them is edited, and then nobody notices.
    from firm.identity import installed_identity

    _emit({"ok": True, "cadre": installed_identity(), "heartbeats": entries})
    return 0
