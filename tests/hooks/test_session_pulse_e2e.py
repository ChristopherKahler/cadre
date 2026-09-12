"""End-to-end test: spawn the entrypoint script as a subprocess, diff stdout
against the golden file, and verify the hook does not mutate the DB.

The entrypoint under test is ``src/firm/hooks/session_pulse_entry.py`` — the
exact bytes `cadre init --install-hooks` writes into a workspace. It used to
point at ``install/firm-session-pulse.py``, which no user ever ran: the
installer wrote a separate `_HOOK_TEMPLATE` string, and the two drifted 123
diff lines apart while this test stayed green. Golden output lives at
``tests/golden/session-pulse-chrisai.txt``.

To regenerate the golden file after an intentional format change, run:

    PYTHONPATH=src python3 -m tests.hooks.test_session_pulse_e2e --regen

(``--regen`` path is wired via ``__main__`` below.)
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations
from firm.core.repo import ALL_TABLES, create

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "src" / "firm" / "hooks" / "session_pulse_entry.py"
GOLDEN = REPO_ROOT / "tests" / "golden" / "session-pulse-chrisai.txt"

# These two tests run a file from the repo checkout, not from the installed
# package, so a suite run detached from the checkout cannot execute them.
# Without this the entrypoint is simply missing, python exits 2, and the
# assertion reads "assert 2 == 0" - which was reported as a Windows
# portability failure on 2026-09-10 and was nothing of the kind. Say what
# is absent instead of failing blind.
needs_checkout = pytest.mark.skipif(
    not ENTRYPOINT.exists() or not GOLDEN.exists(),
    reason=f"needs the repo checkout: {ENTRYPOINT} / {GOLDEN}",
)

FIXED_NOW_ISO = "2026-04-15 20:00:00"
# updated_at values chosen so the time_ago renderer produces stable labels
# against FIXED_NOW_ISO. Gate expires_at at +3h exercises [URGENT].
UPDATED_AT_1H_AGO = "2026-04-15 19:00:00"
GATE_EXPIRES_URGENT = "2026-04-15 23:00:00"


def _seed_chrisai_full(workspace: Path) -> None:
    """Seed the full ChrisAI state required by the golden output."""
    db_path = get_db_path(workspace)
    if db_path.exists():
        db_path.unlink()
    conn = connect(db_path)
    try:
        apply_migrations(conn)

        create(conn, "firm", {
            "id": "chrisai", "name": "ChrisAI",
            "operator": {"name": "Chris Kahler", "role": "Board / Founder"},
        })
        create(conn, "member", {
            "id": "MEM-002", "firm_id": "chrisai",
            "name": "Sterling", "role": "CMO",
        })
        create(conn, "contract", {
            "id": "CON-001", "firm_id": "chrisai",
            "name": "Quill Blog Author Contract",
            "runtime_type": "claude_code",
            "runtime_config": {"entry_command": "/quill:run"},
        })
        create(conn, "member", {
            "id": "MEM-001", "firm_id": "chrisai",
            "name": "Quill", "role": "Blog Author",
            "reports_to_member_id": "MEM-002",
            "contract_id": "CON-001",
        })
        create(conn, "member", {
            "id": "MEM-003", "firm_id": "chrisai",
            "name": "Sage", "role": "Content Strategist",
            "reports_to_member_id": "MEM-002",
        })
        create(conn, "operation", {
            "id": "OPS-001", "firm_id": "chrisai",
            "name": "Content Publishing",
        })
        create(conn, "project", {
            "id": "PROJ-001", "firm_id": "chrisai",
            "operation_id": "OPS-001",
            "name": "Blog Pipeline", "status": "in_progress",
            "due_date": "2026-12-31",
        })
        create(conn, "unit", {
            "id": "UNIT-014", "firm_id": "chrisai", "project_id": "PROJ-001",
            "name": "Blog post #14 draft",
            "status": "in_progress",
            "claimed_by": "MEM-001",
        })
        # GOAL-001 full metric; GOAL-002/003 null-metric edge cases
        create(conn, "goal", {
            "id": "GOAL-001", "firm_id": "chrisai", "level": "operation",
            "parent_entity_type": "operation", "parent_entity_id": "OPS-001",
            "target": "Publish 2 longform blog posts per week",
            "metric": {"type": "publish_rate", "value": 2,
                       "unit": "posts_per_week", "current": None},
            "updated_at": UPDATED_AT_1H_AGO,
        })
        create(conn, "goal", {
            "id": "GOAL-002", "firm_id": "chrisai", "level": "operation",
            "parent_entity_type": "operation", "parent_entity_id": "OPS-001",
            "target": "Monthly unique visitors trending upward",
            "metric": {"type": "unique_visitors", "value": None,
                       "unit": "per_month", "current": None,
                       "trend": "growing"},
            "updated_at": UPDATED_AT_1H_AGO,
        })
        create(conn, "goal", {
            "id": "GOAL-003", "firm_id": "chrisai", "level": "operation",
            "parent_entity_type": "operation", "parent_entity_id": "OPS-001",
            "target": "Unique-visitor-to-subscriber ratio held or growing",
            "metric": {"type": "conversion_ratio", "value": None,
                       "unit": "subs_per_unique", "current": None,
                       "trend": "stable_or_growing"},
            "updated_at": UPDATED_AT_1H_AGO,
        })
        # One pending URGENT gate on UNIT-014, requested by Quill
        create(conn, "gate", {
            "id": "GATE-001", "firm_id": "chrisai",
            "requesting_member_id": "MEM-001",
            "action": "publish_post",
            "target_entity_type": "unit", "target_entity_id": "UNIT-014",
            "context": "Blog post #14 draft complete, AC resolved, ready for publish.",
            "expires_at": GATE_EXPIRES_URGENT,
        })
    finally:
        conn.commit()
        conn.close()


def _snapshot_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ALL_TABLES
        }
    finally:
        conn.close()


def _run_hook(
    workspace: Path, *, member_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"session_id": "e2e-test", "cwd": str(workspace)})
    env = {
        **os.environ,
        "FIRM_ID": "chrisai",
        "FIRM_NOW_OVERRIDE": FIXED_NOW_ISO,
        "FIRM_SRC": str(REPO_ROOT / "src"),
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }
    # The hook words its output for whoever the session is: the Board, or the
    # Member a pulse spawned with CADRE_MEMBER_ID. Never the ambient shell's.
    env.pop("CADRE_MEMBER_ID", None)
    if member_id:
        env["CADRE_MEMBER_ID"] = member_id
    # Deliberately cleared. A developer machine with PYTHONIOENCODING set
    # gives the child a UTF-8 stdout for free and hides every locale
    # defect in the hook; CI and a fresh Windows install have it unset.
    # The test has to measure the default, not the ambient shell.
    env.pop("PYTHONIOENCODING", None)
    # encoding is explicit on both sides. text=True alone decodes with the
    # platform locale, which is cp1252 on Windows, so the comparison below
    # would be measuring the console code page rather than the hook.
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT)],
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )


# ---------------------------------------------------------------------------
# AC-6: end-to-end subprocess + golden file + read-only invariant
# ---------------------------------------------------------------------------

@needs_checkout
def test_entrypoint_output_matches_golden(tmp_path: Path) -> None:
    _seed_chrisai_full(tmp_path)
    db_path = get_db_path(tmp_path)
    before = _snapshot_counts(db_path)

    result = _run_hook(tmp_path)

    assert result.returncode == 0, f"non-zero exit: {result.returncode}\nstderr: {result.stderr}"
    assert result.stderr == "", f"unexpected stderr: {result.stderr!r}"

    expected = GOLDEN.read_text(encoding="utf-8")
    assert result.stdout == expected, (
        "Golden mismatch. If this is an intentional format change, "
        "regenerate with: "
        "PYTHONPATH=src python3 tests/hooks/test_session_pulse_e2e.py --regen\n"
        f"--- expected ---\n{expected}\n--- actual ---\n{result.stdout}"
    )

    after = _snapshot_counts(db_path)
    assert before == after, f"hook mutated DB. before={before} after={after}"


@needs_checkout
def test_entrypoint_inside_a_member_run_names_no_command_the_member_is_refused(
    tmp_path: Path,
) -> None:
    # #106: the hook fires inside every Member run too, where the spawn stamps
    # CADRE_MEMBER_ID. The Board's wording names /gate:decide, which is
    # Board-only, and `cadre goal update`, which needs the authority key no
    # founded Member holds. The Board's own session still gets the golden.
    _seed_chrisai_full(tmp_path)

    board = _run_hook(tmp_path)
    member = _run_hook(tmp_path, member_id="MEM-001")

    assert board.returncode == 0, board.stderr
    assert member.returncode == 0, member.stderr
    assert board.stdout == GOLDEN.read_text(encoding="utf-8")
    assert "<pending-gates" in member.stdout and "<goal-health" in member.stdout
    for refused in ("/gate:decide", "goal update", "firm_update_goal_metric"):
        assert refused not in member.stdout, refused
    assert "--target-type goal --target-id <GOAL-id>" in member.stdout


@needs_checkout
def test_entrypoint_survives_a_name_the_locale_cannot_encode(tmp_path: Path) -> None:
    """A Member name outside the platform locale must not silence the hook.

    Measured on Windows 10 / Python 3.12.6 with PYTHONIOENCODING unset: a
    piped stdout defaults to cp1252, and writing U+014D raised
    UnicodeEncodeError, exit 1, nothing on stdout. In the hook that exception
    meets the last-resort guard and becomes exit 0 with no output - which the
    documented contract says means "no firm here". The operator sees an empty
    pulse and concludes everything is fine.

    Runs on every platform. On Linux it locks the behaviour; on Windows it is
    the actual red arm.
    """
    _seed_chrisai_full(tmp_path)
    name = "Zo\u00eb \u014ctani"
    conn = sqlite3.connect(get_db_path(tmp_path))
    try:
        conn.execute("UPDATE member SET name = ? WHERE id = 'MEM-002'", (name,))
        conn.commit()
    finally:
        conn.close()

    result = _run_hook(tmp_path)

    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert name in result.stdout, (
        f"the name did not survive the hook's stdout\n--- stdout ---\n"
        f"{result.stdout}\n--- stderr ---\n{result.stderr}")


@needs_checkout
def test_entrypoint_silent_when_db_missing(tmp_path: Path) -> None:
    # No .firm/ directory seeded — hook must exit 0 silently.
    result = _run_hook(tmp_path)
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# Regeneration helper: `python3 tests/hooks/test_session_pulse_e2e.py --regen`
# ---------------------------------------------------------------------------

def _regenerate_golden() -> None:
    """Run the fixture + hook once and overwrite the golden file."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        _seed_chrisai_full(ws)
        result = _run_hook(ws)
        if result.returncode != 0 or result.stderr:
            print(f"hook failed: rc={result.returncode} stderr={result.stderr}",
                  file=sys.stderr)
            sys.exit(1)
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(result.stdout)
        print(f"Wrote {GOLDEN} ({len(result.stdout)} bytes)")


if __name__ == "__main__":
    if "--regen" in sys.argv:
        _regenerate_golden()
    else:
        print("Run via pytest. Use --regen to refresh the golden file.")
