"""A timer says which command installed it, and doctor names the ones that cannot (#158).

`heartbeat enable` writes ``--source heartbeat`` into the argv it installs
(``cli/heartbeat.py:200``), so a timer installed from PR B onwards labels its own
pulses. A timer installed BEFORE that carries no flag, records ``unset``
(``services/pulse_ledger.py:43``) -- the true answer for it -- and nothing tells
the operator that running ``cadre heartbeat enable`` again would fix it.

THE LABEL, NEVER THE ARGV, and the reason is measured rather than stylistic.
``systemd`` flattens the argv with a space join (``sched/systemd.py:37``) and
writes it as ``ExecStart=``, so a list handed back from that line is a GUESS the
moment any element contains a space. The label survives the same join exactly,
because every label is one token. Windows already records the argv as a list
(``sched/winlaunch.py:110``) and launchd keeps one in ``ProgramArguments``
(``sched/launchd.py:58``), so the narrow question is the only one all three
answer without guessing. The scheduler contract's rule stands: a key the platform
cannot answer is absent, never guessed.

THREE STATES, AND THE THIRD IS THE POINT (osprey's condition 1). ``source`` is
**absent** from ``status()`` when the stored command cannot be read at all,
**None** when it is read and carries no flag, and **the label** when it does.
Absent and None are different findings: one is "this timer has no label" and the
other is "I could not tell", and a card that collapses them reports clean over a
reader that could not see (law 48).

THE CARD IS ROUTED TO THE OPERATOR, AND THAT IS A RULING, NOT A DEFAULT.
``doctor.py:604`` makes ``--fix`` select on ``route == "mechanical"`` and
``fix()`` then dispatches per key, so ``mechanical`` is a promise that ``--fix``
performs the repair. Re-running ``enable`` rewrites a LIVE timer
(``systemctl enable --now``, ``schtasks /Create``), which the doctor has never
done, and on Windows that is issue #147 -- enable over a running heartbeat
re-creates the task and leaves the old pulse running. A ``--fix`` that can leave
two pulses running is worse than a card that prints one command. `R_FIX` is the
leg that PINS that ruling rather than describing it.

THE FENCE, COPIED AND NOT REFERENCED (osprey's condition 5). Every child-process
leg here sets ``HOME`` **and** ``USERPROFILE`` and drops
``HOMEDRIVE``/``HOMEPATH``, because ``ntpath.expanduser`` reads ``USERPROFILE``
first and ignores ``HOME`` while ``posixpath.expanduser`` reads ``HOME``.
PR C paid a red Windows CI run to learn that, and the control leg is copied into
this file rather than imported so that THIS file proves its own fence.
"""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

import firm.cli.heartbeat as hb
import firm.sched.base as schedbase
import firm.sched.launchd as launchd_mod
import firm.sched.systemd as systemd_mod
import firm.sched.winsched as winsched_mod
from firm.cli import doctor as doctor_mod
from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.sched.launchd import LaunchdScheduler
from firm.sched.systemd import SystemdScheduler
from firm.sched.winsched import WindowsScheduler

FIRM = "labelco"
CARD = "timer-source"
STEM = f"{hb._UNIT_PREFIX}{FIRM}"

#: The argv `heartbeat enable` installs today, as `run_enable` builds it.
LABELLED = ["/usr/bin/python3", "-m", "firm", "pulse", "--workspace",
            "/srv/firms/labelco", "--source", "heartbeat"]
#: The same command as a timer installed before the flag existed carries it.
UNLABELLED = ["/usr/bin/python3", "-m", "firm", "pulse", "--workspace",
              "/srv/firms/labelco"]


# ---------------------------------------------------------------------------
# the fence (osprey's condition 5), copied from PR C, not imported
# ---------------------------------------------------------------------------

def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".config" / "systemd" / "user").mkdir(parents=True)
    return home


