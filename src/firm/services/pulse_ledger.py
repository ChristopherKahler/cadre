"""The pulse ledger — one row per pulse PROCESS (#128 D3, migration 016).

A firm's own durable record that it pulsed, and the surface two readers use:
``heartbeat status``'s ``last_pulse`` and the doctor's gap card. Before this,
the only record was ``.firm/last-pulse.json``, which the hub's ``_fire_pulse``
writes and nothing else does, so a firm pulsing on its timer had none.

EVERY WRITE HERE IS BEST EFFORT, AND THAT IS A CONTRACT, NOT A CONVENIENCE.
A firm upgraded without ``cadre init`` or ``cadre doctor --fix`` has no
``pulse_run`` table, and the pulse must still run and still exit on its own
outcome: a side record that fails must not turn a pulse that ran into a pulse
that errored (the rule the exports already follow, ``cli/pulse.py``). So the
callers here go through :func:`best_effort`, which returns the reason instead
of raising, and the pulse prints that reason in its JSON.

AND THE PULSE NEVER MIGRATES. Nothing in this module creates a table. Several
machines can share one database, so a pulse that migrated would change the
schema under all of them from an unattended timer tick, in a process a SIGTERM
can stop half way. Migrations are ``init``'s and ``doctor --fix``'s.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable

#: The migration that carries the table. Named here so the readers can say
#: which one is missing rather than saying only that something is.
MIGRATION = "016_pulse_run"

TABLE = "pulse_run"

#: What a pulse may say about where it came from. ``unset`` is what a pulse
#: with no ``--source`` records and is deliberately NOT accepted as a value:
#: every timer installed before this change passes no flag, and if ``unset``
#: could be passed the column would stop separating "this pulse did not say"
#: from "this pulse said none of them".
HEARTBEAT = "heartbeat"
BOARD = "board"
CLI = "cli"
QUEUE = "queue"
SOURCES = (HEARTBEAT, BOARD, CLI, QUEUE)
UNSET = "unset"

#: The outcome of a row nobody closed. Never ``died``: a pulse that was killed
#: and a pulse whose closing write failed leave the identical open row with the
#: identical dead pid, so ``died`` would name a crash nobody read (osprey's
#: verdict on #128, item 5; law 37).
UNCLOSED = "unclosed"


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class LedgerUnavailable(RuntimeError):
    """The ledger could not be read or written, with the reason in words."""


def best_effort(work: Callable[[], Any]) -> tuple[Any, str | None]:
    """Run *work*; return ``(value, None)`` or ``(None, reason)``.

    The whole missing-table contract lives in this one function, so the note
    the pulse prints and the note the doctor prints are the same sentence, and
    removing it is a single visible change rather than seven quiet ones.
    """
    try:
        return work(), None
    except sqlite3.OperationalError as exc:
        return None, _reason(exc)
    except sqlite3.DatabaseError as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _reason(exc: sqlite3.OperationalError) -> str:
    """The reason in the words a reader can act on.

    ``no such table: pulse_run`` is the one a firm actually meets, and the
    action it implies is a migration, so the sentence names the migration
    rather than leaving the reader to map the table to it.
    """
    message = str(exc)
    if "no such table" in message and TABLE in message:
        return f"migration {MIGRATION} is not applied ({TABLE} is absent)"
    return f"{type(exc).__name__}: {message}"


def table_is_present(conn: Any) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,)).fetchone())


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def open_run(conn: Any, firm_id: str, *, source: str, holder: str) -> int:
    """Start a row for this pulse process and return its id."""
    cur = conn.execute(
        f"INSERT INTO {TABLE} (firm_id, started_at, source, holder)"
        " VALUES (?, ?, ?, ?)",
        (firm_id, _now(), source or UNSET, holder))
    conn.commit()
    return int(cur.lastrowid or 0)


def close_run(conn: Any, run_id: int, result: dict[str, Any]) -> None:
    """Close *run_id* with the pulse's own printed result.

    ``outcome`` is ``ok`` when the result says so, and otherwise the result's
    own ``reason``, so the row and the exit code cannot disagree: both are read
    off the same object, in the same function (``cli/pulse.py::_exit_with``).
    A result with neither is an exit shape nobody has named yet, and it is
    recorded as ``error`` rather than left NULL, which would make it look like
    a row nobody closed.
    """
    ok = result.get("ok") is True
    outcome = "ok" if ok else str(result.get("reason") or "error")
    conn.execute(
        f"UPDATE {TABLE} SET ended_at = ?, outcome = ?, ok = ?, ran = ?,"
        " errors = ?, skipped = ? WHERE id = ?",
        (_now(), outcome, 1 if ok else 0, _count(result, "ran"),
         _count(result, "errors"), _count(result, "skipped"), run_id))
    conn.commit()


def _count(result: dict[str, Any], key: str) -> int | None:
    """A count the result carries, or None.

    None, never 0: a pulse that ended at its preflight ran no cycle, and a
    zero there would read as a cycle that found nothing to do. Absent and
    zero are different claims (law 7).
    """
    value = result.get(key)
    return value if isinstance(value, int) else None


def close_out_dead_local_runs(
    conn: Any, *, host: str, is_alive: Callable[[int], bool],
) -> list[int]:
    """Close rows this host left open whose process is gone. Returns their ids.

    ONLY THIS HOST'S. The holder is ``host:pid:nonce`` because the database can
    be shared across machines, and a pid from another machine's process table
    means nothing in ours — ``_pid_alive`` would answer about whatever local
    process happens to hold that number, or about nothing, and either answer
    would close a row belonging to a pulse that is still running somewhere
    else. Rows held elsewhere are left for that machine's next pulse.
    """
    rows = conn.execute(
        f"SELECT id, holder FROM {TABLE} WHERE ended_at IS NULL").fetchall()
    closed: list[int] = []
    for row in rows:
        run_id, holder = row[0], row[1]
        parts = str(holder).split(":")
        if len(parts) < 2 or parts[0] != host:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        if is_alive(pid):
            continue
        conn.execute(
            f"UPDATE {TABLE} SET ended_at = ?, outcome = ?, ok = 0"
            " WHERE id = ? AND ended_at IS NULL",
            (_now(), UNCLOSED, run_id))
        closed.append(int(run_id))
    if closed:
        conn.commit()
    return closed


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def last_started_at(conn: Any, firm_id: str) -> str | None:
    """When this firm's newest pulse STARTED, or None when it has none.

    Started, not ended: a pulse takes 20-30 minutes, and a reader asking when
    the firm last pulsed is asking when it last came alive, not when it last
    finished. It is also the only one of the two a running pulse can answer.
    """
    row = conn.execute(
        f"SELECT started_at FROM {TABLE} WHERE firm_id = ?"
        " ORDER BY started_at DESC LIMIT 1", (firm_id,)).fetchone()
    return row[0] if row else None


def starts(conn: Any, firm_id: str) -> list[str]:
    rows = conn.execute(
        f"SELECT started_at FROM {TABLE} WHERE firm_id = ?"
        " ORDER BY started_at", (firm_id,)).fetchall()
    return [r[0] for r in rows]


def gaps(started: list[str], interval_seconds: int) -> list[tuple[str, str]]:
    """Consecutive starts more than two intervals apart.

    Two, not one: a timer that fires a little late still lands inside the
    second interval, and only past it has a scheduled pulse certainly not
    started. Below that the card would report the cadence's own jitter.

    BETWEEN RECORDED PULSES ONLY. The window from the newest row to now is
    deliberately not reported here: a firm whose timer is off has no newest
    row moving, and this card would then fire forever about a finding the
    timer card already owns (``firm.pulse_interval`` against the installed
    timer, ``cli/doctor.py``).

    And it is a GAP, never a DROP. A logged-out Windows box does not pulse,
    and that leaves exactly the evidence a real dropped tick leaves: no row.
    The card may report the window it measured; it may not name a cause it
    did not read.
    """
    if interval_seconds <= 0:
        return []
    found: list[tuple[str, str]] = []
    for before, after in zip(started, started[1:]):
        first, second = _parse(before), _parse(after)
        if first is None or second is None:
            continue
        if (second - first).total_seconds() > 2 * interval_seconds:
            found.append((before, after))
    return found


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
