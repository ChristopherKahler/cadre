"""Hub extension registry — validation, round-trip, hand-broken files."""

from __future__ import annotations

import json

import pytest

from firm.dashboard import hub_extensions


@pytest.fixture(autouse=True)
def _tmp_cadre_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CADRE_HOME", str(tmp_path / "cadre-home"))


def test_validate_accepts_a_link_manifest_and_rejects_junk():
    entry, err = hub_extensions.validate(
        {"id": "cadre-chat", "title": "Co-Board Chat",
         "url": "http://firm.chat", "icon": "💬"})
    assert err == "" and entry["id"] == "cadre-chat"

    for bad, why in (
        ({"id": "Bad Slug!", "title": "x", "url": "http://a"}, "id"),
        ({"id": "ok", "title": "", "url": "http://a"}, "title"),
        ({"id": "ok", "title": "x", "url": "ftp://nope"}, "url"),
        ({"id": "ok", "title": "x", "url": 'http://a"b'}, "url"),
        ("not a dict", "object"),
    ):
        entry, err = hub_extensions.validate(bad)
        assert entry is None and why in err


def test_save_load_remove_roundtrip():
    entry, _ = hub_extensions.validate(
        {"id": "cadre-chat", "title": "Co-Board Chat", "url": "http://firm.chat"})
    hub_extensions.save(entry)
    assert [e["id"] for e in hub_extensions.load_all()] == ["cadre-chat"]
    assert hub_extensions.remove("cadre-chat") is True
    assert hub_extensions.load_all() == []
    assert hub_extensions.remove("cadre-chat") is False
    assert hub_extensions.remove("../escape") is False


def test_hand_broken_registry_file_is_skipped_not_fatal():
    (hub_extensions.registry_dir() / "junk.json").write_text("{nope", encoding="utf-8")
    (hub_extensions.registry_dir() / "half.json").write_text(
        json.dumps({"id": "half"}), encoding="utf-8")
    assert hub_extensions.load_all() == []
