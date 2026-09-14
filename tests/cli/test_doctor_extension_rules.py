"""``firm doctor`` must say when base is serving somebody else's rules.

Issue #115. An extension's prompt domain lives in two places and they are not
the same store. base MATCHES the domain's keywords from the installed manifest
and SERVES its rule text from the GRAPH whenever the graph holds a copy — and
the graph copy wins outright. Measured on base 0.15.2, 2026-09-14, on the
maintainer's machine: a keyword that exists only in Cadre's shipped manifest
(``unit complete``) fired the domain, and what arrived were three rules written
in 2026-08 naming one firm's WSL paths. None of the shipped rules reached the
session.

base owns the repair and has no verb for it, measured one at a time:
``rule remove --index N`` matches an ``index`` triple that rules written by
``base domain sync`` do not carry; ``domain sync`` appends rather than replaces
on a populated graph (three rules became six); ``graph supersede`` does not
index rule records; ``graph apply-ops`` retires only facts carrying a sync id.

So the card below is deliberately NOT mechanical and there is nothing for
``--fix`` to do. Its whole job is that the second rule set cannot sit there
quietly. Every arm has a paired control that changes exactly one thing, because
a check only ever seen disagreeing has not been shown to discriminate.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from firm.cli import doctor as doctor_mod
from firm.core.db import get_db_path
from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.services import base_extension
from firm.sysconfig import service as sysconfig_service

FIRM = "zqrules"
KEY = "base-extension-rules"

#: The REAL resolver, captured at import. conftest sets CADRE_NO_BASE=1 for the
#: whole suite, so an arm that needs base to look present replaces the module
#: attribute, and an arm that needs it genuinely absent calls this.
REAL_WHICH_BASE = sysconfig_service.which_base

#: This host's magic bytes, so a stub base is a REAL FILE.
#:
#: `tests/test_no_weak_base_stubs.py` caught this file stubbing `which_base` at
#: a bare string, and it was right to. Since #75 `install()` identifies the
#: binary it resolved before running it, so a stub that is not a file is
#: refused BEFORE any check under test is reached — and an arm built on one
#: cannot tell REFUSED CORRECTLY from NEVER GOT THERE. The same reasoning is
#: why the `fake_base` fixture in tests/services/test_base_extension.py writes
#: real bytes. Nothing here currently goes through that refusal, which is
#: exactly how a weak stub survives until the day something does.
_MAGIC = {"pe": b"MZ\x90\x00", "macho": b"\xcf\xfa\xed\xfe"}


def _stub_base(tmp_path: Path) -> str:
    from firm.sysconfig.binaries import native_image_format

    bindir = tmp_path / "stubbin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "base"
    stub.write_bytes(_MAGIC.get(native_image_format(), b"\x7fELF") + b"\x00" * 128)
    stub.chmod(0o755)
    return str(stub)

#: Copied out of a real ``base rule list`` run on the maintainer's machine,
#: not typed from memory. A control keyed on a string somebody invented cannot
#: show that the reader works.
FOREIGN_LISTING = (
    "[ext:cadre:cadre-firm] 3 rules across both tiers:\n"
    "  workspace 0. Cadre firm active. chrisai firm root: "
    "/home/chriskahler/firms/chrisai (WSL).\n"
    "  workspace 1. CLI: /home/chriskahler/firms/chrisai/.venv/bin/firm "
    "{init|pulse|unit|run}.\n"
    "  workspace 2. Board rules bind every session: never approve/reject Gates.\n"
    "  (global: none)\n"
    "\n"
    "Indices are per tier; `rule remove` takes the index shown beside its own tier.\n"
)

CLEAN_LISTING = "No rules for domain 'ext:cadre:cadre-firm' in either tier.\n"


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _firm(workspace: Path) -> None:
    db_path = get_db_path(workspace)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    create(conn, "firm", {"id": FIRM, "name": "Rules Firm"})
    create(conn, "member", {"id": "MEM-001", "firm_id": FIRM,
                            "name": "Vantage", "role": "Chief of Staff"})
    conn.commit()
    conn.close()


def _base_answers(monkeypatch, tmp_path: Path, listing: str) -> None:
    """Make base look present and answer `rule list` with ``listing``.

    Only our stub argv is intercepted. ``diagnose`` shells out for other checks
    too, so a blanket replacement of ``subprocess.run`` would move more than
    the one variable under test.
    """
    stub = _stub_base(tmp_path)
    monkeypatch.setattr(sysconfig_service, "which_base", lambda: stub)
    real_run = subprocess.run

    def _run(cmd, **kwargs):
        if cmd and str(cmd[0]) == stub:
            return _Result(listing)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", _run)


def _own_rules_listing() -> str:
    """A graph copy that is a faithful mirror of the shipped manifest.

    Built from the manifest itself rather than hand-written, so it cannot drift
    away from the file it is supposed to match — which is the whole property
    the control arm exists to hold.
    """
    rendered = base_extension.render("/opt/cadre")
    (_name, rules), = base_extension.manifest_domains(rendered)
    body = "\n".join(f"  workspace {i}. {r}" for i, r in enumerate(rules))
    return (f"[ext:cadre:cadre-firm] {len(rules)} rules across both tiers:\n"
            f"{body}\n  (global: none)\n\nIndices are per tier;\n")


def _card(workspace: Path) -> dict:
    checks = doctor_mod.diagnose(workspace, FIRM)
    cards = [c for c in checks if c["key"] == KEY]
    assert len(cards) == 1, (
        f"expected exactly one {KEY} card, got {len(cards)} — an assertion "
        "about a card that is not there proves nothing")
    return cards[0]


# ---------------------------------------------------------------------------
# The arm
# ---------------------------------------------------------------------------

def test_doctor_reports_rules_the_manifest_did_not_write(tmp_path, monkeypatch):
    _firm(tmp_path)
    _base_answers(monkeypatch, tmp_path, FOREIGN_LISTING)

    card = _card(tmp_path)
    assert card["ok"] is False, (
        "base is serving three rules Cadre never wrote, under Cadre's own "
        "domain name, and the doctor calls that healthy")
    assert "chrisai firm root" in card["detail"], (
        "the card says a collision exists without printing the rule, which "
        "leaves the operator exactly where they started")
    assert "OWNER: base" in card["detail"], (
        "the card does not name the owner, so the next reader spends a day "
        "inside Cadre looking for a bug that is not here")
    assert card["route"] != "mechanical", (
        "routed to the mechanical fixer, which has nothing it can do here")


def test_doctor_passes_when_the_graph_copy_is_cadres_own(tmp_path, monkeypatch):
    """The control that separates 'a copy exists' from 'a FOREIGN copy exists'.

    Without it, an implementation that flagged the mere presence of a graph
    copy would pass the arm above and fail every healthy machine.
    """
    _firm(tmp_path)
    _base_answers(monkeypatch, tmp_path, _own_rules_listing())

    card = _card(tmp_path)
    assert card["ok"] is True, (
        f"a graph copy that matches the manifest is reported as drift: "
        f"{card['detail']!r}")


def test_doctor_passes_when_the_graph_holds_no_copy(tmp_path, monkeypatch):
    _firm(tmp_path)
    _base_answers(monkeypatch, tmp_path, CLEAN_LISTING)

    card = _card(tmp_path)
    assert card["ok"] is True, card["detail"]


# ---------------------------------------------------------------------------
# Unreadable is not clean
# ---------------------------------------------------------------------------

def test_doctor_does_not_pass_over_a_listing_it_could_not_read(tmp_path,
                                                               monkeypatch):
    """A zero that means "I could not see" reads exactly like a zero that means
    "nothing is wrong", and only one of those is good news."""
    _firm(tmp_path)
    _base_answers(monkeypatch, tmp_path, "some future base says something else entirely")

    card = _card(tmp_path)
    assert card["ok"] is False
    assert card.get("state") == "undeterminable", (
        "no machine-readable third state, so the only way to tell an unread "
        f"answer from a clean one is substring-matching: {card['detail']!r}")
    assert card["route"] != "mechanical"


def test_doctor_fix_claims_no_repair_for_a_defect_base_owns(tmp_path,
                                                            monkeypatch):
    """``--fix`` selects on ``route == "mechanical"``. There is no verb in base
    that removes these rules, so a claimed repair here would be a lie."""
    _firm(tmp_path)
    _base_answers(monkeypatch, tmp_path, FOREIGN_LISTING)

    checks = doctor_mod.diagnose(tmp_path, FIRM)
    card = next(c for c in checks if c["key"] == KEY)
    assert card["ok"] is False, (
        "precondition: the card must be a finding before we can ask whether "
        "--fix claims to have repaired it")

    did = doctor_mod.fix(tmp_path, FIRM, checks, unit_dir=tmp_path / "units")
    claimed = [d for d in did if d.startswith(f"{KEY}:")]
    assert claimed == [], f"--fix claimed a repair it cannot make: {claimed}"


# ---------------------------------------------------------------------------
# No base at all
# ---------------------------------------------------------------------------

def test_doctor_says_nothing_is_injected_when_base_is_absent(tmp_path):
    """Absent is not the same as broken. With no base on the machine there is
    no graph, no injection and nothing to collide — and the card says which of
    the three it is rather than printing a bare tick."""
    assert REAL_WHICH_BASE() is None, (
        "precondition not met: base resolved here, so this arm would be "
        "testing the readable path instead")

    _firm(tmp_path)
    card = _card(tmp_path)
    assert card["ok"] is True
    assert "not on this machine" in card["detail"], (
        f"the card passes without saying why: {card['detail']!r}")
