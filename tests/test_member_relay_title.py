"""A Member registers on the relay under its own name, not a small mammal's.

base assigns every headless session a codename from a name pool and prints a
~7 KB wake contract for it, so a firm's Members arrive as "tapir" and "puffin"
and cannot be steered by name from the Boardroom. BASE_RELAY_AS pins the title.

Two conditions had to hold, both measured against base 0.15.0 (md5
052b9a95d6afbd938ddf21aba26b0601) on 2026-09-10, and both are asserted here on
the REAL env ``spawn_member_run`` hands Popen rather than on a replica of the
env builder — a replica would only prove the test agrees with itself.

  1. WT_SESSION must not reach the child. base binds a title partly by that
     GUID, so a session presenting one already bound RECLAIMS its title instead
     of taking the pinned name. With the variable unset or distinct per run,
     three runs took three distinct pinned titles; with one shared value, run 2
     reclaimed run 1's title and the registry held a single row.
  2. An inherited BASE_RELAY_AS must not survive. The spawner is often pinned
     itself, and ``env = dict(os.environ)`` would hand its title to every Member
     in the pulse, so whichever answered a relay alert first would clear it for
     all the others.

Both directions are held throughout: that the title IS set when a member can be
named, and that nothing leaks through when it cannot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


# ---------------------------------------------------------------------------
# The title is pinned
# ---------------------------------------------------------------------------

def test_a_named_member_gets_its_own_title(monkeypatch, tmp_path):
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(tmp_path),
                               firm_id="zqfirm", member_id="MEM-001")
    assert cap["env"]["BASE_RELAY_AS"] == "zqfirm-MEM-001"


def test_two_members_of_one_firm_get_two_titles(monkeypatch, tmp_path):
    """A pulse runs Members one at a time through the same process, so a title
    that were computed once and cached would give the whole firm one name."""
    titles = []
    for member in ("MEM-001", "MEM-002", "MEM-003"):
        cap = _capture(monkeypatch)
        spawn_mod.spawn_member_run("work", cwd=str(tmp_path),
                                   firm_id="zqfirm", member_id=member)
        titles.append(cap["env"]["BASE_RELAY_AS"])
    assert titles == ["zqfirm-MEM-001", "zqfirm-MEM-002", "zqfirm-MEM-003"]
    assert len(set(titles)) == 3, f"visited 3 members, got {len(set(titles))} titles"


def test_members_of_different_firms_do_not_collide(monkeypatch, tmp_path):
    seen = set()
    for firm in ("zqfirm", "zqother"):
        cap = _capture(monkeypatch)
        spawn_mod.spawn_member_run("work", cwd=str(tmp_path),
                                   firm_id=firm, member_id="MEM-001")
        seen.add(cap["env"]["BASE_RELAY_AS"])
    assert seen == {"zqfirm-MEM-001", "zqother-MEM-001"}


# ---------------------------------------------------------------------------
# WT_SESSION never reaches the child
# ---------------------------------------------------------------------------

def test_wt_session_is_stripped_even_when_the_spawner_has_one(monkeypatch, tmp_path):
    """A hub started inside Windows Terminal carries one. If it reached the
    child, base would reclaim a title instead of taking the pinned one and every
    Member in the pulse would end up sharing a single registry row."""
    monkeypatch.setenv("WT_SESSION", "133ce31a-8475-4e49-a2e6-9d3160fa1c9f")
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(tmp_path),
                               firm_id="zqfirm", member_id="MEM-001")
    assert "WT_SESSION" not in cap["env"]
    assert cap["env"]["BASE_RELAY_AS"] == "zqfirm-MEM-001"


def test_wt_session_absent_stays_absent(monkeypatch, tmp_path):
    """The control: stripping must not invent the key with an empty value, which
    would still present a binding to base."""
    monkeypatch.delenv("WT_SESSION", raising=False)
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(tmp_path),
                               firm_id="zqfirm", member_id="MEM-001")
    assert "WT_SESSION" not in cap["env"]


def test_the_spawners_own_wt_session_is_not_disturbed(monkeypatch, tmp_path):
    """Stripping is for the CHILD env. Mutating os.environ would retitle the
    session doing the spawning, which is the hub."""
    import os
    monkeypatch.setenv("WT_SESSION", "keep-me")
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(tmp_path),
                               firm_id="zqfirm", member_id="MEM-001")
    assert "WT_SESSION" not in cap["env"]
    assert os.environ.get("WT_SESSION") == "keep-me"


# ---------------------------------------------------------------------------
# An inherited title never survives
# ---------------------------------------------------------------------------

def test_the_spawners_title_does_not_become_the_members(monkeypatch, tmp_path):
    """The hub pins every gated child, so the spawner usually HAS a title. If it
    were inherited, every Member would register as the orchestrator and whichever
    answered a relay alert first would clear it for the rest."""
    monkeypatch.setenv("BASE_RELAY_AS", "ibis")
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(tmp_path),
                               firm_id="zqfirm", member_id="MEM-001")
    assert cap["env"]["BASE_RELAY_AS"] == "zqfirm-MEM-001", \
        "the member's own title must win over an inherited one"


@pytest.mark.parametrize("firm_id,member_id", [
    (None, None),
    ("zqfirm", None),
    (None, "MEM-001"),
])
def test_an_unnamed_run_carries_no_title_at_all(monkeypatch, tmp_path,
                                                firm_id, member_id):
    """When there is no member to name, an inherited title is REMOVED rather
    than passed on. Leaking the spawner's name is worse than a pool codename:
    a pool name is merely unhelpful, a wrong name silently steals its alerts."""
    monkeypatch.setenv("BASE_RELAY_AS", "ibis")
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(tmp_path),
                               firm_id=firm_id, member_id=member_id)
    assert "BASE_RELAY_AS" not in cap["env"]


def test_the_spawners_own_title_is_not_disturbed(monkeypatch, tmp_path):
    import os
    monkeypatch.setenv("BASE_RELAY_AS", "ibis")
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(tmp_path),
                               firm_id="zqfirm", member_id="MEM-001")
    assert os.environ.get("BASE_RELAY_AS") == "ibis"


# ---------------------------------------------------------------------------
# Nothing else about the boot command moved
# ---------------------------------------------------------------------------

def test_the_existing_member_env_still_arrives(monkeypatch, tmp_path):
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(tmp_path), firm_id="zqfirm",
                               member_id="MEM-001", run_id="RUN-9")
    assert cap["env"]["CADRE_MEMBER_ID"] == "MEM-001"
    assert cap["env"]["FIRM_ID"] == "zqfirm"
    assert cap["env"]["CADRE_RUN_ID"] == "RUN-9"


def test_the_board_token_is_still_kept_out(monkeypatch, tmp_path):
    monkeypatch.setenv("CADRE_BOARD_TOKEN", "secret")
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(tmp_path),
                               firm_id="zqfirm", member_id="MEM-001")
    assert "CADRE_BOARD_TOKEN" not in cap["env"]


def test_strict_mcp_config_is_untouched_by_this_change(monkeypatch, tmp_path):
    cap = _capture(monkeypatch)
    spawn_mod.spawn_member_run("work", cwd=str(tmp_path),
                               firm_id="zqfirm", member_id="MEM-001")
    assert "--strict-mcp-config" in cap["cmd"]
