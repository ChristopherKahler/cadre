"""A firm's graph is the firm's alone (#117).

The defect these pin, measured on the operator's Windows machine 2026-09-14:
`firms/seedwin/.base/graph.nq` held 13,774 lines of which 86 were the firm's.
The rest arrived through two separate channels that base names itself in the
firm's own `.base/changes.jsonl` -- `extension.ingest:lore` (2,460,705 bytes)
and `domain.sync` (124 quads, no extension involved at all). Both channels
read from base's GLOBAL tier, and base resolves that tier from BASE_HOME.

So the assertions below are about one thing: every `base` subprocess Cadre
makes while working a firm is pointed at that firm's own tier. Nothing here
names `lore`, because a test that only proves `lore` is absent would pass on
the day the operator installs anything else.

No test in this file spawns the real `base` binary; the suite's own fence
(`conftest._no_ambient_base`) makes that impossible on purpose. The arms that
drive the real binary live in `scripts/verify/verify_firm_graph_isolation.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from firm.services import base_domain, graph_isolation


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _EnvRecorder:
    """Stands in for `run_utf8` and keeps the env of every call it saw."""

    def __init__(self, *results: _Result) -> None:
        self._results = list(results)
        self.envs: list[dict] = []
        self.cwds: list[str] = []

    def __call__(self, cmd, **kwargs):
        self.envs.append(dict(kwargs.get("env") or {}))
        self.cwds.append(str(kwargs.get("cwd") or ""))
        return self._results.pop(0) if self._results else _Result()


def _stub_base(tmp_path: Path) -> str:
    """A real file standing in for the binary, never the string "/fake/base".

    A string that is not a file passes today and fails open the moment a guard
    that identifies the binary arrives on the path under test -- which has
    already happened twice in this repo (#75, #87).
    """
    binary = tmp_path / "stub-base"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    return str(binary)


def _firm(tmp_path: Path) -> Path:
    workspace = tmp_path / "acme"
    (workspace / ".base").mkdir(parents=True)
    (workspace / ".firm").mkdir(parents=True)
    return workspace


# --- the seam ---------------------------------------------------------------


def test_the_env_points_base_at_this_firms_own_tier(tmp_path):
    workspace = _firm(tmp_path)

    env = base_domain._base_env(workspace)

    assert env["BASE_HOME"] == str(workspace / ".firm" / "base-home")


def test_the_tier_exists_by_the_time_base_is_told_about_it(tmp_path):
    """A BASE_HOME naming a path that does not exist is a guess, not isolation."""
    workspace = _firm(tmp_path)
    assert not (workspace / ".firm" / "base-home").exists()

    base_domain._base_env(workspace)

    assert (workspace / ".firm" / "base-home").is_dir()


def test_the_tier_carries_an_empty_extensions_directory(tmp_path):
    """An empty allow-list is a statement; an absent one is not.

    Measured 2026-09-14 against a firm founded by `cadre init`: the tier
    existed, the extensions directory did not, and the census refused with
    "which extensions this firm allows cannot be established" over a firm that
    was in fact clean. Creating it empty turns that into a verdict.
    """
    workspace = _firm(tmp_path)

    graph_isolation.ensure_tier(workspace)

    assert graph_isolation.tier_extensions_dir(workspace).is_dir()
    assert graph_isolation.installed_extensions(workspace) == set()


def test_an_empty_allow_list_makes_every_extension_foreign(tmp_path):
    """The teeth in that change: allowing nothing is not allowing anything."""
    workspace = _firm(tmp_path)
    graph_isolation.ensure_tier(workspace)
    (workspace / ".base" / "domains.toml").write_text(
        '[[domain]]\nname = "acme"\n', encoding="utf-8")
    _seed_graph(workspace, ["ext/cadre/CadreMember/mem-001", "domain/acme"])

    result = graph_isolation.census(workspace)

    assert result["reason"] == ""
    assert result["foreign"] == {"ext/cadre": 1}, (
        "a firm whose tier declares no extensions allows none, including ours")


def test_the_firms_tier_beats_an_ambient_base_home(monkeypatch, tmp_path):
    """The reversal, stated as an assertion.

    An ambient BASE_HOME used to be carried through. A caller aiming base
    somewhere else while Cadre is working a firm is the hole, so the firm wins.
    """
    workspace = _firm(tmp_path)
    monkeypatch.setenv("BASE_HOME", str(tmp_path / "somewhere-else"))

    env = base_domain._base_env(workspace)

    assert env["BASE_HOME"] == str(workspace / ".firm" / "base-home")


def test_without_a_firm_the_passthrough_still_stands(monkeypatch, tmp_path):
    """The control on the test above.

    Without this, an unconditional overwrite would satisfy that assertion while
    breaking every caller that has no firm to isolate -- `cadre extension
    install` and the suite's own fence, both of which set BASE_HOME deliberately
    and have nothing else to fall back on.
    """
    monkeypatch.setenv("BASE_HOME", str(tmp_path / "somewhere-else"))

    env = base_domain._base_env()

    assert env["BASE_HOME"] == str(tmp_path / "somewhere-else")


def _fixed_home(value: str) -> type:
    """A stand-in for `pathlib.Path` that answers `home()` with `value`.

    `pathlib` picks its flavour from `os.name` when a Path is built, so a test
    that patches `os.name` alone makes `Path.home()` try to construct the other
    platform's class and die with "cannot instantiate 'WindowsPath' on your
    system". The same stand-in is in tests/services/test_base_env_one_builder.py
    for the same reason; this is a copy of the idiom, not a second mechanism.
    """

    class _Home:
        @staticmethod
        def home() -> str:
            return value

    return _Home


def test_the_windows_branch_carries_the_firms_tier_too(monkeypatch, tmp_path):
    """`os.name` decides which passthrough block runs; the seam is outside it.

    Windows is the machine this defect was measured on, so an isolation that
    only holds on posix would be isolation nowhere that matters.
    """
    workspace = _firm(tmp_path)
    monkeypatch.setattr(base_domain, "Path", _fixed_home(r"C:\Users\example"))
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\example")
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")

    env = base_domain._base_env(workspace)

    assert env["BASE_HOME"] == str(workspace / ".firm" / "base-home")
    assert env["USERPROFILE"] == r"C:\Users\example"


def test_that_the_windows_arm_can_fail(monkeypatch, tmp_path):
    """The control on the arm above: off `nt`, the Windows passthrough is absent.

    Without it, an unconditional `env.update(os.environ)` would satisfy that
    assertion while carrying the operator's whole environment into every
    subprocess, which is the thing `_base_env` exists to avoid.
    """
    workspace = _firm(tmp_path)
    monkeypatch.setattr(base_domain, "Path", _fixed_home("/home/example"))
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\example")

    env = base_domain._base_env(workspace)

    assert "USERPROFILE" not in env
    assert env["BASE_HOME"] == str(workspace / ".firm" / "base-home")


# --- the call sites, which is where it actually has to be true ---------------


def test_reading_the_rule_count_runs_base_in_the_firms_tier(monkeypatch, tmp_path):
    workspace = _firm(tmp_path)
    recorder = _EnvRecorder(_Result("[acme] 1 rules across both tiers:\n"))
    monkeypatch.setattr(base_domain, "run_utf8", recorder)
    monkeypatch.setattr("firm.sysconfig.service.which_base",
                        lambda: _stub_base(tmp_path))

    base_domain.rule_count(workspace, "acme")

    assert recorder.envs, "no base call was made, so nothing was measured"
    assert recorder.envs[0]["BASE_HOME"] == str(workspace / ".firm" / "base-home")


def test_seeding_a_rule_runs_base_in_the_firms_tier(monkeypatch, tmp_path):
    workspace = _firm(tmp_path)
    recorder = _EnvRecorder(
        _Result("No rules for domain 'acme' in either tier.\n"),   # count: none
        _Result(""),                                               # rule add
        _Result("[acme] 1 rules across both tiers:\n"),            # read-back
    )
    monkeypatch.setattr(base_domain, "run_utf8", recorder)
    monkeypatch.setattr("firm.sysconfig.service.which_base",
                        lambda: _stub_base(tmp_path))

    seeded, why = base_domain._seed_rule_detail(workspace, "acme")

    assert seeded, why
    tier = str(workspace / ".firm" / "base-home")
    assert len(recorder.envs) == 3
    assert [e.get("BASE_HOME") for e in recorder.envs] == [tier, tier, tier], (
        "every call in the seed path must be in the firm's tier, including the "
        "read-back -- a read-back against a different tier answers about a "
        "different graph")


def test_scaffolding_the_tier_runs_base_in_the_firms_tier(monkeypatch, tmp_path):
    workspace = _firm(tmp_path)
    recorder = _EnvRecorder(_Result(""))
    monkeypatch.setattr(base_domain, "run_utf8", recorder)
    monkeypatch.setattr("firm.sysconfig.service.which_base",
                        lambda: _stub_base(tmp_path))
    monkeypatch.setattr("firm.sysconfig.binaries.base_can_honour_tier",
                        lambda binary, expected: (True, ""))

    result = base_domain.scaffold_tier(workspace)

    assert result["scaffolded"], result["detail"]
    assert recorder.envs[0]["BASE_HOME"] == str(workspace / ".firm" / "base-home")


def test_writing_a_note_back_runs_base_in_the_firms_tier(monkeypatch, tmp_path):
    """The writeback path is a third caller and it drifted from the other two once."""
    from firm.services import writeback

    workspace = _firm(tmp_path)
    recorder = _EnvRecorder(_Result(""))
    monkeypatch.setattr(writeback, "run_utf8", recorder)
    monkeypatch.setattr("firm.sysconfig.service.which_base",
                        lambda: _stub_base(tmp_path))

    writeback._base_learn(workspace, domain="acme", entity="acme",
                          text="a thing the firm learned", note_type="insight")

    assert recorder.envs, "no base call was made, so nothing was measured"
    assert recorder.envs[0]["BASE_HOME"] == str(workspace / ".firm" / "base-home")


# --- the census -------------------------------------------------------------


GRAPH_LINE = ("<http://ops-sys.local/ontology#{subject}> "
              "<http://ops-sys.local/ontology#name> \"x\" "
              "<http://ops-sys.local/ontology#graph/ws/acme> .\n")


def _seed_graph(workspace: Path, subjects: list[str]) -> None:
    (workspace / ".base" / "graph.nq").write_text(
        "".join(GRAPH_LINE.format(subject=s) for s in subjects), encoding="utf-8")


def _seed_tier(workspace: Path, extensions: list[str], domains: list[str]) -> None:
    ext_dir = graph_isolation.tier_extensions_dir(workspace)
    ext_dir.mkdir(parents=True, exist_ok=True)
    for name in extensions:
        (ext_dir / f"{name}.toml").write_text("[extension]\n", encoding="utf-8")
    (workspace / ".base" / "domains.toml").write_text(
        "".join(f'[[domain]]\nname = "{d}"\n' for d in domains), encoding="utf-8")


def test_the_census_names_what_came_from_outside_the_firm(tmp_path):
    workspace = _firm(tmp_path)
    _seed_tier(workspace, extensions=["cadre"], domains=["acme"])
    _seed_graph(workspace, [
        "ext/cadre/CadreMember/mem-001",     # the firm's own extension
        "ext/lore/LoreItem/34",              # an extension the firm never installed
        "ext/anythingelse/Thing/1",          # and one nobody has thought of yet
        "domain/acme",                       # the firm's own domain
        "domain/ext-cadre-cadre-firm",       # a domain its own extension brings
        "domain/video",                      # the operator's
        "rule/skyrim-lore/0",                # a rule hanging off the operator's
        "note/something-a-member-wrote",     # the firm's own work
    ])

    result = graph_isolation.census(workspace)

    assert result["reason"] == ""
    assert result["visited"] == 8
    assert result["foreign"] == {"ext/lore": 1, "ext/anythingelse": 1,
                                 "domain/video": 1, "rule/skyrim-lore": 1}
    assert result["foreign_lines"] == 4
    assert result["firm_lines"] == 4


def test_an_extension_nobody_predicted_is_foreign_too(tmp_path):
    """The whitelist is what the firm allows, never a list of known-bad names.

    A deny-list aimed at `lore` would pass the test above and fail the day the
    operator installs something else, which is the whole reason #117 is framed
    this way.
    """
    workspace = _firm(tmp_path)
    _seed_tier(workspace, extensions=["cadre"], domains=["acme"])
    _seed_graph(workspace, ["ext/brandnewthing/Item/1"])

    result = graph_isolation.census(workspace)

    assert result["foreign"] == {"ext/brandnewthing": 1}


def test_a_census_that_visited_nothing_is_a_failure_not_a_clean_firm(tmp_path):
    """This lane's own bug, one level up: a reader that cannot see reads clean."""
    workspace = _firm(tmp_path)
    _seed_tier(workspace, extensions=["cadre"], domains=["acme"])
    (workspace / ".base" / "graph.nq").write_text("", encoding="utf-8")

    result = graph_isolation.census(workspace)

    assert result["visited"] == 0
    assert "proved nothing" in result["reason"]
    assert graph_isolation.summary(result).startswith("isolation unknown")


def test_an_absent_graph_is_absent_and_not_clean(tmp_path):
    workspace = _firm(tmp_path)
    _seed_tier(workspace, extensions=["cadre"], domains=["acme"])

    result = graph_isolation.census(workspace)

    assert "no .base/graph.nq" in result["reason"]
    assert result["foreign"] == {}


def test_without_a_tier_the_census_refuses_rather_than_guesses(tmp_path):
    """No allow-list means no verdict.

    Classifying against an empty allow-list would report every line in the
    graph as foreign, which reads as a catastrophic finding and is really just
    a directory that does not exist yet.
    """
    workspace = _firm(tmp_path)
    _seed_graph(workspace, ["ext/cadre/CadreMember/mem-001"])

    result = graph_isolation.census(workspace)

    assert "cannot be established" in result["reason"]
    assert result["foreign"] == {}


def test_a_founded_firm_reports_itself_isolated(monkeypatch, tmp_path):
    workspace = _firm(tmp_path)
    monkeypatch.setattr("firm.sysconfig.service.which_base",
                        lambda: _stub_base(tmp_path))
    graph_isolation.ensure_tier(workspace)
    graph_isolation.write_session_env(workspace)

    state, why = graph_isolation.isolation_state(workspace)

    assert state is graph_isolation.Isolation.ISOLATED, why
    assert str(graph_isolation.firm_base_home(workspace)) in why


def test_settings_pointing_somewhere_else_is_unisolated(monkeypatch, tmp_path):
    """The read-back is the load-bearing part: a file that was written and then
    rewritten by something else looks identical without it."""
    import json

    workspace = _firm(tmp_path)
    monkeypatch.setattr("firm.sysconfig.service.which_base",
                        lambda: _stub_base(tmp_path))
    graph_isolation.ensure_tier(workspace)
    settings = workspace / ".claude" / "settings.local.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"env": {"BASE_HOME": "/somewhere/else"}}),
                        encoding="utf-8")

    state, why = graph_isolation.isolation_state(workspace)

    assert state is graph_isolation.Isolation.UNISOLATED
    assert "/somewhere/else" in why


def test_no_base_at_all_is_unknown_and_not_a_failure(monkeypatch, tmp_path):
    """base absent is a supported state. A firm on a machine without it is
    degraded, never broken, and must not be reported as isolation FAILING."""
    workspace = _firm(tmp_path)
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: None)

    state, why = graph_isolation.isolation_state(workspace)

    assert state is graph_isolation.Isolation.UNKNOWN
    assert "not the same as a firm whose isolation failed" in why


def test_the_three_states_are_three(tmp_path):
    """UNKNOWN is not UNISOLATED, and a bool cannot hold three answers."""
    values = {graph_isolation.Isolation.ISOLATED,
              graph_isolation.Isolation.UNISOLATED,
              graph_isolation.Isolation.UNKNOWN}

    assert len(values) == 3
