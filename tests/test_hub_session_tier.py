"""The hub's spawned Claude sessions must resolve the FIRM's tier. Issue #143.

The hub spawns four Claude sessions with `cwd=_framework_root()`. base runs
inside each of them as a hook and finds its WORKSPACE tier by walking up from
the directory the session runs in; `BASE_HOME` moves only the GLOBAL tier, not
that walk. On the operator's Windows machine `_framework_root()` is
`...\\cadre-win\\.venv\\Lib`, nothing above it holds a `.base`, and the walk lands
on the operator's OWN workspace graph. So every founding run, reshuffle, wiring
run and Board brief reads and writes the operator's personal tier as if it were
the firm's. Measured once doing real damage: 13,564 lines of an unrelated
extension's data and 116 of the operator's own domains written into a firm's
graph (#117).

WHAT THESE LEGS READ, and why they never compute it themselves. Each one
installs a fake runtime in place of `popen_utf8` that RECORDS the `argv`, `cwd`
and `env` production passed and then raises `OSError`, which every site already
handles. The assertion compares the recorded `cwd` against `base_cwd(...)` --
the production seam -- rather than against a path the test built. A test that
builds its own expected directory passes when both it and the code are wrong in
the same way, which is exactly the failure #136's R2-a shape exists to stop.

THE TWO SHAPES. `_run_brief` and `_run_wiring` are handed a `workspace`, so
their tier is the firm's. `_run_founding` and `_run_reshuffle` are NOT, and
founding runs BEFORE the firm exists -- there is no firm tier to point at, so
they get a scratch home under TEMP that is removed when the job finishes
(osprey's G0 verdict, ruling R-2).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from firm.services.base_domain import base_cwd

FIRM = "chrisai"


class _Recorder:
    """Stands in for `popen_utf8`, records the call, then refuses to spawn.

    Raising `OSError` is deliberate: every one of the four sites already
    handles it by finishing the job with an error, so the arm reaches its
    assertion without a real process, a real `claude.exe`, or a window.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": list(argv),
                           "cwd": kwargs.get("cwd"),
                           "env": dict(kwargs.get("env") or {})})
        raise OSError("the recorder never spawns")

    @property
    def one(self) -> dict:
        assert len(self.calls) == 1, (
            f"expected exactly one spawn, got {len(self.calls)}")
        return self.calls[0]


def _firm(tmp_path: Path, *, with_base: bool) -> Path:
    """A firm workspace, with or without a `.base` of its own."""
    ws = tmp_path / "firm"
    (ws / ".firm").mkdir(parents=True, exist_ok=True)
    if with_base:
        (ws / ".base").mkdir(exist_ok=True)
    return ws


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    for module in ("coboard", "founding", "wiring"):
        mod = __import__(f"firm.dashboard.{module}", fromlist=["x"])
        monkeypatch.setattr(mod, "popen_utf8", rec, raising=True)
        if hasattr(mod, "resolve_claude_bin"):
            monkeypatch.setattr(mod, "resolve_claude_bin",
                                lambda: ("/usr/bin/claude", "test"))
    return rec


# ---------------------------------------------------------------------------
# R1, R2 -- the two sites that already hold a workspace
# ---------------------------------------------------------------------------

def test_r1_the_board_brief_spawns_in_the_firms_tier(recorder, tmp_path,
                                                     monkeypatch):
    """Today it spawns in `_framework_root()`, so its hooks read the operator."""
    from firm.dashboard import coboard

    ws = _firm(tmp_path, with_base=True)
    monkeypatch.setattr(coboard, "_context",
                        lambda w, f: {"firm": {"name": "Demo"}, "members": []})
    coboard._jobs[  # the job record the site finishes into
        "J1"] = {"status": "running", "proc": None}

    coboard._run_brief("J1", ws, FIRM, {})

    assert recorder.one["cwd"] == base_cwd(ws), (
        f"the Board brief was spawned in {recorder.one['cwd']!r}, not in the "
        f"firm's own tier; its base hooks will walk up out of the firm")


