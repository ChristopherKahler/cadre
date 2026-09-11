"""`cadre init` gives the firm its BASE tier, and says what it managed to do.

F4 and F5 were one missing step. ``cadre init`` never created ``.base/``,
because the only code that ran ``base scaffold`` was private inside
``firm/dashboard/founding.py`` and reachable only from the dashboard founding
flow. So a firm made from the command line had no tier, and:

  F4  ``cadre learn`` refused to write, with
      "no .base/ directory found — refusing to write outside a workspace"
  F5  ``base_domain.sync`` returned early at its ``domains.toml`` check, and
      ``_seed_rule`` -- which is only ever called from inside ``sync`` -- never
      ran, so ``base rule list --domain demo`` reported no rules

One directory, both symptoms. The fix moved the wiring into
``firm/services/base_domain.py`` where both callers can reach it, rather than
giving the command line a second copy of it -- that being the mistake
``tests/services/test_base_env_one_builder.py`` exists to stop.

Every failure leg here is paired with a healthy control, because a leg that
only ever sees the broken input cannot show that it discriminates.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from firm.cli.init import run_init
from firm.services import base_domain
from firm.sysconfig import service as sysconfig_service

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The real resolver, captured at import time. conftest's autouse fixture
#: replaces the module attribute with a stub for every test, so a test that
#: needs to exercise the actual body has to hold its own reference.
REAL_WHICH_BASE = sysconfig_service.which_base

#: The one line that shells out to `base scaffold`. Any file carrying it is
#: running the scaffold itself rather than asking the one owner to.
SCAFFOLD_FINGERPRINT = '"scaffold", str(workspace)'


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Recorder:
    """Stands in for subprocess.run and remembers the base calls."""

    def __init__(self, returncode=0, stderr=""):
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        return _Proc(returncode=self.returncode, stderr=self.stderr)

    @property
    def scaffolds(self) -> list[list[str]]:
        return [c for c in self.calls if len(c) > 1 and c[1] == "scaffold"]


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    return w


@pytest.fixture
def fake_base(monkeypatch):
    """base is present. The autouse fixture in conftest says absent by default."""
    monkeypatch.setattr(sysconfig_service, "which_base", lambda: "/fake/base")


def _sync_returns(monkeypatch, result):
    seen: list[dict] = []

    def _sync(workspace, firm_id, *, conn=None, proposal=None):
        seen.append({"workspace": workspace, "firm_id": firm_id,
                     "conn": conn, "proposal": proposal})
        return result

    monkeypatch.setattr(base_domain, "sync", _sync)
    return seen


# ---------------------------------------------------------------------------
# F4 — the tier itself
# ---------------------------------------------------------------------------

def test_init_scaffolds_the_base_tier(monkeypatch, ws, capsys, fake_base):
    """The whole of F4: without this call there is no `.base/` to learn into."""
    rec = _Recorder()
    monkeypatch.setattr(subprocess, "run", rec)

    assert run_init(ws) == 0
    out = capsys.readouterr().out

    assert rec.scaffolds, "init never ran `base scaffold`, which is the defect"
    assert rec.scaffolds[0][:2] == ["/fake/base", "scaffold"]
    assert rec.scaffolds[0][2] == str(ws), "scaffolded some other directory"
    assert ".base/ scaffolded" in out


def test_base_absent_is_skipped_not_failed(monkeypatch, ws, capsys):
    """The control on the leg above, and the contract the codebase keeps.

    A licensee may not carry base. A firm without it is degraded, never broken,
    so init must still succeed and must say which of the two happened.
    """
    rec = _Recorder()
    monkeypatch.setattr(subprocess, "run", rec)
    monkeypatch.setattr(sysconfig_service, "which_base", lambda: None)

    assert run_init(ws) == 0, "a host without base is not a broken firm"
    out = capsys.readouterr().out

    assert not rec.scaffolds
    assert "skipped" in out
    assert "not installed" in out


def test_a_failed_scaffold_is_reported_rather_than_swallowed(
        monkeypatch, ws, capsys, fake_base):
    """A silent failure here is how a firm ships believing it has a memory."""
    monkeypatch.setattr(subprocess, "run", _Recorder(returncode=3, stderr="disk full"))

    assert run_init(ws) == 0, "a degraded tier does not fail the init"
    out = capsys.readouterr().out

    assert "exited 3" in out
    assert ".base/ scaffolded" not in out, "claimed a tier it did not create"


# ---------------------------------------------------------------------------
# F5 — the domain and its first rule
# ---------------------------------------------------------------------------

def test_demo_init_wires_the_domain_and_seeds_its_rule(
        monkeypatch, ws, capsys, fake_base):
    """The whole of F5. `--demo` seeds firm `demo`, so there is one to wire."""
    monkeypatch.setattr(subprocess, "run", _Recorder())
    seen = _sync_returns(monkeypatch, {"ok": True, "rule_seeded": True,
                                       "keywords": ["demo"]})

    assert run_init(ws, demo=True) == 0
    out = capsys.readouterr().out

    assert len(seen) == 1, f"sync called {len(seen)} times, expected once"
    assert seen[0]["firm_id"] == "demo"
    assert seen[0]["conn"] is not None, (
        "sync was handed no connection, so its triggers come from the firm id "
        "alone and the firm reads stale against its own roster on day one")
    assert "'demo'" in out and "seeded" in out


def test_a_workspace_with_no_firm_wires_no_domain(
        monkeypatch, ws, capsys, fake_base):
    """The control on the leg above: a bare init has no firm to write for.

    Syncing with an empty firm id would write a domain block named "" — a wire
    to nothing that every later check has to special-case.
    """
    monkeypatch.setattr(subprocess, "run", _Recorder())
    seen = _sync_returns(monkeypatch, {"ok": True, "rule_seeded": True})

    assert run_init(ws) == 0
    out = capsys.readouterr().out

    assert seen == [], "wrote a domain for a workspace that holds no firm"
    assert "no firm here yet" in out
    assert ".base/ scaffolded" in out, "the tier is still created — F4 is not F5"


def test_a_domain_with_no_rules_names_the_repair(
        monkeypatch, ws, capsys, fake_base):
    """base drops a matched domain carrying zero rules, silently and totally.

    So "written" is not "live", and a report with no route just gets retried.
    """
    monkeypatch.setattr(subprocess, "run", _Recorder())
    _sync_returns(monkeypatch, {"ok": True, "rule_seeded": False,
                                "keywords": ["demo"]})

    assert run_init(ws, demo=True) == 0
    out = capsys.readouterr().out

    assert "no rules" in out
    assert "doctor --fix" in out, "a failure with no route is a failure retried"


def test_a_domain_that_was_never_written_says_why(
        monkeypatch, ws, capsys, fake_base):
    monkeypatch.setattr(subprocess, "run", _Recorder())
    _sync_returns(monkeypatch, {"ok": False, "reason": "no .base/domains.toml"})

    assert run_init(ws, demo=True) == 0
    out = capsys.readouterr().out

    assert "no .base/domains.toml" in out


# ---------------------------------------------------------------------------
# The property that keeps the fix from being undone by a copy
# ---------------------------------------------------------------------------

def test_only_one_tracked_file_runs_base_scaffold():
    """One owner for the scaffold, the same rule `_base_env` now lives under.

    F4 existed because this call sat private in the dashboard where the command
    line could not reach it. The repair is worthless if the command line grows
    its own copy instead, so this fails the build when a second appears.

    Enumerated with `git ls-files`, never a filesystem walk: a tree where a
    wheel has been built carries a whole second copy of the package under
    `build/`, untracked and shipped nowhere.
    """
    done = subprocess.run(["git", "ls-files", "--", "src"],
                          capture_output=True, text=True, cwd=str(REPO_ROOT))
    if done.returncode != 0:
        pytest.skip("not a git checkout")

    carriers = [
        rel for rel in done.stdout.split()
        if rel.endswith(".py")
        and SCAFFOLD_FINGERPRINT in (REPO_ROOT / rel).read_text(encoding="utf-8")
    ]
    assert carriers == ["src/firm/services/base_domain.py"], (
        f"`base scaffold` is run from more than one place: {carriers}. The "
        "second copy is the one that will not get the next fix.")


def test_the_switch_crosses_a_process_boundary(tmp_path):
    """The half an in-process monkeypatch cannot reach, proved by exec.

    Nine test files run the CLI for real with ``sys.executable -m firm``. A
    monkeypatch does not exist in that child, so the child resolved the
    developer's own base and wrote the operator's global workspace registry --
    measured 2026-09-10, `test_doctor_survives_a_pipe` put its pytest temp path
    there, and those entries are then synced into the CLAUDE.md loaded by every
    session the operator starts.

    BASE_HOME alone does not close it. On WSL the binary that gets resolved is
    the WINDOWS one across /mnt/c, and a POSIX BASE_HOME neither redirected its
    write nor tripped base's own isolation panic: the entry landed anyway.
    Only refusing to resolve a binary at all reliably stops a child.

    The second arm is the control. It puts a FAKE base on the child's PATH and
    leaves the switch off, so the child must find it -- which proves the None
    above comes from the switch rather than from a host that simply has no
    base. No real base is ever executed by either arm.
    """
    probe = ("from firm.sysconfig.service import which_base; "
             "print('RESOLVED=' + str(which_base()))")

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    env["CADRE_NO_BASE"] = "1"
    off = subprocess.run([sys.executable, "-c", probe],
                         capture_output=True, text=True, env=env, timeout=120)
    assert off.returncode == 0, off.stderr
    assert "RESOLVED=None" in off.stdout, (
        f"a child process still resolved a base binary: {off.stdout.strip()}")

    fake_dir = tmp_path / "fakebin"
    fake_dir.mkdir()
    fake = fake_dir / ("base.exe" if os.name == "nt" else "base")
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)

    env["CADRE_NO_BASE"] = ""
    env["PATH"] = str(fake_dir) + os.pathsep + env.get("PATH", "")
    on = subprocess.run([sys.executable, "-c", probe],
                        capture_output=True, text=True, env=env, timeout=120)
    assert on.returncode == 0, on.stderr
    assert "RESOLVED=None" not in on.stdout, (
        "the control arm found nothing either, so the first arm proves nothing "
        "about the switch")
    assert str(fake_dir) in on.stdout, on.stdout


def test_an_empty_switch_is_not_a_set_switch(monkeypatch, tmp_path):
    """Whitespace or "" must read as absent, not as "yes, disable it".

    A .env carried into CI commonly sets a variable to the empty string. If
    that read as "disable", base would be silently off on a machine that has
    it, and every degraded-firm message would be a lie about the host.
    """
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    fake = fake_dir / ("base.exe" if os.name == "nt" else "base")
    fake.write_text("", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_dir) + os.pathsep + os.environ["PATH"])

    # The autouse fixture sets the switch; unset it to exercise the real body.
    monkeypatch.delenv("CADRE_NO_BASE", raising=False)
    assert REAL_WHICH_BASE() is not None, "no fake base on PATH"

    monkeypatch.setenv("CADRE_NO_BASE", "   ")
    assert REAL_WHICH_BASE() is not None, (
        "blank read as set, so a stray empty value silently disables base")

    monkeypatch.setenv("CADRE_NO_BASE", "1")
    assert REAL_WHICH_BASE() is None


def test_a_suppressed_base_does_not_read_as_a_missing_one(monkeypatch):
    """An operator must be able to tell a broken install from a set variable.

    Both produce the same None from `which_base()`. Reporting both as "not
    installed" makes a firm's own degraded message a lie about the host, and
    whoever set the variable weeks ago cannot tell which they are looking at.
    That is a fresh silent-success shape, which is the fault family this
    codebase spent the day deleting.
    """
    monkeypatch.setenv("CADRE_NO_BASE", "1")
    suppressed = sysconfig_service.base_absence_reason()

    monkeypatch.delenv("CADRE_NO_BASE", raising=False)
    missing = sysconfig_service.base_absence_reason()

    assert suppressed != missing, "the two states are indistinguishable"
    assert "CADRE_NO_BASE" in suppressed, (
        "the message does not name the variable, so it does not say what to "
        f"change: {suppressed!r}")
    assert "CADRE_NO_BASE" not in missing


def test_both_absence_messages_keep_the_skip_substring():
    """The substring is load-bearing, not phrasing.

    `firm extension install` branches on "not installed" in the reason to mean
    "skip, do not fail", and keeps rc 0 there. A suppressed base is equally a
    skip. Reword either sentence without that substring and a host-setup fact
    turns into a failing exit code.
    """
    import os as _os

    before = _os.environ.get("CADRE_NO_BASE")
    try:
        _os.environ["CADRE_NO_BASE"] = "1"
        assert "not installed" in sysconfig_service.base_absence_reason()
        _os.environ.pop("CADRE_NO_BASE")
        assert "not installed" in sysconfig_service.base_absence_reason()
    finally:
        if before is None:
            _os.environ.pop("CADRE_NO_BASE", None)
        else:
            _os.environ["CADRE_NO_BASE"] = before


def test_init_says_the_switch_is_why_base_was_skipped(monkeypatch, ws, capsys):
    """The report an operator actually reads, not just the helper behind it."""
    monkeypatch.setenv("CADRE_NO_BASE", "1")
    monkeypatch.setattr(sysconfig_service, "which_base", lambda: None)

    assert run_init(ws) == 0
    out = capsys.readouterr().out

    assert "CADRE_NO_BASE" in out, (
        "init reported base missing without saying a variable suppressed it")


def test_doctor_names_the_switch_rather_than_blaming_the_install(monkeypatch,
                                                                 tmp_path):
    """`doctor` is where an operator goes when something looks wrong.

    `assess` is the line it prints for the firm's base domain, and it used
    to say "base absent" for three different situations: base missing, base
    suppressed, and base present but printing something unparseable. Someone
    who set CADRE_NO_BASE reads "absent" and goes debugging an install that is
    fine.

    `rule_count` is stubbed to None because all three situations reach this
    line through it; the point under test is which of them the message names.
    """
    from firm.services import base_domain

    ws = tmp_path / "firm"
    (ws / ".base").mkdir(parents=True)

    # A block that is genuinely current, so `assess` reaches the rule-count
    # branch instead of returning "stale" earlier. `have` is rebuilt as
    # BEGIN + <between> + END, so an empty between and a render that returns
    # the same pair is the smallest input that gets past that check.
    block = base_domain.BEGIN + base_domain.END
    (ws / ".base" / "domains.toml").write_text(block, encoding="utf-8")
    monkeypatch.setattr(base_domain, "render", lambda *a, **k: block)
    monkeypatch.setattr(base_domain, "rule_count", lambda *a, **k: None)

    monkeypatch.setenv("CADRE_NO_BASE", "1")
    monkeypatch.setattr(sysconfig_service, "which_base", lambda: None)
    _, suppressed = base_domain.assess(ws, "demo", None)

    monkeypatch.delenv("CADRE_NO_BASE", raising=False)
    _, missing = base_domain.assess(ws, "demo", None)

    monkeypatch.setattr(sysconfig_service, "which_base", lambda: "/fake/base")
    _, unparseable = base_domain.assess(ws, "demo", None)

    assert "CADRE_NO_BASE" in suppressed, suppressed
    assert "CADRE_NO_BASE" not in missing, missing
    assert len({suppressed, missing, unparseable}) == 3, (
        "three different situations still produce fewer than three messages: "
        f"{suppressed!r} / {missing!r} / {unparseable!r}")
    assert "not recognised" in unparseable, (
        "a base that is present but unparseable is reported as absent")


def test_the_suite_does_not_reach_the_machines_real_base():
    """The control on conftest's autouse fixture, which this file depends on.

    Without it `run_init` shells out to whatever base the host carries and
    registers every pytest temp directory in the operator's global workspace
    registry. Measured 2026-09-10: four junk entries from one run of
    tests/test_init.py, and on WSL they landed in the WINDOWS global tier
    because `which_base()` resolved across /mnt/c.
    """
    assert sysconfig_service.which_base() is None, (
        "a test can see the host's real base binary, so the suite writes the "
        "operator's machine")

    # And the same for anything this test spawns. The check above only covers
    # this interpreter; without the environment variable actually being set in
    # conftest, every subprocess in the suite goes back to resolving the real
    # binary and the in-process half above stays green while it happens.
    assert os.environ.get("CADRE_NO_BASE", "").strip(), (
        "conftest no longer sets CADRE_NO_BASE, so child processes can reach "
        "the host's base again — the in-process stub does not cross exec")
