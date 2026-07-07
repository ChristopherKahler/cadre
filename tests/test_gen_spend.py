"""Tests for the universal generative-spend ledger + adapters."""

from __future__ import annotations

import sqlite3

from firm.core.migrate import apply_migrations
from firm.services import gen_adapters, gen_spend


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_migrations(c)
    c.execute("INSERT INTO firm (id, name) VALUES ('f', 'F')")
    return c


def test_record_and_summary() -> None:
    c = _conn()
    gen_spend.record(c, "f", platform="elevenlabs", units=1000, member_id="MEM-1", ref="e1")
    gen_spend.record(c, "f", platform="nano-banana", units=2, ref="e2")
    s = {r["platform"]: r for r in gen_spend.summary(c, "f")}
    assert set(s) == {"elevenlabs", "nano-banana"}
    assert s["elevenlabs"]["kind"] == "tts"
    assert s["elevenlabs"]["units"] == 1000
    assert s["elevenlabs"]["cost_usd"] > 0
    assert s["nano-banana"]["units"] == 2


def test_unknown_platform_still_logs() -> None:
    """A tool with no adapter yet must never be silently dropped."""
    c = _conn()
    gen_spend.record(c, "f", platform="mystery", units=5)
    s = gen_spend.summary(c, "f")
    assert s[0]["platform"] == "mystery"
    assert s[0]["kind"] == "unknown"
    assert s[0]["cost_usd"] == 0.0


def test_history_carries_attribution_and_asset() -> None:
    c = _conn()
    gen_spend.record(c, "f", platform="elevenlabs", units=100,
                     member_id="MEM-2", ref="e9", asset_path="game/audio/a.mp3")
    h = gen_spend.history(c, "f", "elevenlabs")
    assert h[0]["member_id"] == "MEM-2"
    assert h[0]["asset_path"] == "game/audio/a.mp3"


def test_adapter_cost_and_registration() -> None:
    assert gen_adapters.get("elevenlabs").unit_label == "chars"
    assert gen_adapters.get("nano-banana").kind == "image"
    # a new tool is a few lines — register at runtime and it works immediately
    gen_adapters.register(gen_adapters.GenAdapter(
        "x-tts", "tts", "chars", 0.001, label="X voice"))
    a = gen_adapters.get("x-tts")
    assert a.cost(100) == 0.1
    assert a.display() == "X voice"