def test_r2_the_wiring_run_spawns_in_the_firms_tier(recorder, tmp_path,
                                                    monkeypatch):
    """THE PRECONDITION IS STUBBED, and the reason is recorded here.

    This leg was counted RED on the unfixed tree while it never reached its
    own assertion. `_run_wiring` loads the roster out of the firm's database
    and surveys the workspace BEFORE it spawns anything, and against a bare
    `tmp_path` the first of those raised

        sqlite3.OperationalError: no such table: firm

    measured on the unfixed tree 2026-09-21 by stashing the fix and running
    this leg alone. So "R2 fails today" was true of the database and said
    nothing about the working directory the site passes. A leg that cannot
    fail for the reason it claims is worse than no leg, because its name is
    read as coverage -- the same defect R11 carried twice.

    The two reads before the spawn are stubbed and nothing else is: the `cwd`
    still comes from production and is still compared against `base_cwd`
    itself rather than a path built here.
    """
    from firm.dashboard import wiring

    ws = _firm(tmp_path, with_base=True)
    monkeypatch.setattr(wiring, "_load_roster",
                        lambda w, f: ({"name": "Demo", "description": ""}, []))
    monkeypatch.setattr(wiring.discovery, "survey",
                        lambda w, folders: {
                            "knowledge": {"skills": [], "commands": [],
                                          "attached": []},
                            "mcp": {"servers": [], "equipped": []},
                            "cli": []})
    wiring._jobs["J2"] = {"status": "running", "proc": None}

    wiring._run_wiring("J2", ws, FIRM, {})

    assert recorder.one["cwd"] == base_cwd(ws), (
        f"the wiring run was spawned in {recorder.one['cwd']!r}, not in the "
        f"firm's own tier")


# ---------------------------------------------------------------------------
# R3, R4 -- the two sites with no workspace at all
# ---------------------------------------------------------------------------

def test_r4_founding_never_resolves_the_operators_own_tier(recorder, tmp_path):
    """Founding runs BEFORE a firm exists, so it gets a scratch tier.

    The assertion is not "it is the firm's tier" -- there is no firm. It is
    that the directory is NOT the framework root, whose walk is what lands on
    the operator's workspace, and that the session is pointed at a `BASE_HOME`
    of its own.
    """
    from firm.dashboard import founding

    founding._jobs["J3"] = {"status": "running", "proc": None}

    founding._run_founding("J3", "a two-person writing firm")

    call = recorder.one
    assert call["cwd"] != str(founding._framework_root()), (
        "founding still spawns in the framework root, so its base hooks walk "
        "up to whatever `.base` sits above the install -- the operator's own")
    assert call["env"].get("BASE_HOME"), (
        "the founding session carries no BASE_HOME of its own, so its global "
        "tier is whatever the hub's environment happens to name")
    assert Path(tempfile.gettempdir()) in Path(call["env"]["BASE_HOME"]).parents, (
        f"the scratch tier {call['env']['BASE_HOME']!r} is not under TEMP")


def test_r3_reshuffle_never_resolves_the_operators_own_tier(recorder, tmp_path):
    """A proposal is not a firm, so reshuffle gets a scratch tier of its own."""
    from firm.dashboard import founding

    founding._jobs["J4"] = {"status": "running", "proc": None}

    founding._run_reshuffle("J4", {"members": []}, "make it smaller")

    call = recorder.one
    assert call["cwd"] != str(founding._framework_root()), (
        "reshuffle still spawns in the framework root")
    assert call["env"].get("BASE_HOME"), (
        "the reshuffle session carries no BASE_HOME of its own")


# ---------------------------------------------------------------------------
# R10 -- the global tier and the working directory must agree
# ---------------------------------------------------------------------------

