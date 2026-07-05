"""Self-monitoring: notification-bot connectivity.

The sweeper's whole job is to *tell someone* when a machine is unhealthy. If
its own Telegram / Slack bots are misconfigured (revoked token, bot kicked from
the channel, wrong chat id), every alert silently goes nowhere — the worst
possible failure mode for a monitoring agent.

This module probes **every** configured bot on **both** platforms and returns
one aggregate health section, so the agent stays "awake" about its own comms
channels regardless of which machines it happens to be watching.

Sources probed (all live, read-only — nothing is posted to any channel):
  - Telegram bots in the multi-bot store (``notify/store.py``) → ``getMe``
  - Slack App bots in the store → ``auth.test``
  - The legacy single Telegram target from ``notifications.env`` (if set)

The legacy Slack *webhook* has no read-only validation endpoint (only posting
would confirm it), so it is reported as ``disabled`` rather than probed — we
never spam a channel just to health-check it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import structlog

from .._log import log_suppressed

logger = structlog.get_logger("ipracticom_sweeper.bot_connectivity")

# Where the last live probe result is cached. The read-path (build_self_section,
# /api/self-health, /healthz) loads this instead of re-probing so a dashboard
# refresh never fires N blocking network calls; the live probe runs once per
# sweep from run_all()'s self phase.
RESULT_FILE = "bot_connectivity.json"

# status → DEFCON. "disabled" is not a fault (no bot of that kind configured),
# so it maps to green; a dead token is crit because alerts are silently lost.
_STATUS_DEFCON = {"ok": 5, "warn": 4, "crit": 2, "disabled": 5, "unknown": 3}
_RANK = {"ok": 0, "disabled": 0, "unknown": 1, "warn": 2, "crit": 3}


def _probe_telegram(token: str) -> dict[str, Any]:
    from ipracticom_sweeper.telegram_bot.health import probe_bot_token
    r = probe_bot_token(token)
    return {
        "status": r.status,
        "identity": r.bot_username,
        "error": r.error,
        "error_code": r.error_code,
        "latency_ms": r.latency_ms,
    }


def _probe_slack(token: str) -> dict[str, Any]:
    from ipracticom_sweeper.notify.slack_health import probe_slack_token
    r = probe_slack_token(token)
    return {
        "status": r.status,
        "identity": r.bot_username,
        "error": r.error,
        "error_code": r.error_code,
        "latency_ms": r.latency_ms,
    }


def _legacy_telegram_token() -> str | None:
    """The single legacy Telegram token from notifications.env, if configured."""
    try:
        from ipracticom_sweeper import config as _cfg
        return _cfg.telegram_bot_token() or None
    except Exception as e:
        log_suppressed("bot_connectivity_legacy_token", e)
        return None


def check_all_bots() -> dict[str, Any]:
    """Probe every configured bot and return one aggregate health section.

    Never raises: a probe failure degrades that one bot to ``warn``/``crit``,
    never aborts the sweep. Shape::

        {
          "status": "ok" | "warn" | "crit" | "disabled",
          "defcon": int,
          "configured": int,          # bots actually probed (excludes disabled)
          "counts": {"ok", "warn", "crit", "disabled"},
          "bots": [{platform, id, name, status, identity, error, latency_ms}],
          "summary": "telegram: 2 ok · slack: 1 crit",
        }
    """
    bots: list[dict[str, Any]] = []

    # --- Store bots (multi-bot) ---------------------------------------------
    try:
        from ipracticom_sweeper.notify import store as _store
        telegram_store = _store.telegram_bots()
        slack_store = _store.slack_bots()
    except Exception as e:
        log_suppressed("bot_connectivity_store_read", e)
        telegram_store, slack_store = [], []

    for b in telegram_store:
        probe = _safe_probe(_probe_telegram, b.get("bot_token", ""))
        bots.append({
            "platform": "telegram",
            "id": b.get("id", ""),
            "name": b.get("name") or "Telegram bot",
            **probe,
        })

    for b in slack_store:
        probe = _safe_probe(_probe_slack, b.get("bot_token", ""))
        bots.append({
            "platform": "slack",
            "id": b.get("id", ""),
            "name": b.get("name") or "Slack app",
            **probe,
        })

    # --- Legacy single Telegram target (notifications.env) ------------------
    legacy_token = _legacy_telegram_token()
    if legacy_token:
        probe = _safe_probe(_probe_telegram, legacy_token)
        bots.append({
            "platform": "telegram",
            "id": "legacy-telegram",
            "name": "Telegram (legacy)",
            **probe,
        })

    return _aggregate(bots)


def _safe_probe(fn, token: str) -> dict[str, Any]:
    """Run a probe, converting any unexpected error into a warn result."""
    try:
        return fn(token)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("bot_probe_failed", error=str(e))
        return {
            "status": "warn",
            "identity": None,
            "error": str(e)[:200],
            "error_code": None,
            "latency_ms": None,
        }


def _aggregate(bots: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"ok": 0, "warn": 0, "crit": 0, "disabled": 0}
    worst = "ok"
    for b in bots:
        st = b.get("status", "unknown")
        counts[st] = counts.get(st, 0) + 1
        if _RANK.get(st, 1) > _RANK.get(worst, 0):
            worst = st

    # No bots at all → "disabled" (nothing configured), not a fault.
    if not bots:
        worst = "disabled"

    configured = sum(1 for b in bots if b.get("status") != "disabled")
    defcon = min((_STATUS_DEFCON.get(b.get("status", "ok"), 3) for b in bots), default=5)

    tg = [b for b in bots if b["platform"] == "telegram"]
    sl = [b for b in bots if b["platform"] == "slack"]
    summary = " · ".join(
        p for p in (
            _platform_summary("telegram", tg),
            _platform_summary("slack", sl),
        ) if p
    ) or "אין בוטים מוגדרים"

    return {
        "status": worst,
        "defcon": defcon,
        "configured": configured,
        "counts": counts,
        "bots": bots,
        "summary": summary,
    }


def _platform_summary(label: str, items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    worst = "ok"
    for b in items:
        st = b.get("status", "unknown")
        if _RANK.get(st, 1) > _RANK.get(worst, 0):
            worst = st
    return f"{label}: {len(items)} {worst}"


# --- Persistence (write on live probe, read on dashboard refresh) ------------


def _result_path(state_dir: Path) -> Path:
    return state_dir / RESULT_FILE


def save_result(result: dict[str, Any], state_dir: Path) -> None:
    """Cache the aggregate result so the read-path avoids re-probing.

    Never raises: a state-dir that isn't writable (dev, read-only mount) just
    means the read-path falls back to a live probe. The sweep must not abort.
    """
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        payload = {"checked_at": time.time(), **result}
        _result_path(state_dir).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as e:
        log_suppressed("bot_connectivity_save", e)


def load_result(state_dir: Path) -> dict[str, Any] | None:
    """Read the last cached probe result, or None if absent/corrupt."""
    path = _result_path(state_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError) as e:
        log_suppressed("bot_connectivity_load", e)
        return None


def check_and_persist(state_dir: Path) -> dict[str, Any]:
    """Run the live probe and cache the result. Called once per sweep."""
    result = check_all_bots()
    save_result(result, state_dir)
    return result
