"""Slice 8.3: Telegram bot token health probe.

Polls Telegram's `getMe` API to verify the bot token is valid. A revoked
or wrong token means the sweeper's notifications go nowhere — and we
need to alert the operator via a different channel (email, pager, log).

Tracks consecutive failures so transient network blips don't spam.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .._log import log_suppressed

HEALTH_FILE = "telegram_bot_health.json"
CONSECUTIVE_FAIL_THRESHOLD = 3
PROBE_TIMEOUT = 5.0


@dataclass
class BotHealthResult:
    """Single probe outcome."""

    status: str  # ok | warn | crit | disabled
    error_code: Optional[int]
    bot_username: Optional[str]
    error: Optional[str] = None
    latency_ms: Optional[float] = None
    timestamp: float = field(default_factory=time.time)


def _http_getme(url: str, token: str, timeout: float) -> tuple[int, dict]:
    """Raw HTTP call to Telegram getMe. Returns (status_code, body_json)."""
    req = urllib.request.Request(f"{url}/bot{token}/getMe", method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        import json as _json

        return resp.getcode(), _json.loads(body) if body else {}


def probe_bot_token(
    token: str,
    api_base: str = "https://api.telegram.org",
    timeout: float = PROBE_TIMEOUT,
) -> BotHealthResult:
    """Probe a single token and return the result."""
    if not token:
        return BotHealthResult(
            status="disabled",
            error_code=None,
            bot_username=None,
            error="no_token_configured",
        )

    started = time.time()
    try:
        code, body = _http_getme(api_base, token, timeout)
    except urllib.error.HTTPError as e:
        elapsed = (time.time() - started) * 1000
        return BotHealthResult(
            status="crit" if e.code in (401, 403) else "warn",
            error_code=e.code,
            bot_username=None,
            error=str(e.reason)[:200],
            latency_ms=elapsed,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        elapsed = (time.time() - started) * 1000
        return BotHealthResult(
            status="warn",
            error_code=None,
            bot_username=None,
            error=str(e)[:200],
            latency_ms=elapsed,
        )

    elapsed = (time.time() - started) * 1000

    if code == 200 and body.get("ok") is True:
        result = body.get("result", {})
        username = result.get("username")
        return BotHealthResult(
            status="ok",
            error_code=None,
            bot_username=username,
            latency_ms=elapsed,
        )
    if code in (401, 403):
        return BotHealthResult(
            status="crit",
            error_code=code,
            bot_username=None,
            error=body.get("description", "unauthorized")[:200],
            latency_ms=elapsed,
        )
    return BotHealthResult(
        status="warn",
        error_code=code,
        bot_username=None,
        error=f"unexpected_status_{code}",
        latency_ms=elapsed,
    )


# --- TokenHealthTracker ------------------------------------------------------

class TokenHealthTracker:
    """Persist probe history to disk; expose last status + consecutive failures."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.path = state_dir / HEALTH_FILE
        self.last_status: str = "unknown"
        self.last_bot_username: Optional[str] = None
        self.last_error_code: Optional[int] = None
        self.last_checked_at: Optional[float] = None
        self.consecutive_failures: int = 0
        self.history: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log_suppressed("telegram_health_read", e)
            return
        self.last_status = data.get("last_status", "unknown")
        self.last_bot_username = data.get("last_bot_username")
        self.last_error_code = data.get("last_error_code")
        self.last_checked_at = data.get("last_checked_at")
        self.consecutive_failures = int(data.get("consecutive_failures", 0))
        self.history = list(data.get("history", []))

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "last_status": self.last_status,
                    "last_bot_username": self.last_bot_username,
                    "last_error_code": self.last_error_code,
                    "last_checked_at": self.last_checked_at,
                    "consecutive_failures": self.consecutive_failures,
                    "history": self.history[-50:],  # cap
                }
            )
        )

    def record(self, status: str, error_code: Optional[int] = None,
               bot_username: Optional[str] = None) -> None:
        """Append a result. Resets consecutive_failures on ok."""
        self.last_status = status
        self.last_error_code = error_code
        self.last_bot_username = bot_username
        self.last_checked_at = time.time()
        if status in ("crit", "warn"):
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 0
        self.history.append({
            "ts": self.last_checked_at,
            "status": status,
            "error_code": error_code,
        })
        self._save()

    def probe_if_configured(self, token: Optional[str]) -> BotHealthResult:
        """Probe only if a token was provided. Returns disabled otherwise."""
        if not token:
            return BotHealthResult(
                status="disabled",
                error_code=None,
                bot_username=None,
                error="no_token_configured",
            )
        result = probe_bot_token(token)
        self.record(
            status=result.status,
            error_code=result.error_code,
            bot_username=result.bot_username,
        )
        return result


def should_alert_admin(
    tracker: TokenHealthTracker, threshold: int = CONSECUTIVE_FAIL_THRESHOLD
) -> bool:
    """Alert human after `threshold` consecutive crit failures."""
    return tracker.consecutive_failures >= threshold and tracker.last_status == "crit"


def resolve_token() -> Optional[str]:
    """Read TELEGRAM_BOT_TOKEN from the process env."""
    return os.environ.get("TELEGRAM_BOT_TOKEN")