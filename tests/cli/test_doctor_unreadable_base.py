"""``firm doctor`` must not print a tick over an answer it could not read.

Issue #62. ``is_current`` returned ``tuple[bool, str]``. A bool has two values,
so the case "the rule count could not be read at all" had nowhere to go and was
folded into True, and ``doctor`` put that bool straight in as the check's
pass/fail. On a machine with no readable base the card said:

    ✓ The firm's graph reaches its Members — domain block matches the roster;
      rule count unread — base is not installed

The detail string was honest. Nothing read it. Unreadable presented as healthy,
which is the ``absent-is-a-passing-value`` family arriving in the product rather
than in an instrument.

Every arm here goes through ``diagnose()`` rather than through the service, for
two reasons. It holds the property an operator actually sees, and it runs
UNCHANGED against the sha before the fix, so the arm can be shown red before it
is shown green. An arm never seen red has not been shown to be an arm.

The paired control is not optional. A check only ever seen disagreeing has not
been shown to discriminate, so every "must not say current" arm here has a twin
that changes exactly one thing — whether the count can be read — and requires
the opposite answer.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from firm.cli import doctor as doctor_mod
from firm.core.db import get_db_path
from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.services import base_domain
from firm.sysconfig import service as sysconfig_service

FIRM = "zqfirm"

#: The REAL resolver, captured at import time. conftest replaces the module
#: attribute with a stub for every test in the suite, so an arm that needs to
#: prove base is genuinely unresolvable — rather than merely stubbed out — has
#: to hold its own reference and call this one. Same pattern as
#: tests/cli/test_init_wires_base.py.
REAL_WHICH_BASE = sysconfig_service.which_base

#: A path that does not exist and is never executed. The control replaces
#: `subprocess.run` for this argv only, so the string just has to be
#: recognisable, not real.
STUB_BASE = "/nonexistent/base-stub-for-the-control-arm"

#: Real ``base rule list`` output, copied verbatim from base 0.15.0 in the same
#: way tests/services/test_base_domain.py copied its fixtures. An assertion
#: keyed on a string somebody invented is a control that cannot show the reader
#: is working.
OUT_ONE_RULE = (
    f"[{FIRM}] 1 rules across both tiers:\n"
    "  workspace 0. godwit probe rule one\n"
    "  (global: none)\n"
    "\n"
    "Indices are per tier; `rule remove` takes the index shown beside its own tier.\n"
)


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _firm(workspace: Path) -> None:
    """A real firm with a real DB, so ``diagnose`` builds its whole card."""
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


def _wire(workspace: Path) -> None:
    """Give the firm a domain block that is GENUINELY current.

    Written by ``sync`` from the live roster rather than hand-built, so the
    block matches what the check recomputes. That matters: if it did not match,
    every arm below would stop at "stale" and never reach the rule-count branch
    the issue is about. The control arm is what proves it does match — it
    requires CURRENT, and CURRENT is unreachable from a stale block.
    """
    (workspace / ".base").mkdir(parents=True, exist_ok=True)
    (workspace / ".base" / "domains.toml").write_text("", encoding="utf-8")
    conn = sqlite3.connect(get_db_path(workspace))
    conn.row_factory = sqlite3.Row
    try:
        result = base_domain.sync(workspace, FIRM, conn=conn)
    finally:
        conn.close()
    assert result["ok"], f"the fixture never wrote a block: {result}"


def _card(workspace: Path) -> dict:
    checks = doctor_mod.diagnose(workspace, FIRM)
    cards = [c for c in checks if c["key"] == "base-domain"]
    assert len(cards) == 1, (
        f"expected exactly one base-domain card, got {len(cards)} — "
        "an assertion about a card that is not there proves nothing")
    return cards[0]


def _readable_count(monkeypatch) -> None:
    """Make the rule count READABLE, without disturbing any other subprocess.

    ``diagnose`` shells out for other checks too, so a blanket replacement of
    ``subprocess.run`` would change more than the one variable under test.
    Everything that is not our stub argv falls through to the real function.
    """
    monkeypatch.setattr(sysconfig_service, "which_base", lambda: STUB_BASE)
    real_run = subprocess.run

    def _run(cmd, **kwargs):
        if cmd and str(cmd[0]) == STUB_BASE:
            return _Result(OUT_ONE_RULE)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", _run)


# ---------------------------------------------------------------------------
# The arm. Red at 5d19e1b45f75, green after the fix.
# ---------------------------------------------------------------------------

def test_doctor_does_not_report_current_when_the_count_is_unreadable(tmp_path):
    """THE acceptance condition for #62.

    The count is genuinely unreadable here, not stubbed unreadable: conftest
    really sets CADRE_NO_BASE=1, so the REAL resolver really answers None. The
    precondition is asserted before anything is claimed about what it anchors —
    if base ever resolves in this environment the arm says so instead of
    quietly passing down a different code path.
    """
    assert REAL_WHICH_BASE() is None, (
        "precondition not met: base resolved, so the rule count is READABLE "
        "here and this arm would be testing nothing")

    _firm(tmp_path)
    _wire(tmp_path)

    card = _card(tmp_path)
    assert card["ok"] is False, (
        "doctor reported the firm's base domain as passing while the rule "
        "count could not be read at all — unreadable is presenting as healthy")
    assert card.get("state") == "undeterminable", (
        "the card does not carry a machine-readable third state, so the only "
        f"way to tell is substring-matching the detail: {card.get('detail')!r}")
    assert card["route"] != "mechanical", (
        "an unreadable count is routed to the mechanical fixer, which will "
        "rebuild the domain block and report a repair that cannot help")


def test_doctor_reports_current_when_the_count_is_readable(tmp_path, monkeypatch):
    """The control. Same firm, same block, one thing changed: the count reads.

    Without this, the arm above passes just as well over a check that always
    says False, and a check that cannot say yes is not a check.
    """
    _firm(tmp_path)
    _wire(tmp_path)
    _readable_count(monkeypatch)

    card = _card(tmp_path)
    assert card["ok"] is True, (
        f"a current block with one live rule is not passing: {card['detail']!r}")
    assert "1 rule" in card["detail"]


# ---------------------------------------------------------------------------
# The repair must not be claimed either
# ---------------------------------------------------------------------------

def test_doctor_fix_does_not_claim_a_repair_it_cannot_make(tmp_path):
    """``--fix`` rebuilds the block from the roster. That cannot make an
    unreadable base readable, so it must not report a base-domain repair.

    ``fix()`` selects on ``route == "mechanical"``, so routing the
    undeterminable case anywhere else is what holds this, and this arm is what
    proves the routing took.
    """
    _firm(tmp_path)
    _wire(tmp_path)

    checks = doctor_mod.diagnose(tmp_path, FIRM)
    card = next(c for c in checks if c["key"] == "base-domain")
    assert card["ok"] is False, (
        "precondition: the card must be a finding before we can ask whether "
        "--fix claims to have repaired it")

    did = doctor_mod.fix(tmp_path, FIRM, checks, unit_dir=tmp_path / "units")
    claimed = [d for d in did if d.startswith("base-domain:")]
    assert claimed == [], (
        f"--fix claimed a base-domain repair it cannot have made: {claimed}")


# ---------------------------------------------------------------------------
# A file that could not be read is not a file that is stale
# ---------------------------------------------------------------------------

def test_doctor_cannot_call_an_unreadable_domains_file_stale(tmp_path):
    """The mirror image of #62, in the same function.

    #62 asserts HEALTHY from no evidence. This branch asserted STALE from no
    evidence: ``domains.toml`` that raises on read returned False, i.e. "the
    block is out of date", which nobody established. It errs loud rather than
    quiet, so it is the less dangerous of the two, but it is the same mistake
    and it routed ``mechanical`` — so ``--fix`` would try to rebuild a file it
    had just failed to read.

    The file is made unreadable by being a DIRECTORY, so the OSError is real
    rather than injected. Linux raises IsADirectoryError and Windows raises
    PermissionError; both are OSError, which is what the code catches.
    """
    _firm(tmp_path)
    (tmp_path / ".base").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".base" / "domains.toml").mkdir()

    card = _card(tmp_path)
    assert card.get("state") == "undeterminable", (
        "a domains.toml that cannot be read is being reported as something "
        f"other than undeterminable: {card.get('state')!r} / {card['detail']!r}")
    assert card["ok"] is False
    assert card["route"] != "mechanical", (
        "--fix is routed to rebuild a file it just failed to read")
