"""Board notification — deterministic, service-layer, zero-model.

Fires from inside service actions (gate requested, escalation raised) so
delivery is structural: a Member cannot forget to notify the Board, and no
messaging credential ever appears in a Member's loadout — the token lives in
an env var on the harness side and the HTTP call happens in this process.

Per-firm config lives in ``firm.notify_config`` (JSON):

    {"provider": "slack",                       # or "webhook" / "telegram"
     "slack_user_id": "U0XXXXXXX",              # DM target (slack provider)
     "token_env": "CADRE_SLACK_TOKEN",          # env var holding the token
     "webhook_url_env": "CADRE_NOTIFY_WEBHOOK", # env var holding URL (webhook provider)
     "telegram_chat_id": "123456789",           # DM target (telegram provider)
     "telegram_token_env": "CADRE_TELEGRAM_TOKEN",  # env var holding the bot token
     "remind_hours": 24}                        # escalation re-notify window

Providers:
- ``slack``   — chat.postMessage DM via bot/user token. True direct DM.
- ``webhook`` — POST {"text": ...} to any incoming-webhook URL (Slack,
  Discord ``/slack``-compat, etc.). Zero app setup for framework adopters.
- ``telegram`` — Bot API sendMessage to a chat id. Pairs with the official
  Claude Code Telegram plugin: same bot pushes here, operator replies land
  in the paired boardroom session.

All failures are soft: notification must never break the firm action that
triggered it. Callers get {"sent": bool, "reason": str} for the audit trail.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from typing import Any

from firm.core import repo

DEFAULT_TOKEN_ENV = "CADRE_SLACK_TOKEN"
DEFAULT_WEBHOOK_ENV = "CADRE_NOTIFY_WEBHOOK"
DEFAULT_TELEGRAM_ENV = "CADRE_TELEGRAM_TOKEN"
DEFAULT_REMIND_HOURS = 24
_HTTP_TIMEOUT_SEC = 10


def get_notify_config(conn: sqlite3.Connection, firm_id: str) -> dict[str, Any] | None:
    """Return the firm's notify_config dict, or None if absent/malformed."""
    firm = repo.get(conn, "firm", firm_id)
    if not firm:
        return None
    cfg = firm.get("notify_config")
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except (json.JSONDecodeError, TypeError):
            return None
    return cfg if isinstance(cfg, dict) else None


def token_env_name(cfg: dict[str, Any]) -> str | None:
    """The environment variable holding this firm's notify credential — the
    bot token, or for a webhook the URL itself, since anyone holding it can
    post — or None for an unknown provider.

    One answer for every reader: the rail check, delivery, and the pulse that
    fills the variable from the firm's stores when it starts
    (``firm.pulse.environment``), so the token is put where the rail looks.
    """
    provider = cfg.get("provider", "slack")
    if provider == "slack":
        # `token_env` is the documented key; firms seeded with `slack_token_env`
        # (chief-of-staff among them) had their override silently ignored — accept both.
        return cfg.get("token_env") or cfg.get("slack_token_env") or DEFAULT_TOKEN_ENV
    if provider == "telegram":
        return cfg.get("telegram_token_env", DEFAULT_TELEGRAM_ENV)
    if provider == "webhook":
        return cfg.get("webhook_url_env", DEFAULT_WEBHOOK_ENV)
    return None