def _child_env_for(home: Path) -> dict[str, str]:
    """The environment every child in this file runs with.

    One producer, so the fence the control proves is the fence the legs use.
    BOTH variables, because the two platforms read different ones: Windows reads
    USERPROFILE (then HOMEDRIVE+HOMEPATH) and IGNORES HOME; POSIX reads HOME.
    `default_unit_dir()` is `Path.home()/.config/systemd/user`, so a fence that
    sets only HOME leaves the child resolving the operator's real profile.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(hb.__file__).resolve().parents[2])
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env.pop("HOMEDRIVE", None)            # so the fallback cannot fire either
    env.pop("HOMEPATH", None)
    env["CADRE_SCHEDULER"] = "systemd"
    env["CADRE_CLAUDE_BIN"] = sys.executable
    env.pop("CADRE_DB_URL", None)
    env.pop("FIRM_ID", None)
    return env


def test_S0_control_the_child_really_lives_in_the_temp_home(tmp_path):
    """The fence, proved rather than trusted -- copied from PR C on purpose.

    A fence that is merely SET is not a fence that WORKS: setting HOME alone did
    nothing on Windows and four of PR C's legs failed in CI for a reason no Linux
    run could show. This file inherits the fence, so it inherits the obligation
    to prove it rather than to cite someone else's proof.
    """
    home = _home(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-c", "from pathlib import Path; print(Path.home())"],
        capture_output=True, env=_child_env_for(home), timeout=120,
        stdin=subprocess.DEVNULL)
    answered = proc.stdout.decode("utf-8", "replace").strip()
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert Path(answered) == home, (
        "the child resolved a different home, so every timer leg would look "
        "somewhere the fixture never wrote", answered, str(home))


# ---------------------------------------------------------------------------
# the parser (osprey's condition 2): ONE function, BOTH shapes
# ---------------------------------------------------------------------------

def test_P1_the_parser_reads_the_separated_shape():
    assert schedbase.source_label(LABELLED) == "heartbeat"


def test_P2_the_parser_reads_the_joined_shape():
    """`--source=heartbeat`, which argparse accepts and nothing here writes.

    Nothing in the tree installs this shape today, and that is exactly why it is
    pinned: a parser that only reads what we currently write is a parser that
    breaks silently the first time anything writes the other one.
    """
    joined = [*UNLABELLED, "--source=heartbeat"]
    assert schedbase.source_label(joined) == "heartbeat"


def test_P3_a_command_with_no_flag_reads_None_not_empty():
    """None, never "" and never "unset".

    `unset` is the LEDGER's word for a pulse that passed no flag. Returning it
    here would make the scheduler answer a question about the ledger, and the
    two would drift the moment either changed its wording.
    """
    assert schedbase.source_label(UNLABELLED) is None


def test_P4_the_label_is_reported_verbatim_not_validated():
    """The doctor reports what is STORED; the pulse refuses what is invalid.

    `cadre pulse --source` has its own `choices`, so a bad label is refused at
    run time by the thing that acts on it. A parser that also validated would
    report None for a timer that really does carry a label, and the card would
    then tell the operator to re-enable a timer that is already labelled.
    """
    odd = [*UNLABELLED, "--source", "not-a-real-source"]
    assert schedbase.source_label(odd) == "not-a-real-source"


def test_P5_a_trailing_flag_with_no_value_reads_None():
    """`--source` as the last token, with nothing after it.

    Hostile input, law 49: the guard is only tested if something supplies a
    command that is malformed rather than merely unlabelled. An IndexError here
    would crash `status()`, which every doctor card and `heartbeat status` call.
    """
    assert schedbase.source_label([*UNLABELLED, "--source"]) is None


# ---------------------------------------------------------------------------
# the three states, per backend (osprey's condition 1)
# ---------------------------------------------------------------------------

def _systemd_units(unit_dir: Path, ws: Path, argv: list[str] | None,
                   interval: str = "30m", stem: str = STEM) -> None:
    """A seeded systemd timer, in the two files `status()` reads.

    SEEDED, NOT ENABLED, and the sentence belongs at the fixture: `enable` runs
    `systemctl enable --now`, and `systemctl --user` talks to the REAL user
    manager whatever the unit directory says. A leg that enabled would install
    into the operator's own systemd.
    """
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / f"{stem}.timer").write_text(
        f"[Timer]\nOnUnitActiveSec={interval}\n", encoding="utf-8")
    body = f"[Service]\nWorkingDirectory={ws}\n"
    if argv is not None:
        body += "ExecStart=" + " ".join(argv) + "\n"
    (unit_dir / f"{stem}.service").write_text(body, encoding="utf-8")


@pytest.fixture
def quiet_ctl(monkeypatch):
    """`systemctl`/`launchctl` answer nothing, so these legs read FILES only."""
    for mod in (systemd_mod, launchd_mod):
        monkeypatch.setattr(mod, "run_cmd", lambda a, timeout=30: (1, ""))
    return None


def test_B1_systemd_reports_the_label_its_ExecStart_carries(tmp_path, quiet_ctl):
    units = tmp_path / "units"
    _systemd_units(units, tmp_path / "ws", LABELLED)
    st = SystemdScheduler(unit_dir=units).status(STEM)
    assert "source" in st, ("the KEY, not only its value: absent and None "
                            "serialize differently", st)
    assert st["source"] == "heartbeat", st


def test_B2_systemd_reports_None_when_the_command_carries_no_flag(
        tmp_path, quiet_ctl):
    units = tmp_path / "units"
    _systemd_units(units, tmp_path / "ws", UNLABELLED)
    st = SystemdScheduler(unit_dir=units).status(STEM)
    assert "source" in st, ("read and unlabelled is an ANSWER, not a silence", st)
    assert st["source"] is None, st


def test_B3_systemd_omits_source_when_there_is_no_command_to_read(
        tmp_path, quiet_ctl):
    """ABSENT, not None. The .service has no ExecStart at all.

    This is the state the whole three-state design exists for. Reporting None
    here would tell the card "this timer has no label", and the card would tell
    the operator to re-enable a timer whose command it never managed to read.
    THE POSITIVE SIBLING IS IN THIS LEG ON PURPOSE. Asserting only that the key
    is absent makes this leg GREEN against an implementation that answers
    nothing at all -- it was green at the base sha for exactly that reason, and
    a leg green before the build measures nothing. Carrying the readable case in
    the same run means it can only pass when `source` exists AND the two states
    are told apart, which is the whole design.
    """
    units = tmp_path / "units"
    readable = tmp_path / "readable"
    _systemd_units(readable, tmp_path / "ws", UNLABELLED)
    blind = tmp_path / "blind"
    _systemd_units(blind, tmp_path / "ws", None)

    read = SystemdScheduler(unit_dir=readable).status(STEM)
    unread = SystemdScheduler(unit_dir=blind).status(STEM)

    assert "source" in read and read["source"] is None, read
    assert "source" not in unread, unread


def _launchd_plist(agent_dir: Path, ws: Path, argv: list[str] | None) -> None:
    agent_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {"Label": STEM, "WorkingDirectory": str(ws),
                     "StartInterval": 1800}
    if argv is not None:
        payload["ProgramArguments"] = list(argv)
    (agent_dir / f"{STEM}.plist").write_bytes(plistlib.dumps(payload))


def test_B4_launchd_reports_the_label_from_ProgramArguments(
        tmp_path, quiet_ctl):
    agents = tmp_path / "agents"
    _launchd_plist(agents, tmp_path / "ws", LABELLED)
    st = LaunchdScheduler(agent_dir=agents).status(STEM)
    assert "source" in st, st
    assert st["source"] == "heartbeat", st


def test_B5_launchd_reports_None_when_the_arguments_carry_no_flag(
        tmp_path, quiet_ctl):
    agents = tmp_path / "agents"
    _launchd_plist(agents, tmp_path / "ws", UNLABELLED)
    st = LaunchdScheduler(agent_dir=agents).status(STEM)
    assert "source" in st and st["source"] is None, st


def test_B6_launchd_omits_source_when_the_plist_records_no_arguments(
        tmp_path, quiet_ctl):
    agents = tmp_path / "agents"
    readable = tmp_path / "readable"
    _launchd_plist(readable, tmp_path / "ws", UNLABELLED)
    blind = tmp_path / "blind"
    _launchd_plist(blind, tmp_path / "ws", None)

    read = LaunchdScheduler(agent_dir=readable).status(STEM)
    unread = LaunchdScheduler(agent_dir=blind).status(STEM)

    assert "source" in read and read["source"] is None, read
    assert "source" not in unread, unread


@pytest.fixture
def schtasks_says_installed(monkeypatch):
    """`schtasks /Query` answers for a task that exists, and nothing else runs.

    Mocked at `run_cmd` the way `tests/test_sched.py` drives this backend, so
    these legs measure the SPEC READ rather than the host's task scheduler.
    """
    def fake(argv, timeout=30):
        if argv and argv[0] == "schtasks":
            return 0, "Status:  Ready\nNext Run Time: 1/1/2030 9:00:00 AM\n"
        return 1, ""
    monkeypatch.setattr(winsched_mod, "run_cmd", fake)
    return None


def _win_spec(launcher_dir: Path, ws: Path, argv: list[str] | None) -> None:
    launcher_dir.mkdir(parents=True, exist_ok=True)
    spec: dict = {"stem": STEM, "env": {}, "cwd": str(ws), "supervise": False,
                  "interval": "30m"}
    if argv is not None:
        spec["argv"] = [str(a) for a in argv]
    (launcher_dir / f"{STEM}.json").write_text(
        json.dumps(spec, indent=2) + "\n", encoding="utf-8")


def test_B7_windows_reports_the_label_from_the_spec_it_already_writes(
        tmp_path, schtasks_says_installed):
    """The spec ALREADY records argv (`winlaunch.py:110`); nothing new is stored.

    `status()` reads `cwd` and `interval` out of this same dict and simply never
    read `argv`. On this backend the change is a read, not a new field.
    """
    launchers = tmp_path / "launchers"
    _win_spec(launchers, tmp_path / "ws", LABELLED)
    st = WindowsScheduler(launcher_dir=launchers).status(STEM)
    assert "source" in st, st
    assert st["source"] == "heartbeat", st


def test_B8_windows_reports_None_when_the_spec_carries_no_flag(
        tmp_path, schtasks_says_installed):
    launchers = tmp_path / "launchers"
    _win_spec(launchers, tmp_path / "ws", UNLABELLED)
    st = WindowsScheduler(launcher_dir=launchers).status(STEM)
    assert "source" in st and st["source"] is None, st


def test_B9_windows_omits_source_when_the_spec_records_no_argv(
        tmp_path, schtasks_says_installed):
    launchers = tmp_path / "launchers"
    readable = tmp_path / "readable"
    _win_spec(readable, tmp_path / "ws", UNLABELLED)
    blind = tmp_path / "blind"
    _win_spec(blind, tmp_path / "ws", None)

    read = WindowsScheduler(launcher_dir=readable).status(STEM)
    unread = WindowsScheduler(launcher_dir=blind).status(STEM)

    assert "source" in read and read["source"] is None, read
    assert "source" not in unread, unread


# ---------------------------------------------------------------------------
# the doctor card (osprey's conditions 1 and 2)
# ---------------------------------------------------------------------------

def _firm_at(root: Path, interval: str | None = "30m") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    conn = connect(get_db_path(root))
    try:
        apply_migrations(conn)
        create(conn, "firm", {"id": FIRM, "name": "Label Co"})
        if interval is not None:
            conn.execute("UPDATE firm SET pulse_interval = ? WHERE id = ?",
                         (interval, FIRM))
            conn.commit()
    finally:
        conn.close()
    return root


def _card(ws: Path, unit_dir: Path) -> dict:
    cards = [c for c in doctor_mod.diagnose(ws, FIRM, unit_dir=unit_dir)
             if c["key"] == CARD]
    assert len(cards) == 1, (
        f"expected one doctor card keyed {CARD!r}, found {len(cards)}")
    return cards[0]


def test_R1_the_card_names_a_timer_whose_command_carries_no_label(
        tmp_path, quiet_ctl):
    ws = _firm_at(tmp_path / "ws")
    units = tmp_path / "units"
    _systemd_units(units, ws, UNLABELLED)

    card = _card(ws, units)

    assert card["ok"] is False, card
    assert card["route"] == "operator", (
        "mechanical would promise a repair --fix must not perform", card)
    assert "unset" in card["detail"], (
        "the operator needs the ledger's own word to connect the two", card)


def test_R2_the_card_prints_the_command_with_the_firms_own_values(
        tmp_path, quiet_ctl):
    """A route with no command is the #28 complaint; this route HAS one.

    The command has to carry this firm's workspace, id and interval, because a
    generic `cadre heartbeat enable` would install a timer with the default
    cadence over one the Board chose.
    """
    ws = _firm_at(tmp_path / "ws", interval="45m")
    units = tmp_path / "units"
    _systemd_units(units, ws, UNLABELLED, interval="45m")

    said = _card(ws, units)["fix"]

    assert "heartbeat enable" in said, said
    assert str(ws) in said and FIRM in said and "45m" in said, said


def test_R3_control_a_timer_that_already_carries_a_label_is_not_named(
        tmp_path, quiet_ctl):
    """The control law 25 asks for: the leg that separates repaired from noisy.

    A card that always failed would pass R1 and fail this.
    """
    ws = _firm_at(tmp_path / "ws")
    units = tmp_path / "units"
    _systemd_units(units, ws, LABELLED)

    card = _card(ws, units)

    assert card["ok"] is True, card
    assert "heartbeat" in card["detail"], ("it names the label it found", card)


def test_R4_a_command_it_could_not_read_is_UNDETERMINABLE_not_a_finding(
        tmp_path, quiet_ctl):
    """Law 48: a reader that cannot see does not report clean -- and does not
    report the OTHER answer either.

    `ok` is a bool and a bool has two values, so a check that could not
    establish its answer still had to pick one. `state` carries the third.
    Reporting this as the named case would send the operator to re-enable a
    timer whose command the doctor never read.
    """
    ws = _firm_at(tmp_path / "ws")
    units = tmp_path / "units"
    _systemd_units(units, ws, None)

    card = _card(ws, units)

    assert card["state"] == "undeterminable", card
    assert card["ok"] is False, card
    assert "could not" in card["detail"].lower(), (
        "it says WHAT it could not read", card)


def test_R5_no_timer_installed_is_nothing_to_label_not_a_finding(
        tmp_path, quiet_ctl):
    """The `schedule` card owns "there is no timer". This one must not
    duplicate it: two cards for one condition is two findings an operator has
    to reconcile."""
    ws = _firm_at(tmp_path / "ws")
    card = _card(ws, tmp_path / "empty-units")
    assert card["ok"] is True, card


# ---------------------------------------------------------------------------
# the ruling, pinned (osprey's condition 3)
# ---------------------------------------------------------------------------

def test_R_FIX_doctor_fix_never_touches_the_timer(tmp_path, quiet_ctl,
                                                  monkeypatch):
    """THE LEG THAT PINS RULING (b) RATHER THAN DESCRIBING IT.

    `--fix` selects on `route == "mechanical"` and dispatches per key, so
    routing this card mechanical would be a promise. Re-running `enable`
    rewrites a live timer -- `systemctl enable --now`, `schtasks /Create` -- and
    on Windows that is #147: enable over a running heartbeat re-creates the task
    and leaves the old pulse running. A `--fix` that can leave two pulses
    running is worse than a card that prints one command.

    So this asserts the NEGATIVE, by count and not by outcome: zero calls to
    either door into installing a timer, and the card still failed afterwards.
    Asserting only that the card is still failed would pass if `fix()` re-ran
    enable and the re-run silently failed.
    """
    ws = _firm_at(tmp_path / "ws")
    units = tmp_path / "units"
    _systemd_units(units, ws, UNLABELLED)

    called: list[str] = []
    monkeypatch.setattr(hb, "run_enable",
                        lambda *a, **k: called.append("run_enable") or 0)
    monkeypatch.setattr(systemd_mod.SystemdScheduler, "install_timer",
                        lambda *a, **k: called.append("install_timer") or {})

    card = _card(ws, units)
    assert card["ok"] is False, ("the card must be failed for this to mean "
                                 "anything", card)

    doctor_mod.fix(ws, FIRM, [card], unit_dir=units)

    assert called == [], (
        "doctor --fix rewrote a live timer; that is ruling (b) broken", called)
    assert _card(ws, units)["ok"] is False, (
        "the card must still be failed: nothing repaired it")


# ---------------------------------------------------------------------------
# heartbeat status carries it (osprey's condition 4)
# ---------------------------------------------------------------------------

def test_R6_heartbeat_status_carries_the_label_for_each_timer(tmp_path):
    """The CLI, the doctor and the ledger say one thing.

    Driven as a CHILD PROCESS, because that is the channel a caller reads, and
    with the two-variable fence above -- these entries come from
    `default_unit_dir()`, which is `Path.home()`-derived.
    """
    home = _home(tmp_path)
    units = home / ".config" / "systemd" / "user"
    ws = _firm_at(tmp_path / "ws")
    _systemd_units(units, ws, UNLABELLED)

    proc = subprocess.run(
        [sys.executable, "-m", "firm", "heartbeat", "status"],
        capture_output=True, env=_child_env_for(home), timeout=180,
        stdin=subprocess.DEVNULL)
    payload = json.loads(proc.stdout.decode("utf-8", "replace"))

    entries = payload["heartbeats"]
    assert len(entries) == 1, payload
    assert "source" in entries[0], (
        "the entry copies the key the scheduler answered", payload)
    assert entries[0]["source"] is None, payload


def test_R7_a_status_entry_gains_no_EMPTY_source_when_there_was_none(tmp_path):
    """ABSENT STAYS ABSENT all the way out to the CLI (avocet's binding P-row).

    R6 pins the None case. This pins the other one, and the pair is the whole
    design: a build that collapses absent into None passes every value-only
    check and still tells the operator that a timer it could not read is a
    timer with no label. The two serialize differently -- `"source": null`
    against no key at all -- so the assertion is on the KEY.
    """
    home = _home(tmp_path)
    units = home / ".config" / "systemd" / "user"
    ws = _firm_at(tmp_path / "ws")
    # TWO firms, ONE status call: the discriminating pair. A run that shows
    # only the unreadable one cannot tell "absent" from "not implemented".
    _systemd_units(units, ws, UNLABELLED, stem=f"{hb._UNIT_PREFIX}readco")
    _systemd_units(units, ws, None, stem=f"{hb._UNIT_PREFIX}blindco")

    proc = subprocess.run(
        [sys.executable, "-m", "firm", "heartbeat", "status"],
        capture_output=True, env=_child_env_for(home), timeout=180,
        stdin=subprocess.DEVNULL)
    payload = json.loads(proc.stdout.decode("utf-8", "replace"))

    by_firm = {e["firm_id"]: e for e in payload["heartbeats"]}
    assert set(by_firm) == {"readco", "blindco"}, payload
    assert "source" in by_firm["readco"], (
        "the readable one answers, so the key is there", payload)
    assert by_firm["readco"]["source"] is None, payload
    assert "source" not in by_firm["blindco"], (
        "an entry the scheduler could not answer must carry no key, never an "
        "empty one -- st.get(k) here would invent a null, which is exactly "
        "what the `contained` tuple beside it already refuses to do", payload)


def _doctor_report(home: Path, ws: Path) -> str:
    """`cadre doctor`'s PRINTED report, from a child, fenced.

    The printed report and `--json` are two surfaces, and osprey's finding was
    about the printed one: the card loop shows label, route and detail and never
    a card's `fix`. A leg that read `--json` here would pass while the operator
    running `cadre doctor` saw nothing.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "firm", "doctor",
         "--workspace", str(ws), "--firm-id", FIRM],
        capture_output=True, env=_child_env_for(home), timeout=180,
        stdin=subprocess.DEVNULL)
    return proc.stdout.decode("utf-8", "replace")


