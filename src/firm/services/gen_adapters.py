"""Generative-spend adapters — one per cost-incurring tool a firm uses.

An adapter normalizes a platform's raw usage into the common shape the
boardroom reports on: a `kind`, a `unit_label`, a $ cost per unit, and an
optional live-balance probe. Callers (narrate.py, illustrate.py, any future
tool) record only raw *units* + context; the adapter supplies the rest.

Adding a platform is deliberately a few lines — as a firm inherits a new
spend tool, register a GenAdapter here (or at runtime via `register`) and the
ledger + boardroom pick it up with zero schema or UI change:

    register(GenAdapter("openai-tts", "tts", "chars", 15.0 / 1_000_000,
                        label="OpenAI (voice)"))

Cost rates are best-effort estimates (plans change) — the ledger stores the
number the adapter computed at write time, so historical rows stay honest even
if a rate is later corrected.
"""

from __future__ import annotations

import dataclasses
import json
import os
import urllib.request
from typing import Any, Callable


@dataclasses.dataclass
class GenAdapter:
    platform: str                              # stable id: 'elevenlabs'
    kind: str                                  # 'tts' | 'image' | 'video' | …
    unit_label: str                            # 'chars' | 'images'
    cost_per_unit_usd: float                   # $ per unit (best-effort)
    label: str = ""                            # display name for the boardroom
    # optional live-balance probe -> {"used", "limit", "resets_at"} or None
    balance: Callable[[], dict[str, Any] | None] | None = None

    def cost(self, units: float) -> float:
        return round(float(units) * self.cost_per_unit_usd, 6)

    def display(self) -> str:
        return self.label or self.platform


REGISTRY: dict[str, GenAdapter] = {}


def register(adapter: GenAdapter) -> GenAdapter:
    REGISTRY[adapter.platform] = adapter
    return adapter


def get(platform: str) -> GenAdapter | None:
    return REGISTRY.get(platform)


# ---------------------------------------------------------------------------
# Live-balance probes (best-effort; return None when creds/API are absent)
# ---------------------------------------------------------------------------

def _elevenlabs_balance() -> dict[str, Any] | None:
    """ElevenLabs character allotment for the current billing period. Reads
    ELEVENLABS_API_KEY from the process env (the firm .env); returns None if
    absent so the boardroom simply omits the live meter."""
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return None
    try:
        req = urllib.request.Request(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": key})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
    except Exception:
        return None
    used = d.get("character_count")
    limit = d.get("character_limit")
    if used is None or limit is None:
        return None
    return {"used": used, "limit": limit,
            "resets_at": d.get("next_character_count_reset_unix")}


# ---------------------------------------------------------------------------
# Built-in adapters
# ---------------------------------------------------------------------------

# ElevenLabs Creator: ~$22 / 233,351 chars per period. Plan-specific; the live
# probe above is the source of truth for the balance meter.
register(GenAdapter("elevenlabs", "tts", "chars", 22.0 / 233_351,
                    label="ElevenLabs · voice", balance=_elevenlabs_balance))

# nano-banana (Gemini 2.5 Flash Image): ~$0.039 per generated image.
register(GenAdapter("nano-banana", "image", "images", 0.039,
                    label="nano-banana · scene art"))
