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


def run_disable(firm_id: str | None = None, *,
                workspace: Path | None = None,
                unit_dir: Path | None = None) -> int:
    """Stop and remove a firm's heartbeat timer.

    *workspace* names the firm the way ``enable`` does (#131): the three verbs
    manage one timer and disagreed about how you say which one.
    """
    named = _firm_from(workspace, firm_id)
    if isinstance(named, int):
        return named
    firm_id = named
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

    # THE BACKEND CAN RAISE, AND ON WINDOWS IT ORDINARILY DOES (avocet's G2
    # finding 1). `SystemdScheduler.remove` unlinks the unit files, and a file
    # another process holds open cannot be deleted on Windows -- an editor, a
    # backup agent, an antivirus scanner, a sync client. On Linux the same call
    # raises on a read-only mount, an immutable attribute or directory
    # permissions. Bare, it escaped through `main()`, which has no catch-all:
    # traceback on stderr, STDOUT EMPTY, exit 1. That broke the contract this
    # file exists to hold (one JSON object on every exit of every verb) and
    # never reached condition 2 below, because the raise happens INSIDE
    # `remove()`, before the re-query.
    #
    # THE BOUNDARY, stated where the code is: `remove()` unlinks `.timer` then
    # `.service`, so a raise part-way through can leave a HALF-REMOVED unit.
    # This verb reports the raise and repairs nothing. Repairing a half-removed
    # unit belongs to the backend or to the doctor, not to a verb whose one job
    # is to say what happened.
    #
    # It takes condition 2's branch for the same reason condition 2 exists: the
    # state is partial or unknown, so clearing `firm.pulse_interval` or
    # finalizing the runs would be a claim this verb cannot support. And it does
    # NOT re-query here -- `status` is the verb for asking, and a second
    # unguarded backend call inside a handler is this same defect one line over.
    try:
        answer = sched.remove(stem)
    except Exception as exc:
        _emit({"ok": False, "reason": "remove-raised", "firm_id": firm_id,
               "error": {"type": type(exc).__name__, "message": str(exc)},
               "schedule_recorded": False,
               "cleanup": {"lock": "not-attempted",
                           "reason": "the removal raised, so what the timer is "
                                     "doing is unknown and the lock may still "
                                     "be its pulse's"}})
        return 1

    # PROVED, NOT TRUSTED (#131). This used to be `sched.remove(stem)` with the
    # answer discarded and `ok: true` printed regardless, so a removal that
    # failed reported success and the timer kept firing.
    #
    # The proof is the scheduler's own query, not `remove()`'s list. On Windows
    # that list collects leftover files -- the stub, the spec, the log, a
    # pre-#119 .cmd, the containment file -- beside the task name, so a
    # non-empty list can mean "I deleted a stale log and nothing else". On
    # systemd and launchd it names unit FILES that existed, which is a
    # filesystem fact rather than a scheduler one.
    #
    # WHAT `installed` MEANS DIFFERS BY BACKEND, and this is the honest
    # sentence for it (osprey's Q5): on Windows it is `schtasks /Query`, a real
    # question put to the scheduler; on systemd and launchd it is whether the
    # unit definition still exists after reload, which is the strongest
    # question those platforms answer. Teaching them a real query is not this
    # change.
    if sched.status(stem).get("installed"):
        # NOTHING ELSE CHANGES (osprey's condition 2). The timer still fires,
        # so `firm.pulse_interval` is still true and the lock is still that
        # pulse's: clearing the interval would leave the row lying about a
        # cadence that is still running, and finalizing the runs would close
        # rows belonging to a pulse nobody stopped.
        _emit({"ok": False, "reason": "not-removed", "firm_id": firm_id,
               "scheduler_removed": answer,
               "schedule_recorded": False,
               "cleanup": {"lock": "not-attempted",
                           "reason": "the timer is still installed, so the "
                                     "lock is still its pulse's"}})
        return 1

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

    # `removed` is the unit this verb set out to remove and keeps its meaning
    # for existing readers. `scheduler_removed` is what the backend itself said
    # it removed -- reported for the record, never what the exit code rests on.
    # Two different claims, so two keys.
    _emit({"ok": True, "firm_id": firm_id, "removed": f"{stem}.timer",
           "scheduler_removed": answer,
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


def _firm_from(workspace: Path | None, firm_id: str | None):
    """The firm these verbs are being pointed at, or an exit code with its
    JSON already printed.

    A NAMED workspace with no database is a failure, not a quiet fall-through
    to the current directory (#131, osprey's condition 3): an operator who
    typed a path meant that path, and a verb that silently acted on a
    different firm is the defect this whole issue is about. The words are
    ``run_pulse``'s for the same missing database, so the verbs and the pulse
    say one thing.
    """
    if workspace is not None:
        db = get_db_path(workspace)
        if not db_is_remote() and not db.exists():
            _emit({"ok": False, "reason": "db-not-found",
                   "workspace": str(workspace)})
            return 1
    if firm_id:
        return firm_id
    db = get_db_path(workspace if workspace is not None else Path.cwd())
    if db_is_remote() or db.exists():
        conn = connect(db)
        try:
            return resolve_firm_id(conn)
        except ValueError:
            return None
        finally:
            conn.close()
    return None


def run_status(*, unit_dir: Path | None = None,
               workspace: Path | None = None,
               firm_id: str | None = None) -> int:
    """List installed heartbeat timers.

    With NEITHER *workspace* nor *firm_id*, every installed heartbeat is
    listed -- that is what this verb is for. With either, the answer is about
    one firm, and a firm with no timer installed is ``ok: true`` with an empty
    list beside its id: the query ran and found none, which is an answer and
    not a failure (law 7).
    """
    wanted: str | None = None
    if workspace is not None or firm_id is not None:
        named = _firm_from(workspace, firm_id)
        if isinstance(named, int):
            return named
        if named is None:
            # A FLAG WAS GIVEN AND NOTHING RESOLVED, which is not the same as
            # no flag at all. `wanted = None` would fall through to listing
            # every installed heartbeat, and an operator who named a workspace
            # would read someone else's timers as the answer to their question
            # -- the silent fall-through condition 3 forbids, one branch over.
            # `disable` already fails here because it needs the id to build a
            # stem; `status` did not, because a None filter reads as no filter.
            # The word is the pulse's for the same failure (`cli/pulse.py`).
            payload: dict = {"ok": False, "reason": "firm-id-unresolved"}
            if workspace is not None:
                payload["workspace"] = str(workspace)
            if firm_id is not None:
                payload["firm_id"] = firm_id
            _emit(payload)
            return 1
        wanted = named

    sched = _sched(unit_dir)
    entries = []
    for stem in sched.list_installed(_UNIT_PREFIX):
        firm_id = stem[len(_UNIT_PREFIX):]
        if wanted is not None and firm_id != wanted:
            continue
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
        # `source` belongs in this tuple for the reason the comment above
        # gives (#158): it is None in exactly the case the card exists to
        # report, and ABSENT in the case where the command could not be read at
        # all. `st.get("source")` would collapse the two into one null and tell
        # an operator that a timer nobody could read is a timer with no label.
        for k in ("contained", "containment_reason", "containment_flags",
                  "source"):
            if k in st:
                entry[k] = st[k]
        entry["interpreter"] = _service_python(stem, unit_dir)
        entries.append(entry)

    # The identity block is the SAME dict cadre identity prints and
    # cadre doctor --install checks. One producer, several consumers: three
    # readers that each compute their own answer agree until the day one of
    # them is edited, and then nobody notices.
    from firm.identity import installed_identity

    payload: dict = {"ok": True, "cadre": installed_identity(),
                     "heartbeats": entries}
    if wanted is not None:
        # Named beside the list, so an empty answer says WHAT it is empty
        # about. Without it, "no timers" and "no timers for this firm" print
        # the same thing.
        payload["firm_id"] = wanted
    _emit(payload)
    return 0