def test_R8_the_printed_report_carries_the_command_not_only_the_json(tmp_path):
    """The card's whole point is handing over one command, so it has to PRINT.

    `doctor.py`'s card loop prints `label`, `route` and `detail`. It has never
    printed `fix`, so a command that lives only there reaches `--json` readers
    and nobody else. 8b already had the answer: the detail carries the fact and
    the remedy together.

    The command must also carry THIS firm's values -- a generic
    `cadre heartbeat enable` would install the default cadence over the one the
    Board chose.
    """
    home = _home(tmp_path)
    ws = _firm_at(tmp_path / "ws", interval="45m")
    _systemd_units(home / ".config" / "systemd" / "user", ws, UNLABELLED,
                   interval="45m")

    report = _doctor_report(home, ws)

    assert "The timer says which command installed it" in report, report
    assert "cadre heartbeat enable" in report, (
        "the command reached --json and not the operator", report)
    assert str(ws) in report and FIRM in report and "45m" in report, (
        "the printed command must carry this firm's own values", report)


def test_R9_a_pre_flag_timer_is_not_told_to_install_base(tmp_path):
    """The footer printed base-domain's advice under EVERY operator finding.

    A timer installed before `--source` existed was read perfectly well. It is
    not undeterminable and it is not a missing base install, and sending that
    operator to install base points them at a failure that is not there. Wrong
    advice under a real finding is worse than no footer at all.
    """
    home = _home(tmp_path)
    ws = _firm_at(tmp_path / "ws")
    _systemd_units(home / ".config" / "systemd" / "user", ws, UNLABELLED)

    report = _doctor_report(home, ws)

    assert "install base" not in report, (
        "base-domain's advice printed under a timer finding", report)
    assert "operator findings: each card's detail says what to do" in report, (
        "and the operator still needs pointing at where the answer is", report)


