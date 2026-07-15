"""Tests for firm.dashboard.discovery — the machine surveys behind Equip/Train.

Focus: base CLI extensions surface as equippable CLI tools (fork
cadre-armory-base-cli-tools). Host probes are kept out by mocking which -> None,
so these stay hermetic and fast (no real `gh auth status` etc.).
"""

from __future__ import annotations

from unittest import mock

from firm.dashboard import discovery


def test_base_ext_clis_tags_and_prefixes():
    fake = {"present": True, "extensions": [
        {"name": "nano-banana", "version": "0.1.0", "description": "image gen"}]}
    with mock.patch("firm.dashboard.discovery.base_survey", return_value=fake):
        out = discovery._base_ext_clis()
    assert out == [{
        "name": "base nano-banana", "ext": "nano-banana", "source": "base-ext",
        "what": "image gen", "present": True, "path": "", "live": None,
        "detail": "", "version": "0.1.0",
    }]


def test_base_ext_clis_empty_without_base():
    with mock.patch("firm.dashboard.discovery.base_survey",
                    return_value={"present": False, "extensions": []}):
        assert discovery._base_ext_clis() == []


def test_base_ext_clis_skips_nameless_and_defaults_description():
    fake = {"present": True, "extensions": [
        {"name": "", "version": "1", "description": "no name"},
        {"name": "meta-cli", "version": "0.2.0"}]}   # no description
    with mock.patch("firm.dashboard.discovery.base_survey", return_value=fake):
        out = discovery._base_ext_clis()
    assert [e["name"] for e in out] == ["base meta-cli"]
    assert out[0]["what"] == "base extension (meta-cli)"


def test_cli_survey_appends_base_exts_and_tags_hosts():
    # which -> None makes every host absent, so no verify probe runs: hermetic.
    discovery._cli_cache = None
    fake = {"present": True, "extensions": [
        {"name": "nano-banana", "version": "0.1.0", "description": "image gen"},
        {"name": "meta-cli", "version": "0.2.0", "description": "meta ads"}]}
    try:
        with mock.patch("firm.dashboard.discovery.shutil.which", return_value=None), \
             mock.patch("firm.dashboard.discovery.base_survey", return_value=fake):
            cli = discovery.cli_survey()
    finally:
        discovery._cli_cache = None   # never leak the all-absent mock result
    by_name = {c["name"]: c for c in cli}
    assert "base nano-banana" in by_name and "base meta-cli" in by_name
    assert by_name["base nano-banana"]["source"] == "base-ext"
    assert by_name["base nano-banana"]["ext"] == "nano-banana"
    assert by_name["base nano-banana"]["present"] is True
    # every entry is source-tagged; the hardcoded host probes are tagged 'host'
    assert all("source" in c for c in cli)
    assert any(c["source"] == "host" for c in cli)