def test_r10_the_env_tier_and_the_spawn_directory_agree(recorder, tmp_path,
                                                        monkeypatch):
    """Both sides read from what production recorded, neither computed here.

    base compares `BASE_HOME` plus `/.base-gbl` against the working directory
    of the process it runs in. Two strings that name the same directory in
    different spellings make base walk out of the tier (#136, `_one_spelling`).
    """
    from firm.dashboard import coboard
    from firm.services.base_domain import _base_env

    ws = _firm(tmp_path, with_base=True)
    monkeypatch.setattr(coboard, "_context",
                        lambda w, f: {"firm": {"name": "Demo"}, "members": []})
    coboard._jobs["J5"] = {"status": "running", "proc": None}

    coboard._run_brief("J5", ws, FIRM, {})

    call = recorder.one
    assert call["env"].get("BASE_HOME") == _base_env(ws).get("BASE_HOME"), (
        f"the session's BASE_HOME is {call['env'].get('BASE_HOME')!r}, not the "
        f"value the isolation seam produces for this firm")


# ---------------------------------------------------------------------------
# R11 -- the scratch tier is cleaned up, or the job record says why not
# ---------------------------------------------------------------------------

def test_r11_the_scratch_tier_is_gone_after_the_job_finishes(recorder,
                                                             tmp_path):
    """A scratch tier left behind is litter in TEMP on every founding run."""
    from firm.dashboard import founding

    founding._jobs["J6"] = {"status": "running", "proc": None}
    founding._run_founding("J6", "a two-person writing firm")

    call = recorder.one
    home_str = call["env"].get("BASE_HOME")
    # THE PRECONDITION FIRST. On today's tree the site passes `dict(os.environ)`
    # straight through, so an AMBIENT BASE_HOME is present and points at the
    # operator's own tier -- which satisfied a bare "is it gone?" check and made
    # this leg pass for a reason with nothing to do with the fix. A leg that
    # cannot fail for the reason it claims is worse than no leg at all.
    assert home_str, "no BASE_HOME was passed to the founding session at all"
    home = Path(home_str)
    # KEYED ON THE PREFIX THE FIX PRODUCES, not on "somewhere under TEMP".
    # The suite sets a BASE_HOME of its own under /tmp for its fence, so a
    # test that only asked "is it under TEMP" passed on the unfixed tree --
    # the SECOND time this one leg passed for a reason unrelated to the fix.
    # `cadre-founding-` is a string only this build can put there.
    assert home.name.startswith("cadre-founding-"), (
        f"the founding session's BASE_HOME is {home}, which is not a scratch "
        f"tier this run created -- it is whatever the environment named")
    job = founding._jobs["J6"]
    assert not home.exists() or job.get("tier_removed") is False, (
        f"the scratch tier {home} survived the job and the record does not say "
        f"why: {job.get('tier_removed')!r}")


# ---------------------------------------------------------------------------
# R8 -- one producer of the rule
# ---------------------------------------------------------------------------

def test_r8_no_site_computes_a_tier_directory_of_its_own():
    """The sites call the seam. They do not build a directory.

    Read on the source rather than on behaviour, because a second copy of the
    rule passes every behavioural leg on the day it is written and drifts
    later. That is exactly how `cli/pulse.py` came to hold a second copy of
    the pulse cleanup.
    """
    import inspect

    from firm.dashboard import coboard, founding, wiring

    for module in (coboard, founding, wiring):
        src = inspect.getsource(module)
        for spawn in ("_run_brief", "_run_wiring", "_run_founding",
                      "_run_reshuffle"):
            if f"def {spawn}" not in src:
                continue
            body = src.split(f"def {spawn}", 1)[1].split("\ndef ", 1)[0]
            assert "cwd=str(_framework_root())" not in body, (
                f"{module.__name__}.{spawn} still names the framework root as "
                f"its working directory instead of calling the seam")
            # STRENGTHENED AFTER MUTATION ME READ INERT. The assertion above
            # forbids ONE literal string, so a site that computes the same
            # directory by hand -- a real second copy of the tier rule, the
            # exact defect this leg is named for -- walked straight through
            # it. A site must CALL the seam, and must not name the framework
            # root in any spelling.
            assert "_framework_root" not in body, (
                f"{module.__name__}.{spawn} still names the framework root in "
                f"its own body")
            assert "session_spawn(" in body or "_scratch_session(" in body, (
                f"{module.__name__}.{spawn} reaches no tier seam at all — a "
                f"directory computed at the site is a second copy of the "
                f"rule, and a second copy passes every behavioural leg on the "
                f"day it is written and drifts afterwards")