def test_R10_control_a_base_domain_finding_still_gets_its_own_advice(
        tmp_path, capsys, monkeypatch):
    """Keying the sentence away is only HALF the change.

    A leg that checked only "the timer finding no longer says install base"
    would pass a footer that was deleted outright, and base-domain's operator
    would lose the one line that tells them what to do. So this drives the
    printer with the two cards side by side and asserts BOTH lines: the keyed
    one for base-domain, the generic one for the other.
    """
    ws = _firm_at(tmp_path / "ws")
    cards = [
        {"key": "base-domain", "label": "The firm's graph reaches its Members",
         "ok": False, "route": "operator", "detail": "could not read the tier",
         "state": "undeterminable", "fix": None},
        {"key": "timer-source",
         "label": "The timer says which command installed it",
         "ok": False, "route": "operator", "detail": "Run: cadre heartbeat enable",
         "state": "finding", "fix": "cadre heartbeat enable"},
    ]
    monkeypatch.setattr(doctor_mod, "diagnose", lambda *a, **k: cards)

    doctor_mod.run_doctor(ws, firm_id=FIRM)
    report = capsys.readouterr().out

    assert "install base" in report, (
        "base-domain's own advice must survive the keying", report)
    assert "operator findings: each card's detail says what to do" in report, (
        "and the other operator finding still needs its line", report)
