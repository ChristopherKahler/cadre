"""Founding and doctor must be able to SAY the firm's base wire is dead.

The three states fail separately and a single flag cannot tell them apart: the
tier can exist, the domain block can be perfect, and the firm can still reach no
Member, because base drops a matched domain carrying zero rules.

Founding used to return True on ``base scaffold`` exiting 0 and discard
everything ``base_domain.sync`` told it. Doctor's ``--fix`` used to print
"rebuilt from the roster" while reading only ``ok``. Both reported a repair that
had not happened, which is what this file exists to keep out.

Every failure leg below is paired with its healthy control. A leg that only ever
sees the broken input cannot show that it discriminates, and the doctor legs
drive the REAL ``doctor.fix`` rather than a copy of its arm — the message a
copy produces is not the message an operator reads.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from firm.cli import doctor as doctor_mod
from firm.core.db import get_db_path
from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.services import base_domain


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "firm"
    w.mkdir()
    return w


def _wire(monkeypatch, *, base_present=True, scaffold_rc=0, sync_result=None):
    monkeypatch.setattr("firm.sysconfig.service.which_base",
                        lambda: "/fake/base" if base_present else None)
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: _Proc(returncode=scaffold_rc))
    if sync_result is not None:
        monkeypatch.setattr("firm.services.base_domain.sync",
                            lambda *a, **k: sync_result)


# ---------------------------------------------------------------------------
# base_domain.wire_workspace — the three states, separately
# ---------------------------------------------------------------------------

def test_a_live_wire_reports_live(monkeypatch, ws):
    _wire(monkeypatch, sync_result={"ok": True, "changed": True,
                                    "keywords": ["zqfirm"], "rule_seeded": True})
    res = base_domain.wire_workspace(ws, "zqfirm", {})
    assert (res["scaffolded"], res["domain_ok"], res["rule_seeded"], res["live"]) \
        == (True, True, True, True)


def test_a_failed_rule_seed_is_not_live_and_says_why(monkeypatch, ws):
    """The exact shape chief-of-staff-cto shipped in: tier fine, block fine, no
    rules, and the old code called it wired."""
    _wire(monkeypatch, sync_result={"ok": True, "changed": True,
                                    "keywords": ["zqfirm"], "rule_seeded": False})
    res = base_domain.wire_workspace(ws, "zqfirm", {})
    assert res["scaffolded"] is True, "the tier really was created"
    assert res["domain_ok"] is True, "and the block really was written"
    assert res["rule_seeded"] is False
    assert res["live"] is False, "but nothing reaches a Member, so it is not live"
    assert "no rules" in res["detail"]
    assert "firm doctor --fix" in res["detail"], "a report with no route gets retried"


def test_a_missing_domain_block_is_not_live(monkeypatch, ws):
    _wire(monkeypatch, sync_result={"ok": False, "changed": False,
                                    "reason": "no .base/domains.toml"})
    res = base_domain.wire_workspace(ws, "zqfirm", {})
    assert res["scaffolded"] is True
    assert res["domain_ok"] is False
    assert res["live"] is False
    assert "no .base/domains.toml" in res["detail"]


def test_base_absent_is_degraded_not_broken(monkeypatch, ws):
    _wire(monkeypatch, base_present=False)
    res = base_domain.wire_workspace(ws, "zqfirm", {})
    assert res["scaffolded"] is False
    assert res["live"] is False
    assert "degraded, not broken" in res["detail"]


def test_a_failed_scaffold_carries_the_exit_code(monkeypatch, ws):
    _wire(monkeypatch, scaffold_rc=3)
    res = base_domain.wire_workspace(ws, "zqfirm", {})
    assert res["scaffolded"] is False
    assert "exited 3" in res["detail"]


def test_a_scaffold_that_cannot_run_is_not_a_silent_false(monkeypatch, ws):
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: "/fake/base")

    def _boom(cmd, **kw):
        raise OSError("no such binary")

    monkeypatch.setattr(subprocess, "run", _boom)
    res = base_domain.wire_workspace(ws, "zqfirm", {})
    assert res["scaffolded"] is False
    assert "did not run" in res["detail"]


def test_three_inputs_give_three_distinguishable_answers(monkeypatch, ws):
    """A single flag cannot tell a missing tier from a dead rule seed, and the
    operator's next action differs for each."""
    seen = []
    cases = [
        ({"ok": True, "rule_seeded": True, "keywords": []}, (True, True, True)),
        ({"ok": True, "rule_seeded": False, "keywords": []}, (True, True, False)),
        ({"ok": False, "reason": "x"}, (True, False, False)),
    ]
    for sync_result, expect in cases:
        _wire(monkeypatch, sync_result=sync_result)
        r = base_domain.wire_workspace(ws, "zqfirm", {})
        got = (r["scaffolded"], r["domain_ok"], r["rule_seeded"])
        seen.append(got)
        assert got == expect
    assert len(seen) == len(cases) == 3, f"visited {len(seen)} states, expected 3"
    assert len(set(seen)) == 3, "three inputs must give three different answers"


