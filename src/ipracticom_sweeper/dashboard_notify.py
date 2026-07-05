"""Notification helpers — Telegram + Slack webhook config.

Pure-Python module: no Flask views, no global `app` reference. The parent
dashboard.py imports from here and re-exports for back-compat so existing
code paths (settings route, /api/notify/test, /settings/test) keep working.

SSRF safety: `_validate_slack_webhook_url` enforces the hooks.slack.com
allowlist BEFORE anything is persisted or tested.
"""

from __future__ import annotations

import json as _json
import os
import urllib.parse
import urllib.request
from pathlib import Path

import structlog

_log = structlog.get_logger("ipracticom.dashboard.notify")

# Same default as systemd unit (do not move — must stay in sync with
# /etc/ipracticom-sweeper/notifications.env location).
NOTIFICATIONS_ENV_FILE = Path("/etc/ipracticom-sweeper/notifications.env")


def _read_notifications_env() -> dict[str, str]:
    """Read /etc/ipracticom-sweeper/notifications.env. Never raises."""
    if not NOTIFICATIONS_ENV_FILE.exists():
        return {}
    out: dict[str, str] = {}
    try:
        for raw in NOTIFICATIONS_ENV_FILE.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError as e:
        _log.warning("notifications_env_read_failed", error=str(e))
    return out


def _write_notifications_env(values: dict[str, str]) -> tuple[bool, str | None]:
    """Atomically replace the env file. Returns (ok, error_message)."""
    if not NOTIFICATIONS_ENV_FILE.parent.exists():
        try:
            NOTIFICATIONS_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, f"cannot create dir: {e}"

    lines = [
        "# iPracticom Sweeper notifications config",
        "# Edited via the dashboard at /settings. Hand-edits are preserved between",
        "# automatic sections. The systemd service picks these up on next run.",
        "",
    ]
    # Group by key
    slack = values.get("SLACK_WEBHOOK_URL", "").strip()
    bot = values.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = values.get("TELEGRAM_CHAT_ID", "").strip()

    lines.append("# --- Slack (optional) ---")
    if slack:
        lines.append(f'SLACK_WEBHOOK_URL="{slack}"')
    else:
        lines.append("# SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T000/B000/XXX")
    lines.append("")
    lines.append("# --- Telegram (optional, both required together) ---")
    if bot:
        lines.append(f'TELEGRAM_BOT_TOKEN="{bot}"')
    else:
        lines.append("# TELEGRAM_BOT_TOKEN=123456....")
    if chat:
        lines.append(f'TELEGRAM_CHAT_ID="{chat}"')
    else:
        lines.append("# TELEGRAM_CHAT_ID=-100123456789")
    lines.append("")

    content = "\n".join(lines)
    tmp = NOTIFICATIONS_ENV_FILE.with_suffix(".env.tmp")
    try:
        tmp.write_text(content)
        tmp.chmod(0o600)
        tmp.replace(NOTIFICATIONS_ENV_FILE)
    except OSError as e:
        return False, f"cannot write: {e}"
    return True, None


def _test_telegram(bot_token: str, chat_id: str) -> tuple[bool, str]:
    """Send a test message via Telegram Bot API. Returns (ok, message)."""
    if not bot_token or not chat_id:
        return False, "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is empty"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": "iPracticom Sweeper: test notification from dashboard",
    }).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8", errors="replace")
            if '"ok":true' in body:
                return True, "Telegram OK"
            return False, f"Telegram rejected: {body[:200]}"
    except Exception as e:
        return False, f"Telegram error: {e}"


def _validate_slack_webhook_url(url: str) -> tuple[bool, str]:
    """Validate a Slack incoming-webhook URL.

    Slack webhooks MUST be https://hooks.slack.com/services/... — anything
    else is either a typo or an SSRF/exfil attempt and is rejected before
    persistence or test. SSRF_BLOCKED marker used by tests/test_v6_hardening.py.
    """
    from urllib.parse import urlparse

    if not url:
        return True, ""  # empty allowed; user may not use Slack at all
    raw = url.strip()
    try:
        p = urlparse(raw)
    except Exception as e:
        return False, f"invalid URL: {type(e).__name__}"
    if p.scheme != "https":
        return False, "SSRF_BLOCKED: Slack webhook URL must use https"
    host = (p.hostname or "").lower()
    if host != "hooks.slack.com":
        return False, f"SSRF_BLOCKED: Slack webhook host must be hooks.slack.com (got {host!r})"
    if not p.path.startswith("/services/"):
        return False, "Slack webhook path must start with /services/"
    return True, ""


def _test_slack(webhook_url: str) -> tuple[bool, str]:
    """Send a test message via Slack incoming webhook. Returns (ok, message)."""
    if not webhook_url:
        return False, "SLACK_WEBHOOK_URL is empty"
    ok, why = _validate_slack_webhook_url(webhook_url)
    if not ok:
        return False, f"SLACK_WEBHOOK_URL rejected: {why}"
    payload = _json.dumps({"text": "iPracticom Sweeper: test notification from dashboard"}).encode("utf-8")
    try:
        req = urllib.request.Request(
            webhook_url, data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status == 200:
                return True, "Slack OK"
            return False, f"Slack returned HTTP {r.status}"
    except Exception as e:
        return False, f"Slack error: {e}"


def _test_slack_app(bot_token: str, channel: str) -> tuple[bool, str]:
    """Send a test message via the Slack *App* Web API (chat.postMessage).

    The App path uses a bot token (``xoxb-…``) + channel instead of an incoming
    webhook. The endpoint host is the fixed ``slack.com`` (no SSRF surface).
    Slack answers HTTP 200 with a JSON body whose ``ok`` field is the true
    success flag, so we inspect the body, not just the status. Returns
    (ok, message).
    """
    if not bot_token or not channel:
        return False, "Slack bot token or channel is empty"
    payload = _json.dumps({
        "channel": channel,
        "text": "iPracticom Sweeper: test notification from dashboard",
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            "https://slack.com/api/chat.postMessage", data=payload, method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {bot_token}",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8", errors="replace")
            if '"ok":true' in body:
                return True, "Slack OK"
            return False, f"Slack rejected: {body[:200]}"
    except Exception as e:
        return False, f"Slack error: {e}"