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


# ---------------------------------------------------------------------------
# Run attribution (fork cadre-calibration-run-scoring sibling: gen_spend.run_id)
# ---------------------------------------------------------------------------

def test_run_id_column_and_explicit_attribution() -> None:
    c = _conn()
    cols = {r[1] for r in c.execute("PRAGMA table_info(gen_spend)")}
    assert "run_id" in cols
    gen_spend.record(c, "f", platform="nano-banana", units=1,
                     member_id="MEM-1", run_id="RUN-9", ref="entity:x")
    h = gen_spend.history(c, "f", "nano-banana")
    assert h[0]["member_id"] == "MEM-1"
    assert h[0]["run_id"] == "RUN-9"


def test_attribution_falls_back_to_run_env(monkeypatch) -> None:
    """The framework fix: a tool that logs a generation inside a Member run is
    attributed from CADRE_MEMBER_ID / CADRE_RUN_ID even when it passes neither —
    so no firm has to thread the ids through its own scripts."""
    c = _conn()
    monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-5")
    monkeypatch.setenv("CADRE_RUN_ID", "RUN-42")
    gen_spend.record(c, "f", platform="nano-banana", units=1, ref="entity:y")
    h = gen_spend.history(c, "f", "nano-banana")
    assert h[0]["member_id"] == "MEM-5"
    assert h[0]["run_id"] == "RUN-42"


def test_explicit_attribution_wins_over_env(monkeypatch) -> None:
    c = _conn()
    monkeypatch.setenv("CADRE_MEMBER_ID", "MEM-env")
    monkeypatch.setenv("CADRE_RUN_ID", "RUN-env")
    gen_spend.record(c, "f", platform="nano-banana", units=1,
                     member_id="MEM-explicit", run_id="RUN-explicit")
    h = gen_spend.history(c, "f", "nano-banana")
    assert h[0]["member_id"] == "MEM-explicit"
    assert h[0]["run_id"] == "RUN-explicit"


def test_out_of_run_generation_stays_unattributed(monkeypatch) -> None:
    """A manual CLI / Board-side call has no run env → NULL is honest, not a gap."""
    c = _conn()
    monkeypatch.delenv("CADRE_MEMBER_ID", raising=False)
    monkeypatch.delenv("CADRE_RUN_ID", raising=False)
    gen_spend.record(c, "f", platform="nano-banana", units=1)
    h = gen_spend.history(c, "f", "nano-banana")
    assert h[0]["member_id"] is None
    assert h[0]["run_id"] is None