# ---------------------------------------------------------------------------
# doctor.fix — driving the REAL arm, not a copy of it
# ---------------------------------------------------------------------------

FIRM = "zqfirm"


def _seed_firm(workspace: Path) -> None:
    db_path = get_db_path(workspace)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": FIRM, "name": "Test Firm"})
    create(conn, "member", {"id": "MEM-001", "firm_id": FIRM,
                            "name": "Vantage", "role": "Chief of Staff"})
    conn.commit()
    conn.close()


_FAILED_BASE_DOMAIN = [{
    "key": "base-domain",
    "label": "The firm's graph reaches its Members",
    "ok": False,
    "route": "mechanical",
    "detail": "seeded for the test",
}]


def _run_fix(monkeypatch, tmp_path, sync_result) -> str:
    workspace = tmp_path / "wsfix"
    workspace.mkdir()
    _seed_firm(workspace)
    monkeypatch.setattr("firm.services.base_domain.sync", lambda *a, **k: sync_result)
    did = doctor_mod.fix(workspace, FIRM, _FAILED_BASE_DOMAIN, unit_dir=tmp_path / "units")
    lines = [d for d in did if d.startswith("base-domain:")]
    assert len(lines) == 1, f"expected exactly 1 base-domain line, got {len(lines)}: {did}"
    return lines[0]


def test_doctor_fix_says_the_seed_failed(monkeypatch, tmp_path):
    msg = _run_fix(monkeypatch, tmp_path,
                   {"ok": True, "keywords": ["a"], "rule_seeded": False})
    assert "SEED FAILED" in msg
    assert "injects nothing" in msg
    assert "base rule add" in msg, "the message must carry the route, not just a verdict"


def test_doctor_fix_control_a_healthy_seed_reads_healthy(monkeypatch, tmp_path):
    """The control. Without it the test above would pass on a message that says
    SEED FAILED unconditionally."""
    msg = _run_fix(monkeypatch, tmp_path,
                   {"ok": True, "keywords": ["a"], "rule_seeded": True})
    assert "SEED FAILED" not in msg
    assert "rule seeded" in msg
    assert "rebuilt from the roster" in msg


def test_doctor_fix_reports_a_missing_tier_as_itself(monkeypatch, tmp_path):
    msg = _run_fix(monkeypatch, tmp_path,
                   {"ok": False, "reason": "no .base/domains.toml"})
    assert "no .base/domains.toml" in msg
    assert "rebuilt" not in msg, "nothing was rebuilt, so nothing may claim it was"


def test_doctor_fix_does_nothing_when_the_check_passed(monkeypatch, tmp_path):
    """A fix arm that runs on a green check is a repair nobody asked for."""
    workspace = tmp_path / "wsgreen"
    workspace.mkdir()
    _seed_firm(workspace)
    called = []
    monkeypatch.setattr("firm.services.base_domain.sync",
                        lambda *a, **k: called.append(1) or {"ok": True})
    passing = [dict(_FAILED_BASE_DOMAIN[0], ok=True)]
    did = doctor_mod.fix(workspace, FIRM, passing, unit_dir=tmp_path / "units")
    assert called == [], "sync must not run for a check that passed"
    assert [d for d in did if d.startswith("base-domain:")] == []
