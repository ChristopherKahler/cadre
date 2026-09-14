"""A Member registers under its own NAME, in the same inbox, every run.

base assigns every headless session a codename from a name pool ("tapir",
"puffin"), so a firm's Members arrive as small mammals and cannot be steered by
name. BASE_RELAY_AS pins the title instead.

#122 changed WHAT it is pinned to, and the reason is the operator's: he steers a
Member knowing only that Member's name. The title used to be
`<firm_id>-<member_id>` -- stable, and unusable, because `member_id` is the row
id: the Member called Pen registered as `demo-MEM-001`, so steering Pen required
first looking up that Pen is MEM-001. The title is now the Member's name.

Three conditions hold here, the first two measured against base 0.15.0 (md5
052b9a95d6afbd938ddf21aba26b0601) on 2026-09-10, and all asserted on the REAL
env ``spawn_member_run`` hands Popen rather than on a replica of the env builder
-- a replica would only prove the test agrees with itself.

  1. WT_SESSION must not reach the child. base binds a title partly by that
     GUID, so a session presenting one already bound RECLAIMS its title instead
     of taking the pinned name. With the variable unset or distinct per run,
     three runs took three distinct pinned titles; with one shared value, run 2
     reclaimed run 1's title and the registry held a single row.
  2. An inherited BASE_RELAY_AS must not survive. The spawner is often pinned
     itself, and ``env = dict(os.environ)`` would hand its title to every Member
     in the pulse, so whichever answered a relay alert first would clear it for
     all the others.
  3. The name resolves from the firm's own roster, and when the roster cannot be
     read the OLD `<firm>-<member>` shape is the fallback -- an ugly title is
     recoverable, an invented one is not.

Both directions are held throughout: that the title IS set when a member can be
named, and that nothing leaks through when it cannot.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from firm.core import repo
from firm.core.migrate import apply_migrations
from firm.pulse import spawn as spawn_mod


def _capture(monkeypatch) -> dict:
    """Run the real spawn; capture the real argv AND the real child env."""
    captured: dict = {}

    class FakePopen:
        def __init__(self, *args, **kwargs):
            captured["cmd"] = args[0] if args else kwargs.get("args")
            captured["env"] = kwargs.get("env")
            raise OSError("captured — never exec in tests")

    monkeypatch.setattr(spawn_mod, "resolve_claude_bin", lambda: ("/bin/echo", "test"))
    monkeypatch.setattr(spawn_mod.subprocess, "Popen", FakePopen)
    return captured


@pytest.fixture()
def firm(tmp_path: Path) -> Path:
    """A real firm with a real roster: Pen (MEM-001) and Editor (MEM-002).

    The names matter. Against a directory with no `.firm/firm.db` every
    assertion below would pass through the FALLBACK path and this file would be
    testing the shape it was written to replace.
    """
    workspace = tmp_path / "acme"
    (workspace / ".firm").mkdir(parents=True)
    conn = sqlite3.connect(workspace / ".firm" / "firm.db")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    repo.create(conn, "firm", {"id": "zqfirm", "name": "Acme"})
    repo.create(conn, "member", {"id": "MEM-001", "firm_id": "zqfirm",
                                 "name": "Pen", "role": "Writer",
                                 "status": "active"})
    repo.create(conn, "member", {"id": "MEM-002", "firm_id": "zqfirm",
                                 "name": "Editor", "role": "Editor",
                                 "status": "active"})
    conn.commit()
    conn.close()
    return workspace


# ---------------------------------------------------------------------------
# The title is the Member's own name
# ---------------------------------------------------------------------------

def test_a_member_registers_under_its_own_name(monkeypatch, firm):
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(firm),
                               firm_id="zqfirm", member_id="MEM-001")
    assert cap["env"]["BASE_RELAY_AS"] == "pen", (
        "the operator steers by name; a row id is the lookup he asked to be "
        "rid of")


def test_the_same_member_gets_the_same_title_every_run(monkeypatch, firm):
    """Stability is the whole request: same name, same inbox, run after run."""
    titles = []
    for _ in range(3):
        cap = _capture(monkeypatch)
        spawn_mod.spawn_member_run("work", cwd=str(firm),
                                   firm_id="zqfirm", member_id="MEM-001")
        titles.append(cap["env"]["BASE_RELAY_AS"])
    assert titles == ["pen", "pen", "pen"]


def test_two_members_of_one_firm_get_two_titles(monkeypatch, firm):
    """A pulse runs Members one at a time through the same process, so a title
    computed once and cached would give the whole firm one name."""
    titles = []
    for member in ("MEM-001", "MEM-002"):
        cap = _capture(monkeypatch)
        spawn_mod.spawn_member_run("work", cwd=str(firm),
                                   firm_id="zqfirm", member_id=member)
        titles.append(cap["env"]["BASE_RELAY_AS"])
    assert titles == ["pen", "editor"]
    assert len(set(titles)) == 2, f"visited 2 members, got {len(set(titles))} titles"


def test_a_multi_word_name_becomes_one_steerable_word(monkeypatch, firm):
    conn = sqlite3.connect(firm / ".firm" / "firm.db")
    conn.row_factory = sqlite3.Row
    repo.create(conn, "member", {"id": "MEM-003", "firm_id": "zqfirm",
                                 "name": "Ops Lead", "role": "Ops",
                                 "status": "active"})
    conn.commit()
    conn.close()

    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(firm),
                               firm_id="zqfirm", member_id="MEM-003")

    assert cap["env"]["BASE_RELAY_AS"] == "ops-lead"


def test_two_members_with_one_name_both_carry_their_row_id(monkeypatch, firm):
    """Neither of them wins silently.

    A deterministic tie-break would land the Board's steer in one of the two
    inboxes and look exactly like success. Both titles change, so the ambiguity
    is visible in the one place anyone looks -- the name they typed does not
    resolve -- and `firm doctor` names the pair.
    """
    conn = sqlite3.connect(firm / ".firm" / "firm.db")
    conn.row_factory = sqlite3.Row
    repo.create(conn, "member", {"id": "MEM-004", "firm_id": "zqfirm",
                                 "name": "Pen", "role": "Writer",
                                 "status": "active"})
    conn.commit()
    conn.close()

    titles = []
    for member in ("MEM-001", "MEM-004"):
        cap = _capture(monkeypatch)
        spawn_mod.spawn_member_run("work", cwd=str(firm),
                                   firm_id="zqfirm", member_id=member)
        titles.append(cap["env"]["BASE_RELAY_AS"])

    assert titles == ["pen-mem-001", "pen-mem-004"]


def test_a_retired_namesake_does_not_push_a_member_off_its_name(monkeypatch, firm):
    """The control on the collision rule: only ACTIVE Members contend.

    Without this, one retired Member would rename a working one forever, and
    the rule would be indistinguishable from a rule that fires on anything.
    """
    conn = sqlite3.connect(firm / ".firm" / "firm.db")
    conn.row_factory = sqlite3.Row
    repo.create(conn, "member", {"id": "MEM-005", "firm_id": "zqfirm",
                                 "name": "Pen", "role": "Writer",
                                 "status": "retired"})
    conn.commit()
    conn.close()

    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(firm),
                               firm_id="zqfirm", member_id="MEM-001")

    assert cap["env"]["BASE_RELAY_AS"] == "pen"


def test_an_unreadable_roster_falls_back_to_the_row_id(monkeypatch, tmp_path):
    """No roster, no name. The fallback is the OLD shape, deliberately.

    An invented title is worse than an ugly one: `zqfirm-MEM-001` is at least
    derivable from what the caller already knows, and it is what every earlier
    run used.
    """
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(tmp_path),
                               firm_id="zqfirm", member_id="MEM-001")
    assert cap["env"]["BASE_RELAY_AS"] == "zqfirm-MEM-001"


# ---------------------------------------------------------------------------
# The firm's own tier (#117)
# ---------------------------------------------------------------------------

def test_a_member_run_is_pointed_at_its_own_firms_tier(monkeypatch, firm):
    """The Member's session start ingests into the firm's graph, nowhere else."""
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(firm),
                               firm_id="zqfirm", member_id="MEM-001")
    assert cap["env"]["BASE_HOME"] == str(firm / ".firm" / "base-home")


def test_a_run_outside_a_firm_invents_no_tier(monkeypatch, tmp_path, ):
    """The control: BASE_HOME is set for a firm, not for everything.

    `spawn_member_run` is also used where there is no firm to isolate. Inventing
    a tier there would point base at a directory nothing else ever reads.
    """
    monkeypatch.delenv("BASE_HOME", raising=False)
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(tmp_path),
                               firm_id="zqfirm", member_id="MEM-001")
    assert "BASE_HOME" not in cap["env"]


# ---------------------------------------------------------------------------
# WT_SESSION never reaches the child
# ---------------------------------------------------------------------------

def test_wt_session_is_stripped_even_when_the_spawner_has_one(monkeypatch, firm):
    """A hub started inside Windows Terminal carries one. If it reached the
    child, base would reclaim a title instead of taking the pinned one and every
    Member in the pulse would end up sharing a single registry row."""
    monkeypatch.setenv("WT_SESSION", "133ce31a-8475-4e49-a2e6-9d3160fa1c9f")
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(firm),
                               firm_id="zqfirm", member_id="MEM-001")
    assert "WT_SESSION" not in cap["env"]
    assert cap["env"]["BASE_RELAY_AS"] == "pen"


def test_wt_session_absent_stays_absent(monkeypatch, firm):
    """The control: stripping must not invent the key with an empty value, which
    would still present a binding to base."""
    monkeypatch.delenv("WT_SESSION", raising=False)
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(firm),
                               firm_id="zqfirm", member_id="MEM-001")
    assert "WT_SESSION" not in cap["env"]


def test_the_spawners_own_wt_session_is_not_disturbed(monkeypatch, firm):
    """Stripping is for the CHILD env. Mutating os.environ would retitle the
    session doing the spawning, which is the hub."""
    import os
    monkeypatch.setenv("WT_SESSION", "keep-me")
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(firm),
                               firm_id="zqfirm", member_id="MEM-001")
    assert "WT_SESSION" not in cap["env"]
    assert os.environ.get("WT_SESSION") == "keep-me"


# ---------------------------------------------------------------------------
# An inherited title never survives
# ---------------------------------------------------------------------------

def test_the_spawners_title_does_not_become_the_members(monkeypatch, firm):
    """The hub pins every gated child, so the spawner usually HAS a title. If it
    were inherited, every Member would register as the orchestrator and whichever
    answered a relay alert first would clear it for the rest."""
    monkeypatch.setenv("BASE_RELAY_AS", "ibis")
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(firm),
                               firm_id="zqfirm", member_id="MEM-001")
    assert cap["env"]["BASE_RELAY_AS"] == "pen", \
        "the member's own title must win over an inherited one"


@pytest.mark.parametrize("firm_id,member_id", [
    (None, None),
    ("zqfirm", None),
    (None, "MEM-001"),
])
def test_an_unnamed_run_carries_no_title_at_all(monkeypatch, firm,
                                                firm_id, member_id):
    """When there is no member to name, an inherited title is REMOVED rather
    than passed on. Leaking the spawner's name is worse than a pool codename:
    a pool name is merely unhelpful, a wrong name silently steals its alerts."""
    monkeypatch.setenv("BASE_RELAY_AS", "ibis")
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(firm),
                               firm_id=firm_id, member_id=member_id)
    assert "BASE_RELAY_AS" not in cap["env"]


def test_the_spawners_own_title_is_not_disturbed(monkeypatch, firm):
    import os
    monkeypatch.setenv("BASE_RELAY_AS", "ibis")
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(firm),
                               firm_id="zqfirm", member_id="MEM-001")
    assert os.environ.get("BASE_RELAY_AS") == "ibis"


# ---------------------------------------------------------------------------
# Nothing else about the boot command moved
# ---------------------------------------------------------------------------

def test_the_existing_member_env_still_arrives(monkeypatch, firm):
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(firm), firm_id="zqfirm",
                               member_id="MEM-001", run_id="RUN-9")
    assert cap["env"]["CADRE_MEMBER_ID"] == "MEM-001"
    assert cap["env"]["FIRM_ID"] == "zqfirm"
    assert cap["env"]["CADRE_RUN_ID"] == "RUN-9"


def test_the_board_token_is_still_kept_out(monkeypatch, firm):
    monkeypatch.setenv("CADRE_BOARD_TOKEN", "secret")
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(firm),
                               firm_id="zqfirm", member_id="MEM-001")
    assert "CADRE_BOARD_TOKEN" not in cap["env"]


def test_strict_mcp_config_is_untouched_by_this_change(monkeypatch, firm):
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(firm),
                               firm_id="zqfirm", member_id="MEM-001")
    assert "--strict-mcp-config" in cap["cmd"]