# ---------------------------------------------------------------------------
# R6, R7, R5, R9 -- CONTROLS. Green today, and they must stay green.
# ---------------------------------------------------------------------------

def test_r6_control_a_firm_with_its_own_base_resolves_to_it(tmp_path):
    ws = _firm(tmp_path, with_base=True)
    assert base_cwd(ws) == str(ws.resolve()) or Path(base_cwd(ws)) == ws, (
        f"a firm holding its own .base resolved to {base_cwd(ws)!r}")


def test_r7_control_a_firm_without_a_base_does_not_climb_out(tmp_path):
    """#136's rule 2, and the case that bit in the field."""
    ws = _firm(tmp_path, with_base=False)
    got = Path(base_cwd(ws, create=False))
    assert ws in got.parents or got == ws, (
        f"a firm with no .base resolved to {got}, which is outside the firm")


def test_r7b_control_a_firm_whose_tier_exists_resolves_to_the_tier(tmp_path):
    """R7's loophole, closed, because mutation MB walked through it.

    MB -- the seam returns the workspace whether or not it holds a `.base` --
    reddened NOTHING: not here, not in the seam's own suite. R7 accepts
    `got == ws`, and on a firm with no tier on disk the workspace IS the
    honest answer (`_existing` hands a reader its fallback rather than
    climbing out), so the mutant and the fix give the same string for that
    input. The input that tells them apart is a firm whose tier EXISTS: rule 2
    must return the tier, and MB returns the firm.
    """
    import os

    from firm.services.graph_isolation import firm_base_home

    ws = _firm(tmp_path, with_base=False)
    tier = firm_base_home(ws) / ".base-gbl"
    tier.mkdir(parents=True)

    got = base_cwd(ws, create=False)

    assert got == os.path.abspath(str(tier)), (
        f"a firm whose own tier exists resolved to {got!r} instead of "
        f"{str(tier)!r} — rule 2 is not being applied, and base will walk up "
        f"from wherever this points")


def test_r5_control_the_house_docs_still_inline_the_same_bytes(tmp_path,
                                                               monkeypatch):
    """The guard against fixing the tier by breaking the founding agent.

    `_house_rules` reads both docs through an ABSOLUTE path built from
    `_framework_root()` and inlines their text, so the working directory has
    never been what made them resolve. This leg pins that the build does not
    change it.

    STRENGTHENED AFTER MUTATION MD READ INERT. As written it asserted that
    each document's NAME appears in the result -- and the failure branch
    prints `### <name>` followed by "(unavailable — design from the rules
    above)", so both names are there whether the read worked or not. Making
    the doc paths relative, which is precisely the damage moving the working
    directory would do, reddened nothing. The leg the design exists to protect
    could not see the harm it was named for.

    It now asserts the text actually ARRIVED, and calls the function again
    from a different working directory -- the change #143 makes, in miniature
    -- and demands the same bytes back.
    """
    from firm.dashboard.founding import _house_rules

    first = _house_rules()
    assert "(unavailable" not in first, (
        "neither house doc inlined, so this leg would sit green over a "
        "founding prompt that has lost both of them")
    assert "FIRM-SCAFFOLDING-GUIDE.md" in first
    assert "org-design.md" in first
    monkeypatch.chdir(tmp_path)
    assert _house_rules() == first, (
        "the house docs stopped resolving the moment the working directory "
        "moved, which is the founding agent losing its brief")


def test_r9_control_every_site_still_spawns_through_popen_utf8():
    """`popen_utf8` is what carries CREATE_NO_WINDOW. No site may bypass it."""
    import inspect

    from firm.dashboard import coboard, founding, wiring

    for module in (coboard, founding, wiring):
        src = inspect.getsource(module)
        assert "subprocess.Popen(" not in src, (
            f"{module.__name__} calls subprocess.Popen directly, which does "
            f"not carry CREATE_NO_WINDOW and can open a window")