def rail_health(conn: sqlite3.Connection, firm_id: str) -> dict[str, Any]:
    """Cheap, side-effect-free-as-possible resolvability check for the rail.

    Distinct from :func:`send_board_dm`: this never delivers a Board message.
    For ``slack`` it makes one read-only ``auth.test`` call to confirm the
    resolved token actually authenticates — config-and-env-only checks would
    have called ESC-022's "correct-looking, inert" config healthy right up
    until the moment a real escalation tried to fire through it. Returns
    ``{"ok": bool, "reason": str}``; never raises.
    """
    cfg = get_notify_config(conn, firm_id)
    if not cfg:
        return {"ok": False, "reason": "notify_config not set on firm"}

    provider = cfg.get("provider", "slack")
    try:
        if provider == "slack":
            if not cfg.get("slack_user_id"):
                return {"ok": False, "reason": "notify_config.slack_user_id missing"}
            token_env = token_env_name(cfg)
            token = os.environ.get(token_env)
            if not token:
                return {"ok": False, "reason": f"token env {token_env} unset in this process"}
            result = _post_json(
                "https://slack.com/api/auth.test", {}, {"Authorization": f"Bearer {token}"},
            )
            if result.get("ok"):
                return {"ok": True, "reason": "slack auth.test ok"}
            return {"ok": False, "reason": f"slack auth.test error: {result.get('error', 'unknown')}"}

        if provider == "webhook":
            url_env = token_env_name(cfg)
            if not os.environ.get(url_env):
                return {"ok": False, "reason": f"webhook env {url_env} unset in this process"}
            return {"ok": True, "reason": "webhook url present (unverified — webhooks have no read-only probe)"}

        if provider == "telegram":
            if not cfg.get("telegram_chat_id"):
                return {"ok": False, "reason": "notify_config.telegram_chat_id missing"}
            token_env = token_env_name(cfg)
            token = os.environ.get(token_env)
            if not token:
                return {"ok": False, "reason": f"token env {token_env} unset in this process"}
            result = _post_json(f"https://api.telegram.org/bot{token}/getMe", {}, {})
            if result.get("ok"):
                return {"ok": True, "reason": "telegram getMe ok"}
            return {"ok": False, "reason": f"telegram getMe error: {result.get('description', 'unknown')}"}

        return {"ok": False, "reason": f"unknown notify provider {provider!r}"}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"ok": False, "reason": f"health check failed: {exc}"}


def remind_interval_hours(cfg: dict[str, Any] | None) -> float:
    """The escalation re-notify window for this firm (default 24h)."""
    if not cfg:
        return DEFAULT_REMIND_HOURS
    try:
        val = float(cfg.get("remind_hours", DEFAULT_REMIND_HOURS))
        return val if val > 0 else DEFAULT_REMIND_HOURS
    except (TypeError, ValueError):
        return DEFAULT_REMIND_HOURS


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        # Slack incoming webhooks answer plain "ok"
        return {"ok": body.strip().lower() == "ok", "raw": body[:200]}


def send_board_dm(
    conn: sqlite3.Connection,
    firm_id: str,
    text: str,
) -> dict[str, Any]:
    """Send *text* to the Board via the firm's configured channel.

    Returns {"sent": bool, "reason": str} — never raises.
    """
    cfg = get_notify_config(conn, firm_id)
    if not cfg:
        return {"sent": False, "reason": "notify_config not set on firm"}

    provider = cfg.get("provider", "slack")

    try:
        if provider == "slack":
            user_id = cfg.get("slack_user_id")
            if not user_id:
                return {"sent": False, "reason": "notify_config.slack_user_id missing"}
            token_env = token_env_name(cfg)
            token = os.environ.get(token_env)
            if not token:
                return {"sent": False, "reason": f"token env {token_env} unset in this process"}
            result = _post_json(
                "https://slack.com/api/chat.postMessage",
                {"channel": user_id, "text": text},
                {"Authorization": f"Bearer {token}"},
            )
            if result.get("ok"):
                return {"sent": True, "reason": "slack chat.postMessage ok"}
            return {"sent": False, "reason": f"slack error: {result.get('error', 'unknown')}"}

        if provider == "webhook":
            url_env = token_env_name(cfg)
            url = os.environ.get(url_env)
            if not url:
                return {"sent": False, "reason": f"webhook env {url_env} unset in this process"}
            result = _post_json(url, {"text": text}, {})
            if result.get("ok", True):  # webhooks that return nothing count as sent
                return {"sent": True, "reason": "webhook post ok"}
            return {"sent": False, "reason": f"webhook error: {result.get('raw', 'unknown')}"}

        if provider == "telegram":
            chat_id = cfg.get("telegram_chat_id")
            if not chat_id:
                return {"sent": False, "reason": "notify_config.telegram_chat_id missing"}
            token_env = token_env_name(cfg)
            token = os.environ.get(token_env)
            if not token:
                return {"sent": False, "reason": f"token env {token_env} unset in this process"}
            result = _post_json(
                f"https://api.telegram.org/bot{token}/sendMessage",
                {"chat_id": chat_id, "text": text},
                {},
            )
            if result.get("ok"):
                return {"sent": True, "reason": "telegram sendMessage ok"}
            return {"sent": False, "reason": f"telegram error: {result.get('description', 'unknown')}"}

        return {"sent": False, "reason": f"unknown notify provider {provider!r}"}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"sent": False, "reason": f"delivery failed: {exc}"}
