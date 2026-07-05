"""Slack App bot-token health probe — the Slack counterpart of
``telegram_bot/health.py``.

A revoked / wrong Slack bot token means the sweeper's notifications go
nowhere. We verify it the same way the Telegram probe uses ``getMe``:
by calling Slack's ``auth.test`` — a read-only endpoint that validates the
token and returns the bot/team identity **without posting to any channel**
(so the health check itself never spams the ops channel).

Tracks consecutive failures so transient network blips don't page anyone.
Mirrors ``telegram_bot.health`` deliberately: same statuses (ok / warn /
crit / disabled), same tracker shape, so the self-monitoring aggregator can
treat both platforms uniformly.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .._log import log_suppressed

HEALTH_FILE = "slack_bot_health.json"
CONSECUTIVE_FAIL_THRESHOLD = 3
PROBE_TIMEOUT = 5.0

# Slack error codes that mean the token itself is dead — not a transient blip.
# Anything else (ratelimited, fatal_error, service unavailable) is treated as a
# warn so a passing storm doesn't escalate to crit.
_FATAL_TOKEN_ERRORS = frozenset({
    "invalid_auth",
    "account_inactive",
    "token_revoked",
    "token_expired",
    "not_authed",
    "no_permission",
})


@dataclass
class SlackBotHealthResult:
    """Single probe outcome (shape-compatible with BotHealthResult)."""

    status: str  # ok | warn | crit | disabled
    error_code: Optional[str]  # Slack returns string error codes, not ints
    bot_username: Optional[str]  # bot/user name, when auth.test succeeds
    team: Optional[str] = None
    error: Optional[str] = None
    latency_ms: Optional[float] = None
    timestamp: float = field(default_factory=time.time)


def _http_auth_test(token: str, timeout: float) -> tuple[int, dict]:
    """Raw HTTP call to Slack ``auth.test``. Returns (status_code, body_json).

    The token travels in the Authorization header (never in the URL/body), and
    the host is the fixed ``slack.com`` — no SSRF surface, same as the sender.
    """
    req = urllib.request.Request(
        "https://slack.com/api/auth.test",
        data=b"",  # POST with empty body; auth is header-only
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return resp.getcode(), json.loads(body) if body else {}


def probe_slack_token(
    token: str,
    timeout: float = PROBE_TIMEOUT,
) -> SlackBotHealthResult:
    """Probe a single Slack bot token via ``auth.test`` and return the result."""
    if not token:
        return SlackBotHealthResult(
            status="disabled",
            error_code=None,
            bot_username=None,
            error="no_token_configured",
        )

    started = time.time()
    try:
        code, body = _http_auth_test(token, timeout)
    except urllib.error.HTTPError as e:
        elapsed = (time.time() - started) * 1000
        # 401/403 at the HTTP layer = dead token; other codes = transient.
        return SlackBotHealthResult(
            status="crit" if e.code in (401, 403) else "warn",
            error_code=str(e.code),
            bot_username=None,
            error=str(e.reason)[:200],
            latency_ms=elapsed,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        elapsed = (time.time() - started) * 1000
        return SlackBotHealthResult(
            status="warn",
            error_code=None,
            bot_username=None,
            error=str(e)[:200],
            latency_ms=elapsed,
        )

    elapsed = (time.time() - started) * 1000

    # Slack returns HTTP 200 even for auth failures — the truth is in body.ok.
    if code == 200 and body.get("ok") is True:
        return SlackBotHealthResult(
            status="ok",
            error_code=None,
            bot_username=body.get("user"),
            team=body.get("team"),
            latency_ms=elapsed,
        )

    err = str(body.get("error") or f"unexpected_status_{code}")
    return SlackBotHealthResult(
        status="crit" if err in _FATAL_TOKEN_ERRORS else "warn",
        error_code=err,
        bot_username=None,
        error=err[:200],
        latency_ms=elapsed,
    )


# --- SlackTokenHealthTracker -------------------------------------------------


class SlackTokenHealthTracker:
    """Persist probe history to disk; expose last status + consecutive failures.

    Mirrors ``telegram_bot.health.TokenHealthTracker`` so both platforms share
    the same self-monitoring plumbing.
    """

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.path = state_dir / HEALTH_FILE
        self.last_status: str = "unknown"
        self.last_bot_username: Optional[str] = None
        self.last_error_code: Optional[str] = None
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
            log_suppressed("slack_health_read", e)
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

    def record(self, status: str, error_code: Optional[str] = None,
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

    def probe_if_configured(self, token: Optional[str]) -> SlackBotHealthResult:
        """Probe only if a token was provided. Returns disabled otherwise."""
        if not token:
            return SlackBotHealthResult(
                status="disabled",
                error_code=None,
                bot_username=None,
                error="no_token_configured",
            )
        result = probe_slack_token(token)
        self.record(
            status=result.status,
            error_code=result.error_code,
            bot_username=result.bot_username,
        )
        return result


def should_alert_admin(
    tracker: SlackTokenHealthTracker, threshold: int = CONSECUTIVE_FAIL_THRESHOLD
) -> bool:
    """Alert human after `threshold` consecutive crit failures."""
    return tracker.consecutive_failures >= threshold and tracker.last_status == "crit"
